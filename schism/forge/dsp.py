"""dsp.py — the little signal processing the forge needs, in plain Python.

A real FFT, windows, and the octave-band split. The forge reads a few
hundred notes in a search and every one needs three or four spectra, so the
transform is the one place where plain Python has to be fast: it is a
Stockham autosort FFT written as whole-list operations (one list
comprehension per stage, no index arithmetic in the inner loop), and a real
input is folded into a half-size complex transform. A 16384-point
spectrum takes about 30 ms.

numpy is not used and not needed. Everything here is deterministic.
"""

import cmath
import math
import operator

_TWIDDLES = {}
_HANN = {}


def next_pow2(n: int) -> int:
    p = 1
    while p < n:
        p <<= 1
    return p


def hann(n: int):
    """Symmetric-in-the-period Hann window (periodic form, i / n), cached."""
    window = _HANN.get(n)
    if window is None:
        if n < 2:
            window = [1.0] * n
        else:
            window = [0.5 - 0.5 * math.cos(2.0 * math.pi * i / n)
                      for i in range(n)]
        if len(_HANN) > 24:
            _HANN.clear()
        _HANN[n] = window
    return window


def _stage_twiddles(n: int):
    """Per stage of the Stockham FFT: the list of w_j repeated m times."""
    cached = _TWIDDLES.get(n)
    if cached is not None:
        return cached
    stages = []
    l, m = n >> 1, 1
    while l >= 1:
        base = [cmath.exp(-2j * math.pi * j / (2 * l)) for j in range(l)]
        if m == 1:
            stages.append(base)
        else:
            stages.append([w for w in base for _ in range(m)])
        l >>= 1
        m <<= 1
    if len(_TWIDDLES) > 8:
        _TWIDDLES.clear()
    _TWIDDLES[n] = stages
    return stages


