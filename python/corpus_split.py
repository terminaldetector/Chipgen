"""
corpus_split.py — divide a transcribed corpus into sectors by quality.

A corpus of a few hundred game tracks is not uniform. Some transcribe
cleanly and some barely land on a grid; some games contribute forty
tracks and some fifteen. Handing all of it over as one lump means whoever
learns from it learns whichever composer wrote the most.

So this splits into ordered sectors, each one internally balanced:

  * Sector 1 is the priority set — the highest-confidence transcriptions,
    taken round-robin across the games so no single soundtrack dominates.
    It is the sector meant to ship with the engine.
  * Later sectors take what is left, by the same rule, in descending
    quality.

Every sector gets its own manifest carrying the same caveats as the whole,
so a sector read on its own still says what it does and does not contain.
"""

import collections
import json
import os
import shutil


def game_of(entry: dict) -> str:
    """Which soundtrack a track came from, from its score path."""
    score = entry.get("score", "")
    stem = os.path.basename(score)
    for marker in ("_mega_drive", "_genesis"):
        if marker in stem:
            return stem.split(marker)[0]
    return stem.split("_")[0] or "unknown"


def rank(entries):
    """Order tracks so the best of each game comes first, round-robin.

    Sorting by quality alone would fill the priority sector with whichever
    game happens to transcribe best; round-robin over per-game queues
    keeps every soundtrack represented at every quality level.
    """
    queues = collections.defaultdict(list)
    for entry in entries:
        queues[game_of(entry)].append(entry)
    for queue in queues.values():
        # Grid fit first, then note count: a confident transcription with
        # more music in it is worth more than a confident short one.
        queue.sort(key=lambda e: (-e.get("grid_fit", 0.0), -e.get("notes", 0)))

    order = []
    games = sorted(queues, key=lambda g: -len(queues[g]))
    while any(queues[g] for g in games):
        for game in games:
            if queues[game]:
                order.append(queues[game].pop(0))
    return order


def split(manifest_path: str, out_dir: str, sectors: int = 3):
    """Write `sectors` directories of scores, banks and their manifests."""
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    source = os.path.dirname(os.path.abspath(manifest_path))

    accepted = [e for e in manifest["entries"] if e.get("accepted")]
    rejected = [e for e in manifest["entries"] if not e.get("accepted")]
    ordered = rank(accepted)

    per_sector = -(-len(ordered) // sectors)          # ceiling division
    written = []
    for index in range(sectors):
        chunk = ordered[index * per_sector:(index + 1) * per_sector]
        if not chunk:
            break
        name = f"sector{index + 1}"
        target = os.path.join(out_dir, name)
        os.makedirs(os.path.join(target, "scores"), exist_ok=True)
        os.makedirs(os.path.join(target, "banks"), exist_ok=True)

        total_bytes = 0
        for entry in chunk:
            for key, folder in (("score", "scores"), ("bank_file", "banks")):
                relative = entry.get(key)
                if not relative:
                    continue
                src = os.path.join(source, relative)
                dst = os.path.join(target, relative)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                total_bytes += os.path.getsize(dst)

        games = collections.Counter(game_of(e) for e in chunk)
        fits = sorted(e.get("grid_fit", 0.0) for e in chunk)
        sector_manifest = {
            "sector": index + 1,
            "priority": index == 0,
            "of": sectors,
            "tracks": len(chunk),
            "total_notes": sum(e.get("notes", 0) for e in chunk),
            "total_seconds": round(sum(e.get("duration", 0) for e in chunk), 1),
            "bytes": total_bytes,
            "grid_fit": {"min": fits[0], "median": fits[len(fits) // 2],
                         "max": fits[-1]},
            "games": dict(games.most_common()),
            "produced_by": "python/corpus_split.py",
            "caveats": manifest.get("caveats", []),
            "entries": chunk,
        }
        with open(os.path.join(target, "manifest.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(sector_manifest, handle, indent=2, ensure_ascii=False)
        written.append(sector_manifest)

    index_path = os.path.join(out_dir, "sectors.json")
    with open(index_path, "w", encoding="utf-8") as handle:
        json.dump({
            "sectors": [{k: v for k, v in s.items() if k != "entries"}
                        for s in written],
            "rejected": len(rejected),
            "note": "Sector 1 is the priority set: highest-confidence "
                    "transcriptions, balanced across soundtracks so no one "
                    "composer dominates. Later sectors descend in "
                    "confidence by the same rule.",
        }, handle, indent=2, ensure_ascii=False)
    return written


def main(argv):
    import argparse

    parser = argparse.ArgumentParser(
        prog="corpus_split",
        description="Split a transcribed corpus into quality-ordered sectors.")
    parser.add_argument("manifest", help="a corpus manifest.json")
    parser.add_argument("-o", "--out", default="sectors")
    parser.add_argument("-n", "--sectors", type=int, default=3)
    args = parser.parse_args(argv)

    written = split(args.manifest, args.out, args.sectors)
    for sector in written:
        flag = " (priority)" if sector["priority"] else ""
        print(f"sector {sector['sector']}{flag}: {sector['tracks']} tracks, "
              f"{sector['total_notes']} notes, "
              f"{sector['bytes']/1024/1024:.1f} MB, "
              f"fit {sector['grid_fit']['min']:.2f}-"
              f"{sector['grid_fit']['max']:.2f} "
              f"(median {sector['grid_fit']['median']:.2f})")
        print(f"   games: {', '.join(f'{g} {n}' for g, n in list(sector['games'].items())[:5])}"
              + (" ..." if len(sector["games"]) > 5 else ""))
    print(f"\nwrote {args.out}/sectors.json")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
