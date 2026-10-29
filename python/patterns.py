"""patterns.py — extract reusable musical patterns, not note sequences.

A pattern stored as `C2 C2 G2 C3` is worth nothing: it only fits one key.
Stored as `0 0 +7 +12` with its rhythm, the same pattern plays in A minor,
E minor and C minor, which is what makes a pattern bank a bank rather than
a scrapbook.

So every pattern here is **transposition-invariant**: intervals from its
own first note, plus onsets and lengths in rows. Two bass lines a fifth
apart are one pattern seen twice, and that is the whole point — a pattern
that occurs once is an accident, and only counting occurrences separates
the idiom from the accident.

Three levels, because collapsing them produces a bag of unrelated
fragments:

- **cell** — the rhythm alone, pitches discarded. `x--x-x--`.
- **pattern** — one bar of intervals and rhythm. The unit that gets reused.
- **phrase** — four bars, which is where repetition-with-variation lives.

Patterns are extracted per voice AFTER the voice has a role, because
pooling a bass line with a counter-melody because both came from fm2 is
exactly the mistake that makes a pattern bank useless. See roles.py: the
channel number does not tell you the role.
"""

import collections
import os
import sys
from typing import NamedTuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import roles as roles_mod
import score_model

#: A pattern with fewer notes than this is a fragment, not a pattern.
MIN_NOTES = 3
#: Intervals beyond this are almost always a transcription artefact.
MAX_INTERVAL = 24


class Pattern(NamedTuple):
    intervals: tuple      #: semitones from the pattern's own first note
    onsets: tuple         #: row of each note, from the pattern's start
    lengths: tuple        #: length of each note in rows
    span_rows: int        #: how many rows the pattern occupies
    lpb: int              #: rows per beat, needed to know what "on the beat" is
    role: str
    source: str           #: track it came from

    @property
    def key(self):
        """Identity for counting: shape and rhythm, not role or source."""
        return (self.intervals, self.onsets, self.lengths)

    @property
    def cell(self):
        """The rhythm alone — the same pattern's skeleton."""
        return (self.onsets, self.span_rows)

    @property
    def notes(self) -> int:
        return len(self.intervals)

    @property
    def density(self) -> float:
        return self.notes / max(1, self.span_rows)

    @property
    def syncopation(self) -> float:
        """Share of onsets that miss a beat. 0 = every note on the beat.

        Against the score's own rows-per-beat, not a fixed 4. Assuming 4
        made a straight run of eighth notes at lpb 2 report 75%
        syncopated, which is the opposite of true.
        """
        if not self.onsets or self.lpb <= 0:
            return 0.0
        return sum(1 for o in self.onsets if o % self.lpb) / len(self.onsets)

    @property
    def melodic(self) -> bool:
        """False for a repeated single pitch — real, common, and trivial.

        These dominate any frequency count: every ostinato in the corpus
        collapses to the same all-zero shape whatever pitch it sits on. Worth
        counting, worth reporting separately, not worth burying the tunes
        under.
        """
        return any(v != self.intervals[0] for v in self.intervals)

    def contour(self) -> str:
        """Up/down/flat shape, the coarsest useful melodic signature."""
        out = []
        for i in range(1, len(self.intervals)):
            step = self.intervals[i] - self.intervals[i - 1]
            out.append("=" if step == 0 else ("+" if step > 0 else "-"))
        return "".join(out)

    def transpose_to(self, root: int):
        """-> [(row offset, pitch, length)] with this pattern rooted at `root`."""
        return [(self.onsets[i], root + self.intervals[i], self.lengths[i])
                for i in range(len(self.intervals))]

    def __repr__(self):
        shape = " ".join(f"{v:+d}" if v else "0" for v in self.intervals)
        return f"<{self.role} [{shape}] over {self.span_rows} rows>"


