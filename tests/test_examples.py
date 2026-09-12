"""Every shipped example still renders.

Nothing covered examples/ before, so a score in there could rot silently
against a renamed patch or a changed directive and nobody would know
until a model copied it. Two of the five already needed a `--bank` flag
that no test was passing, which is the same failure one step removed:
the example works, but only if you know something the directory does not
tell you.
"""

import glob
import os

import support

#: Imported lazily inside the test elsewhere, but _prefix needs it at
#: module level to tell a directive line from a row.
def _directive_names():
    import tracker
    return tracker.DIRECTIVES


#: Examples that need an imported bank, and which file provides it. Kept
#: here rather than guessed so a new example with a missing bank fails
#: loudly instead of being quietly skipped.
BANKS = {
    "neon_transit.trk": ("--bank", "examples/neon_transit_bank.json"),
    "dos_transit.trk": ("--opl-bank", "examples/opl_furnace_bank.json"),
}


def _examples():
    pattern = os.path.join(support.ROOT, "examples", "*.trk")
    found = sorted(glob.glob(pattern))
    assert found, "examples/ has no .trk scores at all"
    return found


def test_every_example_parses():
    import tracker

    for path in _examples():
        name = os.path.basename(path)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        try:
            events, meta = tracker.loads(text)
        except Exception as error:
            raise AssertionError(
                f"examples/{name} no longer parses: {error}") from None
        assert events, f"examples/{name} parses to no events"


#: Rows to keep when rendering. Two of the examples are full-length
#: tracks — 64 and 53 seconds — and rendering both on every test run put
#: the whole suite past two minutes. A prefix still exercises everything
#: that can actually rot: the bank loading, every directive in the
#: preamble, the patch names, the column layout. The full renders are
#: one CLI call away when something here does fail. 32 rows keeps this
#: test to about 12 seconds of the suite.
RENDER_ROWS = 32


def _prefix(text: str) -> str:
    """The score's preamble plus the first few rows, ending cleanly."""
    kept = []
    rows = 0
    for line in text.splitlines():
        stripped = line.strip()
        kept.append(line)
        # A row is a line that is neither blank, comment nor directive.
        if (stripped and not stripped.startswith((";", "#"))
                and stripped.split()[0].lower() not in _directive_names()):
            rows += 1
            if rows >= RENDER_ROWS:
                break
    return "\n".join(kept) + "\nend\n"


def test_every_example_renders_with_the_bank_it_needs():
    """Renders through the CLI, because that is how anyone runs these.

    A score naming a patch from an imported bank fails with a bare
    KeyError when the bank is not passed — informative once you read it,
    but the example itself says nothing about needing one. BANKS above is
    that missing information, and this test is what keeps it true.
    """
    import subprocess
    import sys
    import tempfile

    directory = tempfile.mkdtemp()
    try:
        for path in _examples():
            name = os.path.basename(path)
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            trimmed = os.path.join(directory, name)
            with open(trimmed, "w", encoding="utf-8") as handle:
                handle.write(_prefix(text))
            argv = [sys.executable, "python/chipgen.py", trimmed,
                    "-o", os.devnull, "--peak", "0"]
            argv.extend(BANKS.get(name, ()))
            finished = subprocess.run(argv, cwd=support.ROOT,
                                      capture_output=True, text=True)
            _check(name, finished)
    finally:
        import shutil
        shutil.rmtree(directory, ignore_errors=True)


def _check(name, finished):
    assert finished.returncode == 0, (
        f"examples/{name} failed to render:\n"
        f"{finished.stderr.strip()[-800:]}")
    # "N events, T s, peak P" — a score that renders to silence is not a
    # working example.
    assert " events, " in finished.stdout, (
        f"examples/{name} produced no render summary:\n"
        f"{finished.stdout.strip()}")
    peak = finished.stdout.split("peak ")[1].split(",")[0]
    assert float(peak) > 0.01, (
        f"examples/{name} rendered at peak {peak} — effectively silent")
