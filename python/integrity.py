"""integrity.py — notice when the engine has been edited.

## Why this exists

Handed an archive full of Python and asked for a song, some models start
improving the engine: a "fix" to the tracker so their score parses, a
louder default in the mixer, a new instrument pasted into the bank. The
render still succeeds, so nothing says that what came out is no longer
chipgen — and a score tuned against a modified engine is wrong
everywhere else.

So the archive carries a checksum of every engine file
(`bridge/integrity.json`, written by make_zip.py), and every render and
every --check compares against it. A modified engine is not refused —
refusing invites the model to delete the checksum file next — it is
*named*, in the warnings, in the --check verdict and in bootstrap's
report, where both the model and the person reading its output see it.

A development checkout has no checksum file and pays nothing: changing
the engine is the point of a checkout.
"""

import hashlib
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(_HERE, ".."))
MANIFEST = os.path.join("bridge", "integrity.json")

#: What counts as the engine: the code, the C cores, the bridge scripts,
#: and the tests a model might "fix" to make a failure go away. Not the
#: examples and not the docs — editing those changes nothing a render
#: does — and not bridge/manifest.json, which `bootstrap.py --manifest`
#: legitimately regenerates from the code.
COVERED_PREFIXES = ("python/", "core/", "tests/")
COVERED_FILES = ("bridge/bootstrap.py", "bridge/make_zip.py")
#: Code, not the READMEs and licence text that sit beside it in core/.
COVERED_EXTENSIONS = (".py", ".c", ".h")

_cache = {}


def covered(relative: str) -> bool:
    relative = relative.replace(os.sep, "/")
    if relative in COVERED_FILES:
        return True
    return relative.startswith(COVERED_PREFIXES) and \
        relative.endswith(COVERED_EXTENSIONS)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(entries) -> dict:
    """{relative path: sha256} for every covered (path, relative) pair."""
    files = {}
    for path, relative in entries:
        relative = relative.replace(os.sep, "/")
        if covered(relative):
            with open(path, "rb") as handle:
                files[relative] = digest(handle.read())
    return {"version": 1, "files": dict(sorted(files.items()))}


def changed(root: str = None) -> list:
    """Engine files that differ from the archive's checksums.

    [] when there is no checksum file (a development checkout) or nothing
    changed. Cached on each file's size and mtime, so a process that
    renders a hundred times hashes the engine once.
    """
    root = root or ROOT
    manifest = os.path.join(root, MANIFEST)
    if not os.path.exists(manifest):
        return []
    try:
        with open(manifest, encoding="utf-8") as handle:
            expected = json.load(handle).get("files", {})
    except (OSError, ValueError):
        return [f"{MANIFEST} (unreadable)"]

    out = []
    for relative, want in expected.items():
        path = os.path.join(root, relative)
        try:
            stat = os.stat(path)
        except OSError:
            out.append(f"{relative} (deleted)")
            continue
        key = (path, stat.st_size, stat.st_mtime_ns)
        have = _cache.get(key)
        if have is None:
            with open(path, "rb") as handle:
                have = digest(handle.read())
            _cache[key] = have
        if have != want:
            out.append(relative)
    return out


def warning(root: str = None) -> str:
    """One line for a render's warnings, or "" when the engine is intact."""
    edited = changed(root)
    if not edited:
        return ""
    shown = ", ".join(edited[:4])
    more = f" (+{len(edited) - 4} more)" if len(edited) > 4 else ""
    return (f"the engine has been edited since this archive was built: "
            f"{shown}{more}. A render from a modified engine is not a "
            f"chipgen render — restore those files from the archive and "
            f"fix the SCORE instead")