def extract(score, bars: int = 1, roles_by_voice=None):
    """Every `bars`-long window of every voice, as transposable Patterns."""
    roles_by_voice = roles_by_voice or {}
    span = score.bar * bars
    out = []
    for name, notes in score.voices.items():
        if len(notes) < MIN_NOTES:
            continue
        role = roles_by_voice.get(name)
        if role is None:
            role = roles_mod.best_role(name, score)[0] or "unknown"
        by_window = collections.defaultdict(list)
        for note in notes:
            by_window[note.row // span].append(note)
        for window, group in sorted(by_window.items()):
            pattern = _make(group, window * span, span, score.lpb, role,
                            score.title)
            if pattern is not None:
                out.append(pattern)
    return out


def _make(group, start: int, span: int, lpb: int, role: str, source: str):
    group = sorted(group, key=lambda n: (n.row, n.pitch))
    if len(group) < MIN_NOTES:
        return None
    base = group[0].pitch
    intervals = tuple(n.pitch - base for n in group)
    if any(abs(v) > MAX_INTERVAL for v in intervals):
        return None
    return Pattern(intervals=intervals,
                   onsets=tuple(n.row - start for n in group),
                   lengths=tuple(min(n.length, span) for n in group),
                   span_rows=span, lpb=lpb, role=role, source=source)


# --------------------------------------------------------------------------
# The bank
# --------------------------------------------------------------------------
class Bank:
    """Patterns counted by shape, kept per role."""

    def __init__(self):
        self.by_key = {}
        self.counts = collections.Counter()
        self.sources = collections.defaultdict(set)

    def add(self, pattern: Pattern):
        key = (pattern.role, pattern.key)
        self.by_key.setdefault(key, pattern)
        self.counts[key] += 1
        self.sources[key].add(pattern.source)

    def extend(self, patterns):
        for pattern in patterns:
            self.add(pattern)
        return self

    def shapes(self, role: str = None, limit: int = 10, min_tracks: int = 2):
        """Recurring MELODIC shapes, matched on contour and rhythm.

        Exact interval matching finds almost nothing shared between tracks
        — measured, 2893 distinct one-bar patterns reused 2.11 times each,
        nearly all of that inside a single track. Contour plus rhythm is
        the coarser equivalence that actually recurs across composers:
        "up a step, up a step, down a third, on these rows" is a shape two
        different pieces can both use.
        """
        groups = collections.defaultdict(lambda: [0, set(), None])
        for key, count in self.counts.items():
            pattern = self.by_key[key]
            if role is not None and key[0] != role:
                continue
            if not pattern.melodic:
                continue
            shape = (key[0], pattern.contour(), pattern.onsets)
            row = groups[shape]
            row[0] += count
            row[1] |= self.sources[key]
            if row[2] is None:
                row[2] = pattern
        rows = [(count, len(tracks), example)
                for count, tracks, example in groups.values()
                if len(tracks) >= min_tracks]
        rows.sort(key=lambda r: (-r[1], -r[0]))
        return rows[:limit]

    def common(self, role: str = None, limit: int = 10, min_tracks: int = 2,
               melodic_only: bool = False):
        """Most-used patterns. `min_tracks` is what separates an idiom from
        one composer's habit inside one track — a pattern repeated forty
        times in a single piece is that piece's ostinato, not the genre's.
        """
        rows = []
        for key, count in self.counts.items():
            if role is not None and key[0] != role:
                continue
            if len(self.sources[key]) < min_tracks:
                continue
            if melodic_only and not self.by_key[key].melodic:
                continue
            rows.append((count, len(self.sources[key]), self.by_key[key]))
        rows.sort(key=lambda r: (-r[1], -r[0]))
        return rows[:limit]

    def cells(self, role: str = None, limit: int = 10, min_tracks: int = 3):
        """Rhythms alone, pooled across every pattern that uses them."""
        return self._vocabulary(lambda p: p.onsets, role, limit, min_tracks)

    def contours(self, role: str = None, limit: int = 10, min_tracks: int = 3,
                 min_length: int = 4):
        """Melodic shapes alone, ignoring rhythm and exact intervals.

        `min_length` is a floor on how many moves a shape has to make
        before it counts. Without it the list is "-+", "+-", "--" — true,
        shared by eighteen tracks each, and saying nothing: every melody
        ever written contains a step up followed by a step down.
        """
        def index(pattern):
            shape = pattern.contour()
            if len(shape) < min_length:
                return None
            # A shape has to move somewhere. "====" clears any length
            # floor and is a repeated note, which is the same trivial case
            # that dominated the pattern counts before `melodic` existed.
            return shape if any(c != "=" for c in shape) else None
        return self._vocabulary(index, role, limit, min_tracks)

    def interval_sets(self, role: str = None, limit: int = 10,
                      min_tracks: int = 3):
        """Which intervals a part uses at all, order and rhythm discarded."""
        return self._vocabulary(
            lambda p: tuple(sorted(set(p.intervals))) if p.melodic else None,
            role, limit, min_tracks)

    def _vocabulary(self, index, role, limit, min_tracks):
        groups = collections.defaultdict(lambda: [0, set(), None])
        for key, count in self.counts.items():
            if role is not None and key[0] != role:
                continue
            pattern = self.by_key[key]
            token = index(pattern)
            if token is None:
                continue
            row = groups[token]
            row[0] += count
            row[1] |= self.sources[key]
            if row[2] is None:
                row[2] = pattern
        rows = [(token, count, len(tracks), example)
                for token, (count, tracks, example) in groups.items()
                if len(tracks) >= min_tracks]
        rows.sort(key=lambda r: (-r[2], -r[1]))
        return rows[:limit]

    def coverage(self, index, min_tracks: int = 3) -> float:
        """Share of all instances explained by tokens shared across tracks.

        This is the number that decides what a pattern bank should store.
        Measured over the embedded corpus, 79 tracks from 10 games:

            exact intervals + rhythm + lengths      1.8%
            intervals + onsets                      2.3%
            contour + onsets                        3.8%
            interval set, order-free               24.6%
            rhythm cell alone                      32.1%
            contour alone                          35.6%

        Rhythm and melodic contour each transfer between tracks. Their
        COMBINATION does not — fusing them into one "pattern" drops
        coverage from a third of the corpus to a fortieth. So the bank
        stores them as separate vocabularies to be recombined, rather than
        as whole patterns to be replayed. A bank of whole patterns built
        from this corpus would have 5 entries.
        """
        groups = collections.defaultdict(lambda: [0, set()])
        for key, count in self.counts.items():
            token = index(self.by_key[key])
            if token is None:
                continue
            row = groups[token]
            row[0] += count
            row[1] |= self.sources[key]
        total = sum(self.counts.values())
        shared = sum(c for c, tracks in groups.values()
                     if len(tracks) >= min_tracks)
        return shared / total if total else 0.0

    def __len__(self):
        return len(self.counts)


def build(scores, bars: int = 1) -> Bank:
    bank = Bank()
    for score in scores:
        by_voice = {name: (roles_mod.best_role(name, score)[0] or "unknown")
                    for name in score.voices}
        bank.extend(extract(score, bars, by_voice))
    return bank


def format_cell(onsets, span: int) -> str:
    """`x--x-x--` — one character per row."""
    marks = set(onsets)
    return "".join("x" if r in marks else "-" for r in range(span))
