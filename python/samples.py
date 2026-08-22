"""
samples.py — the PCM kit that plays through the YM2612 DAC.

Genesis drums are samples: the composer gives up FM channel 6, flips
register 0x2B, and streams 8-bit unsigned PCM at register 0x2A. That is
how Streets of Rage and Contra: Hard Corps get a kick that FM cannot make.
chipgen now does the same thing, so the DAC entry in the README's "not
here yet" list is closed.

The kit is SYNTHESISED, not recorded — a few lines of math per drum
instead of a megabyte of WAV. Two reasons, both practical:

  * the zip you hand to a model stays tiny, which is the entire point of
    the bridge path;
  * there is nothing to license, so the whole kit is as reusable as the
    rest of the Python layer.

Noise comes from a fixed-seed LCG rather than `random`, so a given
chipgen version renders byte-identical drums every run — otherwise two
renders of the same event list would not compare equal, and the tests
could not check anything.

Load real samples instead with `load_wav(name, path)` whenever you want
them; anything in KIT is addressable by name from a DACSample event.
"""

import math
from array import array

#: Genesis drivers usually stream the DAC somewhere between 8 and 26 kHz.
#: Higher costs more CPU on real hardware; 16 kHz is the common compromise
#: and what every drum here is authored at.
DEFAULT_RATE = 16000


class PCMSample:
    """8-bit unsigned PCM, exactly what register 0x2A eats."""

    __slots__ = ("name", "data", "rate")

    def __init__(self, name: str, data, rate: int = DEFAULT_RATE):
        self.name = name
        self.data = data if isinstance(data, array) else array("B", data)
        self.rate = rate

    def __len__(self):
        return len(self.data)

    @property
    def duration(self) -> float:
        return len(self.data) / float(self.rate)

    def __repr__(self):
        return f"PCMSample({self.name!r}, {len(self)} bytes @ {self.rate} Hz)"


class _Noise:
    """Reproducible white noise. Numerical Recipes LCG, top bits only."""

    __slots__ = ("state",)

    def __init__(self, seed: int = 0x1234_5678):
        self.state = seed & 0xFFFFFFFF

    def __call__(self) -> float:
        self.state = (1664525 * self.state + 1013904223) & 0xFFFFFFFF
        return (self.state >> 8) / 8388607.5 - 1.0


def _quantise(frames, rate: int, name: str, peak: float = 0.92) -> PCMSample:
    """Float [-1,1] -> unsigned 8-bit centred on 128, normalised to `peak`."""
    high = max((abs(v) for v in frames), default=0.0)
    gain = (peak / high) if high > 0 else 0.0
    data = array("B", bytes(len(frames)))
    for i, v in enumerate(frames):
        s = int(round(128 + v * gain * 127))
        data[i] = 0 if s < 0 else (255 if s > 255 else s)
    return PCMSample(name, data, rate)


def _env(i: int, n: int, decay: float) -> float:
    """Exponential decay with a short fade-out so nothing clicks at the end."""
    value = math.exp(-decay * i / n)
    tail = n // 16
    if i > n - tail:
        value *= (n - i) / float(tail)
    return value


def _kick(rate: int = DEFAULT_RATE) -> PCMSample:
    n = int(rate * 0.22)
    out, phase = [], 0.0
    for i in range(n):
        t = i / n
        freq = 45.0 + 95.0 * math.exp(-9.0 * t)   # pitch drop = the "thump"
        phase += 2 * math.pi * freq / rate
        body = math.sin(phase)
        click = math.sin(phase * 5.0) * math.exp(-60.0 * t) * 0.25
        out.append((body + click) * _env(i, n, 5.0))
    return _quantise(out, rate, "kick")


def _snare(rate: int = DEFAULT_RATE) -> PCMSample:
    n = int(rate * 0.17)
    noise, out, phase = _Noise(0xBEEF), [], 0.0
    prev = 0.0
    for i in range(n):
        t = i / n
        phase += 2 * math.pi * 185.0 / rate
        tone = math.sin(phase) * math.exp(-16.0 * t) * 0.55
        # one-pole high-pass on the noise: raw white noise reads as "hiss",
        # the snares on a real drum sit above the body
        raw = noise()
        hp = raw - prev
        prev = raw
        out.append((tone + hp * 0.8) * _env(i, n, 8.0))
    return _quantise(out, rate, "snare")


