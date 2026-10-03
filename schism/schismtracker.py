"""schismtracker.py — Schism Tracker itself, as the second outside opinion.

libopenmpt (openmpt.py) is the engine OpenMPT plays with; Schism Tracker is
the program this project is named for, with its own mixer and its own
reading of Impulse Tracker's quirks. Two engines that were written apart
and agree are a better witness than either alone, and where they disagree
the module is relying on something a player is free to do differently —
which is worth knowing before a model leans on it.

Schism renders without a screen or a sound card: `--diskwrite=FILE` plays
the module into a WAV and exits, and its SDL drivers can be told to be
`dummy`. This module wraps exactly that and nothing more. It is optional:
`available()` is False where `schismtracker` is not on the PATH (on Debian
and Ubuntu: apt install schism), and the tests that use it skip.
"""

import array
import os
import shutil
import subprocess
import tempfile
import wave

#: interpolation_mode values in Schism's config file
INTERPOLATION = {"nearest": 0, "linear": 1, "spline": 2, "fir": 3}


class SchismError(RuntimeError):
    pass


def executable():
    return shutil.which("schismtracker") or shutil.which("schism")


def available() -> bool:
    return executable() is not None


def version() -> str:
    exe = executable()
    if not exe:
        return ""
    run = subprocess.run([exe, "--version"], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, timeout=30)
    return (run.stdout or run.stderr).splitlines()[0].strip() if (
        run.stdout or run.stderr) else ""


def render(module_bytes: bytes, sample_rate: int = 44100,
           interpolation: str = "spline", timeout: float = 300.0):
    """Play a module through Schism Tracker into memory.

    -> interleaved stereo floats as an `array('f')`, like
    `openmpt.Song.render`. A throwaway home directory holds the config, so
    the user's own settings are neither read nor changed.
    """
    exe = executable()
    if not exe:
        raise SchismError("schismtracker is not installed (apt install "
                          "schism, or https://schismtracker.org)")
    if interpolation not in INTERPOLATION:
        raise SchismError(f"interpolation is one of {', '.join(INTERPOLATION)}")
    with tempfile.TemporaryDirectory(prefix="schism-run-") as home:
        os.makedirs(os.path.join(home, ".schism"))
        with open(os.path.join(home, ".schism", "config"), "w") as handle:
            handle.write(
                "[Audio]\n"
                f"sample_rate={sample_rate}\nbits=16\nchannels=2\n"
                "buffer_size=1024\nmaster.left=31\nmaster.right=31\n"
                "[Mixer Settings]\nchannel_limit=128\n"
                f"interpolation_mode={INTERPOLATION[interpolation]}\n"
                "no_ramping=0\nsurround_effect=1\n")
        source = os.path.join(home, "in.it")
        target = os.path.join(home, "out.wav")
        with open(source, "wb") as handle:
            handle.write(module_bytes)
        env = dict(os.environ, HOME=home, SDL_VIDEODRIVER="dummy",
                   SDL_AUDIODRIVER="dummy")
        run = subprocess.run(
            [exe, "-v", "dummy", "-a", "dummy", f"--diskwrite={target}",
             source], env=env, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=timeout)
        if not os.path.exists(target):
            raise SchismError("schismtracker wrote no audio: "
                              + (run.stderr or run.stdout).strip()[-300:])
        with wave.open(target) as handle:
            if handle.getsampwidth() != 2 or handle.getnchannels() != 2:
                raise SchismError("unexpected diskwrite format")
            pcm = array.array("h")
            pcm.frombytes(handle.readframes(handle.getnframes()))
    import sys
    if sys.byteorder == "big":
        pcm.byteswap()
    return array.array("f", (v / 32768.0 for v in pcm))
