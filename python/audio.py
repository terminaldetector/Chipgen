"""
audio.py — the one place that knows whether numpy/scipy are installed.

chipgen's whole point is that you can drop it somewhere and it works, and
"somewhere" includes a chat model's code sandbox with no network access to
`pip install` anything. So every array operation the engine needs lives
behind these functions, with three implementations picked at import time:

    scipy present  -> scipy.signal.resample (band-limited, best quality)
    numpy only     -> numpy linear interpolation
    neither        -> pure-Python linear interpolation over array('f')

When numpy is available the buffers ARE numpy arrays — float32, shape
(N, 2) for stereo and (N,) for mono, exactly as the original API
documented, so nothing downstream has to change. Without numpy you get a
`Buffer`, a small object over `array('f')` supporting the handful of
operations the engine actually performs. Both satisfy len(), indexing and
iteration, so example code that just writes a WAV works either way.
"""

import math
from array import array

try:
    import numpy as _np
    HAVE_NUMPY = True
except ImportError:              # pragma: no cover - exercised in bare sandboxes
    _np = None
    HAVE_NUMPY = False

try:
    from scipy.signal import resample as _scipy_resample
    HAVE_SCIPY = True
except ImportError:              # pragma: no cover
    _scipy_resample = None
    HAVE_SCIPY = False


def backend_name() -> str:
    if HAVE_SCIPY:
        return "scipy"
    if HAVE_NUMPY:
        return "numpy"
    return "pure-python"


# --------------------------------------------------------------------------
# Fallback buffer
# --------------------------------------------------------------------------
class Buffer:
    """Interleaved float samples with a channel count — the no-numpy stand-in.

    Stored flat (`array('f')`) rather than as a list of frames: flat is what
    both the C cores and the WAV writer want, and it keeps the pure-Python
    resampler's inner loop free of tuple allocation.
    """

    __slots__ = ("data", "channels")

    def __init__(self, data=None, channels=2):
        self.channels = channels
        if data is None:
            self.data = array("f")
        elif isinstance(data, array) and data.typecode == "f":
            self.data = data
        else:
            self.data = array("f", data)

    # -- sequence protocol: len() is FRAMES, not raw samples ----------------
    def __len__(self):
        return len(self.data) // self.channels

    def __getitem__(self, i):
        if isinstance(i, slice):
            start, stop, step = i.indices(len(self))
            if step != 1:
                raise ValueError("Buffer slicing supports step=1 only")
            c = self.channels
            return Buffer(self.data[start * c:stop * c], c)
        if i < 0:
            i += len(self)
        c = self.channels
        if c == 1:
            return self.data[i]
        return tuple(self.data[i * c:i * c + c])

    def __iter__(self):
        for i in range(len(self)):
            yield self[i]

    def __repr__(self):
        return f"Buffer(frames={len(self)}, channels={self.channels})"

    def tolist(self):
        if self.channels == 1:
            return list(self.data)
        return [list(self.data[i:i + self.channels])
                for i in range(0, len(self.data), self.channels)]


def is_fallback(buf) -> bool:
    return isinstance(buf, Buffer)


# --------------------------------------------------------------------------
# Construction
# --------------------------------------------------------------------------
def zeros(frames: int, channels: int = 2):
    if HAVE_NUMPY:
        shape = (frames, channels) if channels > 1 else (frames,)
        return _np.zeros(shape, dtype=_np.float32)
    return Buffer(array("f", bytes(4 * frames * channels)), channels)


def from_int16(raw, channels: int = 2):
    """Wrap a ctypes int16 buffer (or bytes) of interleaved PCM as floats."""
    if HAVE_NUMPY:
        arr = _np.frombuffer(raw, dtype=_np.int16).astype(_np.float32) / 32768.0
        return arr.reshape(-1, channels) if channels > 1 else arr
    src = array("h")
    src.frombytes(bytes(memoryview(raw).cast("B")))
    out = array("f", bytes(4 * len(src)))
    for i, v in enumerate(src):
        out[i] = v / 32768.0
    return Buffer(out, channels)


def from_floats(values, channels: int = 2):
    """Wrap an interleaved sequence of Python floats as a buffer.

    The pure-Python cores build their output in an array('f') because
    per-element assignment into a numpy array is slower than building the
    array and converting once at the end.
    """
    if HAVE_NUMPY:
        arr = _np.frombuffer(memoryview(values).cast("B"), dtype=_np.float32) \
            if isinstance(values, array) else _np.asarray(values, dtype=_np.float32)
        return arr.reshape(-1, channels).copy() if channels > 1 else arr.copy()
    return Buffer(values if isinstance(values, array) else array("f", values),
                  channels)


def concat(chunks, channels: int = 2):
    chunks = [c for c in chunks if len(c)]
    if not chunks:
        return zeros(0, channels)
    if HAVE_NUMPY:
        return _np.concatenate(chunks, axis=0)
    out = array("f")
    for c in chunks:
        out.extend(c.data)
    return Buffer(out, channels)


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------
def peak(buf) -> float:
    if not len(buf):
        return 0.0
    if HAVE_NUMPY and not is_fallback(buf):
        return float(_np.max(_np.abs(buf)))
    return max(abs(v) for v in buf.data)


