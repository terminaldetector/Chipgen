"""
effects.py — the things a tracker does BETWEEN notes.

Until now chipgen could only say "this note, now". Everything a tracker
actually sounds like — a pitch sliding into place, a note breathing,
a chord swelling and ducking — happens in the time between note-ons, and
none of it was expressible. A score could name a hundred notes and still
sound like a hundred notes.

This is the missing half: effects that run on a clock. The sequencer
subdivides the time it renders and calls `advance()` on the way through,
and each voice's accumulated pitch and volume are pushed to whichever chip
owns it.

## Units are physical, not tracker units

A tracker spells vibrato as `4x7` and portamento as `301`, where the
numbers mean whatever that tracker decided. Here depth is cents, speed is
Hz, and a slide is cents per second. Two reasons: those are the units the
rest of chipgen already uses (FMPitch is cents, note_to_freq is Hz), and a
model writing a score can reason about "60 cents at 6 Hz" without having
first memorised somebody's effect table.

## Effects compose

Pitch effects add: portamento's offset plus vibrato's swing is the
channel's total detune, and each is tracked separately so stopping one
does not cancel the other. Volume effects multiply against the note's own
velocity rather than replacing it, so a tremolo on a quiet note stays
quiet.
"""

import math

#: How often effects are recomputed, in Hz. Real Mega Drive drivers run
#: theirs on the vertical blank — 60 Hz — and a vibrato stepping at that
#: rate is part of why the hardware sounds the way it does. Finer would be
#: smoother and less like the thing being emulated.
DEFAULT_RATE = 60.0

#: Names of the voices effects can address, in the same spelling the
#: tracker's columns use.
def voice_names():
    return ([f"fm{i}" for i in range(6)] + [f"psg{i}" for i in range(3)]
            + [f"opl{i}" for i in range(9)])


class _Voice:
    """One voice's live effect state."""
    __slots__ = ("portamento_rate", "portamento_target", "portamento_cents",
                 "vibrato_depth", "vibrato_speed", "vibrato_delay",
                 "vibrato_phase", "vibrato_elapsed", "sounding",
                 "volume_rate", "volume_offset", "volume_floor",
                 "volume_ceiling", "tremolo_depth", "tremolo_speed",
                 "tremolo_phase")

    def __init__(self):
        self.reset()

    def reset(self):
        self.portamento_rate = 0.0
        self.portamento_target = None
        self.portamento_cents = 0.0
        self.vibrato_depth = 0.0
        self.vibrato_speed = 0.0
        self.vibrato_delay = 0.0
        self.vibrato_phase = 0.0
        self.vibrato_elapsed = 0.0
        self.sounding = False
        self.volume_rate = 0.0
        self.volume_offset = 0.0
        self.volume_floor = 0.0
        self.volume_ceiling = 127.0
        self.tremolo_depth = 0.0
        self.tremolo_speed = 0.0
        self.tremolo_phase = 0.0

    def active(self) -> bool:
        return bool(self.portamento_rate or self.vibrato_depth
                    or self.volume_rate or self.tremolo_depth
                    or self.portamento_cents or self.volume_offset)

    # -- per-tick ----------------------------------------------------------
    def advance(self, dt: float):
        if self.portamento_rate:
            step = self.portamento_rate * dt
            if self.portamento_target is None:
                self.portamento_cents += step
            else:
                remaining = self.portamento_target - self.portamento_cents
                if abs(remaining) <= abs(step) or remaining == 0:
                    self.portamento_cents = self.portamento_target
                    self.portamento_rate = 0.0      # arrived; stop pulling
                else:
                    self.portamento_cents += math.copysign(abs(step), remaining)

        if self.vibrato_depth and self.vibrato_speed:
            self.vibrato_elapsed += dt
            if self.vibrato_elapsed >= self.vibrato_delay:
                self.vibrato_phase = (self.vibrato_phase
                                      + self.vibrato_speed * dt) % 1.0

        if self.volume_rate:
            self.volume_offset = max(
                self.volume_floor - 127.0,
                min(self.volume_ceiling - 127.0,
                    self.volume_offset + self.volume_rate * dt))

        if self.tremolo_depth and self.tremolo_speed:
            self.tremolo_phase = (self.tremolo_phase
                                  + self.tremolo_speed * dt) % 1.0

    def pitch_cents(self) -> float:
        cents = self.portamento_cents
        if (self.vibrato_depth and self.vibrato_speed
                and self.vibrato_elapsed >= self.vibrato_delay):
            cents += self.vibrato_depth * math.sin(2 * math.pi
                                                   * self.vibrato_phase)
        return cents

    def volume_scale(self) -> float:
        """A multiplier on the note's own velocity, 0..1-ish.

        Volume slide is in the engine's 0-127 units so that `-40 per
        second` means the same thing as a `vol` directive moving 40; that
        is turned into a ratio here because the note it modifies was
        already scaled by its velocity.
        """
        scale = (127.0 + self.volume_offset) / 127.0
        if self.tremolo_depth and self.tremolo_speed:
            swing = self.tremolo_depth * math.sin(2 * math.pi
                                                  * self.tremolo_phase)
            scale *= (127.0 - swing) / 127.0
        return max(0.0, min(1.0, scale))


