"""spectral.py — a sound as lines in a spectrum, made into a seamless loop.

A tracker plays a sample at any pitch by reading it faster or slower, so a
sustained note is a *loop*: a stretch of waveform that joins itself. The
easy loop is one cycle of a waveform; the useful loops are longer, because
everything that makes a held note alive — two voices beating against each
other, a vibrato, a breath of noise, partials that are not exact multiples
of the fundamental — is a pattern that has to repeat exactly once per loop.

The way to guarantee that is to build the loop in the frequency domain. A
loop of `N` frames can hold only frequencies that complete a whole number
of cycles in it: the *bins*. Put a line at bin `b` and it joins itself; so
does any sum of such lines; so do the sidebands that a tremolo or a vibrato
adds, if the modulation also falls on a bin. One inverse FFT turns the
lines into the loop. That is this module:

    lines         (bin, amplitude, phase), made from a spectrum description
    voices        unison: the same spectrum a few bins sharp and flat, which
                  beats at (bins apart) x 44100/N hertz
    modulation    amplitude (tremolo) and phase (vibrato) sidebands, in
                  closed form, so a vibrato of 14 cents is 14 cents
    noise         random-phase lines in every bin, shaped
    loop          irfft, normalised
    sample        the loop with an intro before it (an attack ramp and a
                  bright transient), tuned by `c5speed`

Tuning is arithmetic rather than hope: the loop holds `m` cycles of the
fundamental in `N` frames, so playing C-5 at `c5speed = C5 * N / m` frames a
second sounds C-5, and every other note follows (the same rule the plain
`synth.multisample` uses, with `m = 1`).

Pure Python; a 32768-frame loop is built in about 60 ms.
"""

import math
import random
from array import array

from . import dsp

SAMPLE_RATE = 44100
C5_HZ = 440.0 * 2.0 ** (3 / 12.0)
#: Highest frequency a note may carry after it is played, as `synth` has it.
BAND_LIMIT_HZ = 19000.0
#: No line closer to the top of a loop than this many frames per cycle:
#: the player interpolates between frames, and below four a line is
#: mostly its own image.
MIN_FRAMES_PER_CYCLE = 4


def hz_of_note(note: int) -> float:
    """IT note number (1 = C-0, 58 = A-4) -> Hz."""
    return 440.0 * 2.0 ** ((note - 58) / 12.0)


# -- Bessel functions, for the vibrato sidebands -----------------------------
#: past this the power series loses its digits to cancellation
SERIES_LIMIT = 12.0


def _miller(x: float, nmax: int):
    """[J_0(x) .. J_nmax(x)] by Miller's backward recurrence, which stays
    accurate where the power series does not (a high partial under a deep
    vibrato has a modulation index of tens)."""
    ax = abs(x)
    start = int(max(nmax, ax)) + 40
    start += start % 2
    values = [0.0] * (start + 2)
    values[start] = 1e-30
    for k in range(start, 0, -1):
        values[k - 1] = 2.0 * k / ax * values[k] - values[k + 1]
        if abs(values[k - 1]) > 1e200:
            for i in range(k - 1, start + 2):
                values[i] *= 1e-200
    norm = values[0] + 2.0 * sum(values[2:start + 1:2])
    table = [v / norm for v in values[:nmax + 1]]
    if x < 0:
        table = [-v if n % 2 else v for n, v in enumerate(table)]
    return table


def bessel_table(x: float, nmax: int):
    """[J_0(x) .. J_nmax(x)]."""
    if abs(x) > SERIES_LIMIT:
        return _miller(x, nmax)
    return [bessel_j(n, x) for n in range(nmax + 1)]


def bessel_j(n: int, x: float) -> float:
    """J_n(x): its power series, right to 1e-12 for |x| < 12, and past that
    the backward recurrence."""
    if n < 0:
        return (-1) ** (-n) * bessel_j(-n, x)
    if abs(x) > SERIES_LIMIT:
        return _miller(x, n)[n]
    half = x / 2.0
    term = half ** n / math.factorial(n)
    total = term
    for k in range(1, 40):
        term *= -half * half / (k * (k + n))
        total += term
        if abs(term) < 1e-14 * max(1.0, abs(total)):
            break
    return total


