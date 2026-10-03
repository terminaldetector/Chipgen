"""
wavio.py — WAV in/out through the standard library only.

scipy.io.wavfile is a fine tool and chipgen uses it when it is there, but
depending on scipy just to write a RIFF header would be the difference
between "unzip and run" and "unzip, install 40 MB of wheels, then run" in
a sandbox with no network. `wave` has been in the stdlib since forever.
"""

import os
import wave

import audio as _audio


def write(path: str, buf, sample_rate: int = 44100) -> str:
    """Write a chipgen buffer (numpy (N,2)/(N,) float or audio.Buffer) as 16-bit PCM."""
    channels = _channels_of(buf)
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(_audio.to_pcm16_bytes(buf))
    return path


def read(path: str):
    """Read a 16-bit PCM WAV. Returns (buffer, sample_rate)."""
    with wave.open(path, "rb") as w:
        channels = w.getnchannels()
        width = w.getsampwidth()
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"{path}: only 16-bit PCM is supported (got {width * 8}-bit)")
    return _audio.from_int16(raw, channels), rate


def _channels_of(buf) -> int:
    if _audio.is_fallback(buf):
        return buf.channels
    return 2 if getattr(buf, "ndim", 1) == 2 else 1


def describe(buf, sample_rate: int = 44100) -> str:
    """One-line summary used by the examples and the bridge self-test."""
    frames = len(buf)
    return (f"{frames} frames ({frames / sample_rate:.2f}s @ {sample_rate} Hz), "
            f"peak={_audio.peak(buf):.3f} rms={_audio.rms(buf):.4f}")
