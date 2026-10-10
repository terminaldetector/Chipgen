"""study.py — turn a corpus into a style profile a generator can use.

Runs the musical layer end to end: roles, then chords and keys, then
patterns, and reports what the corpus actually does rather than what
chiptune is supposed to do.

    python3 python/study.py                     # the embedded corpus
    python3 python/study.py --json              # machine-readable
    python3 python/study.py --role bass         # one role's vocabulary

The profile deliberately reports vocabularies separately — rhythm cells
here, melodic contours there — rather than whole patterns. That is not a
simplification: measured on this corpus, whole patterns shared between
tracks explain 2% of what is played, while rhythms explain 32% and
contours 36%. A bank of whole patterns built from 79 tracks would have
five usable entries. See patterns.Bank.coverage.
"""

import argparse
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


def profile(scores, min_tracks: int = 3) -> dict:
    bank = patterns_mod.build(scores)
    role_counts = collections.Counter()
    keys = collections.Counter()
    qualities = collections.Counter()
    progressions = collections.Counter()
    tempos = []
    simultaneous = collections.Counter()

    for score in scores:
        tempos.append(score.bpm)
        for name in score.voices:
            role, confidence = roles_mod.best_role(name, score)
            if role and confidence >= 0.2:
                role_counts[role] += 1
        tonic, mode, confidence = harmony.key_of(score)
        if tonic is not None:
            keys[mode] += 1
        for chord in harmony.chords(score):
            qualities[chord.quality] += 1
            simultaneous[chord.simultaneous] += 1
        _, sequence = harmony.progression(score)
        for window, count in harmony.common_progressions(sequence, 4, 3):
            progressions[window] += count

    tempos.sort()
    chords_total = max(1, sum(qualities.values()))
    return {
        "tracks": len(scores),
        "tempo": {
            "min": tempos[0] if tempos else None,
            "median": tempos[len(tempos) // 2] if tempos else None,
            "max": tempos[-1] if tempos else None,
        },
        "modes": dict(keys),
        "roles": dict(role_counts.most_common()),
        "chord_qualities": {q: round(n / chords_total, 4)
                            for q, n in qualities.most_common(10)},
        "played_as_chords": round(
            simultaneous[True] / max(1, sum(simultaneous.values())), 4),
        "progressions": [{"chords": list(window), "count": count}
                         for window, count in progressions.most_common(8)],
        "pattern_coverage": {
            "fused_intervals_and_rhythm": round(bank.coverage(
                lambda p: (p.role, p.intervals, p.onsets), min_tracks), 4),
            "rhythm_alone": round(bank.coverage(
                lambda p: (p.role, p.onsets), min_tracks), 4),
            "contour_alone": round(bank.coverage(
                lambda p: (p.role, p.contour() or None), min_tracks), 4),
        },
        "vocabularies": {
            role: {
                "rhythm_cells": [
                    {"cell": patterns_mod.format_cell(token, example.span_rows),
                     "rows": example.span_rows, "count": count,
                     "tracks": tracks,
                     "syncopation": round(example.syncopation, 3)}
                    for token, count, tracks, example
                    in bank.cells(role, limit=6, min_tracks=min_tracks)],
                "contours": [
                    {"contour": token, "count": count, "tracks": tracks}
                    for token, count, tracks, _
                    in bank.contours(role, limit=6, min_tracks=min_tracks)],
            }
            for role in ("bass", "lead", "harmony", "arp", "counter", "pad")
        },
    }


def render(data: dict) -> str:
    out = [f"style profile — {data['tracks']} tracks", ""]
    tempo = data["tempo"]
    out.append(f"tempo      {tempo['min']:.0f} .. {tempo['max']:.0f} BPM, "
               f"median {tempo['median']:.0f}")
    modes = data["modes"]
    out.append(f"mode       " + ", ".join(f"{k} {v}" for k, v in modes.items()))
    out.append(f"voices     " + ", ".join(f"{k} {v}" for k, v in
                                          list(data["roles"].items())[:6]))
    out.append("")
    out.append("harmony    " + ", ".join(
        f"{q} {share:.0%}" for q, share in
        list(data["chord_qualities"].items())[:6]))
    out.append(f"           {data['played_as_chords']:.0%} played as chords, "
               f"the rest spelled out")
    if data["progressions"]:
        out.append("")
        out.append("progressions repeated within a track and seen in several:")
        for row in data["progressions"][:5]:
            out.append(f"           {' - '.join(row['chords']):32s} x{row['count']}")
    cover = data["pattern_coverage"]
    out.append("")
    out.append("what transfers between tracks (share of all pattern instances):")
    out.append(f"           rhythm alone            {cover['rhythm_alone']:.1%}")
    out.append(f"           contour alone           {cover['contour_alone']:.1%}")
    out.append(f"           the two fused           "
               f"{cover['fused_intervals_and_rhythm']:.1%}")
    for role, vocab in data["vocabularies"].items():
        if not vocab["rhythm_cells"] and not vocab["contours"]:
            continue
        out.append("")
        out.append(f"--- {role} ---")
        for cell in vocab["rhythm_cells"][:4]:
            out.append(f"  rhythm   {cell['cell']:18s} x{cell['count']:<5d} "
                       f"{cell['tracks']:2d} tracks   sync {cell['syncopation']:.2f}")
        for contour in vocab["contours"][:3]:
            out.append(f"  contour  {contour['contour']:18s} "
                       f"x{contour['count']:<5d} {contour['tracks']:2d} tracks")
    return "\n".join(out)


def main(argv):
    parser = argparse.ArgumentParser(description="study a corpus of scores")
    parser.add_argument("paths", nargs="*", help="scores; default: the corpus")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--min-tracks", type=int, default=3,
                        help="how many tracks a pattern must appear in")
    args = parser.parse_args(argv)

    paths = args.paths or score_model.corpus_paths()
    scores = score_model.load_all(paths)
    if not scores:
        print("no scores parsed", file=sys.stderr)
        return 1
    data = profile(scores, args.min_tracks)
    print(json.dumps(data, indent=1) if args.json else render(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
