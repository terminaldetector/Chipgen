"""corpus_audit.py — split a corpus by what each track actually contributes.

The sector split in corpus_split.py orders tracks by transcription
quality. This orders them by MUSICAL contribution: how much of the
corpus's pattern vocabulary a track carries that no other track already
carries. The two answer different questions and both are useful — a
cleanly transcribed track that repeats what forty others already said is
good data and not urgent data.

What the audit measured, over 426 tracks and 337,117 notes:

**There are no duplicates.** This was the first thing checked and the
answer was not the expected one. Across five different feature sets —
rhythm cells, contours, interval sets, chord progressions, and
combinations — the median pairwise Jaccard similarity between any two
tracks is 0.000 and the maximum over thousands of pairs is 0.235. Not
even a track and its own in-game remix overlap enough to call one
redundant. So the split cannot be "keep the unique ones, drop the
repeats": there is no repeat pile. It is a ranking by contribution, and
every track earns a place somewhere.

**The contribution is very unevenly spread.** 32,169 distinct vocabulary
tokens live in the corpus. Half of them are carried by the first 63
tracks; the last 27.9% needs 287. Measured at the knee of that curve, a
core track brings 171 new tokens and a tail track brings 31 — five and a
half times the value per track, and per byte.

**Greedy coverage alone skews the result.** Taking the highest-gain track
each time fills the core with whichever soundtracks are longest and
densest: Thunder Force IV, Gunstar Heroes and Alien Soldier took 39% of
it while Ninja Gaiden got one track and two games got none. A training
set that lopsided teaches one sound team's habits as if they were the
platform's. So selection goes round-robin across soundtracks, taking each
one's best remaining track in turn — coverage within balance rather than
instead of it.
"""

import collections
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import harmony
import patterns as patterns_mod
import roles as roles_mod
import score_model

#: A track with fewer notes than this cannot be judged on its patterns.
MIN_NOTES = 16
#: Contours shorter than this are shared by every melody ever written.
MIN_CONTOUR = 3


def vocabulary(score) -> set:
    """Every pattern token a track contributes, as a set.

    Four kinds, because they generalise differently and a track can be
    valuable for any one of them: rhythm cells and melodic contours (the
    two that transfer between tracks at all), interval sets, and
    four-chord progressions in roman numerals.
    """
    tokens = set()
    by_voice = {name: (roles_mod.best_role(name, score)[0] or "unknown")
                for name in score.voices}
    for pattern in patterns_mod.extract(score, 1, by_voice):
        tokens.add(("rhythm", pattern.role, pattern.onsets))
        shape = pattern.contour()
        if len(shape) >= MIN_CONTOUR and any(c != "=" for c in shape):
            tokens.add(("contour", pattern.role, shape))
        if pattern.melodic:
            tokens.add(("intervals", pattern.role,
                        tuple(sorted(set(pattern.intervals)))))
    _key, sequence = harmony.progression(score)
    for i in range(len(sequence) - 3):
        tokens.add(("progression", tuple(sequence[i:i + 4])))
    return tokens


def soundtrack_of(path: str) -> str:
    """Which soundtrack a score belongs to, from its filename.

    Corpus filenames are `<game>__<track>.json` for NES and
    `<game>_<platform>_<track>.trk` for Genesis. Splitting on the platform
    marker alone left every Zyrinx track as its own soundtrack, because
    those filenames carry the studio name where the others carry a
    platform.
    """
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    if "__" in stem:
        return stem.split("__")[0]
    for marker in ("_mega_drive", "_genesis", "_zyrinx", "_family_computer"):
        if marker in stem:
            return stem.split(marker)[0]
    return stem.split("_")[0] or "unknown"


def collect(paths):
    """-> [{path, title, soundtrack, notes, vocab}] for every readable score."""
    out = []
    for path in paths:
        try:
            score = score_model.load(path)
        except Exception:
            continue
        notes = sum(len(v) for v in score.voices.values())
        if notes < MIN_NOTES:
            continue
        out.append({"path": path, "title": score.title or os.path.basename(path),
                    "soundtrack": soundtrack_of(path), "notes": notes,
                    "vocab": vocabulary(score)})
    return out


def rank(entries):
    """Order tracks by contribution, round-robin across soundtracks.

    Each pass takes, from every soundtrack that still has tracks left, the
    one adding the most tokens nothing chosen so far has. That keeps the
    head of the list both high-value and broad.
    """
    remaining = collections.defaultdict(list)
    for entry in entries:
        remaining[entry["soundtrack"]].append(entry)
    covered = set()
    ordered = []
    while any(remaining.values()):
        for soundtrack in sorted(remaining):
            bucket = remaining[soundtrack]
            if not bucket:
                continue
            best = max(bucket, key=lambda e: (len(e["vocab"] - covered),
                                              e["notes"]))
            bucket.remove(best)
            gain = len(best["vocab"] - covered)
            covered |= best["vocab"]
            ordered.append(dict(best, gain=gain, cumulative=len(covered)))
    return ordered, len(covered)


def knee(ordered, total: int) -> int:
    """Where the coverage curve bends — the point of diminishing returns.

    The furthest point from the straight line joining the first track to
    the last, which is the usual way to read a knee off a curve that has
    no other natural break.
    """
    if len(ordered) < 3:
        return len(ordered)
    x0, y0 = 1.0, ordered[0]["cumulative"] / total
    x1, y1 = float(len(ordered)), 1.0
    span = ((y1 - y0) ** 2 + (x1 - x0) ** 2) ** 0.5
    if span <= 0:
        return len(ordered)
    best_index, best_distance = len(ordered), -1.0
    for i, entry in enumerate(ordered, 1):
        y = entry["cumulative"] / total
        distance = abs((y1 - y0) * i - (x1 - x0) * y + x1 * y0 - y1 * x0) / span
        if distance > best_distance:
            best_index, best_distance = i, distance
    return best_index


def audit(paths, cut: int = None):
    entries = collect(paths)
    ordered, total = rank(entries)
    boundary = cut or knee(ordered, total)
    core, tail = ordered[:boundary], ordered[boundary:]
    strip = lambda rows: [{k: v for k, v in r.items() if k != "vocab"}
                          for r in rows]
    return {
        "tracks": len(ordered),
        "vocabulary": total,
        "boundary": boundary,
        "core_coverage": core[-1]["cumulative"] / total if core else 0.0,
        "core": strip(core),
        "topup": strip(tail),
    }
