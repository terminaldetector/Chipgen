"""harmony.py — chords, keys and progressions from a transcribed score.

Three things make this different from chord detection on a MIDI file.

**Chip harmony is usually incomplete.** One FM channel plays one note, and
there are six of them shared between bass, melody, drums and everything
else. A full triad is a luxury. Fifths, octaves and bare thirds carry the
harmony instead, so "C and G sounding" has to come back as C5 — a power
chord, confidently — and not as "unknown" or as a C major missing a note.

**A chord and an arpeggio look identical if you only look at onsets.**
C-E-G played together is a chord; C then E then G is an arpeggio of the
same chord, and the difference matters because one is harmony and the
other is a pattern. This reads note LENGTHS and asks what is actually
sounding at the same instant, then separately asks what harmony a window
of rows implies. Both get reported, tagged for which they are.

**Confidence, not verdicts.** Two pitch classes are genuinely ambiguous.
Reporting `G7 0.62 / Gm 0.21 / G5 0.17` is honest; reporting `G7` is not,
and a generator that stacks a progression on top of a false certainty
compounds it.

Roman numerals are relative to a detected key, so `Am F C G` in A minor
and `Em C G D` in E minor come back as the same progression — which is
the whole point of storing one.
"""

import collections
import os
import sys
from typing import NamedTuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

#: Chord templates as intervals above the root. Ordered roughly from
#: simplest to most specific; ties break toward the earlier entry, which
#: keeps a bare fifth reading as a power chord rather than as a triad
#: that happens to be missing a note.
TEMPLATES = (
    ("5",     (0, 7)),
    ("maj",   (0, 4, 7)),
    ("min",   (0, 3, 7)),
    ("dim",   (0, 3, 6)),
    ("aug",   (0, 4, 8)),
    ("sus2",  (0, 2, 7)),
    ("sus4",  (0, 5, 7)),
    ("maj6",  (0, 4, 7, 9)),
    ("min6",  (0, 3, 7, 9)),
    ("dom7",  (0, 4, 7, 10)),
    ("maj7",  (0, 4, 7, 11)),
    ("min7",  (0, 3, 7, 10)),
    ("m7b5",  (0, 3, 6, 10)),
    ("dim7",  (0, 3, 6, 9)),
    ("add9",  (0, 2, 4, 7)),
)

#: Krumhansl-Kessler key profiles: how strongly each scale degree belongs
#: to a major and a minor key, from listener ratings. Used by correlation
#: against the corpus's own pitch-class histogram.
_MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39,
                  3.66, 2.29, 2.88)
_MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98,
                  2.69, 3.34, 3.17)

#: Roman numerals by semitone above the tonic, for each mode.
_ROMAN_MAJOR = ("I", "bII", "II", "bIII", "III", "IV", "#IV", "V", "bVI",
                "VI", "bVII", "VII")
_ROMAN_MINOR = ("i", "bII", "ii", "III", "iii", "iv", "#iv", "v", "VI",
                "vi", "VII", "vii")


class Chord(NamedTuple):
    row: int
    root: int            #: pitch class 0-11
    quality: str
    confidence: float
    bass: int            #: pitch class of the lowest sounding note
    pitches: tuple       #: pitch classes that produced the label
    simultaneous: bool   #: True if actually sounding together, False if implied

    @property
    def name(self) -> str:
        return f"{NOTE_NAMES[self.root]}{'' if self.quality == 'maj' else self.quality}"

    @property
    def inverted(self) -> bool:
        return self.bass != self.root


# --------------------------------------------------------------------------
# Labelling one set of pitch classes
# --------------------------------------------------------------------------
def weigh(score, row: int, window: int, voices=None):
    """-> {pitch class: rows it sounds for} over [row, row+window).

    Weighting by duration is what keeps a melody from rewriting the
    harmony. Collected as a flat set of pitch classes, a C major triad
    under a melody that passes through B and D reads as Cmaj7 or Cadd9 —
    measured on the corpus that way, maj7 and sus2 came out as the second
    and fourth most common "chords" in Mega Drive music, which they are
    not. A passing note is short. Weighting by how long each pitch class
    actually sounds, and then dropping the ones far below the strongest,
    leaves the notes that are holding the chord up.
    """
    weights = collections.Counter()
    for name, notes in score.voices.items():
        if voices is not None and name not in voices:
            continue
        for note in notes:
            overlap = min(note.end, row + window) - max(note.row, row)
            if overlap > 0:
                weights[note.pitch % 12] += overlap
    return weights


def strong(weights, floor: float = 0.34):
    """Pitch classes carrying at least `floor` of the strongest one's weight."""
    if not weights:
        return []
    top = max(weights.values())
    return [pc for pc, w in weights.items() if w >= top * floor]


