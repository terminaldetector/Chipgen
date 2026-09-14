"""
tests/run_tests.py — the test suite, runnable with nothing installed.

    python3 tests/run_tests.py            everything
    python3 tests/run_tests.py vgm        only tests whose name contains "vgm"
    python3 tests/run_tests.py -v         print every test as it runs

pytest works too (`pytest tests/`) — the test functions are ordinary
`test_*` callables and the assertions are plain `assert`. But chipgen's
promise is that it runs in a sandbox with no network, and a test suite
that needed `pip install pytest` before it could tell you whether the
engine works would undercut that on the first day someone tried it.
"""

import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
for path in (os.path.join(ROOT, "python"), HERE):
    if path not in sys.path:
        sys.path.insert(0, path)

MODULES = ["test_events", "test_tracker", "test_chips", "test_vgm",
           "test_render", "test_bridge", "test_sanity", "test_profile",
           "test_furnace", "test_it", "test_opl", "test_transcribe", "test_effects",
           "test_analysis", "test_selection", "test_musical", "test_nes",
           "test_fx", "test_livefm", "test_pcm", "test_patterns_notation", "test_ch3", "test_nes_score", "test_examples", "test_levels", "test_studio", "test_arrange"]


def _collect(pattern=""):
    cases = []
    for name in MODULES:
        module = __import__(name)
        for attribute in sorted(dir(module)):
            if not attribute.startswith("test_"):
                continue
            full = f"{name}.{attribute}"
            if pattern and pattern not in full:
                continue
            cases.append((full, getattr(module, attribute)))
    return cases


def main(argv):
    verbose = "-v" in argv
    pattern = next((a for a in argv if not a.startswith("-")), "")

    cases = _collect(pattern)
    if not cases:
        print(f"no tests match {pattern!r}")
        return 1

    import support

    failures = []
    skipped = []
    for name, function in cases:
        try:
            function()
            if verbose:
                print(f"  ok   {name}")
            else:
                sys.stdout.write(".")
                sys.stdout.flush()
        except support.Skipped as exc:
            skipped.append(f"{name}: {exc}")
            sys.stdout.write("s")
            sys.stdout.flush()
        except Exception:
            failures.append((name, traceback.format_exc()))
            sys.stdout.write("F")
            sys.stdout.flush()

    if not verbose:
        print()
    for name, trace in failures:
        print(f"\n{'=' * 70}\nFAIL {name}\n{'-' * 70}\n{trace}")
    for name in skipped:
        print(f"skipped: {name}")

    total = len(cases)
    passed = total - len(failures) - len(skipped)
    print(f"\n{passed} passed, "
          f"{len(failures)} failed"
          + (f", {len(skipped)} skipped" if skipped else "")
          + f"  ({total} total)")

    # Stamp the result so studio.health() can report it. An interface
    # asking "are you healthy" must not trigger two minutes of rendering,
    # and shelling out to this runner to answer is exactly that.
    # Only a full run is stamped: a filtered one says nothing about the
    # suite, and stamping it would report 6/6 OK on a broken tree.
    if not pattern:
        _stamp(passed, len(failures), len(skipped), total)
    return 1 if failures else 0


def _stamp(passed, failed, skipped, total):
    import datetime
    import json

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "last_run.json")
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"passed": passed, "failed": failed,
                       "skipped": skipped, "total": total,
                       "when": datetime.datetime.now(
                           datetime.timezone.utc).isoformat(
                               timespec="seconds")},
                      handle, indent=1)
    except OSError:
        pass                     # a read-only checkout is not a failure


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
