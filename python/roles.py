"""roles.py — work out what each voice is DOING, before extracting anything from it.

Extracting patterns per channel and calling them "the fm2 patterns" is
worthless, because fm2 is a bass on one track and a counter-melody on the
next. The channel number is a wiring detail. Measured over the embedded
corpus, FM0 — the channel everyone calls "the bass channel" — is the
lowest-pitched FM voice on only 41% of tracks; FM1 or FM2 takes the bass
on half of them.

So a role has to be inferred from the part itself. The features are the
ones that survive transcription from a register log:

- **register rank within the track**, not absolute pitch. A bass is the
  lowest thing playing, and what counts as low depends on the track.
- **density** in notes per bar.
- **repetition** — how often the pitch simply stays put.
- **root motion** — the share of intervals that are unisons, fourths,
  fifths or octaves. This is the bass signature that survives everything
  else; a bass line moves by scale degree, a melody moves by step.
- **cycling** — whether the pitch sequence repeats with a short period,
  which is what an arpeggio is.
- **simultaneity** — how often this voice's onsets land with another
  voice's. Harmony moves with things; a lead moves against them.

Note length is deliberately weighted low. 67.5% of FM notes in the corpus
are exactly one row long, so duration carries much less information here
than it would in a MIDI file, and a classifier that leans on it is
leaning on a transcription artefact as much as on the music.

Confidence, not labels. A voice that plays low repeated notes under a
melody is a bass; a voice that plays low repeated notes and nothing else
is happening is a bass OR an ostinato lead, and saying so is more useful
than picking one.
"""

import collections
import os
import statistics
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

#: Intervals a bass line lives on: staying put, fourths, fifths, octaves.
_ROOT_INTERVALS = {0, 5, 7, 12, -5, -7, -12}
#: Longest repeat period we will call "a cycle" rather than "a tune".
_MAX_CYCLE = 6
#: A voice with fewer notes than this cannot be classified honestly.
MIN_NOTES = 8


def features(name: str, score, notes=None) -> dict:
    """Everything the classifier reasons about, for one voice."""
    notes = notes if notes is not None else score.voices.get(name, [])
    if len(notes) < MIN_NOTES:
        return {}
    pitches = [n.pitch for n in notes]
    lengths = [n.length for n in notes]
    steps = [pitches[i + 1] - pitches[i] for i in range(len(pitches) - 1)]
    bars = max(1.0, score.rows / score.bar)

    peers = {v: ns for v, ns in score.voices.items()
             if len(ns) >= MIN_NOTES and v != name}
    medians = sorted(statistics.median(n.pitch for n in ns)
                     for ns in peers.values())
    mine = statistics.median(pitches)
    if medians:
        below = sum(1 for m in medians if m < mine)
        register_rank = below / len(medians)
    else:
        register_rank = 0.5

    # Simultaneity has to be measured as EXCESS over chance, not as raw
    # overlap. On a busy track the other voices occupy nearly every row
    # that has any onset at all — measured on one corpus track, peers
    # covered 493 of 501 onset rows — so a raw overlap of 0.7 is what
    # random placement would give and says nothing about whether this
    # voice moves WITH anything. Lift: 0.0 means "no more together than
    # chance", 1.0 means "always together".
    my_rows = {n.row for n in notes}
    peer_rows = set()
    for ns in peers.values():
        peer_rows.update(n.row for n in ns)
    span = max(1, score.rows)
    expected = min(0.99, len(peer_rows) / span)
    observed = (len(my_rows & peer_rows) / len(my_rows)) if my_rows else 0.0
    simultaneity = max(0.0, (observed - expected) / (1.0 - expected))

    return {
        "notes": len(notes),
        "register_rank": register_rank,
        "median_pitch": mine,
        "span": max(pitches) - min(pitches),
        "density": len(notes) / bars,
        "median_length": statistics.median(lengths),
        "repeat": _share(steps, lambda s: s == 0),
        "step": _share(steps, lambda s: 0 < abs(s) <= 2),
        "leap": _share(steps, lambda s: abs(s) > 4),
        "root_motion": _share(steps, lambda s: s in _ROOT_INTERVALS),
        "cycle": _cycle_strength(pitches),
        # Thirds, fourths, fifths, sixths and octaves: the intervals an
        # arpeggio walks. A melody uses seconds; a bass uses fourths and
        # fifths, which is why this alone does not identify an arpeggio.
        "chord_tone": _share(steps, lambda s: abs(s) in (3, 4, 5, 7, 8, 9, 12)),
        "simultaneity": simultaneity,
    }


def _share(values, predicate) -> float:
    return (sum(1 for v in values if predicate(v)) / len(values)
            if values else 0.0)