def fft(values):
    """Forward DFT of a list of complex numbers whose length is a power of
    two. -> list of complex."""
    n = len(values)
    if n & (n - 1):
        raise ValueError(f"fft length {n} is not a power of two")
    if n == 1:
        return [complex(values[0])]
    x = [complex(v) for v in values]
    stages = _stage_twiddles(n)
    half = n >> 1
    m = 1
    for tw in stages:
        a, b = x[:half], x[half:]
        sums = [p + q for p, q in zip(a, b)]
        diffs = [(p - q) * w for p, q, w in zip(a, b, tw)]
        y = [0j] * n
        if m == 1:
            y[0::2] = sums
            y[1::2] = diffs
        elif m == half:
            y = sums + diffs
        elif m <= 16:
            step = 2 * m
            for k in range(m):
                y[k::step] = sums[k::m]
                y[m + k::step] = diffs[k::m]
        else:
            for j in range(half // m):
                at, src = 2 * j * m, j * m
                y[at:at + m] = sums[src:src + m]
                y[at + m:at + 2 * m] = diffs[src:src + m]
        x = y
        m <<= 1
    return x


def rfft(values):
    """DFT bins 0..n/2 of a real signal (length a power of two, at least 4).
    -> list of complex, length n/2 + 1."""
    n = len(values)
    if n < 4 or n & (n - 1):
        raise ValueError(f"rfft length {n} must be a power of two, >= 4")
    h = n >> 1
    z = fft([complex(values[2 * k], values[2 * k + 1]) for k in range(h)])
    out = [0j] * (h + 1)
    out[0] = complex(z[0].real + z[0].imag, 0.0)
    out[h] = complex(z[0].real - z[0].imag, 0.0)
    w = _half_twiddles(n)
    for k in range(1, h):
        zk, zc = z[k], z[h - k].conjugate()
        even = 0.5 * (zk + zc)
        odd = -0.5j * (zk - zc)
        out[k] = even + w[k] * odd
    return out


_HALF = {}


def _half_twiddles(n: int):
    cached = _HALF.get(n)
    if cached is None:
        cached = [cmath.exp(-2j * math.pi * k / n) for k in range(n >> 1)]
        if len(_HALF) > 8:
            _HALF.clear()
        _HALF[n] = cached
    return cached


def spectrum(x, start: int, size: int, rate: float):
    """Magnitudes of a Hann-windowed frame of `size` samples (zero-padded
    past the end). -> (list of size/2 + 1 magnitudes, bin width in Hz)."""
    frame = x[start:start + size]
    if len(frame) < size:
        frame = list(frame) + [0.0] * (size - len(frame))
    window = hann(size)
    bins = rfft(list(map(operator.mul, frame, window)))
    # the factor 2/size makes a full-scale sine read about 0.5*window gain;
    # absolute scale only matters in ratios here
    return [abs(b) for b in bins], rate / size


def dot(a, b) -> float:
    return sum(map(operator.mul, a, b))


def rms(x) -> float:
    n = len(x)
    return math.sqrt(sum(map(operator.mul, x, x)) / n) if n else 0.0


def window_rms(mono, rate: float, seconds: float = 0.3) -> float:
    """Loudest `seconds` of a signal, as RMS."""
    n = len(mono)
    if n == 0:
        return 0.0
    width = min(n, max(1, int(seconds * rate)))
    energy = sum(v * v for v in mono[:width])
    best = energy
    for i in range(width, n):
        energy += mono[i] * mono[i] - mono[i - width] * mono[i - width]
        if energy > best:
            best = energy
    return math.sqrt(max(0.0, best) / width)


#: octave bands from 44 Hz, the way the arrangement model reads a note
BAND_EDGES = (0.0, 44.2, 88.4, 176.8, 353.6, 707.1, 1414.2, 2828.4,
              5656.9, 11313.7, 1e9)
BAND_NAMES = ("sub", "62", "125", "250", "500", "1k", "2k", "4k", "8k", "16k")


def band_energy(mono, rate: float, size: int = 16384):
    """-> {band name: share of the energy}; the shares sum to 1."""
    n = min(size, next_pow2(max(4, len(mono))))
    mags, binw = spectrum(mono, 0, n, rate)
    totals = [0.0] * len(BAND_NAMES)
    band = 0
    for i, a in enumerate(mags):
        f = i * binw
        while f >= BAND_EDGES[band + 1]:
            band += 1
        totals[band] += a * a
    grand = sum(totals) or 1.0
    return {name: totals[i] / grand for i, name in enumerate(BAND_NAMES)}


def irfft(bins, n: int):
    """The inverse of `rfft`: bins 0..n/2 of a real signal -> n samples.
    `n` is a power of two, at least 4. The imaginary parts of bin 0 and bin
    n/2 are ignored (they cannot belong to a real signal)."""
    if n < 4 or n & (n - 1):
        raise ValueError(f"irfft length {n} must be a power of two, >= 4")
    h = n >> 1
    if len(bins) != h + 1:
        raise ValueError(f"irfft of {n} points wants {h + 1} bins, got "
                         f"{len(bins)}")
    w = _half_twiddles(n)
    z = [0j] * h
    for k in range(h):
        xk = bins[k]
        xc = bins[h - k].conjugate()          # X[k + h]
        even = 0.5 * (xk + xc)
        odd = 0.5 * (xk - xc) / w[k]
        # z = even + i * odd ; the inverse transform is conj(fft(conj(z)))/h
        z[k] = (even + 1j * odd).conjugate()
    y = fft(z)
    scale = 1.0 / h
    out = [0.0] * n
    out[0::2] = [v.real * scale for v in y]
    out[1::2] = [-v.imag * scale for v in y]
    return out


# -- filters -------------------------------------------------------------------
def biquad(x, b0, b1, b2, a1, a2):
    """Direct form I, coefficients already divided by a0."""
    y = [0.0] * len(x)
    x1 = x2 = y1 = y2 = 0.0
    for i, v in enumerate(x):
        out = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1 = x1, v
        y2, y1 = y1, out
        y[i] = out
    return y


def _coefficients(kind: str, cutoff: float, q: float, rate: float):
    w0 = 2.0 * math.pi * min(cutoff, rate * 0.49) / rate
    cos_w, sin_w = math.cos(w0), math.sin(w0)
    alpha = sin_w / (2.0 * q)
    if kind == "lp":
        b0 = (1 - cos_w) / 2
        b1, b2 = 1 - cos_w, b0
    elif kind == "hp":
        b0 = (1 + cos_w) / 2
        b1, b2 = -(1 + cos_w), b0
    elif kind == "bp":
        b0, b1, b2 = alpha, 0.0, -alpha
    else:
        raise ValueError(f"no filter {kind!r}")
    a0 = 1 + alpha
    return (b0 / a0, b1 / a0, b2 / a0, -2 * cos_w / a0, (1 - alpha) / a0)


def filtered(x, kind: str, cutoff: float, rate: float, q: float = 0.7071):
    """Second-order low-pass ('lp'), high-pass ('hp') or band-pass ('bp')."""
    return biquad(x, *_coefficients(kind, cutoff, q, rate))


def swept(x, kind: str, cutoffs, rate: float, q: float = 0.7071, block: int = 64):
    """A filter whose cutoff follows `cutoffs(i)` (Hz at sample i), updated
    every `block` samples; the state carries across the changes."""
    y = [0.0] * len(x)
    x1 = x2 = y1 = y2 = 0.0
    n = len(x)
    for start in range(0, n, block):
        b0, b1, b2, a1, a2 = _coefficients(kind, cutoffs(start), q, rate)
        for i in range(start, min(n, start + block)):
            v = x[i]
            out = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1 = x1, v
            y2, y1 = y1, out
            y[i] = out
    return y


def peak(x) -> float:
    return max((abs(v) for v in x), default=0.0)


def normalised(x, level: float = 1.0):
    top = peak(x)
    if top <= 0.0:
        return list(x)
    k = level / top
    return [v * k for v in x]


def peak_frequency(x, near_hz: float, rate: float, size: int = 65536,
                   start: int = 0) -> float:
    """The frequency of the strongest spectral line within 2% of `near_hz`,
    to a fraction of a cent: a Hann-windowed FFT and a parabola through the
    log-magnitudes of the peak and its neighbours. (Autocorrelation is
    biased by tens of cents on a waveform with a sharp period, such as a
    saw; this is not.)"""
    seg = x[start:start + size]
    if len(seg) < size:
        seg = list(seg) + [0.0] * (size - len(seg))
    mags, width = spectrum(seg, 0, size, rate)
    lo = max(2, int(near_hz * 0.98 / width))
    hi = min(len(mags) - 2, int(near_hz * 1.02 / width) + 1)
    k = max(range(lo, hi + 1), key=lambda i: mags[i])
    a, b, c = (math.log(max(mags[k - 1], 1e-12)), math.log(max(mags[k], 1e-12)),
               math.log(max(mags[k + 1], 1e-12)))
    denom = a - 2.0 * b + c
    shift = 0.5 * (a - c) / denom if denom else 0.0
    return (k + shift) * width
