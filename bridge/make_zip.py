"""
bridge/make_zip.py — build the archive you hand to a model.

    python3 bridge/make_zip.py                  -> dist/chipgen-bridge.zip
    python3 bridge/make_zip.py --with-binaries  -> plus prebuilt .so
    python3 bridge/make_zip.py --with-demo      -> plus a rendered demo track

The point of a separate builder rather than zipping the repo is what gets
LEFT OUT. A git checkout carries rendered WAVs, build products, caches and
history; a model that unzips that spends its first thousand tokens on
files it will never open. The bridge archive is the engine and its
documentation, nothing else — small enough to upload without thinking
about it.

Binaries are excluded by default on purpose: a .so built here will not
load on a different OS or architecture, and bootstrap.py compiles the C
sources in about two seconds anyway. Include them with --with-binaries
only when you know the target is Linux x86-64 AND has no compiler.
"""

import os
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
DIST = os.path.join(ROOT, "dist")

#: Everything the engine needs to run and everything a model needs to read.
INCLUDE_FILES = [
    "START_HERE.md",
    "README.md",
    "bridge/bootstrap.py",
    "bridge/make_zip.py",
    "bridge/manifest.json",
    "bridge/CORE.md",
    "bridge/NES.md",
    "bridge/SYNTHESIS.md",
    "bridge/PROMPT.md",
    "bridge/LEARNING.md",
    "bridge/AGENTIC.md",
    "corpus/STUDY.md",
    "examples/README.md",
    "examples/agentic/README.md",
    "core/README.md",
    "core/NUKED_OPN2_LICENSE",
]
INCLUDE_TREES = [
    ("python", (".py",)),
    # the synthesis layer's data: a starter bank of 42 instruments and the
    # example scenarios an agent copies into work/
    ("python/synthesis/banks", (".json",)),
    ("python/synthesis/scenarios", (".json",)),
    ("core", (".c", ".h")),
    # The .trk scores too, not just the .py drivers. They were missing,
    # so the archive shipped an examples/ directory with no scores in it
    # — and examples/README.md, which names all five, would have pointed
    # at files that were not there. The two full-length tracks bring
    # their instrument banks along; all eight files cost 11.8 KB
    # compressed, which is less than the README describing them.
    ("examples", (".py", ".trk", ".json")),
    ("tests", (".py",)),
]
BINARY_EXTENSIONS = (".so", ".dylib", ".dll")
#: `_work` is examples/agentic/make_examples.py's scratch (renders and
#: revisions), left behind only by an interrupted run.
SKIP_DIRECTORIES = {"__pycache__", ".git", ".pytest_cache", "_work"}


def _walk(directory, extensions):
    base = os.path.join(ROOT, directory)
    for current, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRECTORIES)
        for name in sorted(files):
            if name.endswith(extensions):
                path = os.path.join(current, name)
                yield path, os.path.relpath(path, ROOT)


def collect(with_binaries: bool = False, with_demo: bool = False):
    entries = []
    for relative in INCLUDE_FILES:
        path = os.path.join(ROOT, relative)
        if os.path.exists(path):
            entries.append((path, relative))
    for directory, extensions in INCLUDE_TREES:
        entries.extend(_walk(directory, extensions))
    if with_binaries:
        entries.extend(_walk("core", BINARY_EXTENSIONS))
    if with_demo:
        for name in ("demo.wav", "demo.vgm"):
            path = os.path.join(ROOT, "output", name)
            if os.path.exists(path):
                entries.append((path, f"output/{name}"))
    # de-duplicate while keeping order stable, so two builds of the same
    # tree produce the same archive
    seen, unique = set(), []
    for path, relative in entries:
        if relative not in seen:
            seen.add(relative)
            unique.append((path, relative))
    return unique


def build(destination: str = None, with_binaries: bool = False,
          with_demo: bool = False, refresh_manifest: bool = True) -> str:
    if refresh_manifest:
        sys.path.insert(0, os.path.join(ROOT, "python"))
        import bootstrap
        bootstrap.write_manifest()

    destination = destination or os.path.join(DIST, "chipgen-bridge.zip")
    os.makedirs(os.path.dirname(destination), exist_ok=True)

    entries = collect(with_binaries, with_demo)
    generated = _generated(entries)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, relative in entries:
            with open(path, "rb") as fh:
                _write(archive, relative, fh.read())
        for relative, data in generated:
            _write(archive, relative, data)
    return destination


def _write(archive, relative, data: bytes):
    # Fixed timestamps: the same source tree should always produce a
    # byte-identical archive, so "did anything change" is a checksum
    # comparison rather than a diff of two unzipped trees.
    info = zipfile.ZipInfo(f"chipgen/{relative}", date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)


def _generated(entries):
    """Files that exist only in the archive, made from the tree.

    AGENTS.md is what several agent tools read before anything else, so
    it is the one document a model that skims is sure to see — and it
    belongs in the archive only: in a checkout, an agent is meant to
    change the engine. integrity.json lets every render notice when a
    model has changed it anyway.
    """
    import json

    sys.path.insert(0, os.path.join(ROOT, "python"))
    import integrity
    import prompts

    checksums = json.dumps(integrity.build(entries), indent=1,
                           sort_keys=True) + "\n"
    return [("AGENTS.md", prompts.agents_md().encode("utf-8")),
            ("bridge/integrity.json", checksums.encode("utf-8")),
            ("work/README.md", WORK_README.encode("utf-8"))]


#: The one directory a model is meant to write in. Shipped with a README
#: so it exists on unzip, and so `ls` answers "where does my score go".
WORK_README = """\
# work/ — your files go here

Scores (`song.trk`), renders (`song.mp3`, `song.vgz`) and, if you need an
instrument the bank lacks, a patch file you pass with `--bank`.

Everything outside this directory is the engine. It is checksummed, and
a render from an edited engine says so.
"""


def main(argv):
    with_binaries = "--with-binaries" in argv
    with_demo = "--with-demo" in argv
    destination = None
    for i, arg in enumerate(argv):
        if arg in ("-o", "--out") and i + 1 < len(argv):
            destination = argv[i + 1]

    path = build(destination, with_binaries, with_demo)
    size = os.path.getsize(path)
    with zipfile.ZipFile(path) as archive:
        count = len(archive.namelist())
    print(f"{os.path.relpath(path, ROOT)}  —  {count} files, {size / 1024:.0f} KB")
    print()
    print("Hand it to a model with the prompt in bridge/PROMPT.md, or test it:")
    print(f"  mkdir -p /tmp/chipgen-test && cd /tmp/chipgen-test")
    print(f"  unzip -q {os.path.abspath(path)} && cd chipgen")
    print(f"  python3 bridge/bootstrap.py")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