# -- the loop ------------------------------------------------------------------
class LoopSpec:
    """Everything a loop is built from.

    partials   [(ratio, amplitude)] — frequency relative to the fundamental,
               which is 1.0; amplitudes are relative to each other
    phases     'sine' (all sine phase, the classical waveforms) or 'random'
    voices     [(cycle offset, amplitude)] — unison: each voice is the whole
               spectrum at (m + offset) cycles, `offset` counted in bins of
               the fundamental. [(0, 1.0)] is a single voice.
    am, pm     tremolo depth (fraction of the amplitude, peak) and vibrato
               depth (cents, peak); `lfo_bins` is the modulation rate in
               bins, so it is `lfo_bins * 44100 / N` hertz
    noise      level of the noise lines against the loudest partial, and its
               tilt in dB per octave
    cycles     m, cycles of the fundamental in the loop
    frames     N, the loop length, a power of two
    """

    def __init__(self, partials, frames, cycles, phases="sine", voices=None,
                 am=0.0, pm=0.0, lfo_bins=0, noise=0.0, noise_tilt=-3.0,
                 noise_low=0.5, seed=7, top_hz=BAND_LIMIT_HZ, note_high=None):
        self.partials = list(partials)
        self.frames = int(frames)
        self.cycles = int(cycles)
        self.phases = phases
        self.voices = list(voices) if voices else [(0, 1.0)]
        self.am = float(am)
        self.pm = float(pm)
        self.lfo_bins = int(lfo_bins)
        self.noise = float(noise)
        self.noise_tilt = float(noise_tilt)
        self.noise_low = float(noise_low)
        self.seed = int(seed)
        self.top_hz = float(top_hz)
        #: hertz of the highest note this loop will be played at
        self.note_high = note_high

    def key(self):
        return (tuple((round(r, 6), round(a, 6)) for r, a in self.partials),
                self.frames, self.cycles, self.phases,
                tuple((o, round(a, 5)) for o, a in self.voices),
                round(self.am, 5), round(self.pm, 4), self.lfo_bins,
                round(self.noise, 5), round(self.noise_tilt, 3),
                round(self.noise_low, 3), self.seed, round(self.top_hz),
                None if self.note_high is None else round(self.note_high, 3))


