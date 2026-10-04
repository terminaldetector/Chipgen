"""modal.py — struck and plucked things as a sum of damped sinusoids.

A string, a bar, a bell, a plate: after the strike each rings in a handful of
*modes*, each its own frequency and its own fall. That is a complete
description of what makes a pluck a pluck (the high partials die first, so
the sound darkens as it rings), a marimba a marimba (a fundamental and a
tenth, nothing between) and a bell a bell (partials that are not multiples
of anything).

The sum is built in the time domain, mode by mode, with table lookups and
geometric envelopes (the decay of one mode is a running product, which
`itertools.accumulate` does at C speed). A mode that has died is not
computed to the end of the note.
"""

import math
import random
from itertools import accumulate, repeat
from operator import mul

from . import dsp

TABLE = 16384
_MASK = TABLE - 1
_SIN = [math.sin(2.0 * math.pi * i / TABLE) for i in range(TABLE)]

#: partial ratios of the sets a modal instrument can be built on
SETS = {
    "string": tuple(float(h) for h in range(1, 17)),
    "tuned": (1.0, 4.0, 10.0, 18.0, 28.0),
    "bar": (1.0, 2.756, 5.404, 8.933, 13.344, 18.638),
    "bell": (1.0, 2.0, 2.42, 3.0, 4.23, 5.44, 6.8, 8.93),
    "plate": (1.0, 2.32, 4.25, 6.63, 9.38, 12.5),
}


def modes_sum(modes, n: int, rate: float, f_base: float):
    """Sum of damped sinusoids.

    `modes` is [(ratio, amplitude, tau seconds, phase 0..1)]; the mode is
    at ratio * f_base hertz in the stored signal, which has `rate` frames a
    second and `n` frames. -> list of n floats.
    """
    out = [0.0] * n
    for ratio, amp, tau, phase in modes:
        f = ratio * f_base
        if f >= rate * 0.5 or amp <= 0.0:
            continue
        length = min(n, int(7.0 * tau * rate) + 1)
        if length < 2:
            continue
        step = int(round(f / rate * TABLE * 65536))
        p0 = int(phase * TABLE * 65536)
        r = math.exp(-1.0 / (tau * rate))
        env = accumulate(repeat(r, length - 1), mul, initial=amp)
        tone = ((p0 + i * step) >> 16 for i in range(length))
        chunk = [_SIN[t & _MASK] * e for t, e in zip(tone, env)]
        if length == n:
            out = [o + c for o, c in zip(out, chunk)]
        else:
            out[:length] = [o + c for o, c in zip(out[:length], chunk)]
    return out


def ramp_in(x, frames: int):
    for i in range(min(frames, len(x))):
        x[i] *= i / float(frames)
    return x


def tremolo(x, depth: float, hz: float, rate: float):
    """Multiply by 1 + depth*cos, scaled to keep the peak level."""
    w = 2.0 * math.pi * hz / rate
    k = 1.0 / (1.0 + depth)
    return [v * (1.0 + depth * math.cos(w * i)) * k for i, v in enumerate(x)]


def noise_burst(n: int, tau: float, rate: float, seed: int, hp: float = 0.0):
    rng = random.Random(seed)
    raw = [rng.uniform(-1.0, 1.0) for _ in range(n)]
    if hp:
        raw = dsp.filtered(raw, "hp", hp, rate)
    r = math.exp(-1.0 / (tau * rate))
    env = accumulate(repeat(r, n - 1), mul, initial=1.0)
    return [v * e for v, e in zip(raw, env)]
