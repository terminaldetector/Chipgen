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


def calibrate_mix_volume(module, target: float = 0.89, rounds: int = 3):
    """Set `module.mix_volume` so the render peaks at `target`.

    Mix volume is a property of the module, so the loudness it is given
    here is the loudness it has in Schism: the demo does not depend on the
    build's normalisation. Needs libopenmpt. -> the peak it ended on.
    """
    from . import it_write
    peak = 0.0
    for _ in range(rounds):
        blob = it_write.build(module)
        with openmpt.Song(blob) as song:
            frames = song.render(44100)
        peak = openmpt.peak(frames)
        if peak <= 0.0:
            raise RenderError("the module renders as silence; nothing to "
                              "calibrate")
        if abs(peak - target) / target < 0.03:
            break
        module.mix_volume = max(1, min(128, round(
            module.mix_volume * target / peak)))
    return peak