class EffectEngine:
    """Every voice's effects, and the clock they run on.

    Deliberately knows nothing about chips: `advance()` returns which
    voices changed and by how much, and the sequencer decides what to do
    with that. It makes this testable without an emulator, and it means
    adding a fourth chip does not mean touching the effects at all.
    """

    def __init__(self, rate: float = DEFAULT_RATE):
        self.rate = max(1.0, float(rate))
        self.voices = {name: _Voice() for name in voice_names()}

    @property
    def tick_seconds(self) -> float:
        return 1.0 / self.rate

    def any_active(self) -> bool:
        return any(voice.active() for voice in self.voices.values())

    # -- starting and stopping ---------------------------------------------
    def portamento(self, target: str, cents_per_second: float,
                   to_cents: float = None):
        voice = self._voice(target)
        voice.portamento_rate = float(cents_per_second)
        voice.portamento_target = to_cents

    def vibrato(self, target: str, depth_cents: float, speed_hz: float,
                delay: float = 0.0):
        voice = self._voice(target)
        voice.vibrato_depth = float(depth_cents)
        voice.vibrato_speed = float(speed_hz)
        voice.vibrato_delay = max(0.0, float(delay))
        if not depth_cents or not speed_hz:
            # Leaving the phase where it stopped would make the next
            # vibrato start mid-swing, which reads as a pitch jump.
            voice.vibrato_phase = 0.0
        voice.vibrato_elapsed = 0.0

    def volume_slide(self, target: str, per_second: float,
                     floor: float = 0.0, ceiling: float = 127.0):
        voice = self._voice(target)
        voice.volume_rate = float(per_second)
        voice.volume_floor = float(floor)
        voice.volume_ceiling = float(ceiling)

    def tremolo(self, target: str, depth: float, speed_hz: float):
        voice = self._voice(target)
        voice.tremolo_depth = float(depth)
        voice.tremolo_speed = float(speed_hz)
        if not depth or not speed_hz:
            voice.tremolo_phase = 0.0

    def note_on(self, target: str):
        """A new note restarts the modulators but keeps the slides.

        Vibrato that carried its phase across a note-on would start the
        next note mid-swing; a portamento that reset would stop mid-slide,
        which is the opposite of what it is for.
        """
        voice = self.voices.get(target)
        if voice is None:
            return
        voice.vibrato_phase = 0.0
        voice.vibrato_elapsed = 0.0
        voice.tremolo_phase = 0.0
        voice.sounding = True

    def note_off(self, target: str):
        voice = self.voices.get(target)
        if voice is not None:
            voice.sounding = False

    def clear(self, target: str):
        voice = self.voices.get(target)
        if voice is not None:
            voice.reset()

    def _voice(self, target: str) -> _Voice:
        try:
            return self.voices[target]
        except KeyError:
            raise KeyError(
                f"no voice named {target!r}. Valid: "
                f"{', '.join(voice_names())}") from None

    # -- the clock ---------------------------------------------------------
    def advance(self, dt: float):
        """Run every active voice forward, and report what changed.

        Returns {voice: (pitch_cents, volume_scale)} for voices whose
        values moved, so the caller writes only the registers that need
        writing — a chip that gets a redundant write per tick per channel
        makes a .vgm several times bigger for no sound.
        """
        changed = {}
        for name, voice in self.voices.items():
            if not voice.active():
                continue
            before = (voice.pitch_cents(), voice.volume_scale())
            voice.advance(dt)
            after = (voice.pitch_cents(), voice.volume_scale())
            if abs(after[0] - before[0]) > 1e-6 or abs(after[1] - before[1]) > 1e-6:
                changed[name] = after
        return changed

    def state(self, target: str):
        voice = self._voice(target)
        return voice.pitch_cents(), voice.volume_scale()
