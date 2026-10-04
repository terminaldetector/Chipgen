"""layers.py — percussion and effects as a few layers of simple sound.

A kick is a sine that falls in pitch, a click, and a little saturation. A
snare is a pair of tones and a burst of high-passed noise. A hat is six
square waves at awkward ratios, high-passed, and noise. A riser is noise
through a filter that opens. None of them is more than three or four of
the same few parts, so a sound here is a list of **layers**, each a plain
dictionary, and one function turns the list into samples:

    osc     a wave (sine, triangle, saw, square) whose pitch falls (or
            rises) exponentially from f0 to f1, with an attack, a decay,
            and optional saturation
    noise   white noise through a high-pass, low-pass or band-pass (which
            may sweep), with an attack, a decay, or a list of bursts
    metal   squares at inharmonic ratios of a base pitch, high-passed
    click   a few milliseconds of noise

Every layer has `at` (seconds before it starts) and `amp`. The sum is
saturated if `drive` is given, faded at the end so it cannot click, and
left unnormalised; the family does that.

The envelopes are geometric progressions (`itertools.accumulate` at C
speed); only the filters run a Python loop per sample.
"""

import math
import random
from itertools import accumulate, repeat
from operator import mul

from . import dsp

RATE = 44100


def expo(n: int, tau: float, rate: float, start: float = 1.0):
    """exp(-t/tau), n points."""
    if n <= 0:
        return []
    r = math.exp(-1.0 / max(1e-6, tau * rate))
    return list(accumulate(repeat(r, n - 1), mul, initial=start))


def _attack(x, seconds: float, rate: float):
    k = int(seconds * rate)
    for i in range(min(k, len(x))):
        x[i] *= i / float(k)
    return x


def _osc(layer, n, rate, rng):
    wave = layer.get("wave", "sine")
    f0 = layer["f0"]
    f1 = layer.get("f1", f0)
    sweep = layer.get("sweep", 0.0)
    decay = layer.get("decay", 0.1)
    length = min(n, int(layer.get("dur", 7.0 * decay) * rate))
    if length < 2:
        return []
    if sweep > 0 and f0 != f1:
        e = expo(length, sweep, rate)
        step = [(f1 + (f0 - f1) * v) * (2.0 * math.pi / rate) for v in e]
    else:
        step = [f1 * (2.0 * math.pi / rate)] * length
    phase = list(accumulate(step))
    if wave == "sine":
        x = list(map(math.sin, phase))
    elif wave == "triangle":
        x = [(2.0 / math.pi) * math.asin(math.sin(p)) for p in phase]
    elif wave == "square":
        x = [1.0 if math.sin(p) >= 0 else -1.0 for p in phase]
    elif wave == "saw":
        x = [2.0 * ((p / (2.0 * math.pi)) % 1.0) - 1.0 for p in phase]
    else:
        raise ValueError(f"no wave {wave!r}")
    env = expo(length, decay, rate, layer.get("amp", 1.0))
    x = [v * e for v, e in zip(x, env)]
    _attack(x, layer.get("attack", 0.0008), rate)
    drive = layer.get("drive", 0.0)
    if drive > 0:
        k = 1.0 / math.tanh(drive)
        x = [math.tanh(drive * v) * k for v in x]
    return x


def _white(n, rng):
    return [rng.uniform(-1.0, 1.0) for _ in range(n)]


def _band(x, layer, rate):
    if layer.get("hp"):
        x = dsp.filtered(x, "hp", layer["hp"], rate)
    if layer.get("lp"):
        x = dsp.filtered(x, "lp", layer["lp"], rate)
    if layer.get("bp"):
        x = dsp.filtered(x, "bp", layer["bp"][0], rate, layer["bp"][1])
    sweep = layer.get("sweep")
    if sweep:
        kind, a, b, tau = sweep["kind"], sweep["f0"], sweep["f1"], sweep["tau"]
        total = len(x)
        if sweep.get("linear"):
            def cutoff(i, a=a, b=b, total=total):
                return a + (b - a) * min(1.0, i / float(total))
        else:
            def cutoff(i, a=a, b=b, tau=tau):
                return b + (a - b) * math.exp(-i / (tau * rate))
        x = dsp.swept(x, kind, cutoff, rate, sweep.get("q", 0.8))
    return x


