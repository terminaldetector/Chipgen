"""
core_loader.py — decide, once, how the chips get emulated.

Three tiers, best first:

  1. native   — the C cores in core/ (Nuked-OPN2 + the register-level PSG),
                loaded through ctypes. Cycle-accurate, fast.
  2. built    — same thing, but nothing was compiled yet, so compile it now
                (build_cores.py). Still tier 1 once it succeeds.
  3. fallback — python/fallback/, pure Python, no compiler needed. The PSG
                stays exact; the YM2612 becomes an operator-level model,
                which is an APPROXIMATION and says so out loud.

Tier 3 exists for exactly one reason: chipgen should still make sound
after being unzipped into a sandbox that has Python and nothing else. It
is not the point of the project, it is the safety net under it.

Override with CHIPGEN_BACKEND=native|fallback|auto (default auto).
"""

import ctypes
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CORE_DIR = os.path.normpath(os.path.join(HERE, "..", "core"))

NATIVE = "native"
FALLBACK = "fallback"

_state = {"attempted": False, "libs": {}, "notes": []}


def _requested() -> str:
    return os.environ.get("CHIPGEN_BACKEND", "auto").strip().lower()


def _candidate_paths(stem: str):
    """A build product may carry any of the three extensions; try them all
    so a .dylib built on macOS is still found by a name written as .so."""
    for ext in (".so", ".dylib", ".dll"):
        yield os.path.join(CORE_DIR, stem + ext)


def _try_load(stem: str):
    for path in _candidate_paths(stem):
        if os.path.exists(path):
            try:
                return ctypes.CDLL(path)
            except OSError as exc:
                _state["notes"].append(f"{os.path.basename(path)} exists but "
                                       f"will not load: {exc}")
    return None


def _ensure_attempted():
    if _state["attempted"]:
        return
    _state["attempted"] = True

    if _requested() == FALLBACK:
        _state["notes"].append("CHIPGEN_BACKEND=fallback, native cores skipped")
        return

    for stem in ("libopn2", "libpsg"):
        lib = _try_load(stem)
        if lib is not None:
            _state["libs"][stem] = lib

    if len(_state["libs"]) == 2:
        return

    try:
        import build_cores
    except ImportError:                      # pragma: no cover
        _state["notes"].append("build_cores.py not importable")
        return

    build_cores.ensure_cores(quiet=True)
    for stem in ("libopn2", "libpsg"):
        if stem in _state["libs"]:
            continue
        lib = _try_load(stem)
        if lib is not None:
            _state["libs"][stem] = lib

    if len(_state["libs"]) < 2:
        if build_cores.find_compiler() is None:
            _state["notes"].append("no C compiler available")
        _state["notes"].append("falling back to pure-Python cores")


def load(stem: str):
    """Return the ctypes handle for one core, or None to use the fallback."""
    _ensure_attempted()
    if _requested() == NATIVE and stem not in _state["libs"]:
        raise RuntimeError(
            f"CHIPGEN_BACKEND=native was requested but {stem} could not be "
            f"loaded or built. Notes: " + "; ".join(_state["notes"] or ["none"])
        )
    return _state["libs"].get(stem)


def backend_for(stem: str) -> str:
    _ensure_attempted()
    return NATIVE if stem in _state["libs"] else FALLBACK


def status() -> dict:
    """What actually got loaded — used by bridge/bootstrap.py's report."""
    _ensure_attempted()
    return {
        "requested": _requested(),
        "ym2612": backend_for("libopn2"),
        "sn76489": backend_for("libpsg"),
        "core_dir": CORE_DIR,
        "notes": list(_state["notes"]),
    }


#: What is actually lost on each chip's fallback. The PSG fallback keeps
#: the register model intact, so saying "approximation" about it would be
#: a lie in the cautious direction; the FM one really is approximate.
_FALLBACK_CAVEAT = {
    "YM2612": ("it approximates the chip (operator-level, no LFO or SSG-EG) "
               "rather than emulating it cycle-accurately"),
    "SN76489": ("same register model as the C core, so it stays faithful — "
                "only slower"),
}


def warn_once_about_fallback(chip: str):
    """Say it plainly the first time, then stay quiet."""
    key = f"warned:{chip}"
    if _state.get(key):
        return
    _state[key] = True
    caveat = _FALLBACK_CAVEAT.get(chip, "it is an approximation")
    print(f"chipgen: {chip} is running on the pure-Python fallback core; "
          f"{caveat}. Install a C compiler and rerun for the real thing.",
          file=sys.stderr)
