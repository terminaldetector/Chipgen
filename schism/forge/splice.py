"""splice.py — sound cut and joined in the time domain.

Everything the families make is a *recipe*: partials, modes, layers of
noise. A tracker instrument is often a different animal: the first thirty
milliseconds of one sound over the body of another, a low sine under a
bass, a chord kept as one key, the same note twice a few cents apart for a
stereo sample, a cymbal played backwards. That is editing PCM, and this is
the editor: plain Python, one channel at a time, in floats.

Time is counted in the *frames of a sample*, and a sample's frames last as
long as its note makes them: `native_rate` says how many frames a second
that is for a `Built` at a key. Every length a role names ("30 ms") is turned
into frames at that rate, so 30 ms is 30 ms whatever the sample's own rate.

What is here

    floats / pack       int16 <-> floats, and one scale for a stereo pair
    native_rate         frames a second a Built plays at, at a key
    convert             another rate, band-limited, loop-aware
    extend_intro        a loop given a longer front, by whole repeats of itself
    unroll              a loop played out as a one-shot
    chord_sum           notes of one sample, summed
    double              the same sample twice, a few cents apart
    reverse             the sample backwards
    finish              chord, stereo and reverse on a finished Built
    level_ratio         how much gain an edited sample needs to be as loud
"""

import math
from array import array

from . import dsp, spectral

#: a sample is stored at this share of full scale (`spectral.to_int16`)
PEAK = 0.92
#: and never louder than this once it has been edited
CEILING = 0.98
RATE = 44100
#: a stack is built at no fewer frames a second than this, so that the
#: bright top of a click survives being put into a slow sample
MIN_RATE = 40000.0
#: how much of a loop a one-shot made from it holds, seconds
UNROLL_SECONDS = 1.5
#: the window a sample's loudness is read over (as `probe.LEVEL_WINDOW`)
LEVEL_WINDOW = 0.12

CHORDS = {
    "maj": (0, 4, 7), "min": (0, 3, 7), "dim": (0, 3, 6), "aug": (0, 4, 8),
    "sus2": (0, 2, 7), "sus4": (0, 5, 7), "5": (0, 7, 12), "power": (0, 7, 12),
    "6": (0, 4, 7, 9), "min6": (0, 3, 7, 9),
    "7": (0, 4, 7, 10), "maj7": (0, 4, 7, 11), "min7": (0, 3, 7, 10),
    "dim7": (0, 3, 6, 9), "min7b5": (0, 3, 6, 10), "7sus4": (0, 5, 7, 10),
    "9": (0, 4, 7, 10, 14), "maj9": (0, 4, 7, 11, 14),
    "min9": (0, 3, 7, 10, 14), "add9": (0, 4, 7, 14), "min11": (0, 3, 7, 10, 17),
    "13": (0, 4, 7, 10, 14, 21),
}


class SpliceError(ValueError):
    """An edit that cannot be made, with the reason."""


# -- reading and writing samples ------------------------------------------------
def floats(data):
    return [v / 32768.0 for v in data]


def pack(channels, scale: float = 1.0):
    """Floats (-1..1) -> array('h') for each channel, one scale for all."""
    out = []
    k = 32767.0 * scale
    for x in channels:
        out.append(array("h", (max(-32768, min(32767, int(round(v * k))))
                               for v in x)))
    return out


def peak(*channels) -> float:
    return max((max((abs(v) for v in x), default=0.0) for x in channels),
               default=0.0)


def native_rate(built, note: int, pitched: bool) -> float:
    """Frames a second the sample plays at when `note` is pressed. A pitched
    sample follows the key; a drum or an effect is recorded at 44.1 kHz and
    that is the rate it means (`c5speed` only says which key plays it as
    recorded)."""
    if not pitched:
        return float(RATE)
    return built.c5speed * 2.0 ** ((note - 61) / 12.0)


def band_limit(low: int, high: int, note: int = None,
               pitched: bool = True) -> float:
    """Hz, at the base note, above which a sample for `low`..`high` would
    alias when it plays the highest key: the player's limit over the
    transposition of that key. A drum or an effect is made once for the
    whole keyboard and played at its register; it has no band, only the
    player's limit."""
    if not pitched:
        return spectral.BAND_LIMIT_HZ
    base = (low + high) // 2 if note is None else note
    return spectral.BAND_LIMIT_HZ / 2.0 ** ((high - base) / 12.0)


def level(x, rate: float, seconds: float = LEVEL_WINDOW) -> float:
    return dsp.window_rms(x, rate, seconds)


