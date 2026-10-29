"""make_bundle.py — the interface as a zip you can deploy anywhere static.

    python3 studio/make_bundle.py

Writes `dist/chipgen-studio.zip`: four files, no build step, no server, no
dependencies. Unzip it onto any static host — or hand it to something that
deploys a folder — and the whole reference works: every directive, every
preset, the channel bus, the architecture, the engine's own notes.

What a static deployment cannot do is turn a score into sound, because
that needs the emulation. The page says so in its status bar rather than
leaving a dead button, and the Render control explains what to run. Anyone
who wants rendering runs `python3 python/serve.py` locally and gets the
same page with the engine behind it.

The contract is baked in at build time, which is the whole point of
dumping it: the bundle is a snapshot of what the engine could do on the
day it was built, and it cannot drift afterwards because there is nothing
in it to drift — only a copy that is honestly stale rather than silently
wrong. Rebuild it when the engine changes.
"""

import json
import os
import sys
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "python"))

#: Everything the interface needs and nothing else.
FILES = ("index.html", "style.css", "app.js")


def build(destination: str = None) -> str:
    import studio

    destination = destination or os.path.join(_ROOT, "dist",
                                              "chipgen-studio.zip")
    os.makedirs(os.path.dirname(destination), exist_ok=True)

    manifest = studio.manifest()
    contract = json.dumps(manifest, ensure_ascii=False, indent=1)

    # Deterministic, like the bridge archive: the same tree gives the same
    # bytes, so "did anything actually change" is answerable by comparing
    # two builds rather than by reading a diff of a binary.
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        for name in FILES:
            path = os.path.join(_HERE, name)
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(path, "rb") as handle:
                archive.writestr(info, handle.read())
        info = zipfile.ZipInfo("studio.json", date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        archive.writestr(info, contract.encode("utf-8"))

    return destination, manifest


def main(argv):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("-o", "--out", help="where to write the zip")
    args = parser.parse_args(argv)

    path, manifest = build(args.out)
    size = os.path.getsize(path)
    print(f"wrote {os.path.relpath(path, _ROOT)}  ({size / 1024:.0f} KB)")
    print(f"  {len(manifest['chips'])} chips, "
          f"{len(manifest['voices'])} voices, "
          f"{len(manifest['directives'])} directives, "
          f"{len(manifest['presets'])} presets")
    health = manifest.get("health") or {}
    if health.get("tests"):
        t = health["tests"]
        print(f"  contract built against {t['passed']}/{t['total']} tests")
    else:
        print("  no test stamp — run `python3 tests/run_tests.py` first if "
              "you want the status bar to show one")
    print("\nunzip onto any static host. For rendering, run the engine:")
    print("  python3 python/serve.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