def _cycle_strength(pitches) -> float:
    """Best short-period self-similarity of the pitch sequence, 0..1.

    An arpeggio is a cycle: the same three or four pitches over and over.
    A melody is not. Checked against every period up to _MAX_CYCLE and the
    best one wins, because the cycle length is exactly what we do not know.
    """
    if len(pitches) < 8:
        return 0.0
    best = 0.0
    for period in range(2, _MAX_CYCLE + 1):
        pairs = len(pitches) - period
        if pairs < 4:
            break
        matches = sum(1 for i in range(pairs)
                      if pitches[i] == pitches[i + period])
        best = max(best, matches / pairs)
    return best


#: Each role is a set of (feature, target, weight). Targets on rank-like
#: features are 0..1; targets on rates are shares. Weights say which
#: features actually define the role rather than merely correlate.
ROLE_PROFILES = {
    "bass": {
        "register_rank": (0.0, 3.0),
        "root_motion": (0.75, 2.0),
        "leap": (0.15, 1.0),
        "span": (10.0, 0.8, 24.0),
    },
    # Calibrated against the corpus rather than against what a melody is
    # supposed to look like. Measured on the highest-register voice of each
    # track, the median has step motion 0.23 and cycle strength 0.53: chip
    # leads are riffs, not flowing melodies, and a profile that wants
    # stepwise motion and no repetition finds a lead on 13 tracks out of 75.
    "lead": {
        "register_rank": (1.0, 2.5),
        "density": (5.0, 1.0, 16.0),
        "leap": (0.24, 0.8),
        "span": (16.0, 0.8, 24.0),
    },
    "harmony": {
        "simultaneity": (0.85, 2.5),
        "register_rank": (0.5, 1.0),
        "density": (3.0, 0.8, 16.0),
        "leap": (0.2, 0.8),
    },
    # An arpeggio is not merely repetitive — a lead is repetitive too. It
    # is repetitive AND moves by chord tones AND stays inside a narrow
    # span. All three, or the role collapses into "lead".
    "arp": {
        "cycle": (0.85, 2.5),
        "chord_tone": (0.8, 2.0),
        "density": (10.0, 1.2, 16.0),
        "span": (12.0, 1.0, 24.0),
    },
    "counter": {
        "register_rank": (0.6, 1.2),
        "simultaneity": (0.25, 2.0),
        "step": (0.45, 1.0),
        "density": (4.0, 0.8, 16.0),
    },
    "pad": {
        "median_length": (8.0, 2.5, 16.0),
        "density": (1.5, 1.5, 16.0),
        "simultaneity": (0.7, 1.0),
    },
}


def classify(name: str, score, notes=None) -> dict:
    """-> {role: confidence}, summing to 1, best first. {} if too few notes."""
    f = features(name, score, notes)
    if not f:
        return {}
    fits = {}
    for role, profile in ROLE_PROFILES.items():
        total = 0.0
        penalty = 0.0
        for feature, spec in profile.items():
            target, weight = spec[0], spec[1]
            scale = spec[2] if len(spec) > 2 else 1.0
            value = f.get(feature)
            if value is None:
                continue
            distance = min(1.0, abs(value - target) / scale)
            penalty += weight * distance
            total += weight
        fits[role] = max(0.0, 1.0 - penalty / total) if total else 0.0
    # Sharpen before normalising: raw fits sit in a narrow band because
    # every role shares features with its neighbours, and reporting six
    # roles at 0.16 each would be true and useless.
    sharpened = {role: fit ** 4 for role, fit in fits.items()}
    grand = sum(sharpened.values())
    if grand <= 0:
        return {}
    return dict(sorted(((role, value / grand)
                        for role, value in sharpened.items()),
                       key=lambda kv: -kv[1]))


def classify_score(score) -> dict:
    """-> {voice: {role: confidence}} for every voice worth classifying."""
    return {name: classify(name, score)
            for name, notes in score.voices.items()
            if len(notes) >= MIN_NOTES}


def best_role(name: str, score, minimum: float = 0.0):
    """-> (role, confidence) or (None, 0.0)."""
    ranked = classify(name, score)
    if not ranked:
        return None, 0.0
    role, confidence = next(iter(ranked.items()))
    return (role, confidence) if confidence >= minimum else (None, confidence)


def format_score(score) -> str:
    lines = [f"{score.title}   {score.bpm:.0f} BPM, {score.rows} rows",
             f"  {'voice':6s} {'notes':>5s} {'reg':>5s} {'dens':>5s} "
             f"{'root':>5s} {'cyc':>5s} {'simul':>6s}  role"]
    for name in sorted(score.voices):
        f = features(name, score)
        if not f:
            continue
        ranked = classify(name, score)
        top = "  ".join(f"{r} {c:.2f}" for r, c in list(ranked.items())[:2])
        lines.append(
            f"  {name:6s} {f['notes']:5d} {f['register_rank']:5.2f} "
            f"{f['density']:5.1f} {f['root_motion']:5.2f} {f['cycle']:5.2f} "
            f"{f['simultaneity']:6.2f}  {top}")
    return "\n".join(lines)