def _noise(layer, n, rate, rng):
    decay = layer.get("decay", 0.1)
    length = min(n, int(layer.get("dur", 7.0 * decay) * rate))
    if length < 2:
        return []
    x = _band(_white(length, rng), layer, rate)
    amp = layer.get("amp", 1.0)
    bursts = layer.get("bursts")
    if bursts:
        env = [0.0] * length
        for at, tau, a in bursts:
            start = int(at * rate)
            if start >= length:
                continue
            e = expo(length - start, tau, rate, a)
            for i, v in enumerate(e):
                env[start + i] += v
        x = [v * e * amp for v, e in zip(x, env)]
    else:
        env = expo(length, decay, rate, amp)
        x = [v * e for v, e in zip(x, env)]
    shape = layer.get("shape")
    if shape == "hump":
        # amplitude rises to a peak a third of the way and falls
        for i in range(length):
            t = i / float(length)
            x[i] *= math.sin(math.pi * min(1.0, t * 1.5)) if t < 0.67 \
                else math.cos(math.pi * 0.5 * (t - 0.67) / 0.33)
    elif shape == "ramp":
        power = layer.get("power", 2.0)
        for i in range(length):
            x[i] *= (i / float(length)) ** power
    _attack(x, layer.get("attack", 0.0), rate)
    return x


def _metal(layer, n, rate, rng):
    base = layer["base"]
    decay = layer.get("decay", 0.05)
    length = min(n, int(layer.get("dur", 7.0 * decay) * rate))
    if length < 2:
        return []
    out = [0.0] * length
    for k, ratio in enumerate(layer["ratios"]):
        inc = base * ratio / rate
        if inc >= 0.5:
            continue
        phase0 = (0.13 * k) % 1.0
        sq = [1.0 if (phase0 + i * inc) % 1.0 < 0.5 else -1.0
              for i in range(length)]
        out = [o + s for o, s in zip(out, sq)]
    out = [v / len(layer["ratios"]) for v in out]
    if layer.get("hp"):
        out = dsp.filtered(out, "hp", layer["hp"], rate)
    if layer.get("bp"):
        out = dsp.filtered(out, "bp", layer["bp"][0], rate, layer["bp"][1])
    env = expo(length, decay, rate, layer.get("amp", 1.0))
    out = [v * e for v, e in zip(out, env)]
    _attack(out, layer.get("attack", 0.0), rate)
    return out


def _click(layer, n, rate, rng):
    ms = layer.get("ms", 3.0)
    length = min(n, max(2, int(ms * 0.001 * rate * 4)))
    x = _white(length, rng)
    if layer.get("hp"):
        x = dsp.filtered(x, "hp", layer["hp"], rate)
    env = expo(length, ms * 0.001, rate, layer.get("amp", 1.0))
    return [v * e for v, e in zip(x, env)]


_KINDS = {"osc": _osc, "noise": _noise, "metal": _metal, "click": _click}


def render(layers, length: float, rate: int = RATE, seed: int = 1,
           drive: float = 0.0, fade: float = 0.004):
    """-> list of floats, `length` seconds long, unnormalised."""
    n = int(length * rate)
    out = [0.0] * n
    for k, layer in enumerate(layers):
        make = _KINDS.get(layer["t"])
        if make is None:
            raise ValueError(f"no layer type {layer['t']!r}; have "
                             f"{', '.join(_KINDS)}")
        at = int(layer.get("at", 0.0) * rate)
        if at >= n:
            continue
        sig = make(layer, n - at, rate, random.Random(seed * 7919 + k))
        for i, v in enumerate(sig):
            out[at + i] += v
    if drive > 0:
        top = max((abs(v) for v in out), default=0.0) or 1.0
        out = [math.tanh(drive * v / top) * top / math.tanh(drive)
               for v in out]
    f = min(n, int(fade * rate))
    for i in range(f):
        out[n - f + i] *= 0.5 * (1.0 + math.cos(math.pi * (i + 1) / f))
    return out
