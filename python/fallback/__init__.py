"""
fallback/ — the chips in pure Python, for when there is no compiler.

chipgen's real cores are C: Nuked-OPN2 for the YM2612 and a register-level
SN76489. They are what the project is about, and core_loader.py will build
them from source rather than come here if a C compiler exists anywhere on
the machine.

This package is the floor under that. Unzip chipgen into a sandbox with
Python and nothing else — the situation the bridge path is designed for —
and it still makes sound. Two honest caveats, stated once here and again
at the top of each module:

  * `psg.PyPSG` is faithful. Same registers, same 16-bit LFSR, same taps,
    same volume table. It computes tone channels by integrating over the
    counter's toggles rather than stepping 223 kHz of ticks in Python, so
    it is fast AND anti-aliased, but the model it integrates is the same
    one psg.c steps.

  * `fm.PyYM2612` is an APPROXIMATION. It uses the chip's real log-sin and
    exp tables, its real phase increments, detune table, key scaling and
    envelope rates, so it sounds like a YM2612 and plays in tune — but it
    is an operator-level model on a per-sample loop, not the cycle-accurate
    die-derived core. No LFO, no SSG-EG, no DAC ladder.

If what you want is the sound of the chip, get the compiler.
"""

from .psg import PyPSG
from .fm import PyYM2612

__all__ = ["PyPSG", "PyYM2612"]
