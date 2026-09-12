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

#: The pitch a sample is taken to sound at when nothing says otherwise.
#: Asking for this note plays the file at its own rate.
BASE_NOTE = "C-4"

#: Fastest the DAC actually accepts new bytes, in Hz. MEASURED, not read
#: off a datasheet: feeding a 220 Hz tone through register 0x2A at rising
#: rates and reading the pitch back off the render gives
#:
#:      feed 11025 -> 220.0 Hz      feed 26633 -> 132.0 Hz
#:      feed 16000 -> 219.7 Hz      feed 32000 -> 109.9 Hz
#:      feed 22050 -> 159.4 Hz      feed 44100 ->  79.7 Hz
#:
#: Every failing row works out to the same effective rate — 15,979,
#: 15,980, 15,985, 15,977 — so the chip latches a new byte about once
#: every 3.33 output samples and anything faster is quietly decimated.
#: Note what that does: the DURATION stays right and only the pitch
#: drops, which is why it reads as broken repitching rather than as a
#: ceiling. The built-in kit's 16000 Hz sits just under it, which is luck
#: rather than judgement.
#:
#: So a sample plays faster by having its data thinned, never by asking
#: for a faster feed. See `for_note` and `load_wav`.
DAC_RATE_CEILING = 15980


class PCMSample:
    """8-bit unsigned PCM, exactly what register 0x2A eats."""

    __slots__ = ("name", "data", "rate", "base_note")

    def __init__(self, name: str, data, rate: int = DEFAULT_RATE,
                 base_note: str = None):
        self.name = name
        self.data = data if isinstance(data, array) else array("B", data)
        self.rate = rate
        #: The pitch this sample sounds at its own rate, so a score can
        #: ask for another one. A reference point, not a claim: a kick has
        #: no real pitch, and repitching one down an octave is still a
        #: thing drivers do and Genesis composers did.
        self.base_note = base_note or BASE_NOTE

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


def load_wav(name: str, path: str, rate: int = None,
             base_note: str = None) -> PCMSample:
    """Import a real WAV as a DAC sample. Mono-ised and requantised to 8-bit.

    `base_note` is the pitch the file sounds at, which is what lets a
    score play it at another one. Left out, it is assumed to be BASE_NOTE
    and asking for that note plays the file untouched.
    """
    import analysis
    import wavio
    buf, file_rate = wavio.read(path)
    # analysis.to_mono, not a hand-rolled average. wavio hands back either
    # a numpy array shaped (frames, channels) or a flat Buffer with a
    # channel count, and the loop that used to be here tested
    # isinstance(frame, (tuple, list)) — false for a numpy row and false
    # for a flat sample — so it never averaged anything on either
    # backend. A stereo file came out twice as long with the channels
    # interleaved as consecutive samples, which reads as an octave down
    # plus aliasing. Nothing caught it because nothing could reach this
    # function until now.
    frames = list(analysis.to_mono(buf))
    sample = _quantise(frames, rate or file_rate, name)
    sample.base_note = base_note or BASE_NOTE
    if sample.rate > DAC_RATE_CEILING:
        # The DAC cannot be fed this fast, so store the sample at a rate
        # it can play. Left alone, a 22 kHz file plays at the right length
        # and 557 cents flat, because the chip drops the bytes it cannot
        # take and nothing reports it.
        sample = thin(sample, sample.rate / float(DAC_RATE_CEILING),
                      DAC_RATE_CEILING, name)
        sample.base_note = base_note or BASE_NOTE
    return register(sample)


def rate_for_note(sample: PCMSample, note: str, octave: int) -> int:
    """Playback rate that puts `sample` at the asked-for pitch.

    Repitching a sample is done by playing it faster or slower, which
    also makes it shorter or longer — there is no formant correction on a
    chip whose sample player is one register. An octave down is twice as
    long, and on the DAC that is audible as the drum getting fatter,
    which is the reason the trick is used.
    """
    import opn2
    base_name, base_octave = _split_note(sample.base_note)
    target = opn2.note_to_freq(note, octave)
    origin = opn2.note_to_freq(base_name, base_octave)
    if origin <= 0 or target <= 0:
        return sample.rate
    return max(1, int(round(sample.rate * target / origin)))


def thin(sample: PCMSample, factor: float, rate: int = None,
         name: str = None) -> PCMSample:
    """Keep one byte in `factor` so the sample plays `factor` times faster.

    Takes the FACTOR, not a target rate. Passing a rate looked natural and
    was wrong: thinning to the ceiling from a sample already AT the
    ceiling is a factor of one, so pitching up did nothing and the note
    came out at the base pitch.

    Nearest-neighbour, which is what the hardware's own repitching sounds
    like and cheaper than pretending otherwise.
    """
    if factor <= 0:
        return sample
    n_out = max(1, int(round(len(sample) / factor)))
    step = len(sample) / n_out
    data = array("B", bytes(n_out))
    for i in range(n_out):
        data[i] = sample.data[min(len(sample) - 1, int(i * step))]
    return PCMSample(name or sample.name, data, rate or sample.rate,
                     base_note=sample.base_note)


def for_note(sample: PCMSample, note: str, octave: int):
    """-> (name to play, feed rate) for `sample` at the asked-for pitch.

    Below the DAC's ceiling this is just a faster or slower feed. Above
    it, the chip would decimate the feed and the note would come out flat
    at the right length, so the data is thinned here by exactly the excess
    and fed at the ceiling instead. The derived sample is registered under
    its own name so an event can refer to it.
    """
    rate = rate_for_note(sample, note, octave)
    if rate <= DAC_RATE_CEILING:
        return sample.name, rate
    derived = f"{sample.name}@{note}{octave}"
    if derived not in _kit():
        register(thin(sample, rate / float(DAC_RATE_CEILING),
                      DAC_RATE_CEILING, derived))
    return derived, DAC_RATE_CEILING


def _split_note(text: str):
    """'C-4' / 'A#3' -> ('C', 4). Accepts what the tracker accepts."""
    import events as events_mod
    body = text.strip()
    octave = int(body[-1])
    name = body[:-1].rstrip("-")
    canonical = events_mod.normalize_note(name)
    if canonical is None:
        raise ValueError(f"{text!r} is not a note")
    return canonical, octave


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