def rms(buf) -> float:
    if not len(buf):
        return 0.0
    if HAVE_NUMPY and not is_fallback(buf):
        return float(_np.sqrt(_np.mean(_np.square(buf, dtype=_np.float64))))
    n = len(buf.data)
    return math.sqrt(sum(v * v for v in buf.data) / n) if n else 0.0


# --------------------------------------------------------------------------
# Mixing
# --------------------------------------------------------------------------
def scale(buf, factor: float):
    if HAVE_NUMPY and not is_fallback(buf):
        return (buf * factor).astype(_np.float32)
    out = array("f", buf.data)
    for i in range(len(out)):
        out[i] *= factor
    return Buffer(out, buf.channels)


def add_mono_into_stereo(dst, mono, gain: float = 1.0):
    """dst[:n, both channels] += mono[:n] * gain, in place. n = overlap."""
    n = min(len(dst), len(mono))
    if n == 0:
        return dst
    if HAVE_NUMPY and not is_fallback(dst):
        contribution = mono[:n] * gain
        dst[:n, 0] += contribution
        dst[:n, 1] += contribution
        return dst
    for i in range(n):
        v = mono.data[i] * gain
        dst.data[i * 2] += v
        dst.data[i * 2 + 1] += v
    return dst


def add_stereo_into(dst, src, gain: float = 1.0):
    n = min(len(dst), len(src))
    if n == 0:
        return dst
    if HAVE_NUMPY and not is_fallback(dst):
        dst[:n] += src[:n] * gain
        return dst
    for i in range(n * 2):
        dst.data[i] += src.data[i] * gain
    return dst


# --------------------------------------------------------------------------
# Resampling
# --------------------------------------------------------------------------
def resample(buf, rate_in: float, rate_out: float):
    """Resample to rate_out, preserving channel layout."""
    n_in = len(buf)
    if n_in == 0 or abs(rate_in - rate_out) < 1e-9:
        return buf
    n_out = max(1, int(round(n_in * rate_out / rate_in)))

    if HAVE_SCIPY and not is_fallback(buf):
        return _scipy_resample(buf, n_out, axis=0).astype(_np.float32)

    if HAVE_NUMPY and not is_fallback(buf):
        # Linear interpolation. Downsampling this way aliases; the chips run
        # far above 44.1 kHz so we pre-average each output sample's input
        # span, which is a cheap box-filter anti-alias that costs one pass.
        ratio = n_in / n_out
        if ratio > 1.5:
            buf = _boxcar_numpy(buf, ratio)
            n_in = len(buf)
            ratio = n_in / n_out
        pos = _np.arange(n_out, dtype=_np.float64) * ratio
        idx = _np.minimum(pos.astype(_np.int64), n_in - 1)
        nxt = _np.minimum(idx + 1, n_in - 1)
        frac = (pos - idx).astype(_np.float32)
        if buf.ndim == 2:
            frac = frac[:, None]
        return (buf[idx] * (1.0 - frac) + buf[nxt] * frac).astype(_np.float32)

    return _resample_pure(buf, n_out)


def _boxcar_numpy(buf, ratio: float):
    """Average groups of `ratio` input samples before decimating."""
    width = max(1, int(ratio))
    n = (len(buf) // width) * width
    if n == 0:
        return buf
    trimmed = buf[:n]
    if trimmed.ndim == 2:
        return trimmed.reshape(-1, width, trimmed.shape[1]).mean(axis=1).astype(_np.float32)
    return trimmed.reshape(-1, width).mean(axis=1).astype(_np.float32)


def _resample_pure(buf, n_out: int):
    c = buf.channels
    n_in = len(buf)
    src = buf.data
    ratio = n_in / n_out
    out = array("f", bytes(4 * n_out * c))

    if ratio > 1.5:
        # Box-filter decimation, same anti-alias reasoning as the numpy path.
        width = int(ratio)
        for j in range(n_out):
            start = int(j * ratio)
            stop = min(start + width, n_in)
            span = stop - start
            if span <= 0:
                start, stop, span = n_in - 1, n_in, 1
            for ch in range(c):
                total = 0.0
                for k in range(start, stop):
                    total += src[k * c + ch]
                out[j * c + ch] = total / span
        return Buffer(out, c)

    for j in range(n_out):
        pos = j * ratio
        i0 = int(pos)
        if i0 >= n_in - 1:
            i0 = n_in - 2 if n_in >= 2 else 0
        i1 = min(i0 + 1, n_in - 1)
        frac = pos - i0
        for ch in range(c):
            a = src[i0 * c + ch]
            b = src[i1 * c + ch]
            out[j * c + ch] = a + (b - a) * frac
    return Buffer(out, c)


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------
def to_pcm16_bytes(buf) -> bytes:
    """Clip to [-1, 1] and pack as little-endian interleaved int16."""
    if not len(buf):
        return b""
    if HAVE_NUMPY and not is_fallback(buf):
        clipped = _np.clip(buf, -1.0, 1.0)
        return (clipped * 32767.0).astype("<i2").tobytes()
    out = array("h", bytes(2 * len(buf.data)))
    for i, v in enumerate(buf.data):
        if v > 1.0:
            v = 1.0
        elif v < -1.0:
            v = -1.0
        out[i] = int(v * 32767.0)
    import sys as _sys
    if _sys.byteorder == "big":
        out.byteswap()
    return out.tobytes()