# -- filters ----------------------------------------------------------------------
def lowpass(x, hz: float, rate: float):
    """Fourth order (two sections), -6 dB at `hz`."""
    if hz >= 0.49 * rate:
        return list(x)
    return dsp.filtered(dsp.filtered(x, "lp", hz, rate), "lp", hz, rate)


def highpass(x, hz: float, rate: float):
    if hz <= 0.0:
        return list(x)
    return dsp.filtered(dsp.filtered(x, "hp", hz, rate), "hp", hz, rate)


def fade_in(x, frames: int):
    n = min(len(x), max(0, int(frames)))
    for i in range(n):
        x[i] *= 0.5 - 0.5 * math.cos(math.pi * (i + 0.5) / n)
    return x


def fade_out(x, frames: int):
    n = min(len(x), max(0, int(frames)))
    for i in range(n):
        x[len(x) - n + i] *= 0.5 + 0.5 * math.cos(math.pi * (i + 0.5) / n)
    return x


# -- resampling -----------------------------------------------------------------
def resample(x, ratio: float, wrap=None):
    """`x` read at `1 / ratio` frames per step, so that the result has
    `ratio` times as many frames: cubic (Catmull-Rom) interpolation.
    `wrap`, when given, is the frame a periodic tail repeats from: past the
    end of `x` the signal goes on from there, so a loop stays a loop."""
    n = len(x)
    if n == 0 or ratio <= 0.0:
        return []
    if abs(ratio - 1.0) < 1e-12:
        return list(x)
    m = int(round(n * ratio))
    if wrap is None:
        padded = [0.0] + list(x) + [0.0, 0.0]
    else:
        # past the end the signal goes on from frame `wrap`; before the
        # first frame it is the end of the period if the whole sample loops
        # and silence if it has a front
        padded = [x[n - 1] if wrap == 0 else 0.0] + list(x) \
            + [x[wrap], x[wrap + 1] if wrap + 1 < n else x[wrap]]
    step = 1.0 / ratio
    out = [0.0] * m
    for i in range(m):
        pos = i * step
        k = int(pos)
        f = pos - k
        a, b, c, d = padded[k], padded[k + 1], padded[k + 2], padded[k + 3]
        out[i] = b + 0.5 * f * (c - a + f * (2.0 * a - 5.0 * b + 4.0 * c - d
                                             + f * (3.0 * (b - c) + d - a)))
    return out


def convert(x, src_rate: float, dst_rate: float, limit_hz: float = None):
    """A one-shot at `src_rate` frames a second -> the same sound at
    `dst_rate`, band-limited: below the new Nyquist when it goes down, below
    `limit_hz` always (the images a cubic leaves above the old Nyquist, when
    it goes up)."""
    if src_rate <= 0 or dst_rate <= 0:
        raise SpliceError("a rate must be positive")
    top = limit_hz if limit_hz else 1e9
    if dst_rate < src_rate:
        y = lowpass(x, min(top, 0.45 * dst_rate), src_rate)
        return resample(y, dst_rate / src_rate)
    y = resample(x, dst_rate / src_rate)
    return lowpass(y, min(top, 0.45 * src_rate), dst_rate)


