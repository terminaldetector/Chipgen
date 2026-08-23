"""analysis.py — measurement primitives for everything that inspects a render.

Nothing here knows about music. It turns a buffer into numbers: fundamental
frequency, envelope times, where the energy sits, how wide the stereo is.
audition.py and channels.py build the musical reports on top.

Two decisions worth stating, because both were arrived at by getting them
wrong first:

**Octave bands, not linear bands.** A "0-120 / 120-1k / 1k-6k / 6k+" split
looks reasonable and is useless: the top band spans 16 kHz and the bottom
spans 100 Hz, so every instrument ever measured comes out "top-heavy" and
comparing two of them tells you nothing. Bands here are octaves, which is
how pitch actually works and how the numbers stay comparable.

**Autocorrelation for pitch, refined at a high lag.** An FFT bin at 44100
Hz over 16384 samples is 2.7 Hz wide. At C2 that is 70 cents — useless for
checking whether a note landed where it was asked to. Autocorrelation
finds the period directly, and taking the peak at the highest usable
multiple of that period divides the error by that multiple. Measured
against generated sines, this lands inside 1 cent from C2 to C6.
"""

import cmath
import math

import audio


# --------------------------------------------------------------------------
# Buffer handling — mirrors audio.py's two worlds (numpy array / Buffer)
# --------------------------------------------------------------------------
def to_mono(buf):
    """Interleaved stereo buffer -> flat list of mono samples."""
    if not len(buf):
        return []
    if audio.HAVE_NUMPY and not audio.is_fallback(buf):
        import numpy as np
        arr = np.asarray(buf, dtype=np.float64)
        return (arr.mean(axis=1) if arr.ndim > 1 else arr)
    data, channels = buf.data, buf.channels
    if channels == 1:
        return list(data)
    return [sum(data[i:i + channels]) / channels
            for i in range(0, len(data), channels)]


def split_stereo(buf):
    """-> (left, right). Mono buffers come back as the same data twice."""
    if not len(buf):
        return [], []
    if audio.HAVE_NUMPY and not audio.is_fallback(buf):
        import numpy as np
        arr = np.asarray(buf, dtype=np.float64)
        if arr.ndim == 1:
            return arr, arr
        return arr[:, 0], arr[:, -1]
    data, channels = buf.data, buf.channels
    if channels == 1:
        return list(data), list(data)
    return list(data[0::channels]), list(data[channels - 1::channels])


# --------------------------------------------------------------------------
# Spectrum
# --------------------------------------------------------------------------
def _next_pow2(n: int) -> int:
    p = 1
    while p < n:
        p <<= 1
    return p


def _fft_pure(values):
    """Iterative radix-2 FFT. Only reached when numpy is absent."""
    n = len(values)
    out = [complex(v) for v in values]
    # bit-reversal permutation
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            out[i], out[j] = out[j], out[i]
    length = 2
    while length <= n:
        step = cmath.exp(-2j * math.pi / length)
        for start in range(0, n, length):
            w = 1 + 0j
            half = length >> 1
            for k in range(start, start + half):
                a, b = out[k], out[k + half] * w
                out[k], out[k + half] = a + b, a - b
                w *= step
        length <<= 1
    return out


def _hann(n: int):
    if n < 2:
        return [1.0] * n
    return [0.5 - 0.5 * math.cos(2.0 * math.pi * i / (n - 1)) for i in range(n)]


def spectrum(mono, rate: float, size: int = 16384):
    """-> (frequencies, magnitudes) for the loudest `size`-sample window.

    Windowed with a Hann and taken where the signal actually is, rather
    than at sample zero: a percussive patch is mostly silence and a
    spectrum of its tail is a spectrum of nothing.
    """
    n = len(mono)
    if n == 0:
        return [], []
    size = min(_next_pow2(min(size, n)), _next_pow2(n))
    start = _loudest_window_start(mono, size)
    frame = list(mono[start:start + size])
    if len(frame) < size:
        frame += [0.0] * (size - len(frame))

    if audio.HAVE_NUMPY:
        import numpy as np
        windowed = np.asarray(frame, dtype=np.float64) * np.hanning(size)
        mags = np.abs(np.fft.rfft(windowed))
        freqs = np.fft.rfftfreq(size, 1.0 / rate)
        return freqs, mags
    window = _hann(size)
    spec = _fft_pure([frame[i] * window[i] for i in range(size)])
    half = size // 2 + 1
    mags = [abs(spec[i]) for i in range(half)]
    freqs = [i * rate / size for i in range(half)]
    return freqs, mags


