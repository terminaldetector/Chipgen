"""audio_probe.py — render a score through libopenmpt and measure it.

The measurements are deliberately plain: zero-crossing frequency, window
RMS, left/right energy. A test that needs an FFT to say what it heard is a
test whose own bugs are hard to tell from the module's. Everything is pure
Python on the `array('f')` libopenmpt returns, so the suite needs nothing
installed but libopenmpt itself — and skips cleanly without it.
"""

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from schism import it_write, notation, openmpt, schismtracker  # noqa: E402

SR = 44100
TICK = lambda tempo: 2.5 / tempo          # seconds

#: Which program plays the module for the audio tests: libopenmpt (the
#: default) or Schism Tracker itself, chosen by the environment so that the
#: same assertions can be run against either:
#:     SCHISM_ENGINE=schism python3 tests/run_tests.py
ENGINE = os.environ.get("SCHISM_ENGINE", "openmpt").lower()
INTERPOLATION = os.environ.get("SCHISM_INTERPOLATION", "spline")

#: How far a note may be from its pitch, in cents. The recipes are tuned to
#: a fraction of a cent; a player that resamples by nearest neighbour (Schism's
#: crudest interpolation setting) cannot place a note closer than about two.
TUNING_CENTS = 2.5 if (ENGINE == "schism" and INTERPOLATION == "nearest") \
    else 1.5


def available() -> bool:
    if ENGINE == "schism":
        return schismtracker.available()
    return openmpt.available()


def need():
    import support
    if not available():
        support.skip(f"the {ENGINE} engine is not installed")


def score(body: str, *, inst="inst 1 wave=sine oct=1-7 fade=200", channels=1,
          tempo=125, rows=32, extra="") -> str:
    """A one-pattern score around `body` (the rows, one cell per channel)."""
    lines = body.strip("\n").splitlines()
    cells = ["... .. ... ..."] * channels
    while len(lines) < rows:
        lines.append(" | ".join(cells))
    return (f"tempo {tempo}\nchannels {channels}\nmv 48\n{extra}\n{inst}\n"
            f"pattern a rows {rows}\n" + "\n".join(lines) + "\nend\norder a\n")


def render(text: str, seconds: float = None):
    """-> (frames interleaved stereo array('f'), module). Warnings are
    ignored here; tests that care compile for themselves."""
    module, _ = notation.compile_text(text)
    return render_module(module, seconds)


def render_module(module, seconds: float = None):
    """The same, for a module built by hand (the notation cannot say a
    stereo sample or a sustain loop)."""
    blob = it_write.build(module)
    if ENGINE == "schism":
        frames = schismtracker.render(blob, SR, INTERPOLATION)
        if seconds is not None:
            frames = frames[:int(seconds * SR) * 2]
        return frames, module
    with openmpt.Song(blob) as song:
        return song.render(SR, seconds), module


def left(frames):
    return frames[0::2]


def right(frames):
    return frames[1::2]


def mono(frames):
    return [(a + b) * 0.5 for a, b in zip(frames[0::2], frames[1::2])]


def window(x, t0: float, t1: float):
    return x[int(t0 * SR):int(t1 * SR)]


def rms(x) -> float:
    return math.sqrt(sum(v * v for v in x) / len(x)) if len(x) else 0.0


def peak(x) -> float:
    return max((abs(v) for v in x), default=0.0)


def frequency(x) -> float:
    """Fundamental by upward zero crossings, interpolated. Right for a
    waveform that crosses zero once a cycle (sine, saw, square, triangle,
    pulse); wrong for anything richer, which `frequency_acf` is for."""
    crossings = []
    for i in range(1, len(x)):
        if x[i - 1] < 0.0 <= x[i]:
            crossings.append(i - 1 + (-x[i - 1]) / (x[i] - x[i - 1]))
    if len(crossings) < 3:
        return 0.0
    return (len(crossings) - 1) * SR / (crossings[-1] - crossings[0])


def frequency_acf(x, lo: float, hi: float) -> float:
    """Fundamental by autocorrelation, between lo and hi Hz."""
    n = len(x)
    mean = sum(x) / n
    y = [v - mean for v in x]
    best_lag, best = 0, -1e30
    scores = {}
    for lag in range(int(SR / hi) - 1, int(SR / lo) + 2):
        total = sum(a * b for a, b in zip(y, y[lag:]))
        scores[lag] = total
        if total > best:
            best, best_lag = total, lag
    a, b, c = (scores.get(best_lag - 1, best), best,
               scores.get(best_lag + 1, best))
    denominator = a - 2 * b + c
    shift = 0.5 * (a - c) / denominator if denominator else 0.0
    return SR / (best_lag + shift)


def cents(measured: float, expected: float) -> float:
    return 1200.0 * math.log2(measured / expected)


def hz(note_name: str) -> float:
    from schism import model as M
    return M.note_frequency(M.note_number(note_name))