def _hat(name: str, seconds: float, rate: int = DEFAULT_RATE) -> PCMSample:
    n = int(rate * seconds)
    noise, out, prev = _Noise(0xCAFE), [], 0.0
    for i in range(n):
        raw = noise()
        hp = raw - prev * 0.85          # steeper high-pass -> metallic
        prev = raw
        out.append(hp * _env(i, n, 14.0 if seconds < 0.1 else 5.0))
    return _quantise(out, rate, name)


def _tom(rate: int = DEFAULT_RATE) -> PCMSample:
    n = int(rate * 0.26)
    out, phase = [], 0.0
    for i in range(n):
        t = i / n
        freq = 110.0 + 110.0 * math.exp(-6.0 * t)
        phase += 2 * math.pi * freq / rate
        out.append(math.sin(phase) * _env(i, n, 4.5))
    return _quantise(out, rate, "tom")


def _clap(rate: int = DEFAULT_RATE) -> PCMSample:
    n = int(rate * 0.20)
    noise, out, prev = _Noise(0x5EED), [], 0.0
    # three fast bursts then a tail — a clap is many hands slightly apart,
    # and a single noise burst never sounds like one
    bursts = [0.0, 0.011, 0.023]
    for i in range(n):
        t = i / rate
        amp = 0.0
        for b in bursts:
            if t >= b:
                amp = max(amp, math.exp(-90.0 * (t - b)))
        amp = max(amp, 0.42 * math.exp(-13.0 * max(0.0, t - bursts[-1])))
        raw = noise()
        hp = raw - prev * 0.6
        prev = raw
        out.append(hp * amp)
    return _quantise(out, rate, "clap")


def _rim(rate: int = DEFAULT_RATE) -> PCMSample:
    n = int(rate * 0.05)
    out, phase = [], 0.0
    noise = _Noise(0x0DDBA11)
    for i in range(n):
        t = i / n
        phase += 2 * math.pi * 1700.0 / rate
        out.append((math.sin(phase) * 0.6 + noise() * 0.4) * math.exp(-28.0 * t))
    return _quantise(out, rate, "rim")


def _build_kit():
    return {
        "kick": _kick(),
        "snare": _snare(),
        "hat": _hat("hat", 0.045),
        "hat_open": _hat("hat_open", 0.20),
        "tom": _tom(),
        "clap": _clap(),
        "rim": _rim(),
    }


_KIT = None


def _kit():
    global _KIT
    if _KIT is None:
        _KIT = _build_kit()          # ~0.1 s, and only if a DAC event shows up
    return _KIT


class _KitView:
    """Dict-ish access that builds the kit on first touch, not on import."""

    def __getitem__(self, name):
        try:
            return _kit()[name]
        except KeyError:
            raise KeyError(f"unknown DAC sample {name!r}; have: "
                           f"{', '.join(sorted(_kit()))}") from None

    def __contains__(self, name):
        return name in _kit()

    def __iter__(self):
        return iter(_kit())

    def __len__(self):
        return len(_kit())

    def keys(self):
        return _kit().keys()

    def items(self):
        return _kit().items()

    def get(self, name, default=None):
        return _kit().get(name, default)


#: name -> PCMSample, addressable from a DACSample event
KIT = _KitView()


def names():
    return sorted(_kit())


def register(sample: PCMSample):
    """Add (or replace) a kit entry."""
    _kit()[sample.name] = sample
    return sample


def load_wav(name: str, path: str, rate: int = None) -> PCMSample:
    """Import a real WAV as a DAC sample. Mono-ised and requantised to 8-bit."""
    import wavio
    buf, file_rate = wavio.read(path)
    frames = []
    for frame in buf:
        frames.append(sum(frame) / len(frame) if isinstance(frame, (tuple, list))
                      else frame)
    sample = _quantise(frames, rate or file_rate, name)
    return register(sample)


def resample(sample: PCMSample, rate: int) -> PCMSample:
    """Nearest-neighbour repitch — crunchy on purpose, like the hardware."""
    if rate <= 0 or rate == sample.rate:
        return sample
    n_out = max(1, int(len(sample) * rate / sample.rate))
    step = len(sample) / n_out
    data = array("B", bytes(n_out))
    for i in range(n_out):
        data[i] = sample.data[min(len(sample) - 1, int(i * step))]
    return PCMSample(sample.name, data, rate)
