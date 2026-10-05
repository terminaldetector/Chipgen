"""tests/run_tests.py — the suite, runnable with nothing installed.

    python3 tests/run_tests.py            everything
    python3 tests/run_tests.py audio      only tests whose name contains "audio"
    python3 tests/run_tests.py -v         print every test as it runs

The tests that listen — they render through libopenmpt and measure — skip,
and say so, when libopenmpt is missing. The rest need only Python.
pytest works too.
"""

import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for path in (ROOT, HERE):
    if path not in sys.path:
        sys.path.insert(0, path)

MODULES = ["test_format", "test_synth", "test_notation", "test_audio",
           "test_demos", "test_engines", "test_docs", "test_export",
           "test_forge_core", "test_forge", "test_layers", "test_ladder", "test_rearrange", "test_corpus", "test_programs", "test_ablate", "test_structure"]


def _collect(pattern=""):
    cases = []
    for name in MODULES:
        module = __import__(name)
        for attribute in sorted(dir(module)):
            if attribute.startswith("test_"):
                full = f"{name}.{attribute}"
                if not pattern or pattern in full:
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
    failures, skipped = [], []
    started = time.time()
    for name, function in cases:
        try:
            function()
            mark = "."
            if verbose:
                print(f"  ok   {name}")
        except support.Skipped as exc:
            skipped.append(f"{name}: {exc}")
            mark = "s"
        except Exception:
            failures.append((name, traceback.format_exc()))
            mark = "F"
        if not verbose:
            sys.stdout.write(mark)
            sys.stdout.flush()
    if not verbose:
        print()
    for name, trace in failures:
        print(f"\n{'=' * 70}\nFAIL {name}\n{'-' * 70}\n{trace}")
    for line in skipped:
        print(f"skipped: {line}")
    passed = len(cases) - len(failures) - len(skipped)
    print(f"\n{passed} passed, {len(failures)} failed"
          + (f", {len(skipped)} skipped" if skipped else "")
          + f"  ({len(cases)} total, {time.time() - started:.0f} s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