def label(pitch_classes, bass: int = None, limit: int = 3):
    """-> [(root, quality, confidence)] best first, confidences summing to 1.

    Scored by Jaccard overlap between what is sounding and what the
    template expects. Jaccard rather than plain coverage because coverage
    alone lets every larger template swallow a smaller one: {C, G} covers
    C major's root and fifth just as well as it covers C5, and only
    counting the template's UNMATCHED notes against it separates them.
    """
    observed = frozenset(p % 12 for p in pitch_classes)
    if not observed:
        return []
    if len(observed) == 1:
        only = next(iter(observed))
        return [(only, "unison", 1.0)]

    scored = []
    for root in range(12):
        for quality, intervals in TEMPLATES:
            expected = frozenset((root + i) % 12 for i in intervals)
            union = observed | expected
            fit = len(observed & expected) / len(union)
            if bass is not None and bass % 12 == root:
                fit += 0.05          # the bass note is evidence for the root
            scored.append((fit, root, quality))
    scored.sort(key=lambda row: -row[0])

    best = scored[0][0]
    if best <= 0.0:
        return []
    # Softmax over the fits rather than a share of the total. Sharing the
    # total made an EXACT match report 0.40, because a dozen partial
    # matches sit close enough to dilute it — {C,E,G} is unambiguously C
    # major and should say so. The temperature is low enough that a perfect
    # fit dominates and high enough that a real tie stays a tie: C min7 and
    # Eb6 are literally the same four pitch classes, and only the bass note
    # separates them, so they come back near 58/42 rather than as a verdict.
    keep = [row for row in scored[:32] if row[0] >= best * 0.5][:limit]
    weights = [_EXP(fit / _TEMPERATURE - best / _TEMPERATURE)
               for fit, _, _ in keep]
    total = sum(weights)
    return [(root, quality, weight / total)
            for (fit, root, quality), weight in zip(keep, weights)]


#: Softmax temperature for chord confidence. Fits are Jaccard overlaps in
#: 0..1, so 0.15 puts an exact match near 0.95 and leaves two identical
#: pitch-class sets close to even.
_TEMPERATURE = 0.15


def _EXP(x):
    import math
    return math.exp(max(-60.0, x))


# --------------------------------------------------------------------------
# What is sounding, and what is implied
# --------------------------------------------------------------------------
def sounding_at(score, row: int, voices=None):
    """Pitches actually held at `row` — the ones whose note spans it."""
    out = []
    for name, notes in score.voices.items():
        if voices is not None and name not in voices:
            continue
        for note in notes:
            if note.row <= row < note.end:
                out.append(note.pitch)
    return out


def onsets(score, voices=None):
    """Rows where any note starts, sorted."""
    rows = set()
    for name, notes in score.voices.items():
        if voices is not None and name not in voices:
            continue
        rows.update(note.row for note in notes)
    return sorted(rows)


def chords(score, voices=None, window: int = None, min_voices: int = 2):
    """Chord per onset row. Simultaneous where possible, implied otherwise.

    `window` is how many rows of context to gather when fewer than
    `min_voices` notes are literally sounding together — that is where an
    arpeggio gets recognised as the chord it outlines. Defaults to one
    beat, which is the span an arpeggio normally fills.
    """
    window = window if window is not None else max(1, score.lpb)
    out = []
    for row in onsets(score, voices):
        held = sounding_at(score, row, voices)
        if not held:
            continue
        # Weigh across a beat, not across the onset row alone. Inside a
        # single row every note that is sounding has the same weight of 1,
        # so duration weighting cannot separate a held chord tone from a
        # passing note and a triad under a melody touching the seventh
        # still reads as a maj7. A beat is the span over which a chord
        # tone outlasts a passing note.
        weights = weigh(score, row, window, voices)
        chord_tones = strong(weights)
        if not chord_tones:
            continue
        # Simultaneity is a separate question from what the chord is: it
        # says whether this harmony was played as a chord or spelled out
        # as an arpeggio, which is what tells a pattern from a progression.
        together = len(set(p % 12 for p in held))
        simultaneous = together >= min_voices
        ranked = label(chord_tones, bass=min(held))
        if not ranked:
            continue
        root, quality, confidence = ranked[0]
        out.append(Chord(row=row, root=root, quality=quality,
                         confidence=confidence, bass=min(held) % 12,
                         pitches=tuple(sorted(chord_tones)),
                         simultaneous=simultaneous))
    return out


# --------------------------------------------------------------------------
# Key
# --------------------------------------------------------------------------
def pitch_class_weights(score, voices=None):
    """Pitch-class histogram weighted by how long each note sounds."""
    weights = [0.0] * 12
    for name, notes in score.voices.items():
        if voices is not None and name not in voices:
            continue
        for note in notes:
            weights[note.pitch % 12] += note.length
    return weights


