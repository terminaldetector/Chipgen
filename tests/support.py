"""Shared helpers. Kept tiny on purpose — a test suite with its own
framework is a second thing to debug when the first one breaks."""

import math
import os
import shutil
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


class Skipped(Exception):
    """Raised by skip(). The bare runner catches it; so does pytest, because
    when pytest is installed this is its own skip exception."""


try:                                    # pragma: no cover - depends on the host
    import pytest as _pytest
    Skipped = _pytest.skip.Exception
except ImportError:                     # pragma: no cover
    pass


def skip(reason: str):
    """Skip the current test, under either runner."""
    raise Skipped(reason)


class TempDir:
    """`with TempDir() as d:` — a scratch directory that cleans itself up."""

    def __enter__(self):
        self.path = tempfile.mkdtemp(prefix="chipgen-test-")
        return self.path

    def __exit__(self, *exc):
        shutil.rmtree(self.path, ignore_errors=True)
        return False


def db_between(a: float, b: float) -> float:
    """Level difference in dB, for comparing two renders of the same music."""
    if a <= 0 or b <= 0:
        return float("inf")
    return abs(20.0 * math.log10(a / b))


def dominant_frequency(buf, sample_rate: float, skip: int = 2000,
                       window: int = 16384):
    """Loudest non-DC frequency in a buffer, without needing numpy.

    Uses the Goertzel algorithm over a coarse log-spaced sweep and then
    refines: a full FFT would mean either depending on numpy (which the
    engine deliberately does not) or writing one here.
    """
    frames = len(buf) - skip
    if frames < window:
        window = max(256, frames)
    samples = [_mono(buf, i) for i in range(skip, skip + window)]

    def power(frequency):
        omega = 2.0 * math.pi * frequency / sample_rate
        coefficient = 2.0 * math.cos(omega)
        s1 = s2 = 0.0
        for value in samples:
            s0 = value + coefficient * s1 - s2
            s2, s1 = s1, s0
        return s1 * s1 + s2 * s2 - coefficient * s1 * s2

    best = max((frequency for frequency in _sweep(40.0, 6000.0, 1.02)),
               key=power)
    # refine around the winner
    return max((best * step for step in _sweep(0.96, 1.04, 1.002)), key=power)


def _sweep(low, high, ratio):
    value = low
    while value <= high:
        yield value
        value *= ratio


def _mono(buf, index):
    frame = buf[index]
    if isinstance(frame, (tuple, list)):
        return sum(frame) / len(frame)
    try:
        return float(frame)
    except TypeError:                      # numpy row
        return float(sum(frame) / len(frame))
