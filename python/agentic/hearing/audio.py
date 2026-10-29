"""audio.py — the worker's audio: a file decoded by ffmpeg to float32 at a
given rate, a window of it, and its identity. Runs in the hearing
interpreter (numpy). Essentia is built here without libav (the fork's
loaders need FFmpeg 7.1, this system has 6.1), so it reads its audio
through this one decoder.
"""

import hashlib
import shutil
import subprocess

import numpy as np


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load(path: str, rate: int = 44100, channels: int = 1,
         start: float = None, end: float = None) -> np.ndarray:
    """The file (or its [start, end) seconds) as float32, shape (n,) for
    mono or (n, 2) for stereo, resampled by ffmpeg (soxr is not assumed:
    ffmpeg's own resampler)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not on PATH: the hearing plugins "
                           "decode every file with it")
    cmd = [ffmpeg, "-v", "error", "-nostdin"]
    if start:
        cmd += ["-ss", f"{float(start):.6f}"]
    cmd += ["-i", path]
    if end is not None:
        cmd += ["-t", f"{max(0.0, float(end) - float(start or 0.0)):.6f}"]
    cmd += ["-vn", "-ac", str(channels), "-ar", str(rate), "-f", "f32le",
            "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True,
                         timeout=600).stdout
    data = np.frombuffer(raw, dtype=np.float32).copy()
    if channels == 2:
        data = data[: len(data) // 2 * 2].reshape(-1, 2)
    return data

