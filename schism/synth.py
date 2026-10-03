"""synth.py — samples from recipes, so a model never has to write PCM.

An Impulse Tracker instrument is built on recorded sound. A model cannot
record anything, and 40,000 numbers pasted into a reply are not a sound
design. So the sample is described — "a saw, band-limited, one per octave",
"a kick that falls from 160 to 45 Hz in 80 ms" — and made here, from the
description, the same way every time.

Three kinds, because a tracker uses three:

  single-cycle loops   `multisample()`: one period of a waveform, looped.
                       The note sets the pitch by playing the cycle faster
                       or slower; the band-limiting is what keeps the top
                       octaves from folding back as aliasing. Because the
                       mixer resamples, one sample cannot be clean over
                       nine octaves, so there is one per octave (or two).
  long loops           `pad()`: seconds of detuned voices that loop with
                       no click, because every voice completes a whole
                       number of cycles inside the loop.
  one-shots            drums, plucks, bells: played once, any pitch.

Pure Python, standard library only. The generation is deterministic —
noise comes from a seeded generator — so the same recipe is the same file.
"""

import math
import random
from array import array

SAMPLE_RATE = 44100
#: Frequency of note C-5 in the module's tuning (A-4 = 440 Hz).
C5_HZ = 440.0 * 2.0 ** (3 / 12.0)
#: Highest frequency a band-limited cycle may carry, as a fraction of the
#: mix rate. Under Nyquist (22.05 kHz) with room for the resampler's skirt.
BAND_LIMIT_HZ = 19000.0


def to_int16(x, peak: float = 0.9):
    """Floats -> array('h'), scaled so the loudest value is `peak`."""
    top = max((abs(v) for v in x), default=0.0)
    scale = 0.0 if top == 0 else peak * 32767.0 / top
    return array("h", (int(round(v * scale)) for v in x))


def fade_out(x, fraction: float = 0.15, minimum: float = 0.008,
             sample_rate: int = None):
    """Raised-cosine fade over the last `fraction` of a one-shot (and never
    less than `minimum` seconds), in place; the last frame becomes zero.

    A sample that is still sounding when its data ends stops with a step,
    and a step is a click: the kick's tail was 7% of full scale at the
    end, the low pluck's 19%, the bell's 16%. Fading the last stretch
    costs nothing anyone can hear and the click is gone.
    """
    rate = sample_rate or SAMPLE_RATE
    n = min(len(x), max(2, int(round(max(fraction * len(x),
                                         minimum * rate)))))
    start = len(x) - n
    for k in range(n):
        x[start + k] *= 0.5 * (1.0 + math.cos(math.pi * (k + 1) / n))
    return x


def hz_of_note(note: int) -> float:
    """Note number (1 = C-0) -> Hz."""
    return 440.0 * 2.0 ** ((note - 58) / 12.0)


# -- single-cycle waveforms --------------------------------------------------
_SIN_CACHE = {}


def _sine_table(n: int):
    table = _SIN_CACHE.get(n)
    if table is None:
        table = [math.sin(2.0 * math.pi * i / n) for i in range(n)]
        _SIN_CACHE[n] = table
    return table


