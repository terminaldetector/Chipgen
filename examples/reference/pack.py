"""The reference corpus, read into what a model can look things up in: the
index of what plays, each module's structure, episodes with the state they
start in, three kinds of recommendation side by side, and a split by author
that no fragment crosses.

    python3 examples/reference/pack.py CORPUS_ZIP OUT_DIR [--astra FILE] [--ab DIR]

CORPUS_ZIP is the reference corpus as it was given (it is copied unchanged
into OUT_DIR/originals/, and its hash checked); `--astra` is the audit's
corrected_reference_candidates.json, to set its static candidates beside
the trace's; `--ab` is the output folder of examples/reference/make.py,
copied into OUT_DIR/ab/. Nothing is written into the zip or its modules.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import zipfile
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from schism import corpus, structure  # noqa: E402

#: the audit's six addresses: (file, order or None, pattern, row, channels)
AUDIT_ADDRESSES = [
    ("modules/Skaven252/skaven-fourth_symmetriad.it", 1, 1, 96, [2]),
    ("modules/Skaven252/skaven-fourth_symmetriad.it", 22, 20, 0, [11]),
    ("modules/Manwe/new_wind_in_syworld.it", None, 6, 0, [5]),
    ("modules/Manwe/new_wind_in_syworld.it", None, 5, 0, [9]),
    ("modules/FearofDark/dancinginthetube.s3m", 3, 3, 8, [5, 6]),
    ("modules/Necros/isotoxin.s3m", None, 52, 0, [6, 8]),
    ("modules/FearofDark/fod_rosethorn.it", 1, 1, 0, [1, 2, 3]),
]


def _sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _dump(obj, path):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, indent=1, ensure_ascii=False)
        handle.write("\n")


def _stem(file):
    author, name = file.split("/")[-2:]
    return f"{author}_{os.path.splitext(name)[0]}"


def main(zip_path, out_dir, astra=None, ab=None):
    started = time.time()
    os.makedirs(out_dir, exist_ok=True)
    for sub in ("originals", "index", "structure", "episodes", "splits"):
        os.makedirs(os.path.join(out_dir, sub), exist_ok=True)
    zip_sha = _sha(zip_path)
    # an upload may have put a prefix on the name ("29c0a232-..."); the copy
    # keeps the name the corpus was given under, and its bytes
    name = os.path.basename(zip_path)
    if len(name) > 9 and name[8] == "-" and all(
            c in "0123456789abcdef" for c in name[:8]):
        name = name[9:]
    copy = os.path.join(out_dir, "originals", name)
    shutil.copyfile(zip_path, copy)
    assert _sha(copy) == zip_sha
    work = tempfile.mkdtemp(prefix="refcorpus-")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(work)
    base = os.path.join(work, next(n for n in os.listdir(work)
                                   if os.path.isdir(os.path.join(work, n))))
    manifest = json.load(open(os.path.join(base, "manifest.json")))
    by_file = {m["file"]: m for m in manifest}
    summary = {"corpus_zip": name,
               "corpus_zip_sha256": zip_sha, "modules": len(manifest)}
    # -- index, structure, fingerprints ------------------------------------
    entries, prints, stored_prints = [], {}, Counter()
    shapes = {}
    for m in manifest:
        path = os.path.join(base, m["file"])
        source = {k: m.get(k) for k in (
            "author_display", "id", "title", "license_label", "source_page",
            "study_focus")}
        source["file"] = m["file"]
        entry = corpus.index_module(path, source)
        entry["sha256_matches_manifest"] = entry["sha256"] == m["sha256"]
        rec = m.get("recommended_patterns", [])
        reached = set(entry["played"]["reached"])
        entry["corpus_recommended"] = {
            "patterns": rec,
            "not_played": [p for p in rec if p not in reached],
            "not_listed": [p for p in rec
                           if p not in entry["stored"]["listed"]]}
        entries.append(entry)
        with corpus.Module(path) as module:
            played = corpus.trace(module)
            shape = structure.structure(module, played)
            shape["file"] = m["file"]
            shape["source"] = source
            shapes[m["file"]] = shape
            _dump(shape, os.path.join(out_dir, "structure",
                                      _stem(m["file"]) + ".json"))
            prints[m["file"]] = structure.fingerprints(module,
                                                       played["reached"])
            stored_prints.update(structure.fingerprints(
                module, entry["stored"]["nonempty"]))
        print(f"{m['file']:50s} form {shape['letters']:3d} letters, "
              f"{len(shape['visits']):3d} visits, "
              f"{entry['played']['seconds']:6.1f} s", flush=True)
    corpus.write_index(entries, os.path.join(out_dir, "index", "index.jsonl"))
    # -- recommendations side by side ---------------------------------------
    static = {}
    if astra:
        for c in json.load(open(astra)).get("corrections", []):
            static[c["module"]] = c
    recs = []
    for entry in entries:
        file = entry["source"]["file"]
        reached = set(entry["played"]["reached"])
        row = {"file": file,
               "manifest": entry["corpus_recommended"],
               "trace_candidates": entry["candidates"],
               "selection": entry["selection"]}
        if file in static:
            row["audit_static"] = [
                dict(c, played=c["pattern"] in reached)
                for c in static[file]["corrected_candidates"]]
        recs.append(row)
    _dump({"format": "schism-reference-recommendations/1",
           "how": {"manifest": "the corpus's own recommended_patterns: the "
                               "densest patterns, whether or not they play",
                   "audit_static": "the corpus audit's corrected candidates: "
                                   "densest patterns the order list names "
                                   "(only for the five modules it "
                                   "corrected)",
                   "trace_candidates": "16-row windows of patterns the song "
                                       "plays, ranked by the variety of "
                                       "effects and instruments, with the "
                                       "order where the song first plays "
                                       "them"},
           "modules": recs},
          os.path.join(out_dir, "index", "recommendations.json"))
    # -- splits ---------------------------------------------------------------
    groups = {}
    for m in manifest:
        groups.setdefault(m["file"].split("/")[1], []).append(m["file"])
    plan = structure.splits(groups)
    leak = structure.leaks(prints, plan)
    split_of = {item: split for split, e in plan.items()
                for item in e["items"]}
    windows = sum(stored_prints.values())
    _dump({"format": "schism-reference-splits/1",
           "unit": "an author's folder: every composition of an author, "
                   "and every fragment of a composition, in one split",
           "plan": plan,
           "leak_check": dict(leak, how="8-row windows of the patterns each "
                                        "song plays, with 8 notes or more, "
                                        "as row, channel and interval to the "
                                        "first note (transposition folded); "
                                        "a fingerprint in two splits is a "
                                        "leak"),
           "fragments_recounted": {
               "windows_with_8_notes": windows,
               "distinct_after_transposition": len(stored_prints),
               "share": round(len(stored_prints) / max(1, windows), 4),
               "corpus_readme_says": "6385 fragments, 5130 distinct "
                                     "(80.34%)"}},
          os.path.join(out_dir, "splits", "splits.json"))
    # -- episodes -----------------------------------------------------------------
    listing = []

    def episode(file, order, row, channels, why):
        path = os.path.join(base, file)
        source = dict(entries[[e["source"]["file"] for e in entries]
                              .index(file)]["source"])
        try:
            ep = corpus.episode(path, order, row, rows=16, context=2,
                                channels=[c - 1 for c in channels]
                                if channels else None, source=source)
        except corpus.CorpusError as error:
            listing.append({"file": file, "order": order, "row": row,
                            "error": str(error), "why": why})
            return
        name = f"{_stem(file)}__o{order:03d}_r{row:03d}" + (
            "_ch" + "-".join(map(str, channels)) if channels else "") + ".json"
        ep["why"] = why
        ep["split"] = split_of.get(file)
        _dump(ep, os.path.join(out_dir, "episodes", name))
        effects = sorted({c["normalized"]["effect_name"]
                          for line in ep["lines"] if not line["context"]
                          for c in line["cells"]
                          if c["normalized"]["effect_name"]})
        listing.append({"episode": name, "file": file,
                        "author": source.get("author_display"),
                        "title": source.get("title"),
                        "order": order, "pattern": ep["address"]["pattern"],
                        "row": row, "channels": channels or "all",
                        "effects": effects, "why": why,
                        "split": split_of.get(file)})

    for entry in entries:
        for rank, c in enumerate(entry["candidates"], 1):
            episode(entry["source"]["file"], c["order"], c["row"], None,
                    f"trace candidate {rank}: {len(c['effects'])} kinds of "
                    f"effect, {c['instruments']} instruments")
    for file, order, pattern, row, channels in AUDIT_ADDRESSES:
        if order is None:
            with corpus.Module(os.path.join(base, file)) as module:
                order = module.orders().index(pattern)
        episode(file, order, row, channels,
                "an address from the corpus audit, checked by A/B "
                "(ab/ and the cards)")
    with open(os.path.join(out_dir, "episodes", "INDEX.jsonl"), "w",
              encoding="utf-8") as handle:
        for item in listing:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    # -- the A/B ---------------------------------------------------------------
    if ab:
        target = os.path.join(out_dir, "ab")
        os.makedirs(target, exist_ok=True)
        for name in sorted(os.listdir(ab)):
            if name.endswith((".mp3", ".json", ".md")):
                shutil.copyfile(os.path.join(ab, name),
                                os.path.join(target, name))
        if os.path.isdir(os.path.join(ab, "cards")):
            shutil.copytree(os.path.join(ab, "cards"),
                            os.path.join(target, "cards"))
    shutil.rmtree(work)
    summary.update({
        "index_modules": len(entries),
        "sha_all_match": all(e["sha256_matches_manifest"] for e in entries),
        "structures": len(shapes),
        "episodes": sum(1 for x in listing if "episode" in x),
        "episode_errors": [x for x in listing if "error" in x],
        "splits": {k: len(v["items"]) for k, v in plan.items()},
        "leaks": leak["crossing_splits"],
        "fragments": {"windows": windows, "distinct": len(stored_prints)},
        "seconds": round(time.time() - started, 1)})
    _dump(summary, os.path.join(out_dir, "SUMMARY.json"))
    return summary


if __name__ == "__main__":
    args = sys.argv[1:]
    astra = ab = None
    if "--astra" in args:
        i = args.index("--astra")
        astra = args[i + 1]
        del args[i:i + 2]
    if "--ab" in args:
        i = args.index("--ab")
        ab = args[i + 1]
        del args[i:i + 2]
    if len(args) != 2:
        print(__doc__)
        sys.exit(2)
    print(json.dumps(main(args[0], args[1], astra, ab), indent=1,
                     ensure_ascii=False))