def _loudest_window_start(mono, size: int) -> int:
    n = len(mono)
    if n <= size:
        return 0
    step = max(1, size // 4)
    best, best_energy = 0, -1.0
    for start in range(0, n - size + 1, step):
        chunk = mono[start:start + size:max(1, size // 512)]
        energy = sum(float(v) * float(v) for v in chunk)
        if energy > best_energy:
            best, best_energy = start, energy
    return best


# --------------------------------------------------------------------------
# Pitch
# --------------------------------------------------------------------------
#: Search range for the fundamental.
#:
#: The ceiling is 2200 Hz and that is a deliberate limit, not an oversight.
#: Autocorrelation needs roughly 20 samples per period to resolve pitch to
#: within a cent; at 44100 Hz that runs out at about 2205 Hz. Measured, a
#: 4186 Hz sine comes back exactly an octave flat because at 10.5 samples
#: per period the peak at 2T is indistinguishable from the one at T.
#: 2200 Hz covers C7, which is the top of this hardware's musical range —
#: the PSG loses semitone resolution above C6 and FM leads rarely go past
#: C6 either. A signal whose true fundamental is ABOVE the ceiling does not
#: return None: it locks onto a subharmonic inside the range (4186 Hz
#: measures 2093). Nothing on this chip plays there, but do not point this
#: at arbitrary audio and trust the number.
#:
#: Accuracy inside the range, measured against generated tones across five
#: timbres from C1 to C7: 74 of 75 cases within 1 cent, worst 1.24 cents on
#: a bare 49 Hz sine where half a second holds only 24 periods.
MIN_F0, MAX_F0 = 30.0, 2200.0


def fundamental(mono, rate: float, minimum: float = MIN_F0,
                maximum: float = MAX_F0):
    """Measured fundamental in Hz, or None if the signal is not pitched.

    Autocorrelation via FFT, peak-picked in the plausible lag range, then
    refined by locating the same peak at the highest multiple of the
    period that still fits in the analysed window — which divides the
    period error by that multiple.
    """
    n = len(mono)
    if n < 1024:
        return None
    size = min(_next_pow2(n), 65536)
    start = _loudest_window_start(mono, min(size, n))
    frame = list(mono[start:start + size])
    if len(frame) < size:
        frame += [0.0] * (size - len(frame))
    mean = sum(frame) / size
    frame = [v - mean for v in frame]
    energy = sum(v * v for v in frame)
    if energy <= 0.0:
        return None

    corr = _autocorrelation(frame, size)
    min_lag = max(2, int(rate / maximum))
    max_lag = min(size // 2, int(rate / minimum))
    if max_lag <= min_lag:
        return None

    # Start looking only after the correlation has first fallen away.
    # Straight peak-picking from lag 0 finds the fundamental for a spiky
    # waveform and fails outright for a near-sine: at 65 Hz a lag of 8
    # samples is 4 degrees of phase, so corr[8] is 0.997*corr[0] — higher
    # than the true peak at lag 674 once zero-padding has tapered it. The
    # measured symptom was a 65 Hz sine reading 5520 Hz.
    first_dip = 1
    while first_dip < max_lag and corr[first_dip] > 0.0:
        first_dip += 1
    # From lag 1, not from min_lag: at 4186 Hz the period is 10.5 samples
    # and min_lag is already 8, so starting the dip search there walks
    # straight past the trough and lands in the NEXT period. Measured as
    # a clean 1200-cent error.
    first_dip = max(min_lag, min(first_dip, max_lag - 1))

    best_value = 0.0
    for lag in range(first_dip, max_lag):
        if corr[lag] > best_value:
            best_value = corr[lag]
    # A pitched signal correlates with itself. Noise does not.
    if best_value < 0.2 * corr[0]:
        return None

    # The FIRST lag that reaches nearly the best correlation, not the
    # highest one. Autocorrelation peaks at every multiple of the period,
    # and once the zero-padding taper is normalised away those peaks are
    # the same height, so a global max picks an arbitrary multiple: a
    # 4186 Hz sine measured exactly one octave flat because lag 2T won.
    peak_lag = 0
    for lag in range(first_dip, max_lag - 1):
        if corr[lag] >= 0.9 * best_value and corr[lag] >= corr[lag + 1] \
                and corr[lag] > corr[lag - 1]:
            peak_lag = lag
            break
    if peak_lag == 0:
        return None

    period = _refine_period(corr, peak_lag, max_lag)
    return rate / period if period > 0 else None


def _autocorrelation(frame, size: int):
    if audio.HAVE_NUMPY:
        import numpy as np
        arr = np.asarray(frame, dtype=np.float64)
        padded = np.zeros(size * 2)
        padded[:size] = arr
        spec = np.fft.rfft(padded)
        raw = np.fft.irfft(spec * np.conjugate(spec))[:size]
        # Zero-padded correlation tapers linearly with lag simply because
        # fewer samples overlap. Divide that out, or every comparison
        # between a short lag and a long one is biased toward the short one.
        return raw / np.arange(size, 0, -1)
    padded = frame + [0.0] * size
    spec = _fft_pure(padded)
    power = [s * s.conjugate() for s in spec]
    back = _fft_pure([p.conjugate() for p in power])
    return [(back[i].conjugate() / (size * 2)).real / (size - i)
            for i in range(size)]


def _refine_period(corr, peak_lag: int, max_lag: int) -> float:
    """Lock the period onto the highest usable multiple, doubling as it goes.

    One period measured to +/-0.1 samples at C5 is about 2 cents. The same
    0.1 samples spread over eight periods is 0.25 cents, and the peaks are
    there for free — autocorrelation of a periodic signal peaks at every
    multiple of the period.

    Doubling rather than jumping straight to the largest multiple, because
    the starting estimate is an integer lag and its error multiplies too.
    At 1397 Hz the period is 31.57 samples; jumping to 47x searches around
    31*47 = 1457 when the peak is at 1484, misses it, and locks onto the
    wrong one. Measured as a consistent 33-cent error. Doubling keeps the
    estimate refined at every step, so the search window stays valid.
    """
    period = peak_lag + _parabolic(corr, peak_lag)
    if period <= 0:
        return peak_lag
    multiple = 2
    while True:
        centre = period * multiple
        if centre > max_lag - 2:
            break
        window = max(2, int(period * 0.4))
        lo = max(1, int(centre) - window)
        hi = min(max_lag - 2, int(centre) + window)
        if hi <= lo:
            break
        local = max(range(lo, hi + 1), key=lambda i: corr[i])
        if corr[local] < 0.3 * corr[0]:
            break
        period = (local + _parabolic(corr, local)) / multiple
        multiple *= 2
    return period


def _parabolic(values, i: int) -> float:
    """Sub-sample offset of the true peak near index i."""
    if i <= 0 or i >= len(values) - 1:
        return 0.0
    a, b, c = float(values[i - 1]), float(values[i]), float(values[i + 1])
    denom = a - 2.0 * b + c
    if denom == 0.0:
        return 0.0
    offset = 0.5 * (a - c) / denom
    return offset if -1.0 < offset < 1.0 else 0.0


def cents(measured: float, reference: float) -> float:
    """Signed interval between two frequencies, in cents."""
    if not measured or not reference or measured <= 0 or reference <= 0:
        return float("nan")
    return 1200.0 * math.log2(measured / reference)


# --------------------------------------------------------------------------
# Envelope
# --------------------------------------------------------------------------
def envelope(mono, rate: float, window_ms: float = 5.0):
    """Amplitude envelope: peak absolute value per short window.

    Peak rather than RMS per window because attack times are read off the
    leading edge, and RMS smears a 2 ms attack across the whole window.
    """
    width = max(1, int(rate * window_ms / 1000.0))
    out = []
    for start in range(0, len(mono), width):
        chunk = mono[start:start + width]
        out.append(max(abs(float(v)) for v in chunk) if len(chunk) else 0.0)
    return out, width / rate


def attack_seconds(mono, rate: float, threshold: float = 0.9) -> float:
    """Time from the first non-silence to `threshold` of the peak.

    Measured on a 1 ms envelope rather than the 5 ms default: attack rate
    31 on this chip is effectively instantaneous and plenty of useful
    patches land between 1 and 10 ms, which a 5 ms window rounds to either
    0 or 5 and tells you nothing.
    """
    env, step = envelope(mono, rate, window_ms=1.0)
    if not env:
        return 0.0
    top = max(env)
    if top <= 0.0:
        return 0.0
    floor = top * 0.02
    first = next((i for i, v in enumerate(env) if v > floor), 0)
    for i in range(first, len(env)):
        if env[i] >= top * threshold:
            return (i - first) * step
    return (len(env) - first) * step


def decay_seconds(mono, rate: float, drop_db: float = 20.0) -> float:
    """Time from the peak down to `drop_db` below it. Never reached -> None."""
    env, step = envelope(mono, rate)
    if not env:
        return None
    top = max(env)
    if top <= 0.0:
        return None
    target = top * (10.0 ** (-drop_db / 20.0))
    peak_at = env.index(top)
    for i in range(peak_at, len(env)):
        if env[i] <= target:
            return (i - peak_at) * step
    return None


# --------------------------------------------------------------------------
# Where the energy sits
# --------------------------------------------------------------------------
#: Octave bands from 31.25 Hz up. Named by their centre, split at the
#: geometric boundaries, which is how an equaliser is laid out and how two
#: instruments stay comparable.
BAND_EDGES = (0.0, 44.2, 88.4, 176.8, 353.6, 707.1, 1414.2, 2828.4,
              5656.9, 11313.7, float("inf"))
BAND_NAMES = ("sub", "31", "62", "125", "250", "500", "1k", "2k", "4k",
              "8k+")


def band_energy(mono, rate: float):
    """-> {band name: fraction of total energy}. Octave bands, summing to 1."""
    freqs, mags = spectrum(mono, rate)
    if not len(freqs):
        return {name: 0.0 for name in BAND_NAMES}
    totals = [0.0] * len(BAND_NAMES)
    for f, m in zip(freqs, mags):
        power = float(m) * float(m)
        for i in range(len(BAND_NAMES)):
            if BAND_EDGES[i] <= f < BAND_EDGES[i + 1]:
                totals[i] += power
                break
    grand = sum(totals)
    if grand <= 0.0:
        return {name: 0.0 for name in BAND_NAMES}
    return {name: totals[i] / grand for i, name in enumerate(BAND_NAMES)}


#: Bins this far below the loudest one are treated as floor, not content.
CENTROID_FLOOR_DB = -60.0


def spectral_centroid(mono, rate: float,
                      floor_db: float = CENTROID_FLOOR_DB) -> float:
    """Energy-weighted mean frequency — the usual one-number 'brightness'.

    Weighted by POWER and gated at a floor, both of which matter more than
    they sound. An FFT has thousands of bins and almost all of them hold
    nothing but the noise floor; weighting by amplitude and summing them
    all lets that floor outvote the actual harmonics by sheer count.
    Measured on deep_bass at C2, whose harmonics all sit below 400 Hz, the
    ungated amplitude-weighted centroid read 1910 Hz.
    """
    freqs, mags = spectrum(mono, rate)
    if not len(freqs):
        return 0.0
    loudest = max((float(m) for m in mags), default=0.0)
    if loudest <= 0.0:
        return 0.0
    threshold = loudest * (10.0 ** (floor_db / 20.0))
    weight = 0.0
    moment = 0.0
    for f, m in zip(freqs, mags):
        m = float(m)
        if m < threshold:
            continue
        power = m * m
        weight += power
        moment += float(f) * power
    return moment / weight if weight > 0.0 else 0.0


def harmonic_count(mono, rate: float, f0: float = None,
                   floor_db: float = -40.0) -> int:
    """How many harmonics of the fundamental clear `floor_db` below the loudest.

    A count of the timbre's complexity: a sine reads 1, a square-ish FM
    lead reads a dozen or more.
    """
    if f0 is None:
        f0 = fundamental(mono, rate)
    if not f0:
        return 0
    freqs, mags = spectrum(mono, rate)
    if not len(freqs):
        return 0
    resolution = float(freqs[1]) - float(freqs[0]) if len(freqs) > 1 else 1.0
    loudest = max(float(m) for m in mags) or 1.0
    threshold = loudest * (10.0 ** (floor_db / 20.0))
    count = 0
    for h in range(1, 65):
        target = f0 * h
        if target >= rate / 2.0:
            break
        index = int(round(target / resolution))
        lo, hi = max(0, index - 2), min(len(mags), index + 3)
        if lo < hi and max(float(m) for m in mags[lo:hi]) >= threshold:
            count += 1
    return count


# --------------------------------------------------------------------------
# Level and stereo
# --------------------------------------------------------------------------
def dbfs(value: float) -> float:
    return float("-inf") if value <= 0.0 else 20.0 * math.log10(value)


def crest_factor_db(buf) -> float:
    """Peak over RMS. Low means squashed, high means transient."""
    r = audio.rms(buf)
    return float("inf") if r <= 0.0 else dbfs(audio.peak(buf)) - dbfs(r)


def stereo_width(buf) -> float:
    """0.0 = identical channels (mono), 1.0 = uncorrelated, 2.0 = inverted.

    Derived from the correlation between the two channels: width = 1 - r.
    A mix that never touches pan reads 0.0, which is the mono collapse
    that no level meter shows.
    """
    left, right = split_stereo(buf)
    n = min(len(left), len(right))
    if n == 0:
        return 0.0
    if audio.HAVE_NUMPY and not audio.is_fallback(buf):
        import numpy as np
        a, b = np.asarray(left[:n]), np.asarray(right[:n])
        sa, sb = float(a.std()), float(b.std())
        if sa <= 1e-12 or sb <= 1e-12:
            return 0.0
        return 1.0 - float(np.mean((a - a.mean()) * (b - b.mean())) / (sa * sb))
    ma = sum(left[:n]) / n
    mb = sum(right[:n]) / n
    va = sum((v - ma) ** 2 for v in left[:n]) / n
    vb = sum((v - mb) ** 2 for v in right[:n]) / n
    if va <= 1e-24 or vb <= 1e-24:
        return 0.0
    cov = sum((left[i] - ma) * (right[i] - mb) for i in range(n)) / n
    return 1.0 - cov / math.sqrt(va * vb)