# -- loops ----------------------------------------------------------------------
def extend_intro(channels, loop, need: int):
    """The part of a looped sample that plays once, `x[:start]`, made at
    least `need` frames long by repeating whole periods of the loop in
    front of it. The sound does not change: the loop, repeated, is the loop.
    -> (channels, loop). Channels are lists of floats of the same length."""
    if loop is None:
        return channels, None
    start, end = loop
    period = end - start
    if period <= 0:
        raise SpliceError("a loop has no length")
    if start >= need:
        return [list(x[:end]) for x in channels], (start, end)
    k = -(-(need - start) // period)
    out = []
    for x in channels:
        body = x[start:end]
        out.append(list(x[:start]) + body * (k + 1))
    return out, (start + k * period, end + k * period)


def unroll(channels, loop, frames: int):
    """A looped sample as a one-shot of `frames` frames: the front, then the
    loop played over and over. Not looped: cut or padded with silence."""
    out = []
    for x in channels:
        if loop is None:
            y = list(x[:frames]) + [0.0] * max(0, frames - len(x))
        else:
            start, end = loop
            y = list(x[:start])
            period = x[start:end]
            while len(y) < frames:
                y.extend(period)
            y = y[:frames]
        out.append(y)
    return out


# -- the three edits --------------------------------------------------------------
def parse_chord(text) -> list:
    """'min9', '0,3,7,10,14', or a list -> semitones above the key."""
    if isinstance(text, (list, tuple)):
        notes = [int(n) for n in text]
    else:
        word = str(text).strip().lower()
        if word in CHORDS:
            notes = list(CHORDS[word])
        else:
            try:
                notes = [int(p) for p in word.replace(" ", "").split(",") if p]
            except ValueError:
                raise SpliceError(
                    f"chord={text!r} is not a chord name or a list of "
                    f"semitones; the names are "
                    f"{', '.join(sorted(CHORDS))}") from None
    if len(notes) < 2:
        raise SpliceError("a chord has at least two notes")
    if len(notes) > 8:
        raise SpliceError("a chord holds at most eight notes")
    if any(abs(n) > 36 for n in notes):
        raise SpliceError("a chord note is more than three octaves from the key")
    return sorted(set(notes))


def chord_notes(spec) -> list:
    """A chord setting -> the semitones above the key it sounds: the
    intervals of the chord, moved by `root` (where the chord's root sits
    against the key), the way a tracker player plays C-5 and hears Am9."""
    if isinstance(spec, dict):
        notes = parse_chord(spec["notes"])
        root = int(spec.get("root", 0))
    else:
        notes, root = parse_chord(spec), 0
    return sorted({n + root for n in notes})


def chord_sum(x, notes, rate: float, limit_hz: float):
    """Each of `notes` (semitones above the key) is the sample read faster
    or slower, and they are summed, each at 1/sqrt(K) so that K notes are
    about as loud as one. A read faster than the sample's own is band-limited
    first, so that nothing folds."""
    if not x:
        return []
    k = len(notes)
    voices = []
    for n in notes:
        r = 2.0 ** (n / 12.0)
        src = x
        if r > 1.0:
            src = lowpass(x, min(limit_hz, 0.45 * rate) / r, rate)
        voices.append(resample(src, 1.0 / r))
    length = max(len(v) for v in voices)
    out = [0.0] * length
    weight = 1.0 / math.sqrt(k)
    for v in voices:
        for i, s in enumerate(v):
            out[i] += s * weight
    fade_out(out, min(length, int(0.02 * rate)))
    return lowpass(out, limit_hz, rate)


def double(x, cents: float):
    """-> (left, right): the sample a little flat and a little sharp,
    padded with silence to the same length. The pair is a stereo sample."""
    up = 2.0 ** (abs(cents) / 1200.0)
    left = resample(x, up)               # more frames, so it plays flat
    right = resample(x, 1.0 / up)        # fewer, sharp
    n = max(len(left), len(right))
    left += [0.0] * (n - len(left))
    right += [0.0] * (n - len(right))
    return left, right


def reverse(channels, rate: float):
    """The sample backwards, with a hair of fade at the end where the
    original began (it may have started loud)."""
    out = []
    for x in channels:
        y = x[::-1]
        fade_out(y, max(2, int(0.0005 * rate)))
        out.append(y)
    return out


# -- finishing a Built -----------------------------------------------------------
def finish(built, post: dict, low: int, high: int, pitched: bool):
    """Apply `chord`, `stereo` (cents) and `rev` to a finished sample, in
    that order. A looped sample is first played out as a one-shot (a chord
    of a loop cannot loop in the time domain, and a reversed loop has no
    end to start from). -> a new Built."""
    base = (low + high) // 2
    rate = native_rate(built, base, pitched)
    channels = [floats(built.data)]
    if built.right is not None:
        channels.append(floats(built.right))
    loop = built.loop
    limit = band_limit(low, high, base, pitched)
    steps = [k for k in ("chord", "stereo", "rev") if post.get(k)]
    if loop is not None and steps:
        seconds = UNROLL_SECONDS
        if isinstance(post.get("rev"), (int, float)) \
                and not isinstance(post.get("rev"), bool) and post["rev"] > 1:
            seconds = float(post["rev"]) / 1000.0
        channels = unroll(channels, loop, int(seconds * rate))
        for x in channels:
            fade_out(x, int(0.02 * rate))
        loop = None
    if post.get("chord"):
        notes = chord_notes(post["chord"])
        channels = [chord_sum(x, notes, rate, limit) for x in channels]
    if post.get("stereo"):
        if len(channels) == 2:
            raise SpliceError("the sample is already stereo")
        channels = list(double(channels[0], float(post["stereo"])))
    if post.get("rev"):
        channels = reverse(channels, rate)
    top = peak(*channels) or 1.0
    packed = pack(channels, min(1.0, PEAK / top))
    return spectral.Built(packed[0], built.c5speed, loop, built.name,
                          right=packed[1] if len(packed) == 2 else None)


def level_ratio(ref, made, pitched: bool, base: int) -> float:
    """How much louder `ref` is than `made`, over the loudest 120 ms of
    each (1.0 when they are as loud, 1.4 when `made` needs 1.4 times the
    gain to match). A chord is a sum of notes and a reversed cymbal is
    another shape of the same energy: the samples are stored at the same
    peak, so the difference in loudness is the instrument's gain to make
    up."""
    if not len(ref.data) or not len(made.data):
        return 1.0
    r_ref = level(floats(ref.data), native_rate(ref, base, pitched))
    r_made = level(floats(made.data), native_rate(made, base, pitched))
    if r_ref <= 0.0 or r_made <= 0.0:
        return 1.0
    return r_ref / r_made


# -- a stack of effects, for samples that loop and samples that do not ----------------------
def drive(x, amount: float):
    """A soft clip: tanh, scaled to keep the peak. 0 does nothing."""
    if amount <= 0.0:
        return list(x)
    top = max((abs(v) for v in x), default=0.0) or 1.0
    k = 1.0 / math.tanh(amount)
    return [math.tanh(amount * v / top) * k * top for v in x]


def cabinet(x, rate: float, low: float = 90.0):
    """A small speaker cabinet: nothing under `low` Hz (90), a lift at 2.5
    kHz, little over 6 kHz (two second-order sections each way and a peak)."""
    y = highpass(x, low, rate)
    y = lowpass(y, 6000.0, rate)
    peak_ = dsp.filtered(y, "bp", 2500.0, rate, 0.9)
    return [a + 0.6 * b for a, b in zip(y, peak_)]


def chorus(x, rate: float, period: int, depth_ms: float = 1.6,
           centre_ms: float = 7.0, mix: float = 0.5, start: int = 0):
    """The sound with a copy of itself behind it on a delay that swings
    `depth_ms` either side of `centre_ms` once every `period` frames, the
    swing's phase counted from frame `start`. Tuned so that a loop of
    `period` frames, a whole number of swings long, is still a loop."""
    n = len(x)
    centre = centre_ms * 0.001 * rate
    depth = depth_ms * 0.001 * rate
    out = [0.0] * n
    two_pi = 2.0 * math.pi
    for i in range(n):
        d = centre + depth * math.sin(two_pi * (i - start) / period)
        pos = i - d
        k = int(math.floor(pos))
        f = pos - k
        a = x[k] if 0 <= k < n else 0.0
        b = x[k + 1] if 0 <= k + 1 < n else 0.0
        out[i] = x[i] + mix * (a + f * (b - a))
    return out


def post_stack(x, post: dict, rate: float, period: int = 0, start: int = 0):
    """drive, then the cabinet, then the chorus, as `post` asks for them."""
    y = list(x)
    if post.get("limit"):
        y = lowpass(y, float(post["limit"]), rate)
    if post.get("drive"):
        y = drive(y, float(post["drive"]))
    if post.get("cabinet"):
        low = post["cabinet"] if isinstance(post["cabinet"], (int, float)) \
            and not isinstance(post["cabinet"], bool) else 90.0
        y = cabinet(y, rate, float(low))
    if post.get("chorus") and period:
        spec = post["chorus"]
        y = chorus(y, rate, period // max(1, int(spec.get("swings", 1))),
                   spec.get("depth_ms", 1.6), spec.get("centre_ms", 7.0),
                   spec.get("mix", 0.5), start)
    return y


def post_loop(head, loop, post: dict, rate: float):
    """The stack applied to a sound with a front and a loop, so that the
    loop is still a loop: the effects run over the front and the loop played
    over and over, until they have settled (a tenth of a second and a half),
    and the last time round is the loop."""
    n = len(loop)
    copies = max(2, -(-int(0.15 * rate) // n) + 1)
    stream = list(head) + list(loop) * copies
    out = post_stack(stream, post, rate, n, len(head))
    h = len(head)
    return out[:h], out[h + (copies - 1) * n:h + copies * n]


def crush(x, hz: float, bits: int, rate: float = RATE):
    """A DAC's sound: band-limited to under `hz`, held for each of its
    samples, quantised to `bits`."""
    if hz >= 0.5 * rate:
        held = list(x)
    else:
        y = lowpass(x, 0.45 * hz, rate)
        step = rate / float(hz)
        held = []
        acc = 0.0
        last = 0.0
        for i, v in enumerate(y):
            if i >= acc:
                last = v
                acc += step
            held.append(last)
    top = max((abs(v) for v in held), default=0.0) or 1.0
    levels = 2 ** (bits - 1) - 1
    return [round(v / top * levels) / levels * top for v in held]
