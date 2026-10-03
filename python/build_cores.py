"""
build_cores.py — compile the two C chip cores into shared libraries.

Called automatically the first time `opn2` or `sn76489` is imported and no
usable library is present, so in normal use you never run this by hand.
Run it directly to force a rebuild or to see why a build failed:

    python3 python/build_cores.py            # build what's missing
    python3 python/build_cores.py --force    # rebuild everything
    python3 python/build_cores.py --check    # report status, build nothing

Platform handling is deliberately boring: find a C compiler, emit a
position-independent shared library with the host's usual extension, and
verify it actually loads through ctypes before declaring success. No
autotools, no CMake, no build directory to clean.
"""

import ctypes
import os
import subprocess
import sys
import sysconfig

HERE = os.path.dirname(os.path.abspath(__file__))
CORE_DIR = os.path.normpath(os.path.join(HERE, "..", "core"))

#: name -> list of C sources (relative to core/) making up that library
TARGETS = {
    "libopn2": ["ym3438.c", "wrapper.c"],
    "libpsg": ["psg.c"],
}

#: candidate compilers, in preference order; CC in the environment wins
COMPILERS = ("cc", "gcc", "clang", "tcc")


def shared_lib_extension() -> str:
    """`.so` on Linux, `.dylib` on macOS, `.dll` on Windows."""
    if sys.platform == "darwin":
        return ".dylib"
    if os.name == "nt":
        return ".dll"
    return ".so"


def library_path(name: str) -> str:
    return os.path.join(CORE_DIR, name + shared_lib_extension())


def find_compiler():
    """Return the first usable C compiler command, or None.

    Honours $CC first (that is how cross-compiles and unusual sandboxes
    tell us what to use), then falls back to the usual suspects. A
    compiler counts as usable only if it actually runs -- some images ship
    a `cc` symlink pointing at nothing.
    """
    candidates = []
    env_cc = os.environ.get("CC")
    if env_cc:
        candidates.append(env_cc)
    # The compiler CPython itself was built with is a good bet in sandboxes
    # where only a versioned binary (gcc-13, clang-17) is on PATH.
    py_cc = sysconfig.get_config_var("CC")
    if py_cc:
        candidates.append(py_cc.split()[0])
    candidates.extend(COMPILERS)

    seen = set()
    for cc in candidates:
        if cc in seen:
            continue
        seen.add(cc)
        try:
            subprocess.run([cc, "--version"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=True, timeout=20)
            return cc
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def _needs_rebuild(name: str, sources) -> bool:
    lib = library_path(name)
    if not os.path.exists(lib):
        return True
    lib_mtime = os.path.getmtime(lib)
    return any(os.path.getmtime(os.path.join(CORE_DIR, s)) > lib_mtime
               for s in sources)


def _verify_loads(path: str) -> bool:
    try:
        ctypes.CDLL(path)
        return True
    except OSError:
        return False


def build_target(name: str, sources, cc: str, quiet: bool = False) -> str:
    """Compile one library. Returns its path, raises RuntimeError on failure."""
    out = library_path(name)
    cmd = [cc, "-O2", "-fPIC", "-shared", "-o", out]
    cmd += [os.path.join(CORE_DIR, s) for s in sources]
    if os.name != "nt":
        cmd.append("-lm")
    if not quiet:
        print("  " + " ".join(os.path.basename(c) for c in cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{cc} failed building {name}:\n{proc.stderr.strip()}")
    if not _verify_loads(out):
        raise RuntimeError(f"{name} compiled but will not load through ctypes")
    return out


def ensure_cores(force: bool = False, quiet: bool = False):
    """Build any missing/stale core. Returns {name: path} for what is usable.

    Never raises: a caller that cannot get native cores is expected to fall
    back to python/fallback/, so a missing compiler is a normal outcome
    here, not an error.
    """
    built = {}
    pending = {n: s for n, s in TARGETS.items() if force or _needs_rebuild(n, s)}
    for name in TARGETS:
        if name not in pending and _verify_loads(library_path(name)):
            built[name] = library_path(name)

    if not pending:
        return built

    cc = find_compiler()
    if cc is None:
        if not quiet:
            print("chipgen: no C compiler found; native cores unavailable",
                  file=sys.stderr)
        return built

    if not quiet:
        print(f"chipgen: building chip cores with {cc}")
    for name, sources in pending.items():
        try:
            built[name] = build_target(name, sources, cc, quiet=quiet)
        except RuntimeError as exc:
            if not quiet:
                print(f"chipgen: {exc}", file=sys.stderr)
    return built


def main(argv):
    force = "--force" in argv
    check = "--check" in argv

    if check:
        cc = find_compiler()
        print(f"compiler:  {cc or 'NOT FOUND'}")
        print(f"extension: {shared_lib_extension()}")
        for name in TARGETS:
            path = library_path(name)
            if os.path.exists(path):
                state = "ok" if _verify_loads(path) else "present but will not load"
            else:
                state = "missing"
            print(f"  {name:10s} {state}")
        return 0

    built = ensure_cores(force=force)
    missing = [n for n in TARGETS if n not in built]
    for name, path in sorted(built.items()):
        print(f"  ok      {os.path.relpath(path, os.path.dirname(CORE_DIR))}")
    for name in missing:
        print(f"  MISSING {name}{shared_lib_extension()}")
    if missing:
        print("\nNative cores unavailable — chipgen will use the pure-Python\n"
              "fallback in python/fallback/ (slower, and an approximation of\n"
              "the YM2612 rather than the cycle-accurate core).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