def key_of(score, voices=None, alternatives: int = 0):
    """-> (tonic pitch class, 'major'|'minor', confidence 0..1).

    Correlation against the Krumhansl-Kessler profiles. Confidence is how
    far the winner sits above the AVERAGE candidate, not above the
    runner-up. The runner-up is almost always the relative major or minor,
    which shares all seven pitches and therefore always scores nearly as
    well: measured across the corpus, a margin-to-runner-up confidence
    averaged 0.11 and was really reporting "you cannot tell C major from A
    minor by pitch content", which is true of any method and not news
    about this track. Against the mean it averages 0.59 and tracks
    something useful — how much better this key explains the notes than an
    arbitrary one does.

    Pass `alternatives` to get the runners-up too, since the relative-key
    ambiguity is real and worth seeing rather than hiding.
    """
    weights = pitch_class_weights(score, voices)
    if sum(weights) <= 0:
        return None, None, 0.0
    scored = []
    for tonic in range(12):
        for mode, profile in (("major", _MAJOR_PROFILE),
                              ("minor", _MINOR_PROFILE)):
            rotated = [profile[(i - tonic) % 12] for i in range(12)]
            scored.append((_correlation(weights, rotated), tonic, mode))
    scored.sort(key=lambda row: -row[0])
    best, tonic, mode = scored[0]
    rest = [row[0] for row in scored[1:]]
    mean = sum(rest) / len(rest)
    spread = best - min(rest)
    confidence = 0.0 if spread <= 0 else max(0.0, (best - mean) / spread)
    if alternatives:
        others = [(t, m, (fit - mean) / spread if spread > 0 else 0.0)
                  for fit, t, m in scored[1:1 + alternatives]]
        return tonic, mode, confidence, others
    return tonic, mode, confidence


def _correlation(a, b) -> float:
    n = len(a)
    mean_a, mean_b = sum(a) / n, sum(b) / n
    da = [v - mean_a for v in a]
    db = [v - mean_b for v in b]
    denom = (sum(v * v for v in da) ** 0.5) * (sum(v * v for v in db) ** 0.5)
    return sum(da[i] * db[i] for i in range(n)) / denom if denom else 0.0


# --------------------------------------------------------------------------
# Progressions
# --------------------------------------------------------------------------
def roman(root: int, quality: str, tonic: int, mode: str) -> str:
    """Chord as a scale degree, so the same progression in any key matches."""
    degree = (root - tonic) % 12
    numerals = _ROMAN_MINOR if mode == "minor" else _ROMAN_MAJOR
    symbol = numerals[degree]
    if quality in ("min", "min7", "min6") and symbol.isupper():
        symbol = symbol.lower()
    elif quality in ("maj", "maj7", "maj6", "dom7") and symbol.islower():
        symbol = symbol.upper()
    suffix = {"dom7": "7", "maj7": "maj7", "min7": "7", "dim": "o",
              "dim7": "o7", "m7b5": "0", "aug": "+", "sus2": "sus2",
              "sus4": "sus4", "5": "5", "add9": "add9"}.get(quality, "")
    return symbol + suffix


def per_bar(score, voices=None, min_confidence: float = 0.3):
    """One chord per bar — the harmonic rhythm most progressions move at.

    The chord with the most weight in the bar wins. Taking every onset's
    chord instead produces a "progression" that is mostly passing notes.
    """
    found = chords(score, voices)
    bars = collections.defaultdict(collections.Counter)
    for chord in found:
        if chord.confidence < min_confidence:
            continue
        weight = chord.confidence * (1.5 if chord.simultaneous else 1.0)
        bars[chord.row // score.bar][(chord.root, chord.quality)] += weight
    out = []
    for bar in sorted(bars):
        (root, quality), weight = bars[bar].most_common(1)[0]
        total = sum(bars[bar].values())
        out.append((bar, root, quality, weight / total if total else 0.0))
    return out


def progression(score, voices=None, length: int = 4):
    """-> (key, [roman numerals]) for the whole score, one chord per bar."""
    tonic, mode, key_confidence = key_of(score, voices)
    if tonic is None:
        return None, []
    sequence = [roman(root, quality, tonic, mode)
                for _, root, quality, _ in per_bar(score, voices)]
    return (tonic, mode, key_confidence), sequence


def common_progressions(sequence, length: int = 4, limit: int = 5,
                        min_distinct: int = 2):
    """Most repeated N-chord windows in a numeral sequence.

    `min_distinct` drops windows that never change chord. Static harmony
    over an ostinato is real and common in chip music, but reporting
    "i - i - i - i" as the corpus's top progression describes the counting
    method rather than the music.
    """
    if len(sequence) < length:
        return []
    windows = collections.Counter(
        tuple(sequence[i:i + length])
        for i in range(len(sequence) - length + 1)
        if len(set(sequence[i:i + length])) >= min_distinct)
    return windows.most_common(limit)


def key_name(tonic: int, mode: str) -> str:
    return f"{NOTE_NAMES[tonic]} {mode}"
