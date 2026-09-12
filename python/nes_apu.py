"""nes_apu.py — the NES / Famicom audio unit (RP2A03), at the register level.

Two pulse channels, one triangle, one noise, one DPCM sample player. Same
contract as the other cores here: you write the registers a real driver
writes, and what comes out is what the hardware does with them.

Four things about this chip are counterintuitive enough to be worth
stating before the code, because every one of them changes how you write
for it:

**The triangle has no volume control.** It is on at full scale or it is
off — four bits of output, fixed. There is no envelope, no attenuator,
nothing. That single fact is why NES music sounds the way it does: the
triangle is the bass on essentially every NES soundtrack, because a voice
you cannot fade is a voice you can only use for a part that never needs
to fade.

**The channels do not mix linearly.** Two things at half volume are not
one thing at full volume. The hardware sums through a resistor ladder
whose response is `95.88 / (8128 / (p1 + p2) + 100)` for the pulses and a
similar curve for the triangle/noise/DPCM group, so adding a second voice
raises the total by less than the voice is worth, and muting one raises
the others. Anything that reasons about levels by adding them is wrong
here in a way it is not wrong on the YM2612.

**The triangle sounds an octave below a pulse at the same timer value.**
Its timer is clocked every CPU cycle and divides by 32; the pulses are
clocked every second cycle and divide by 16. Copying a timer value from a
pulse to the triangle transposes it down an octave.

**Noise is a 15-bit LFSR with sixteen fixed periods, not a frequency.**
You pick a period from a table. The mode bit changes which bit feeds
back, turning hiss into a short metallic loop that is pitched enough to
play as a note.

Register map, all writes to $4000-$4017:

    $4000-$4003  pulse 1     $400C-$400F  noise
    $4004-$4007  pulse 2     $4010-$4013  DMC
    $4008-$400B  triangle    $4015 enable  $4017 frame counter
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import audio

#: CPU clock. Every audio period on this chip is derived from it.
NTSC_CPU_CLOCK = 1789773.0
PAL_CPU_CLOCK = 1662607.0

#: Render one sample per 32 CPU cycles. That puts the native rate at
#: 55.9 kHz on NTSC — the same neighbourhood as the YM2612's 53.3 kHz —
#: and makes every internal divider land on a whole number of steps per
#: sample: the triangle clocks 32 times, the pulses and noise 16.
CYCLES_PER_SAMPLE = 32

#: Duty cycle sequences for the pulse channels, 8 steps each.
#: The fourth is 25% inverted, which sounds identical to 25% in isolation
#: and different the moment a sweep or another channel is phase-related.
DUTY_SEQUENCES = (
    (0, 1, 0, 0, 0, 0, 0, 0),      # 12.5%
    (0, 1, 1, 0, 0, 0, 0, 0),      # 25%
    (0, 1, 1, 1, 1, 0, 0, 0),      # 50%
    (1, 0, 0, 1, 1, 1, 1, 1),      # 25% inverted
)

#: The triangle's fixed 32-step ramp. No volume control anywhere in it.
TRIANGLE_SEQUENCE = tuple(list(range(15, -1, -1)) + list(range(16)))

#: Noise timer periods, NTSC. Index 0 is the fastest (highest hiss).
NOISE_PERIODS_NTSC = (4, 8, 16, 32, 64, 96, 128, 160, 202, 254, 380, 508,
                      762, 1016, 2034, 4068)
NOISE_PERIODS_PAL = (4, 8, 14, 30, 60, 88, 118, 148, 188, 236, 354, 472,
                     708, 944, 1890, 3778)

#: Length counter values, indexed by the 5-bit field in $4003/$400B/$400F.
LENGTH_TABLE = (10, 254, 20, 2, 40, 4, 80, 6, 160, 8, 60, 10, 14, 12, 26,
                14, 12, 16, 24, 18, 48, 20, 96, 22, 192, 24, 72, 26, 16,
                28, 32, 30)

#: DMC rate table (CPU cycles per output bit), NTSC.
DMC_RATES_NTSC = (428, 380, 340, 320, 286, 254, 226, 214, 190, 160, 142,
                  128, 106, 84, 72, 54)

#: The frame sequencer runs at 240 Hz — four "quarter frames" per 60 Hz
#: frame. Envelopes step on every quarter, length counters on every other.
FRAME_RATE = 240.0


class _Envelope:
    """Volume envelope shared by the pulses and the noise.

    Either a constant volume or a decay from 15 to 0 at a chosen rate.
    That is the whole dynamic range: 4 bits, no attack, no sustain level.
    """

    __slots__ = ("start", "loop", "constant", "volume", "divider", "decay")

    def __init__(self):
        self.start = False
        self.loop = False
        self.constant = True
        self.volume = 0
        self.divider = 0
        self.decay = 0

    def clock(self):
        if self.start:
            self.start = False
            self.decay = 15
            self.divider = self.volume
            return
        if self.divider:
            self.divider -= 1
            return
        self.divider = self.volume
        if self.decay:
            self.decay -= 1
        elif self.loop:
            self.decay = 15

    def output(self) -> int:
        return self.volume if self.constant else self.decay


class _Pulse:
    __slots__ = ("timer", "period", "duty", "step", "envelope", "length",
                 "halt", "enabled", "sweep_enabled", "sweep_period",
                 "sweep_negate", "sweep_shift", "sweep_divider",
                 "sweep_reload", "ones_complement")

    def __init__(self, ones_complement: bool):
        self.timer = 0
        self.period = 0
        self.duty = 0
        self.step = 0
        self.envelope = _Envelope()
        self.length = 0
        self.halt = False
        self.enabled = False
        self.sweep_enabled = False
        self.sweep_period = 0
        self.sweep_negate = False
        self.sweep_shift = 0
        self.sweep_divider = 0
        self.sweep_reload = False
        #: Pulse 1 negates with ones' complement, pulse 2 with twos'.
        #: The difference is one semitone at the extreme and it is real
        #: hardware behaviour, not an emulator quirk.
        self.ones_complement = ones_complement

    def target_period(self) -> int:
        change = self.period >> self.sweep_shift
        if self.sweep_negate:
            change = -change - (1 if self.ones_complement else 0)
        return self.period + change

    def muted(self) -> bool:
        return self.period < 8 or self.target_period() > 0x7FF

    def clock_timer(self):
        if self.timer:
            self.timer -= 1
        else:
            self.timer = self.period
            self.step = (self.step + 1) & 7

    def clock_sweep(self):
        if self.sweep_divider == 0 and self.sweep_enabled \
                and self.sweep_shift and not self.muted():
            self.period = max(0, self.target_period())
        if self.sweep_divider == 0 or self.sweep_reload:
            self.sweep_divider = self.sweep_period
            self.sweep_reload = False
        else:
            self.sweep_divider -= 1

    def output(self) -> int:
        if not self.enabled or not self.length or self.muted():
            return 0
        return (self.envelope.output()
                if DUTY_SEQUENCES[self.duty][self.step] else 0)


class _Triangle:
    __slots__ = ("timer", "period", "step", "length", "halt", "enabled",
                 "linear", "linear_reload", "reload_flag")

    def __init__(self):
        self.timer = 0
        self.period = 0
        self.step = 0
        self.length = 0
        self.halt = False
        self.enabled = False
        self.linear = 0
        self.linear_reload = 0
        self.reload_flag = False

    def clock_timer(self):
        if self.timer:
            self.timer -= 1
        else:
            self.timer = self.period
            if self.length and self.linear:
                self.step = (self.step + 1) & 31

    def clock_linear(self):
        if self.reload_flag:
            self.linear = self.linear_reload
        elif self.linear:
            self.linear -= 1
        if not self.halt:
            self.reload_flag = False

    def output(self) -> int:
        # Below period 2 the triangle runs into ultrasound and real
        # hardware produces a click or a DC offset rather than a note;
        # silencing it is what every emulator does and what drivers assume.
        if not self.enabled or self.period < 2:
            return 0
        return TRIANGLE_SEQUENCE[self.step]


class _Noise:
    __slots__ = ("timer", "period", "shift", "mode", "envelope", "length",
                 "halt", "enabled")

    def __init__(self):
        self.timer = 0
        self.period = 4
        #: Powers on as 1. A zero shift register never leaves zero.
        self.shift = 1
        self.mode = False
        self.envelope = _Envelope()
        self.length = 0
        self.halt = False
        self.enabled = False

    def clock_timer(self):
        if self.timer:
            self.timer -= 1
            return
        self.timer = self.period
        tap = 6 if self.mode else 1
        feedback = (self.shift ^ (self.shift >> tap)) & 1
        self.shift = (self.shift >> 1) | (feedback << 14)

    def output(self) -> int:
        if not self.enabled or not self.length or (self.shift & 1):
            return 0
        return self.envelope.output()


class _DMC:
    """Delta modulation channel. Level is settable directly, which is how
    most drivers use it — as a 7-bit DAC for drums — rather than by
    streaming a sample."""

    __slots__ = ("level", "enabled")

    def __init__(self):
        self.level = 0
        self.enabled = False

    def output(self) -> int:
        return self.level if self.enabled else 0


def mix(pulse1: int, pulse2: int, triangle: int, noise: int,
        dmc: int) -> float:
    """The hardware's non-linear summing, as a float in roughly 0..1.

    Straight from the two published transfer curves. This is not a
    refinement — using a linear sum instead puts every level in a mix
    wrong, because the whole point of these curves is that the second
    voice adds less than the first did.
    """
    pulse_sum = pulse1 + pulse2
    pulse_out = 95.88 / ((8128.0 / pulse_sum) + 100.0) if pulse_sum else 0.0
    tnd = triangle / 8227.0 + noise / 12241.0 + dmc / 22638.0
    tnd_out = 159.79 / ((1.0 / tnd) + 100.0) if tnd else 0.0
    return pulse_out + tnd_out


class NESAPU:
    """Register-level RP2A03. Write registers, render samples."""

    def __init__(self, clock: float = NTSC_CPU_CLOCK, logger=None,
                 pal: bool = False):
        self.clock = PAL_CPU_CLOCK if pal else clock
        self.pal = pal
        self.logger = logger
        self.native_rate = self.clock / CYCLES_PER_SAMPLE
        self._noise_periods = (NOISE_PERIODS_PAL if pal
                               else NOISE_PERIODS_NTSC)
        self.pulse1 = _Pulse(ones_complement=True)
        self.pulse2 = _Pulse(ones_complement=False)
        self.triangle = _Triangle()
        self.noise = _Noise()
        self.dmc = _DMC()
        self._shadow = {}
        self._frame_accumulator = 0.0
        self._frame_step = 0
        self._five_step = False

    # -- registers ---------------------------------------------------------
    def write(self, address: int, value: int):
        address &= 0xFFFF
        value &= 0xFF
        self._shadow[address] = value
        if self.logger is not None:
            self.logger(address, value)
        self._decode(address, value)

    def _decode(self, address: int, value: int):
        if 0x4000 <= address <= 0x4007:
            self._write_pulse(self.pulse1 if address < 0x4004 else self.pulse2,
                              address & 3, value)
        elif address == 0x4008:
            self.triangle.halt = bool(value & 0x80)
            self.triangle.linear_reload = value & 0x7F
        elif address == 0x400A:
            self.triangle.period = (self.triangle.period & 0x700) | value
        elif address == 0x400B:
            self.triangle.period = ((self.triangle.period & 0xFF)
                                    | ((value & 7) << 8))
            if self.triangle.enabled:
                self.triangle.length = LENGTH_TABLE[(value >> 3) & 31]
            self.triangle.reload_flag = True
        elif address == 0x400C:
            self.noise.halt = bool(value & 0x20)
            self.noise.envelope.loop = bool(value & 0x20)
            self.noise.envelope.constant = bool(value & 0x10)
            self.noise.envelope.volume = value & 0x0F
        elif address == 0x400E:
            self.noise.mode = bool(value & 0x80)
            self.noise.period = self._noise_periods[value & 0x0F]
        elif address == 0x400F:
            if self.noise.enabled:
                self.noise.length = LENGTH_TABLE[(value >> 3) & 31]
            self.noise.envelope.start = True
        elif address == 0x4011:
            self.dmc.level = value & 0x7F
        elif address == 0x4015:
            self._write_enable(value)
        elif address == 0x4017:
            self._five_step = bool(value & 0x80)
            self._frame_step = 0
            if self._five_step:
                self._quarter_frame()
                self._half_frame()

    def _write_pulse(self, pulse, index: int, value: int):
        if index == 0:
            pulse.duty = (value >> 6) & 3
            pulse.halt = bool(value & 0x20)
            pulse.envelope.loop = bool(value & 0x20)
            pulse.envelope.constant = bool(value & 0x10)
            pulse.envelope.volume = value & 0x0F
        elif index == 1:
            pulse.sweep_enabled = bool(value & 0x80)
            pulse.sweep_period = (value >> 4) & 7
            pulse.sweep_negate = bool(value & 0x08)
            pulse.sweep_shift = value & 7
            pulse.sweep_reload = True
        elif index == 2:
            pulse.period = (pulse.period & 0x700) | value
        else:
            pulse.period = (pulse.period & 0xFF) | ((value & 7) << 8)
            if pulse.enabled:
                pulse.length = LENGTH_TABLE[(value >> 3) & 31]
            pulse.step = 0
            pulse.envelope.start = True

    def _write_enable(self, value: int):
        for bit, channel in ((0x01, self.pulse1), (0x02, self.pulse2),
                             (0x04, self.triangle), (0x08, self.noise)):
            channel.enabled = bool(value & bit)
            if not channel.enabled:
                channel.length = 0
        self.dmc.enabled = bool(value & 0x10)

    # -- frame sequencer ---------------------------------------------------
    def _quarter_frame(self):
        self.pulse1.envelope.clock()
        self.pulse2.envelope.clock()
        self.noise.envelope.clock()
        self.triangle.clock_linear()

    def _half_frame(self):
        for channel in (self.pulse1, self.pulse2, self.triangle, self.noise):
            if channel.length and not channel.halt:
                channel.length -= 1
        self.pulse1.clock_sweep()
        self.pulse2.clock_sweep()

    def _advance_frame(self, samples: int):
        """Step the 240 Hz sequencer forward by `samples` output samples.

        Called once per rendered sample, not once per render() batch.
        Batching it left the envelope, the length counters and the
        triangle's linear counter frozen for the whole buffer — and the
        triangle in particular never sounds at all, because its timer only
        advances while the linear counter is non-zero and the linear
        counter is only loaded by a quarter frame.
        """
        per_frame = self.native_rate / FRAME_RATE
        self._frame_accumulator += samples
        while self._frame_accumulator >= per_frame:
            self._frame_accumulator -= per_frame
            steps = 5 if self._five_step else 4
            self._frame_step = (self._frame_step + 1) % steps
            if self._five_step and self._frame_step == 4:
                continue                     # the extra step does nothing
            self._quarter_frame()
            if self._frame_step in (1, 3) or \
                    (self._five_step and self._frame_step == 0):
                self._half_frame()

    # -- rendering ---------------------------------------------------------
    def render(self, samples: int):
        """-> a stereo buffer. The NES is mono; both channels carry it."""
        out = []
        pulse_ticks = CYCLES_PER_SAMPLE // 2
        for _ in range(samples):
            for _ in range(pulse_ticks):
                self.pulse1.clock_timer()
                self.pulse2.clock_timer()
                self.noise.clock_timer()
            for _ in range(CYCLES_PER_SAMPLE):
                self.triangle.clock_timer()
            self._advance_frame(1)
            value = mix(self.pulse1.output(), self.pulse2.output(),
                        self.triangle.output(), self.noise.output(),
                        self.dmc.output())
            out.append(value)
            out.append(value)
        return audio.from_floats(out, channels=2)

    def close(self):
        pass

    # -- convenience -------------------------------------------------------
    def pulse_period(self, frequency: float) -> int:
        """Timer value for a pitch on a pulse channel."""
        if frequency <= 0:
            return 0
        return max(0, min(0x7FF,
                          int(round(self.clock / (16.0 * frequency) - 1))))

    def triangle_period(self, frequency: float) -> int:
        """Timer value for a pitch on the triangle — a different divider.

        Half the pulse value for the same pitch, which is the same thing
        as saying the triangle plays an octave lower for the same timer.
        """
        if frequency <= 0:
            return 0
        return max(0, min(0x7FF,
                          int(round(self.clock / (32.0 * frequency) - 1))))

    def pulse_frequency(self, period: int) -> float:
        return self.clock / (16.0 * (period + 1))

    def triangle_frequency(self, period: int) -> float:
        return self.clock / (32.0 * (period + 1))


# --------------------------------------------------------------------------
# The musical layer
# --------------------------------------------------------------------------
# Everything above is the chip: write a register, get a sample. What
# follows is the layer every other chip here already had and this one did
# not, which is why a NES score could be READ (nes_transcribe.py) but
# never written. It is deliberately the same shape as opn2/opl2/sn76489 —
# note_on, note_off, set_volume, set_pitch_offset — so the sequencer does
# not learn a fourth vocabulary.

#: The channels a score can address, in the spelling the tracker uses.
VOICE_NAMES = ("pulse1", "pulse2", "triangle", "noise")

#: Pulse duty settings and what they sound like. 75% and 25% are the same
#: waveform inverted, so they measure identically and only differ in
#: phase — worth knowing before spending a channel on the distinction.
DUTY_NAMES = {0: "12.5%", 1: "25%", 2: "50% (square)", 3: "75%"}

#: Length-counter index that means "as long as possible", for notes whose
#: end is a note-off rather than a timer. 0x1F is 254 frames.
_LONGEST_LENGTH = 0x1F


class _Voice:
    __slots__ = ("note", "velocity", "duty", "cents", "sounding",
                 "high_byte")

    def __init__(self):
        self.note = None
        self.velocity = 127
        self.duty = 2
        self.cents = 0.0
        self.sounding = False
        #: The last value written to this voice's period-high register,
        #: so a re-tune that does not change it can skip the write. See
        #: _write_period for why that matters.
        self.high_byte = None


class Voices:
    """A musical interface over NESAPU.

    Holds no audio state of its own — every method turns into register
    writes on the APU it was given, so a .vgm log and the rendered audio
    cannot disagree.
    """

    #: Base register for each pulse channel.
    _PULSE_BASE = {"pulse1": 0x4000, "pulse2": 0x4004}

    def __init__(self, apu):
        self.apu = apu
        self.voices = {name: _Voice() for name in VOICE_NAMES}
        self._enabled = 0x00
        # Nothing sounds until $4015 says so, and a score should not have
        # to know that. All FIVE bits: 0x0F enables the two pulses, the
        # triangle and the noise but leaves bit 4 clear, which gates the
        # DMC's output to zero — a sample written to $4011 then logs
        # every byte and renders exact silence, with nothing to suggest
        # the channel was switched off rather than the sample empty.
        self._write_enable(0x1F)
        # And then the quirk that costs a whole octave. The sweep unit
        # mutes a pulse channel whenever the TARGET period would exceed
        # $7FF, and it does that whether or not the sweep is enabled.
        # With a shift count of zero the target is twice the period, so
        # every period above 1023 mutes — which is everything below about
        # 110 Hz. Measured: with $4001 left at zero, A-1, C-2, E-2 and
        # G-2 all render 0.0000 RMS, exact silence, while A-2 at period
        # 1016 plays fine. Setting the negate bit makes the target
        # negative instead, the overflow check passes, and A-1 comes back
        # at 55.00 Hz within 0.1 cents. Real NES drivers write this at
        # init for the same reason; a score should not have to.
        for base in sorted(self._PULSE_BASE.values()):
            self.apu.write(base + 1, 0x08)

    # -- helpers -----------------------------------------------------------
    def _write_enable(self, mask: int):
        self._enabled = mask & 0x1F
        self.apu.write(0x4015, self._enabled)

    def _voice(self, name: str) -> _Voice:
        try:
            return self.voices[name]
        except KeyError:
            raise KeyError(f"no NES voice named {name!r}. Valid: "
                           f"{', '.join(VOICE_NAMES)}") from None

    @staticmethod
    def _level(velocity: int) -> int:
        """0-127 velocity -> the 4-bit envelope volume.

        Linear in the register, which is linear in amplitude on this chip:
        unlike the YM2612's Total Level there is no dB curve to undo.
        """
        return max(0, min(15, int(round(velocity * 15.0 / 127.0))))

    # -- pulses ------------------------------------------------------------
    def set_duty(self, name: str, duty: int):
        voice = self._voice(name)
        if name not in self._PULSE_BASE:
            raise ValueError(f"{name} has no duty cycle; only "
                             f"{', '.join(self._PULSE_BASE)} do")
        if not 0 <= duty <= 3:
            raise ValueError(f"duty must be 0-3 ({DUTY_NAMES}), got {duty}")
        voice.duty = duty
        if voice.sounding:
            self._write_pulse_control(name)

    def _write_pulse_control(self, name: str):
        voice = self.voices[name]
        base = self._PULSE_BASE[name]
        # Bit 5 halts the length counter: a note ends when the score says
        # so, not when a timer runs out. Bit 4 is constant volume, which
        # is what a velocity means here — the alternative is the hardware
        # envelope, and a score that wanted that would say so.
        self.apu.write(base, ((voice.duty & 3) << 6) | 0x30
                       | self._level(voice.velocity))

    def set_sweep(self, name: str, period: int, shift: int,
                  negate: bool = False, enabled: bool = True):
        """The pulse channels' own pitch slide, run by the hardware.

        Kept separate from the effect column's portamento on purpose: this
        one is free (the CPU writes nothing per frame) but coarse, and it
        silences the channel when the target period goes out of range,
        which is the classic "my sweep killed the note" surprise.
        """
        if name not in self._PULSE_BASE:
            raise ValueError(f"{name} has no sweep unit")
        if not 0 <= period <= 7 or not 0 <= shift <= 7:
            raise ValueError("sweep period and shift are both 0-7, got "
                             f"period={period} shift={shift}")
        value = ((0x80 if enabled else 0) | ((period & 7) << 4)
                 | (0x08 if negate else 0) | (shift & 7))
        self.apu.write(self._PULSE_BASE[name] + 1, value)

    # -- notes -------------------------------------------------------------
    def note_on(self, name: str, note: str, octave: int, velocity: int = 127):
        voice = self._voice(name)
        voice.note = (note, octave)
        voice.velocity = max(1, min(127, velocity))
        voice.sounding = True
        voice.cents = 0.0
        if name in self._PULSE_BASE:
            self._write_pulse_control(name)
            self._write_period(name, self._frequency(voice), force=True)
        elif name == "triangle":
            # The triangle has NO volume control — it plays at one level
            # or not at all, so velocity is ignored rather than quietly
            # approximated. 0x80 halts the linear counter, which is what
            # keeps it sounding until a note-off.
            self.apu.write(0x4008, 0xFF)
            self._write_period(name, self._frequency(voice), force=True)
        else:
            raise ValueError("the noise channel takes noise(), not a note")

    def note_off(self, name: str):
        voice = self._voice(name)
        voice.sounding = False
        voice.note = None
        if name in self._PULSE_BASE:
            # Silence by zeroing the envelope volume, not by clearing the
            # enable bit: clearing enable also resets the length counter,
            # and the next note-on then has to restore it.
            base = self._PULSE_BASE[name]
            self.apu.write(base, ((voice.duty & 3) << 6) | 0x30)
        elif name == "triangle":
            # No volume to zero, so the linear counter is the only way
            # down: reload it with zero and the sequencer stops.
            self.apu.write(0x4008, 0x00)
        elif name == "noise":
            self.apu.write(0x400C, 0x30)

    def noise_on(self, period: int, velocity: int = 127,
                 metallic: bool = False):
        """Gate the noise channel. `period` is 0-15, low index = high pitch.

        `metallic` is the mode bit: it shortens the shift register from
        32767 steps to 93, which is periodic enough to have a pitch. That
        is the NES's only route to a tonal metallic timbre.
        """
        if not 0 <= period <= 15:
            raise ValueError(f"noise period is 0-15, got {period}")
        voice = self._voice("noise")
        voice.velocity = max(1, min(127, velocity))
        voice.sounding = True
        self.apu.write(0x400C, 0x30 | self._level(voice.velocity))
        self.apu.write(0x400E, (0x80 if metallic else 0x00) | (period & 0x0F))
        self.apu.write(0x400F, _LONGEST_LENGTH << 3)

    def noise_off(self):
        self.note_off("noise")

    # -- pitch and volume, for the effect column ---------------------------
    def _frequency(self, voice) -> float:
        if voice.note is None:
            return 0.0
        from opn2 import note_to_freq
        freq = note_to_freq(*voice.note)
        if voice.cents:
            freq *= 2.0 ** (voice.cents / 1200.0)
        return freq

    def _write_period(self, name: str, frequency: float, force: bool = False):
        """Set a voice's timer, writing the high register only if it moved.

        This is not an optimisation. On this chip a write to $4003/$4007
        resets the pulse's duty phase and restarts its envelope, and
        $400B reloads the triangle's linear counter — so re-asserting an
        unchanged high byte 60 times a second turns the effect clock into
        an audible tone. Measured on a held note re-tuned at 60 Hz:
        writing both registers every tick puts a 60 Hz component at
        -30.5 dB with harmonics at -29.7 and -28.2, where writing only
        the low byte leaves it at -148.8 dB. That is 118 dB of buzz from
        one redundant write, and it is the same mistake as re-seeding the
        PSG's noise register on every hat.

        `force` is for a note-on, which does want the phase reset and the
        length-counter reload.
        """
        if frequency <= 0:
            return
        voice = self.voices[name]
        if name in self._PULSE_BASE:
            period = self.apu.pulse_period(frequency)
            base = self._PULSE_BASE[name]
            low, high = base + 2, base + 3
        else:
            period = self.apu.triangle_period(frequency)
            low, high = 0x400A, 0x400B
        high_value = (_LONGEST_LENGTH << 3) | ((period >> 8) & 7)
        self.apu.write(low, period & 0xFF)
        if force or voice.high_byte != high_value:
            self.apu.write(high, high_value)
            voice.high_byte = high_value

    def set_pitch_offset(self, name: str, cents: float):
        """Retune a sounding voice without restarting it.

        Relative to the note that was keyed on, so calls do not compound.
        The timer is 11 bits and the resolution runs out in the top
        octaves exactly as the PSG's does — that is the hardware.
        """
        voice = self._voice(name)
        if voice.note is None:
            return
        voice.cents = float(cents)
        self._write_period(name, self._frequency(voice))

    def set_volume(self, name: str, velocity: int):
        """0-127. A no-op on the triangle, which has no volume register."""
        voice = self._voice(name)
        voice.velocity = max(0, min(127, velocity))
        if name in self._PULSE_BASE:
            if voice.sounding:
                self._write_pulse_control(name)
        elif name == "noise":
            if voice.sounding:
                self.apu.write(0x400C, 0x30 | self._level(voice.velocity))
        # triangle: nothing to write. Documented, not silently rounded.

    # -- DMC, the NES's PCM ------------------------------------------------
    def dmc_level(self, level: int):
        """Write the DMC's 7-bit output directly.

        $4011 is the one register on this chip that is a straight DAC:
        writing it moves the output level immediately. Feeding it in a
        loop is how NES games play samples without DPCM data, and it is
        the same trick as the YM2612's register 0x2A.
        """
        self.apu.write(0x4011, max(0, min(127, int(level))))
