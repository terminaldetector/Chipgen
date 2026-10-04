"""render.py — a module to WAV and MP3, through libopenmpt and LAME.

Listening is the only test that finishes the argument, and a phone cannot
open a `.it`. So the build renders the module with libopenmpt (the same
engine OpenMPT plays with) and encodes it with whatever is installed:
LAME first, ffmpeg second. Neither is required to *write* modules; both
are required to *hear* them here.
"""

import os
import shutil
import subprocess
import tempfile
from typing import NamedTuple

from . import openmpt, schismtracker

ENGINES = ("openmpt", "schism")


class RenderError(RuntimeError):
    pass


def render_frames(module_bytes: bytes, sample_rate: int = 44100,
                  seconds: float = None, engine: str = "openmpt"):
    """Play a module into interleaved stereo floats with the named engine:
    `openmpt` (libopenmpt) or `schism` (Schism Tracker itself)."""
    if engine == "openmpt":
        with openmpt.Song(module_bytes) as song:
            return song.render(sample_rate, seconds)
    if engine == "schism":
        frames = schismtracker.render(module_bytes, sample_rate)
        return frames[:int(seconds * sample_rate) * 2] \
            if seconds is not None else frames
    raise RenderError(f"engine is one of {', '.join(ENGINES)}, not {engine!r}")


def to_wav(module_bytes: bytes, path: str, sample_rate: int = 44100,
           seconds: float = None, tail: float = 0.0, headroom: float = 0.0,
           engine: str = "openmpt"):
    """Render into a 16-bit stereo WAV. -> (seconds, peak).

    `headroom` (dB, positive) lowers the level; the engines' float output is
    not clipped until it is written, so a loud mix says so in `peak`.
    """
    frames = render_frames(module_bytes, sample_rate, seconds, engine)
    if tail:
        frames.extend([0.0] * int(tail * sample_rate) * 2)
    if headroom:
        gain = 10.0 ** (-headroom / 20.0)
        for i in range(len(frames)):
            frames[i] *= gain
    peak = openmpt.peak(frames)
    openmpt.write_wav(frames, path, sample_rate)
    return len(frames) / 2.0 / sample_rate, peak


def to_mp3(wav_path: str, mp3_path: str, bitrate: int = 192,
           title: str = "", artist: str = ""):
    """Encode a WAV. -> the encoder's name."""
    lame = shutil.which("lame")
    if lame:
        command = [lame, "--quiet", "--cbr", "-b", str(bitrate)]
        if title:
            command += ["--tt", title]
        if artist:
            command += ["--ta", artist]
        subprocess.run(command + [wav_path, mp3_path], check=True,
                       stdin=subprocess.DEVNULL)
        return "lame"
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                   "-y", "-i", wav_path, "-codec:a", "libmp3lame",
                   "-b:a", f"{bitrate}k"]
        if title:
            command += ["-metadata", f"title={title}"]
        subprocess.run(command + [mp3_path], check=True,
                       stdin=subprocess.DEVNULL)
        return "ffmpeg"
    raise RenderError("no MP3 encoder: install lame (apt install lame) or "
                      "ffmpeg")


def render_mp3(module_bytes: bytes, mp3_path: str, **kwargs):
    """Module -> MP3, through a temporary WAV. -> (seconds, peak, encoder)."""
    with tempfile.TemporaryDirectory(prefix="schism-") as scratch:
        wav = os.path.join(scratch, "render.wav")
        seconds, peak = to_wav(module_bytes, wav,
                               **{k: v for k, v in kwargs.items()
                                  if k in ("sample_rate", "seconds", "tail",
                                           "headroom", "engine")})
        encoder = to_mp3(wav, mp3_path,
                         **{k: v for k, v in kwargs.items()
                            if k in ("bitrate", "title", "artist")})
    return seconds, peak, encoder


class PeakFit(NamedTuple):
    """What fitting the mix volume to a peak did."""
    before: float           # peak at the mix volume the module came with
    after: float            # peak at the one it has now
    mix_before: int
    mix_after: int
    target: float
    renders: int

    @property
    def reached(self) -> bool:
        return abs(self.after - self.target) / self.target < 0.03

    def text(self) -> str:
        line = (f"peak {self.before:.2f} -> {self.after:.2f} (mix volume "
                f"{self.mix_before} -> {self.mix_after}, target "
                f"{self.target:g})")
        if not self.reached and self.mix_after in (1, 128):
            line += (f"; mix volume {self.mix_after} is the limit, so the "
                     f"peak cannot go {'lower' if self.mix_after == 1 else 'higher'}")
        return line


def _peak_now(module) -> float:
    from . import it_write
    with openmpt.Song(it_write.build(module)) as song:
        return openmpt.peak(song.render(44100))


def fit_peak(module, target: float = 0.89, rounds: int = 4,
             tolerance: float = 0.03) -> PeakFit:
    """Set `module.mix_volume` so the libopenmpt render peaks at `target`.

    Only the header's mix volume moves: no sample is touched, nothing
    clips, and the loudness the module is given is the loudness it has in
    Schism, not something the build does afterwards. Needs libopenmpt.
    """
    mix_before = module.mix_volume
    peak = before = _peak_now(module)
    if peak <= 0.0:
        raise RenderError("the module renders as silence; nothing to "
                          "calibrate")
    renders = 1
    for _ in range(rounds):
        if abs(peak - target) / target < tolerance:
            break
        wanted = max(1, min(128, round(module.mix_volume * target / peak)))
        if wanted == module.mix_volume:
            break                                   # at a limit
        module.mix_volume = wanted
        peak = _peak_now(module)
        renders += 1
    return PeakFit(before, peak, mix_before, module.mix_volume, target,
                   renders)


def calibrate_mix_volume(module, target: float = 0.89, rounds: int = 3):
    """`fit_peak`, for the callers that only want the peak it ended on."""
    return fit_peak(module, target, rounds).after