def harmonic_series(shape: str, max_harmonic: int, duty: float = 0.5):
    """[(harmonic, amplitude)] of a classic shape, up to `max_harmonic`.

    Fourier series, so the waveform is exactly band-limited rather than
    drawn and then filtered: saw -1/h (a ramp that rises from -1 to +1 and
    snaps back, the textbook sawtooth); square odd 1/h; triangle odd 1/h^2
    with alternating sign; pulse (2/(h pi)) sin(h pi duty) with a phase
    handled by the caller as cosine terms; sine just the fundamental.
    """
    if shape == "sine":
        return [(1, 1.0)]
    if shape == "saw":
        return [(h, -1.0 / h) for h in range(1, max_harmonic + 1)]
    if shape == "square":
        return [(h, 1.0 / h) for h in range(1, max_harmonic + 1, 2)]
    if shape == "triangle":
        return [(h, (1.0 if (h // 2) % 2 == 0 else -1.0) / (h * h))
                for h in range(1, max_harmonic + 1, 2)]
    if shape == "pulse":
        return [(h, math.sin(math.pi * h * duty) / h)
                for h in range(1, max_harmonic + 1)]
    raise ValueError(f"no waveform {shape!r}. Have: sine saw square "
                     f"triangle pulse")


def cycle(shape: str, n: int, max_harmonic: int = None, duty: float = 0.5):
    """One period of `shape`, `n` frames, floats in about -1..1.

    Pulse is built from cosine terms so its duty cycle is a real duty
    cycle (the high part starts at the cycle's start); the others are sine
    terms.
    """
    if max_harmonic is None:
        max_harmonic = n // 2 - 1
    max_harmonic = max(1, min(max_harmonic, n // 2 - 1))
    series = harmonic_series(shape, max_harmonic, duty)
    out = [0.0] * n
    if shape == "pulse":
        for h, amp in series:
            for i in range(n):
                out[i] += amp * math.cos(2.0 * math.pi * h * (i / n - duty / 2))
    else:
        table = _sine_table(n)
        for h, amp in series:
            for i in range(n):
                out[i] += amp * table[(h * i) % n]
    top = max(abs(v) for v in out) or 1.0
    return [v / top for v in out]


def fm_cycle(n: int, ratio: int = 1, index: float = 2.0, feedback: float = 0.0,
             max_harmonic: int = None):
    """Two-operator FM, one period: sin(p + index * sin(ratio * p)).

    `ratio` is an integer so the result is periodic in one cycle. With
    `feedback` the modulator modulates itself, which turns a sine into a
    saw-ish buzz as it rises. Band-limited afterwards by an FFT-free
    method: the spectrum of a cycle is the cosine/sine projection, so it
    is computed and cut at `max_harmonic`.
    """
    raw = []
    previous = 0.0
    for i in range(n):
        phase = 2.0 * math.pi * i / n
        mod = math.sin(ratio * phase + feedback * previous)
        previous = mod
        raw.append(math.sin(phase + index * mod))
    if max_harmonic is None or max_harmonic >= n // 2:
        top = max(abs(v) for v in raw) or 1.0
        return [v / top for v in raw]
    return _band_limit(raw, max_harmonic)


def _band_limit(cycle_values, max_harmonic: int):
    """Keep harmonics 1..max_harmonic of a periodic signal (direct DFT)."""
    n = len(cycle_values)
    table = _sine_table(n)
    quarter = n // 4
    out = [0.0] * n
    for h in range(1, max_harmonic + 1):
        a = b = 0.0
        for i in range(n):
            sin_v = table[(h * i) % n]
            cos_v = table[(h * i + quarter) % n]
            a += cycle_values[i] * cos_v
            b += cycle_values[i] * sin_v
        a *= 2.0 / n
        b *= 2.0 / n
        for i in range(n):
            out[i] += a * table[(h * i + quarter) % n] + b * table[(h * i) % n]
    top = max(abs(v) for v in out) or 1.0
    return [v / top for v in out]


def multisample(make_cycle, low_note: int = 1, high_note: int = 120,
                notes_per_sample: int = 12, minimum_cycle: int = 64,
                maximum_cycle: int = 4096):
    """Band-limited single-cycle samples that cover `low_note`..`high_note`.

    `make_cycle(n, max_harmonic)` returns one period as floats. Each group
    of `notes_per_sample` notes gets one sample, built so that its highest
    note carries no energy above `BAND_LIMIT_HZ` — which is the whole point
    of splitting it.

    -> [(low, high, samples_int16, c5speed)]. `c5speed` is chosen so a
    note plays at its own pitch: N frames per cycle at N * C-5 Hz.
    """
    groups = []
    low = low_note
    while low <= high_note:
        high = min(high_note, low + notes_per_sample - 1)
        top_hz = hz_of_note(high)
        max_h = max(1, int(BAND_LIMIT_HZ / top_hz))
        n = 1
        while n < 2 * max_h + 2 and n < maximum_cycle:
            n *= 2
        n = max(minimum_cycle, n)
        values = make_cycle(n, min(max_h, n // 2 - 1))
        groups.append((low, high, to_int16(values, 0.9),
                       int(round(n * C5_HZ))))
        low = high + 1
    return groups


# -- long loops --------------------------------------------------------------
def pad(base_note: int = 49, voices: int = 5, detune_cents: float = 14.0,
        seconds: float = 3.0, shape: str = "saw", seed: int = 7,
        sample_rate: int = SAMPLE_RATE):
    """A detuned chorus of voices as a seamless loop.

    Each voice's frequency is rounded so it completes a whole number of
    cycles in the loop; the loop therefore joins itself exactly, and the
    detune (and the beating it makes) repeats every `seconds`. Voice
    phases are random but seeded.

    -> (samples_int16, c5speed). Played at `base_note` it sounds that
    note; elsewhere it transposes, beating and all.
    """
    rng = random.Random(seed)
    length = int(seconds * sample_rate)
    f0 = hz_of_note(base_note)
    max_h = max(1, int(BAND_LIMIT_HZ / (f0 * 2 ** (detune_cents / 1200.0))))
    table_n = 2048
    table = cycle(shape, table_n, min(max_h, table_n // 2 - 1))
    out = [0.0] * length
    for v in range(voices):
        spread = 0.0 if voices == 1 else (2.0 * v / (voices - 1) - 1.0)
        f = f0 * 2.0 ** (spread * detune_cents / 1200.0)
        cycles = max(1, int(round(f * length / sample_rate)))
        phase = rng.random()
        step = cycles * table_n / length
        pos = phase * table_n
        for i in range(length):
            j = int(pos)
            frac = pos - j
            a = table[j % table_n]
            b = table[(j + 1) % table_n]
            out[i] += a + (b - a) * frac
            pos += step
    # C-5 plays the data unshifted at c5speed; the data holds base_note's
    # pitch at sample_rate, so scale c5speed by the interval to C-5.
    c5speed = int(round(sample_rate * C5_HZ / f0))
    return to_int16(out, 0.85), c5speed


# -- one-shots ---------------------------------------------------------------
def _noise(n: int, seed: int):
    rng = random.Random(seed)
    return [rng.uniform(-1.0, 1.0) for _ in range(n)]


def _highpass(x, cutoff: float, sample_rate: int = SAMPLE_RATE):
    """One-pole high-pass."""
    rc = 1.0 / (2.0 * math.pi * cutoff)
    dt = 1.0 / sample_rate
    a = rc / (rc + dt)
    out, prev_x, prev_y = [], 0.0, 0.0
    for v in x:
        prev_y = a * (prev_y + v - prev_x)
        prev_x = v
        out.append(prev_y)
    return out


def _lowpass(x, cutoff: float, sample_rate: int = SAMPLE_RATE):
    dt = 1.0 / sample_rate
    rc = 1.0 / (2.0 * math.pi * cutoff)
    a = dt / (rc + dt)
    out, y = [], 0.0
    for v in x:
        y += a * (v - y)
        out.append(y)
    return out


def kick(seconds: float = 0.34, start_hz: float = 165.0, end_hz: float = 44.0,
         sweep: float = 0.045, decay: float = 0.11, drive: float = 1.6,
         sample_rate: int = SAMPLE_RATE):
    """A falling sine with a clicked start and a little saturation."""
    n = int(seconds * sample_rate)
    out, phase = [], 0.0
    for i in range(n):
        t = i / sample_rate
        hz = end_hz + (start_hz - end_hz) * math.exp(-t / sweep)
        phase += 2.0 * math.pi * hz / sample_rate
        env = math.exp(-t / decay) * min(1.0, t / 0.0008)
        out.append(math.tanh(drive * math.sin(phase) * env))
    click = _noise(int(0.003 * sample_rate), 1)
    for i, v in enumerate(click):
        out[i] += 0.25 * v * (1.0 - i / len(click))
    return to_int16(fade_out(out, sample_rate=sample_rate), 0.95)


def snare(seconds: float = 0.3, tone_hz: float = 185.0, seed: int = 2,
          sample_rate: int = SAMPLE_RATE):
    n = int(seconds * sample_rate)
    noise = _highpass(_noise(n, seed), 1400.0)
    out = []
    for i in range(n):
        t = i / sample_rate
        body = (math.sin(2 * math.pi * tone_hz * t)
                + 0.5 * math.sin(2 * math.pi * tone_hz * 1.58 * t))
        out.append(0.55 * body * math.exp(-t / 0.045)
                   + 0.9 * noise[i] * math.exp(-t / 0.085))
    return to_int16(fade_out(out, sample_rate=sample_rate), 0.95)


def hat(seconds: float = 0.075, decay: float = 0.022, seed: int = 3,
        sample_rate: int = SAMPLE_RATE):
    """Six square oscillators at the 808's metallic ratios, plus noise,
    high-passed. `seconds` and `decay` make it closed or open."""
    n = int(seconds * sample_rate)
    base = 40.0
    ratios = (2.0, 3.0, 4.16, 5.43, 6.79, 8.21)
    out = [0.0] * n
    for r in ratios:
        period = sample_rate / (base * r)
        for i in range(n):
            out[i] += 1.0 if (i % period) < period / 2 else -1.0
    noise = _noise(n, seed)
    out = _highpass([o * 0.12 + 0.5 * z for o, z in zip(out, noise)], 6500.0)
    shaped = [v * math.exp(-(i / sample_rate) / decay)
              for i, v in enumerate(out)]
    return to_int16(fade_out(shaped, sample_rate=sample_rate), 0.85)


def clap(seconds: float = 0.28, seed: int = 4, sample_rate: int = SAMPLE_RATE):
    n = int(seconds * sample_rate)
    noise = _highpass(_lowpass(_noise(n, seed), 4500.0), 700.0)
    out = [0.0] * n
    for k, at in enumerate((0.0, 0.011, 0.023, 0.034)):
        start = int(at * sample_rate)
        length = int((0.01 if k < 3 else 0.2) * sample_rate)
        tau = 0.004 if k < 3 else 0.06
        for i in range(min(length, n - start)):
            out[start + i] += noise[start + i] * math.exp(-(i / sample_rate) / tau)
    return to_int16(fade_out(out, sample_rate=sample_rate), 0.9)


def tom(seconds: float = 0.5, hz: float = 130.0, sample_rate: int = SAMPLE_RATE):
    """Played at C-5 it sounds `hz` and falls to 70% of it; play it at
    other notes for a set of toms."""
    n = int(seconds * sample_rate)
    out, phase = [], 0.0
    for i in range(n):
        t = i / sample_rate
        f = hz * (0.7 + 0.3 * math.exp(-t / 0.05))
        phase += 2.0 * math.pi * f / sample_rate
        out.append(math.sin(phase) * math.exp(-t / 0.16))
    return to_int16(fade_out(out, sample_rate=sample_rate), 0.95)


def rim(sample_rate: int = SAMPLE_RATE):
    n = int(0.06 * sample_rate)
    out = [(math.sin(2 * math.pi * 1700 * i / sample_rate)
            + 0.7 * math.sin(2 * math.pi * 480 * i / sample_rate))
           * math.exp(-(i / sample_rate) / 0.008) for i in range(n)]
    return to_int16(fade_out(out, sample_rate=sample_rate), 0.9)


def pluck(root_note: int = 61, seconds: float = 1.6, damping: float = 0.997,
          brightness: float = 9000.0, seed: int = 5,
          sample_rate: int = SAMPLE_RATE):
    """Karplus-Strong: a noise burst circulating in a delay line that
    averages neighbours, so the high partials die first and what is left
    rings at the line's pitch.

        y[n] = damping * (y[n-N] + y[n-N-1]) / 2

    The line is a whole number of frames, N, which is what makes it cheap
    and exact: the two-tap average adds half a frame of delay at every
    frequency, so the pitch is sample_rate / (N + 0.5), and `c5speed` is
    set from that rather than hoped for. (A first version updated the
    delay line in place and let the last tap read a value already
    overwritten; it came out 20.7 cents sharp at every note, which is how
    the tuning test found it.)

    `root_note` picks N (a lower root is a longer line, a darker and fuller
    pluck when played higher). -> (samples_int16, c5speed)
    """
    hz = hz_of_note(root_note)
    size = max(4, int(round(sample_rate / hz)))
    rng = random.Random(seed)
    burst = _lowpass([rng.uniform(-1.0, 1.0) for _ in range(size + 1)],
                     brightness, sample_rate)
    total = int(seconds * sample_rate)
    out = burst + [0.0] * max(0, total - len(burst))
    for n in range(size + 1, total):
        out[n] = damping * 0.5 * (out[n - size] + out[n - size - 1])
    out = fade_out(out[:total], sample_rate=sample_rate)
    return to_int16(out, 0.9), int(round((size + 0.5) * C5_HZ))


def bell(hz: float = C5_HZ, seconds: float = 2.4, seed: int = 6,
         sample_rate: int = SAMPLE_RATE):
    """Inharmonic partials with their own decays: the FM-bell recipe done
    additively. Returned at its own pitch: played at C-5 it is `hz`."""
    partials = ((1.0, 1.0, 1.9), (2.0, 0.6, 1.3), (2.76, 0.5, 1.0),
                (5.4, 0.28, 0.55), (8.93, 0.15, 0.35))
    n = int(seconds * sample_rate)
    out = [0.0] * n
    for ratio, amp, tau in partials:
        w = 2.0 * math.pi * hz * ratio / sample_rate
        for i in range(n):
            out[i] += amp * math.sin(w * i) * math.exp(-(i / sample_rate) / tau)
    for i in range(int(0.004 * sample_rate)):      # a short attack, no click
        out[i] *= i / (0.004 * sample_rate)
    return (to_int16(fade_out(out, sample_rate=sample_rate), 0.9),
            int(round(sample_rate * C5_HZ / hz)))


def noise_loop(seconds: float = 1.0, seed: int = 8, color: str = "white",
               sample_rate: int = SAMPLE_RATE):
    """White (or `pink`, or `brown`) noise as a loop. Its ends are joined
    with a short crossfade so the repeat has no click."""
    n = int(seconds * sample_rate)
    x = _noise(n, seed)
    if color == "brown":
        y = 0.0
        out = []
        for v in x:
            y = max(-1.0, min(1.0, y + 0.05 * v))
            out.append(y)
        x = out
    elif color == "pink":
        x = _lowpass(x, 3500.0)
    fade = min(n // 8, int(0.02 * sample_rate))
    for i in range(fade):
        w = i / fade
        x[i] = x[i] * w + x[n - fade + i] * (1.0 - w)
    return to_int16(x[:n - fade], 0.8)