def _line_limit(spec: LoopSpec) -> int:
    """The highest bin a line may sit on: the player's band limit for the
    highest note, and no more than frames/MIN_FRAMES_PER_CYCLE."""
    n = spec.frames
    cap = n // MIN_FRAMES_PER_CYCLE
    if spec.note_high:
        # ratio r sounds at r * note_high; bin = r * cycles
        r_max = spec.top_hz / spec.note_high
        cap = min(cap, int(r_max * spec.cycles))
    return max(1, min(cap, n // 2 - 1))


def make_loop(spec: LoopSpec):
    """-> list of `frames` floats, peak-normalised to 1.0."""
    return dsp.normalised(loop_signal(spec), 1.0)


def loop_signal(spec: LoopSpec):
    """The loop before it is normalised, so that two loops of the same
    recipe (a note and its brighter transient) can share one scale."""
    n = spec.frames
    if n & (n - 1):
        raise ValueError(f"loop length {n} is not a power of two")
    rng = random.Random(spec.seed)
    half = n // 2
    bins = [0j] * (half + 1)
    limit = _line_limit(spec)
    scale = n / 2.0
    loudest = max((a for _, a in spec.partials), default=1.0) or 1.0
    modulated = (spec.am > 0.0 or spec.pm > 0.0) and spec.lfo_bins > 0
    lfo = spec.lfo_bins

    for voice_index, (offset, v_amp) in enumerate(spec.voices):
        cycles_v = spec.cycles + offset
        for ratio, amp in spec.partials:
            b = int(round(ratio * cycles_v))
            if b < 1 or b > limit:
                continue
            a = amp * v_amp
            if spec.phases == "random":
                phase = rng.random() * 2.0 * math.pi
            else:
                # sine phase: sin(x) = cos(x - pi/2); voices are offset so
                # that they are not in step (the beat would start from a
                # peak and the loop would open on a click)
                phase = -math.pi / 2.0 + (0.7 * voice_index)
            if not modulated:
                bins[b] += a * scale * complex(math.cos(phase), math.sin(phase))
                continue
            # sidebands of tremolo and vibrato about this line
            beta = 0.0
            if spec.pm > 0.0:
                # peak phase deviation of this line, radians: the relative
                # frequency swing (cents -> ratio) times the line's rate over
                # the modulation's
                swing = spec.pm * math.log(2.0) / 1200.0
                beta = swing * (b / float(lfo))
            jmax = min(14, int(beta + 3.5) + 1) if beta else 1
            table = bessel_table(beta, jmax + 1) if beta else None
            for j in range(-jmax - 1, jmax + 2):
                bj = b + j * lfo
                if bj < 1 or bj > limit:
                    continue
                gain = _bessel(j, table, jmax)
                if spec.am > 0.0:
                    # (1 + am cos(w t)) x the phase-modulated line: the
                    # carrier's neighbours in the sideband series leak in
                    gain += 0.5 * spec.am * (_bessel(j - 1, table, jmax)
                                             + _bessel(j + 1, table, jmax))
                if gain == 0.0:
                    continue
                bins[bj] += a * gain * scale * complex(
                    math.cos(phase + j * 0.5), math.sin(phase + j * 0.5))

    if spec.noise > 0.0:
        _add_noise(bins, spec, rng, limit, loudest * scale)

    return dsp.irfft(bins, n)


def _bessel(j: int, table, jmax: int) -> float:
    """Sideband j of a phase-modulated line from its Bessel table (1 for the
    carrier when the modulation index is zero, `table` None), nothing past
    `jmax`."""
    if abs(j) > jmax + 1:
        return 0.0
    if table is None:
        return 1.0 if j == 0 else 0.0
    value = table[abs(j)]
    return -value if j < 0 and j % 2 else value


def _add_noise(bins, spec, rng, limit, level):
    """Random-phase lines in every bin from `noise_low` x the fundamental up
    to the limit, tilted in dB per octave about the fundamental. Each noise
    line is small, so `level` is spread over the lines it replaces: the
    noise reads as `spec.noise` times a partial's amplitude *per analysis
    bin*, which is what the flatness measurement sees."""
    m = max(1, spec.cycles)
    low = max(1, int(spec.noise_low * m))
    tilt = spec.noise_tilt / 6.0206          # per doubling, as an exponent
    for b in range(low, limit + 1):
        a = spec.noise * level * 0.5 * (b / float(m)) ** tilt
        phase = rng.random() * 2.0 * math.pi
        bins[b] += a * complex(math.cos(phase), math.sin(phase))


# -- turning a loop into a sample ---------------------------------------------
class Built:
    """What a family hands the module builder for one band of notes.

    `right`, when given, is the right channel of a stereo sample, as long as
    `data` (which is then the left), in the same loop."""

    def __init__(self, data, c5speed, loop=None, name="", right=None):
        self.data = data            # array('h')
        self.c5speed = int(c5speed)
        self.loop = loop            # (start, end) or None
        self.name = name
        self.right = right          # array('h') or None

    @property
    def channels(self) -> int:
        return 1 if self.right is None else 2


def intro_samples(loop, count: int, ramp: int, boost=None, boost_decay: int = 0):
    """The `count` frames that play once, before the loop begins.

    They are the end of the loop's own period (so the loop starts exactly
    where they stop), faded in over `ramp` frames. `boost`, if given, is a
    second signal of the same length as the loop — the same note with more
    top end — and is mixed in at full strength at the start and not at all
    after `boost_decay` frames: the transient of a note, which is how a
    pluck, a blip or a hammered key is brighter for its first milliseconds.
    """
    n = len(loop)
    start = n - count
    out = [loop[start + i] for i in range(count)]
    if boost is not None and boost_decay > 0:
        for i in range(min(count, boost_decay)):
            g = 1.0 - i / float(boost_decay)
            g *= g
            out[i] += g * (boost[start + i] - loop[start + i])
    for i in range(min(ramp, count)):
        out[i] *= i / float(ramp)
    return out


def to_int16(x, peak: float = 0.9):
    top = max((abs(v) for v in x), default=0.0)
    scale = 0.0 if top == 0 else peak * 32767.0 / top
    return array("h", (int(round(v * scale)) for v in x))


def tuned_c5speed(frames: int, cycles: int) -> int:
    """c5speed at which a loop of `frames` holding `cycles` cycles of the
    fundamental sounds C-5 when C-5 is played."""
    return int(round(C5_HZ * frames / float(cycles)))
