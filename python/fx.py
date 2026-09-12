"""fx.py — the effect column: per-cell effects in tracker notation.

The engine could always bend, slide and swing a voice; the notation could
not say so per cell. `vib fm1 34 6` is a directive that applies from that
line onward, which is fine for a setting and useless for writing music
where the effect belongs to one note. Measured on Yuzo Koshiro's driver
in Streets of Rage, the title theme writes 1,892 frequency changes and
1,853 mid-note Total Level writes across 6,256 key-ons — a third of its
notes are bent or ramped individually. There was nowhere to put that.

So a cell may carry effects:

    A-2:100/1F0      note, velocity 100, slide up at speed F0
    .../4A3          held note, vibrato speed A depth 3
    ===/A0C          note off with a fade
    C-4/3A0/4C4      slide to this note, and vibrato once there

Codes follow the tracker convention everyone already knows, one letter or
digit plus two hex digits:

    0xy   arpeggio, x and y semitones above the note
    1xx   pitch slide up          2xx   pitch slide down
    3xx   portamento to the written note
    4xy   vibrato, x speed, y depth
    7xy   tremolo, x speed, y depth
    8xx   pan: 00 left, 80 centre, FF right
    Axy   volume slide, x up, y down
    Cxx   delay the note by xx ticks

## Turning hex into physical units

Tracker parameters are dimensionless by tradition, and this project's
events are in cents, Hz and cents per second, because that is what made
the effects testable. The scales below are chosen so the middle of each
range lands where the corpus actually sits rather than where the hex
happens to be convenient:

Vibrato is the one with a measured anchor. Across 4,839 vibrato spans in
the transcribed corpus the median is 34 cents at 6 Hz, so `455` — speed
5, depth 5 — gives 6 Hz at 40 cents and lands a written vibrato in the
middle of what these composers played. Depth runs 8 cents per step,
which puts the full range at 8 to 120 cents; beyond that it stops being
a vibrato and becomes a bend, and the transcriber refuses to call it one
above 150 cents for the same reason.

Slides run 8 cents per second per unit, so 0x01 crawls at 8 c/s and 0xFF
covers 20 semitones a second. A semitone of portamento at 0x18 takes
about half a second, which is the range a tracker's ear expects.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import events as events_mod

#: Separator between a cell's note and its effects, and between effects.
SEPARATOR = "/"

#: Cents per second per unit of slide speed.
SLIDE_CENTS_PER_UNIT = 8.0
#: Cents per unit of vibrato or tremolo depth.
DEPTH_PER_UNIT = 8.0
#: Hz per unit of vibrato or tremolo speed, plus one so 0 is not static.
SPEED_HZ_PER_UNIT = 1.0
#: Volume units per second per step of a volume slide.
VOLUME_PER_UNIT = 16.0
#: How far a slide runs before it stops, in cents. Large enough to read as
#: open-ended; a bend that never stops leaves the note in another key.
SLIDE_LIMIT_CENTS = 2400.0

CODES = "0123478AC"

#: Codes the parser understands and the cell layer cannot yet realise.
#: Both need to place events BETWEEN rows, which needs the row emitter to
#: subdivide a row — not just a different event.
NOT_IMPLEMENTED = {
    "0": "arpeggio",
    "C": "note delay",
}


class FXError(ValueError):
    """A malformed effect, reported with the text that caused it."""


def split_cell(cell: str):
    """'A-2:100/1F0/4A3' -> ('A-2:100', ['1F0', '4A3'])."""
    parts = cell.split(SEPARATOR)
    return parts[0], [p for p in parts[1:] if p]


def parse(code: str):
    """'1F0' -> ('1', 0x0F, 0x00, 0xF0). Raises FXError on anything else."""
    token = code.strip()
    if len(token) != 3:
        raise FXError(f"{code!r} is not an effect (want three characters, "
                      f"like 1F0 or 4A3)")
    kind = token[0].upper()
    if kind not in CODES:
        raise FXError(f"{code!r} starts with {token[0]!r}, which is not an "
                      f"effect code. Valid: {' '.join(CODES)}")
    try:
        value = int(token[1:], 16)
    except ValueError:
        raise FXError(f"{code!r} has a non-hex parameter {token[1:]!r}") from None
    return kind, value >> 4, value & 0x0F, value


#: Voices with a level but no continuous pitch. A pitch code on one of
#: these used to be accepted and then quietly do nothing, which is the
#: failure this module exists to avoid.
_VOLUME_ONLY = ("noise", "dac", "dmc")

#: The codes that need a pitch to bend.
_PITCH_CODES = {"1": "pitch slide up", "2": "pitch slide down",
                "3": "portamento to note", "4": "vibrato"}

#: The codes that need a level to move.
_VOLUME_CODES = {"7": "tremolo", "A": "volume slide"}

#: Voices with a pitch but no level. Exactly one: the NES triangle has no
#: volume register at all, so it plays at one level or not at all —
#: measured, its RMS is bit-identical at velocity 8 and velocity 127.
#: Accepting a tremolo on it and writing nothing is the failure this
#: module exists to avoid.
_PITCH_ONLY = ("triangle",)


def _dac_ceiling() -> int:
    import samples
    return samples.DAC_RATE_CEILING


def to_events(target: str, code: str, note=None, octave=None):
    """One effect on one voice -> the events that realise it.

    `note` and `octave` are the cell's own note, which `3xx` needs as its
    destination and `0xy` needs as its root.
    """
    kind, high, low, value = parse(code)
    E = events_mod

    if target in _PITCH_ONLY and kind in _VOLUME_CODES:
        raise FXError(
            f"effect {code!r} ({_VOLUME_CODES[kind]}) needs a level to "
            f"move, and the NES triangle has no volume register — it plays "
            f"at one level or not at all, and its output measures "
            f"identical at velocity 8 and velocity 127. Pitch effects "
            f"(1xx/2xx/3xx slides, 4xy vibrato) do work on it; for a "
            f"swelling bass line use a pulse channel, or gate the triangle "
            f"with note-offs.")

    if target in _VOLUME_ONLY and kind in _PITCH_CODES:
        raise FXError(
            f"effect {code!r} ({_PITCH_CODES[kind]}) needs a pitch to bend, "
            f"and {target} has none to bend continuously. The PSG noise "
            f"rate is four discrete settings, and the DAC's pitch is its "
            f"feed rate, already at the chip's "
            f"{_dac_ceiling():,} Hz ceiling. Volume effects (7xy tremolo, Axy "
            f"volume slide) do work on {target}; for a pitched noise "
            f"sweep, bend psg2 and use periodic noise at rate 3.")

    if kind == "1":
        return [E.Portamento(target=target,
                             cents_per_second=value * SLIDE_CENTS_PER_UNIT,
                             to_cents=SLIDE_LIMIT_CENTS)]
    if kind == "2":
        return [E.Portamento(target=target,
                             cents_per_second=value * SLIDE_CENTS_PER_UNIT,
                             to_cents=-SLIDE_LIMIT_CENTS)]
    if kind == "3":
        # Portamento TO the written note: the note has already been keyed
        # on at its own pitch, so the slide runs from wherever the voice
        # was bent to back to zero.
        return [E.Portamento(target=target,
                             cents_per_second=value * SLIDE_CENTS_PER_UNIT,
                             to_cents=0.0)]
    if kind == "4":
        return [E.Vibrato(target=target, speed_hz=(high + 1) * SPEED_HZ_PER_UNIT,
                          depth_cents=low * DEPTH_PER_UNIT)]
    if kind == "7":
        return [E.Tremolo(target=target, speed_hz=(high + 1) * SPEED_HZ_PER_UNIT,
                          depth=low * DEPTH_PER_UNIT)]
    if kind == "8":
        return [_pan_event(target, value)]
    if kind == "A":
        rate = (high - low) * VOLUME_PER_UNIT
        return [E.VolumeSlide(target=target, per_second=rate)]
    # Recognised, not yet implemented — and it says so rather than
    # returning nothing. A cell that parses and silently does nothing is
    # the worst of the three outcomes: the score looks right, the render
    # succeeds, and the effect is simply absent.
    if kind in NOT_IMPLEMENTED:
        raise FXError(f"effect {code!r} ({NOT_IMPLEMENTED[kind]}) is "
                      f"recognised but not implemented yet — it needs "
                      f"row-level timing, which the cell layer does not "
                      f"have. Use the `arp` directive for arpeggios.")
    raise FXError(f"effect {code!r} parsed but has no handler")


def _pan_event(target: str, value: int):
    """8xx -> FMPan. Below 0x40 is left, above 0xC0 is right, else centre."""
    if not target.startswith("fm"):
        raise FXError(f"pan (8xx) is an FM effect; {target} has no panning")
    channel = int(target[2:])
    left = value < 0xC0
    right = value >= 0x40
    return events_mod.FMPan(channel=channel, left=left, right=right)


def arpeggio_offsets(code: str):
    """0xy -> (0, x, y) semitone offsets, or None if this is not an arpeggio."""
    kind, high, low, _ = parse(code)
    return (0, high, low) if kind == "0" else None


def delay_ticks(code: str):
    """Cxx -> xx, or None if this is not a note delay."""
    kind, _high, _low, value = parse(code)
    return value if kind == "C" else None


def describe(code: str) -> str:
    """A sentence for the manifest and for error messages."""
    kind, high, low, value = parse(code)
    if kind == "1":
        return f"slide up at {value * SLIDE_CENTS_PER_UNIT:.0f} cents/second"
    if kind == "2":
        return f"slide down at {value * SLIDE_CENTS_PER_UNIT:.0f} cents/second"
    if kind == "3":
        return f"portamento to the written note at " \
               f"{value * SLIDE_CENTS_PER_UNIT:.0f} cents/second"
    if kind == "4":
        return f"vibrato {low * DEPTH_PER_UNIT:.0f} cents at " \
               f"{(high + 1) * SPEED_HZ_PER_UNIT:.0f} Hz"
    if kind == "7":
        return f"tremolo {low * DEPTH_PER_UNIT:.0f} units at " \
               f"{(high + 1) * SPEED_HZ_PER_UNIT:.0f} Hz"
    if kind == "8":
        return ("pan left" if value < 0x40 else
                "pan right" if value >= 0xC0 else "pan centre")
    if kind == "A":
        rate = (high - low) * VOLUME_PER_UNIT
        return f"volume slide {rate:+.0f} per second"
    if kind == "0":
        return f"arpeggio 0, +{high}, +{low} semitones"
    if kind == "C":
        return f"delay the note by {value} ticks"
    return code


def vocabulary() -> dict:
    """Machine-readable effect list, for --info and the manifest."""
    return {
        "syntax": "NOTE[:velocity][/EFFECT]... e.g. A-2:100/1F0/4A3",
        "separator": SEPARATOR,
        "effects": {
            "0xy": "arpeggio — PARSED BUT NOT IMPLEMENTED; use the "
                   "`arp` directive",
            "1xx": f"pitch slide up, {SLIDE_CENTS_PER_UNIT:.0f} cents/s per unit",
            "2xx": f"pitch slide down, {SLIDE_CENTS_PER_UNIT:.0f} cents/s per unit",
            "3xx": "portamento to the written note",
            "4xy": f"vibrato, speed x+1 Hz, depth y x {DEPTH_PER_UNIT:.0f} cents",
            "7xy": "tremolo, speed x+1 Hz, depth y",
            "8xx": "pan: 00 left, 80 centre, FF right",
            "Axy": f"volume slide, (x-y) x {VOLUME_PER_UNIT:.0f} per second",
            "Cxx": "note delay — PARSED BUT NOT IMPLEMENTED",
        },
        "calibration": "vibrato 455 is 6 Hz at 40 cents, which is the "
                       "corpus median of 34 cents at 6 Hz",
        "columns": {
            "all": "every column takes the effect column: fm0-fm5, "
                   "psg0-psg2, opl0-opl8, noise, dac",
            "pitch_effects": "fm, opl and psg only. `noise` and `dac` "
                             "REFUSE 1/2/3/4 rather than accept and do "
                             "nothing — the noise rate is four discrete "
                             "settings and the DAC's pitch is its feed "
                             "rate, already at the byte ceiling. For a "
                             "pitched noise sweep bend psg2 and use "
                             "periodic noise at rate 3.",
            "pan": "fm only; 8xx on any other column is refused",
            "dac_limits": "measured: the effect clock is 60 Hz and the "
                          "longest built-in sample is 260 ms, so A0F "
                          "reaches only about -2 dB by the end of a tom, "
                          "while tremolo 75A swings 7.4 dB. Once a drum "
                          "decays into its last few 8-bit codes, scaling "
                          "cannot be represented and the tail flattens.",
        },
    }
