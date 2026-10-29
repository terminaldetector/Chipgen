"""nes_driver.py — the 2A03 has no instruments, so this is the thing that plays one.

A Famicom sound engine is a loop that runs once a frame, 60 times a second,
and every NES "instrument" ever made is a handful of *macros* that loop
reads: a list of volumes, a list of duty cycles, a list of pitch offsets,
a list of arpeggio steps, one value per frame. FamiTracker, Furnace and
every game's own driver work this way, because the chip offers nothing
else: it has no envelope worth the name (4 bits, no attack, no sustain
level), no vibrato and no pitch bend, so the driver is the instrument.

This module is that driver:

  * `Macro` — values per frame, a loop point that repeats while the key is
    down, a release point that takes over when it comes up;
  * `NESInstrument` — which channel, which macros, and extra voices that
    play the same notes a few cents away (the one NES trick for width);
  * `frames()` — what every voice does on each frame of one note;
  * `to_events()` — the same note as ordinary chipgen events, so a score,
    the sequencer and the .vgm writer need to know nothing about macros.

The events use only what the engine already has — NESNoteOn, NESVolume,
NESDuty, NESNoiseOn and Portamento at a rate fast enough to be a jump — so
there is no new event type to teach a model, and a VGM exported from a
score using these instruments is a plain register log.
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import events as E

FRAME_RATE = 60.0
#: the fastest Portamento the event vocabulary allows: 800 cents in one
#: 60 Hz effect tick, which is a jump for anything a macro asks of it.
JUMP_RATE = 48000.0
#: A release never rings longer than this many frames (a second).
MAX_TAIL_FRAMES = 60
#: NTSC CPU clock, which the pulse and triangle timers divide
CPU_CLOCK = 1789773.0
#: A pitch offset this small (cents) is a vibrato or a detune and is kept
#: inside the timer's high-byte page; a bigger one is a deliberate jump
#: (a chirp, an arpeggio) and goes where it is told.
VIBRATO_CENTS = 40.0


def period_of(hz: float, triangle: bool = False) -> int:
    """The 11-bit timer the APU would write for a pitch (nes_apu's rule)."""
    if hz <= 0:
        return 0
    divider = 32.0 if triangle else 16.0
    return max(0, min(0x7FF, int(round(CPU_CLOCK / (divider * hz) - 1))))


def page_safe(base_hz: float, cents: float, triangle: bool = False) -> float:
    """Keep a small pitch offset from carrying the timer across a 256 boundary.

    The timer's high byte is a separate register, and writing it restarts
    the pulse's duty phase (nes_apu says so, and measures the buzz). A
    vibrato on a note whose timer sits near a multiple of 256 would cross
    it twice a cycle and click twice a cycle; a real driver gives up a few
    cents of the swing instead, which is what this does: the offset is cut
    off at the edge of the page the note itself is in.
    """
    if base_hz <= 0 or cents == 0.0 or abs(cents) > VIBRATO_CENTS:
        return cents
    p0 = period_of(base_hz, triangle)
    p1 = period_of(base_hz * 2.0 ** (cents / 1200.0), triangle)
    if (p0 >> 8) == (p1 >> 8):
        return cents
    low = p0 & ~0xFF
    p = max(low, min(low | 0xFF, p1))
    divider = 32.0 if triangle else 16.0
    hz = CPU_CLOCK / (divider * (p + 1))
    return 1200.0 * math.log2(hz / base_hz)


@dataclass
class Macro:
    values: List[float]
    loop: Optional[int] = None       # held: jump back here at the end
    release: Optional[int] = None    # key up: carry on from here

    def __post_init__(self):
        if not self.values:
            raise ValueError("a macro needs at least one value")
        for name in ("loop", "release"):
            point = getattr(self, name)
            if point is not None and not 0 <= point < len(self.values):
                raise ValueError(f"{name} point {point} is outside the "
                                 f"{len(self.values)} values")

    def walk(self, hold_frames: int, total_frames: int) -> List[float]:
        """The macro's value on each of `total_frames` frames, with the key
        held for the first `hold_frames` of them.

        While the key is down the values before the release point play
        once and then the span from the loop point up to the release point
        repeats (to the end of the list if there is no release point).
        When the key comes up the macro carries on from the release point.
        """
        n = len(self.values)
        held_end = self.release if self.release is not None else n
        out = []
        pos = 0
        released = False
        for frame in range(total_frames):
            if frame >= hold_frames and not released:
                released = True
                if self.release is not None:
                    pos = self.release
            out.append(self.values[min(pos, n - 1)])
            pos += 1
            if released:
                pos = min(pos, n - 1)
            elif pos >= held_end:
                pos = self.loop if self.loop is not None else held_end - 1
        return out

    def to_dict(self):
        return {"values": [round(v, 3) for v in self.values],
                "loop": self.loop, "release": self.release}

    @classmethod
    def from_dict(cls, data):
        return cls([float(v) for v in data["values"]], data.get("loop"),
                   data.get("release"))


def constant(value: float) -> Macro:
    return Macro([value], loop=0)


def parse_macro(text: str, what: str = "macro", low: float = None,
                high: float = None, whole: bool = False) -> Macro:
    """Read a macro the way trackers write one: values in frames, `|` where
    the loop starts, `/` where the release starts.

        "15 14 13 | 12 11 / 8 4 0"   plays 15 14 13, then loops 12 11 while
                                     the key is down, then 8 4 0 after it

    A macro with no marks plays through and holds its last value."""
    values, loop, release = [], None, None
    for token in text.replace("|", " | ").replace("/", " / ").split():
        if token == "|":
            if loop is not None:
                raise ValueError(f"{what}: only one `|` (loop start)")
            loop = len(values)
        elif token == "/":
            if release is not None:
                raise ValueError(f"{what}: only one `/` (release start)")
            release = len(values)
        else:
            try:
                value = float(token)
            except ValueError:
                raise ValueError(f"{what}: {token!r} is not a number") \
                    from None
            if whole and value != int(value):
                raise ValueError(f"{what}: {token} must be a whole number")
            if low is not None and value < low or high is not None \
                    and value > high:
                raise ValueError(f"{what}: {token} is outside {low:g}..{high:g}")
            values.append(int(value) if whole else value)
    if not values:
        raise ValueError(f"{what}: no values")
    if loop is not None and loop >= len(values):
        raise ValueError(f"{what}: `|` must come before a value")
    if release is not None and release >= len(values):
        raise ValueError(f"{what}: `/` must come before a value")
    if loop is not None and release is not None and release <= loop:
        raise ValueError(f"{what}: the loop `|` comes before the release `/`")
    if loop is None and release is None:
        loop = len(values) - 1                  # hold the last value
    return Macro([float(v) for v in values], loop, release)


@dataclass
class NESInstrument:
    name: str
    channel: str = "pulse"                      # pulse | triangle | noise
    volume: Macro = field(default_factory=lambda: constant(15))
    duty: Macro = field(default_factory=lambda: constant(2))
    pitch: Macro = field(default_factory=lambda: constant(0))
    arp: Macro = field(default_factory=lambda: constant(0))
    period: Macro = field(default_factory=lambda: constant(6))
    mode: int = 0                               # noise: 1 = metallic
    #: extra voices playing the same note: {"cents": 8, "volume": 0.8,
    #: "duty_shift": 1}; they take the other pulse channel
    layers: List[dict] = field(default_factory=list)
    trim: int = 0                               # level, in volume steps (<=0)

    def copy(self):
        return NESInstrument.from_dict(self.to_dict())

    def to_dict(self):
        return {"name": self.name, "channel": self.channel,
                "volume": self.volume.to_dict(), "duty": self.duty.to_dict(),
                "pitch": self.pitch.to_dict(), "arp": self.arp.to_dict(),
                "period": self.period.to_dict(), "mode": self.mode,
                "layers": [dict(l) for l in self.layers], "trim": self.trim}

    @classmethod
    def from_dict(cls, data):
        return cls(name=data["name"], channel=data.get("channel", "pulse"),
                   volume=Macro.from_dict(data["volume"]),
                   duty=Macro.from_dict(data["duty"]),
                   pitch=Macro.from_dict(data["pitch"]),
                   arp=Macro.from_dict(data["arp"]),
                   period=Macro.from_dict(data["period"]),
                   mode=int(data.get("mode", 0)),
                   layers=[dict(l) for l in data.get("layers", [])],
                   trim=int(data.get("trim", 0)))


#: name -> NESInstrument, the NES's counterpart of instruments.BANK. It
#: lives here because the chip itself has no bank to put it in.
BANK: Dict[str, "NESInstrument"] = {}


def add(inst: "NESInstrument") -> "NESInstrument":
    if not inst.name:
        raise ValueError("an NES instrument needs a name to go in the bank")
    BANK[inst.name] = inst
    return inst


def get(name: str) -> "NESInstrument":
    try:
        return BANK[name]
    except KeyError:
        raise KeyError(f"unknown NES instrument {name!r}; have: "
                       f"{', '.join(sorted(BANK)) or 'none loaded'}") from None


def names():
    return sorted(BANK)


# --------------------------------------------------------------------------
# One note, frame by frame
# --------------------------------------------------------------------------
@dataclass
class Frame:
    volume: int           # 0..15
    duty: int             # 0..3 (pulse)
    cents: float          # pitch offset, arpeggio included
    period: int           # noise period index


def frames(inst: NESInstrument, hold_frames: int, velocity: int = 127,
           layer: Optional[dict] = None,
           base_hz: Optional[float] = None) -> List[Frame]:
    """What one voice does each frame of one note.

    The note lasts until the volume macro has gone quiet after the key
    comes up (or a second, whichever is first), so the release is part of
    the note and the driver, not the score, decides when the channel can
    be keyed off.

    `base_hz` is the note's pitch. With it, small pitch offsets (vibrato,
    detune) are kept inside the timer's high-byte page (see page_safe);
    without it they are taken as written.
    """
    total = hold_frames + MAX_TAIL_FRAMES
    vol = inst.volume.walk(hold_frames, total)
    duty = inst.duty.walk(hold_frames, total)
    pitch = inst.pitch.walk(hold_frames, total)
    arp = inst.arp.walk(hold_frames, total)
    period = inst.period.walk(hold_frames, total)
    scale = (velocity / 127.0) * (layer.get("volume", 1.0) if layer else 1.0)
    extra_cents = layer.get("cents", 0.0) if layer else 0.0
    duty_shift = layer.get("duty_shift", 0) if layer else 0
    out = []
    quiet_since = None
    tonal = inst.channel in ("pulse", "triangle") and base_hz
    for f in range(total):
        v = int(round(max(0.0, min(15.0, vol[f] * scale + inst.trim))))
        if layer and v == 0 and vol[f] * (velocity / 127.0) >= 2.0:
            v = 1       # a second voice that rounds to nothing is not a chorus
        steps = 100.0 * arp[f]
        swing = pitch[f] + extra_cents
        if tonal:
            swing = page_safe(base_hz * 2.0 ** (steps / 1200.0), swing,
                              inst.channel == "triangle")
        out.append(Frame(
            volume=v if inst.channel != "triangle" else (15 if v > 0 else 0),
            duty=int(round(duty[f] + duty_shift)) & 3,
            cents=steps + swing,
            period=int(max(0, min(15, round(period[f]))))))
        if f >= hold_frames:
            if v == 0:
                quiet_since = f if quiet_since is None else quiet_since
                if f - quiet_since >= 1:
                    return out[:f + 1]
            else:
                quiet_since = None
    return out


def voices_for(inst: NESInstrument, base_voice: str) -> List[tuple]:
    """[(voice name, layer dict or None)] for one note of this instrument."""
    out = [(base_voice, None)]
    other = {"pulse1": "pulse2", "pulse2": "pulse1"}.get(base_voice)
    for layer in inst.layers:
        if other:
            out.append((other, layer))
    return out


# --------------------------------------------------------------------------
# The same note as events
# --------------------------------------------------------------------------
def _velocity(volume: int) -> int:
    return max(1, int(round(volume * 127.0 / 15.0)))


def to_events(inst: NESInstrument, voice: str, note: str, octave: int,
              length_ticks: int, velocity: int = 127,
              ticks_per_second: float = 192.0, gate: float = 1.0):
    """-> [(tick offset, Event)] for one note. Offsets are from the note's
    start; the last event is the NoteOff, which is where the driver
    decided the channel went quiet."""
    hold_frames = max(1, int(round(length_ticks * gate / ticks_per_second
                                   * FRAME_RATE)))
    from opn2 import note_to_freq
    base_hz = note_to_freq(note, octave)
    out = []
    for voice_name, layer in voices_for(inst, voice):
        seq = frames(inst, hold_frames, velocity, layer, base_hz)
        out += _voice_events(inst, voice_name, note, octave, seq,
                             ticks_per_second)
    out.sort(key=lambda pair: pair[0])
    return out


def _tick_of(frame: int, tps: float) -> int:
    return int(round(frame * tps / FRAME_RATE))


def _voice_events(inst, voice, note, octave, seq, tps):
    out = []
    first = seq[0]
    if inst.channel == "noise":
        out.append((0, E.NESNoiseOn(period=first.period,
                                    velocity=_velocity(first.volume),
                                    metallic=bool(inst.mode))))
    else:
        if inst.channel == "pulse":
            out.append((0, E.NESDuty(voice=voice, duty=first.duty)))
        out.append((0, E.NESNoteOn(voice=voice, note=note, octave=octave,
                                   velocity=_velocity(first.volume))))
        if abs(first.cents) > 0.5:
            out.append((0, E.Portamento(target=voice,
                                        cents_per_second=JUMP_RATE,
                                        to_cents=_cents(first.cents))))
    previous = first
    for f in range(1, len(seq)):
        cur = seq[f]
        tick = _tick_of(f, tps)
        if cur.volume != previous.volume:
            if inst.channel == "noise":
                out.append((tick, E.NESVolume(voice="noise",
                                              velocity=_velocity(cur.volume))))
            elif inst.channel == "pulse" or cur.volume == 0 \
                    or previous.volume == 0:
                out.append((tick, E.NESVolume(voice=voice,
                                              velocity=_velocity(cur.volume))))
        if inst.channel == "pulse" and cur.duty != previous.duty:
            out.append((tick, E.NESDuty(voice=voice, duty=cur.duty)))
        if inst.channel == "noise":
            if cur.period != previous.period:
                out.append((tick, E.NESNoiseOn(
                    period=cur.period, velocity=_velocity(max(cur.volume, 1)),
                    metallic=bool(inst.mode))))
        elif abs(cur.cents - previous.cents) > 0.5:
            out.append((tick, E.Portamento(target=voice,
                                           cents_per_second=JUMP_RATE,
                                           to_cents=_cents(cur.cents))))
        previous = cur
    end = _tick_of(len(seq), tps)
    if inst.channel == "noise":
        out.append((end, E.NESNoiseOff()))
    else:
        out.append((end, E.NESNoteOff(voice=voice)))
        out.append((end, E.Portamento(target=voice,
                                      cents_per_second=JUMP_RATE,
                                      to_cents=0.0)))
    return out


def _cents(value: float) -> float:
    return float(max(-4800.0, min(4800.0, round(value, 1))))


def length_ticks_of(inst: NESInstrument, hold_ticks: int, velocity: int,
                    tps: float, gate: float = 1.0) -> int:
    """How many ticks the whole note, release included, occupies."""
    hold_frames = max(1, int(round(hold_ticks * gate / tps * FRAME_RATE)))
    return _tick_of(len(frames(inst, hold_frames, velocity)), tps)
