"""
opl2.py — YM3812 (OPL2), the AdLib/Sound Blaster chip, as a second FM voice.

The YM2612 in opn2.py is a Genesis chip: four operators, eight algorithms,
stereo, a PCM channel. The OPL2 is the DOS sound card: nine channels of
TWO operators, one modulator into one carrier (or the two side by side),
mono, and four selectable waveforms instead of one. It is strictly less
capable and it does not sound the same — that harder, glassier PC timbre
is the point of having it.

## What is exact here and what is modelled

Exact, and tested:

  * The waveform. Operator output goes through the chip's own log-sine and
    exponential tables — `-log2(sin(x))*256` in, `2^(-x/256)` out — so the
    quantisation of the sine, all four waveform shapes, and the 4084-step
    output range are the hardware's, not a floating-point sine.

  * The pitch. phase increment = (f_num << block) >> 1, scaled by the
    multiple table, against a 2^19 accumulator. A-4 lands on F-Number 580
    at block 4, which is what every published OPL note table says.

  * Total Level and Key Scale Level, in the chip's 0.75 dB and 0.1875 dB
    steps.

Modelled: the envelope generator's timing. The hardware runs its EG off a
global counter whose increment pattern cycles through an eight-entry table
per rate; this uses that pattern's AVERAGE rate instead of stepping the
counter. The attack keeps the hardware's shape (attenuation falls
proportionally, `env -= (env+1)*step/8`), which is what makes an OPL
attack sound like an OPL attack. Envelope timing is therefore right to
within a fraction of a step, not sample-exact.

Not implemented: rhythm mode (the five percussion voices in register
0xBD). The DAC kit already covers drums, and rhythm mode steals channels
6-8 to do it.
"""

import math

#: The AdLib crystal. Every OPL2 card used it, so the note tables in
#: thirty years of .sbi and .bnk files assume exactly this number.
NTSC_CLOCK = 3579545.0
#: The chip emits one sample every 72 clocks — 49716 Hz, an awkward rate
#: that is nonetheless the real one, and resampling from it is the
#: renderer's job rather than something to round away here.
SAMPLE_DIVIDER = 72

CHANNELS = 9
OPERATORS = 18

#: Register offset of each channel's two operators. The chip interleaves
#: its eighteen operator slots across nine channels in threes, and the
#: register map follows the slots, not the channels — channel 3's
#: modulator lives at offset 8, not 6. Same shape of trap as the YM2612's
#: op1/op3/op2/op4 ordering, and worth spelling out once here rather than
#: rediscovering it per register.
_CHANNEL_OFFSETS = tuple(((c // 3) * 8 + (c % 3),
                          (c // 3) * 8 + (c % 3) + 3) for c in range(CHANNELS))
#: The inverse: which (channel, operator) a register offset belongs to.
_OFFSET_TO_SLOT = {}
for _c, (_m, _car) in enumerate(_CHANNEL_OFFSETS):
    _OFFSET_TO_SLOT[_m] = (_c, 0)
    _OFFSET_TO_SLOT[_car] = (_c, 1)

REG_TEST = 0x01
REG_AM_VIB_EGT_KSR_MULT = 0x20
REG_KSL_TL = 0x40
REG_AR_DR = 0x60
REG_SL_RR = 0x80
REG_FNUM_LOW = 0xA0
REG_KEYON_BLOCK_FNUM = 0xB0
REG_DEPTH_RHYTHM = 0xBD
REG_FEEDBACK_CONNECTION = 0xC0
REG_WAVEFORM = 0xE0

#: Multiple, doubled so that "0" can mean x0.5 without fractions. 11, 13
#: and 14 are duplicated on hardware — the register has 16 values but the
#: chip only implements 13 of them.
_MULTIPLE = (1, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 20, 24, 24, 30, 30)

#: Key Scale Level: how much a note is attenuated for being high. Indexed
#: by the top FOUR bits of the ten-bit F-Number, so it has sixteen entries
#: — each doubling of the F-Number costs 8, which after the `<< 2` below
#: works out to the 6 dB per octave that KSL setting 3 is documented to
#: give.
_KSL_ROM = (0, 32, 40, 45, 48, 51, 53, 55, 56, 58, 59, 60, 61, 62, 63, 64)
#: KSL setting 0 means "none", and shifting right by 31 is how the
#: hardware spells that.
_KSL_SHIFT = (31, 1, 2, 0)


def _build_logsin():
    """-log2(sin(x)) * 256 over the first quarter period, 256 entries.

    The chip stores a quarter of a sine and mirrors it; everything about
    the waveform's character — including the fact that it is quantised at
    all — comes from this table's resolution.
    """
    return tuple(round(-math.log2(math.sin((i + 0.5) * math.pi / 512)) * 256)
                 for i in range(256))


def _build_exp():
    """The inverse: 2^(x/256) - 1, as a mantissa the caller shifts.

    Stored without its leading 1 — the lookup ORs 0x400 back in — because
    that is how the ROM is laid out, and it is why _exp_out reads the
    table backwards through `^ 0xFF`.
    """
    return tuple(round((2.0 ** (i / 256.0) - 1) * 1024) for i in range(256))


_LOGSIN = _build_logsin()
_EXP = _build_exp()

#: One envelope unit is this many dB. 512 units span the chip's full
#: 96 dB, and Total Level's 0.75 dB step is therefore four of them.
ENVELOPE_DB = 96.0 / 512.0
_ENV_MAX = 511.0


def _exp_out(level: int) -> int:
    """Attenuation -> linear magnitude, in the chip's own arithmetic.

    `level` is in 1/256ths of an octave of amplitude, so its high byte is
    a straight shift and its low byte is the fractional part the table
    resolves. Full scale is 4084, which is the OPL's real operator range.
    """
    if level > 0x1FFF:
        level = 0x1FFF
    return ((_EXP[(level & 0xFF) ^ 0xFF] | 0x400) << 1) >> (level >> 8)


def envelope_step(rate: int) -> float:
    """Envelope units per sample for an effective rate of 0-63.

    The hardware triggers a step every 2^(12 - rate/4) samples and varies
    the step between 0 and 1 on an eight-entry cycle selected by the low
    two bits; above rate 48 it stops dividing and starts multiplying
    instead. Averaging that cycle gives ((4 + lo) / 8) * 2^(hi - 12),
    which is this — the same curve, without carrying the counter's phase.
    """
    if rate <= 0:
        return 0.0
    rate = min(63, rate)
    hi, lo = rate >> 2, rate & 3
    if hi >= 15:
        return 4.0
    return ((4 + lo) / 8.0) * (2.0 ** (hi - 12))


class OPLOperator:
    """One of the two operators in an OPL2 channel.

    attack/decay/release: 0-15, 0 meaning "never" rather than "instantly"
    sustain_level: 0 (loudest) - 15, where 15 is the chip's special
        "all the way down" value rather than one step below 14
    total_level: 0 (loudest) - 63, in 0.75 dB steps
    key_scale_level: 0 none, 1 = 1.5 dB/octave, 2 = 3, 3 = 6
    waveform: 0 sine, 1 half-sine, 2 absolute sine, 3 pulse-sine
    sustaining: hold at the sustain level instead of continuing to decay
    """
    __slots__ = ("attack", "decay", "sustain_level", "release", "total_level",
                 "key_scale_level", "multiple", "waveform", "sustaining",
                 "key_scale_rate", "tremolo", "vibrato")

    def __init__(self, attack=15, decay=4, sustain_level=2, release=7,
                 total_level=0, key_scale_level=0, multiple=1, waveform=0,
                 sustaining=True, key_scale_rate=0, tremolo=0, vibrato=0):
        self.attack = attack
        self.decay = decay
        self.sustain_level = sustain_level
        self.release = release
        self.total_level = total_level
        self.key_scale_level = key_scale_level
        self.multiple = multiple
        self.waveform = waveform
        self.sustaining = sustaining
        self.key_scale_rate = key_scale_rate
        self.tremolo = tremolo
        self.vibrato = vibrato

    def copy(self) -> "OPLOperator":
        clone = OPLOperator()
        for field in OPLOperator.__slots__:
            setattr(clone, field, getattr(self, field))
        return clone


class OPLInstrument:
    """An OPL2 patch: two operators, feedback, and how they connect.

    connection 0 is FM — the modulator's output bends the carrier's phase,
    which is where every metallic OPL timbre comes from. connection 1 is
    additive: both operators reach the output directly, which on a two-op
    chip mostly buys you an organ.
    """
    __slots__ = ("modulator", "carrier", "feedback", "connection", "name",
                 "trim")

    def __init__(self, modulator=None, carrier=None, feedback=0,
                 connection=0, name="", trim=0):
        self.modulator = modulator or OPLOperator()
        self.carrier = carrier or OPLOperator()
        self.feedback = feedback
        self.connection = connection
        self.name = name
        self.trim = trim

    def copy(self) -> "OPLInstrument":
        return OPLInstrument(self.modulator.copy(), self.carrier.copy(),
                             self.feedback, self.connection, self.name,
                             self.trim)


def note_to_freq(note: str, octave: int) -> float:
    import events as events_mod
    midi = octave * 12 + events_mod.NOTE_NAMES.index(note)
    return 440.0 * 2.0 ** ((midi - 57) / 12.0)      # octave 4 holds A440


def freq_to_fnum_block(freq: float, clock: float = NTSC_CLOCK):
    """(f_num, block) for a frequency.

    f_num = freq * 72 * 2^(20 - block) / clock, and the block is chosen to
    keep f_num in the top half of its ten bits, where the chip's pitch
    resolution is finest.
    """
    for block in range(8):
        fnum = int(round(freq * SAMPLE_DIVIDER * (2 ** (20 - block)) / clock))
        if fnum < 1024:
            if fnum >= 512 or block == 0:
                return max(1, fnum), block
            # Too low for this block to be worth it, but a lower block
            # would overflow — take it anyway rather than fail.
            best = (max(1, fnum), block)
            return best
    raise ValueError(f"{freq} Hz is above what the OPL2 can address")


def fnum_block_to_freq(fnum: int, block: int, clock: float = NTSC_CLOCK):
    """What the chip will ACTUALLY play — the inverse, for callers that
    need the quantised pitch rather than the requested one."""
    return fnum * clock / (SAMPLE_DIVIDER * (2 ** (20 - block)))


#: Sustain level is 3 dB per step, and 15 is the chip's special "all the
#: way down" value (93 dB) rather than one step past 14.
def _sustain_units(level: int) -> float:
    return (31 if level >= 15 else level) * 16.0


class _Slot:
    """Runtime state for one operator: where it is in its cycle and envelope.

    Key scaling, the envelope step and the phase increment are all cached
    rather than recomputed per sample. They only change when the note, the
    patch or the envelope stage changes, and recomputing them 49716 times
    a second made this fifty times slower than real time.
    """
    __slots__ = ("op", "phase", "env", "state", "out", "previous", "channel",
                 "ksl", "ksr", "step", "tl4", "wave", "target", "increment",
                 "am")

    OFF, ATTACK, DECAY, SUSTAIN, RELEASE = range(5)

    def __init__(self, channel):
        self.channel = channel
        self.op = OPLOperator()
        self.phase = 0
        self.env = _ENV_MAX
        self.state = _Slot.OFF
        self.out = 0
        self.previous = 0
        self.ksl = 0.0
        self.ksr = 0
        self.step = 0.0
        self.tl4 = 0
        self.wave = 0
        self.am = 0
        self.target = 0.0
        self.increment = 0

    # -- cached derivations -------------------------------------------------
    def refresh(self):
        """Recompute everything that depends on the note or the patch."""
        channel = self.channel
        op = self.op
        ksv = (channel.block << 1) | ((channel.fnum >> 9) & 1)
        self.ksr = ksv if op.key_scale_rate else (ksv >> 2)

        ksl = (_KSL_ROM[channel.fnum >> 6] << 2) - ((8 - channel.block) << 5)
        self.ksl = float(max(0, ksl) >> _KSL_SHIFT[op.key_scale_level])

        self.tl4 = op.total_level * 4
        self.wave = op.waveform
        self.am = op.tremolo
        self.target = _sustain_units(op.sustain_level)
        base = (channel.fnum << channel.block) >> 1
        self.increment = (base * _MULTIPLE[op.multiple & 15]) >> 1
        self._restep()

    def _restep(self):
        op, ksr, state = self.op, self.ksr, self.state
        if state == _Slot.ATTACK:
            rate = op.attack
        elif state == _Slot.DECAY:
            rate = op.decay
        elif state == _Slot.SUSTAIN:
            rate = 0 if op.sustaining else op.release
        elif state == _Slot.RELEASE:
            rate = op.release
        else:
            rate = 0
        self.step = envelope_step(rate * 4 + ksr) if rate else 0.0

    def key_on(self):
        self.state = _Slot.ATTACK
        self.phase = 0                 # the hardware resets the phase too
        self.refresh()
        if not self.op.attack:
            # Attack rate 0 never rises, so the note would be silent. That
            # is genuinely what the chip does; leaving the envelope where
            # it is says so rather than faking an onset.
            self.env = _ENV_MAX

    def key_off(self):
        if self.state != _Slot.OFF:
            self.state = _Slot.RELEASE
            self._restep()


class _Channel:
    __slots__ = ("fnum", "block", "key_on", "feedback", "connection", "slots",
                 "instrument", "shift", "volume")

    def __init__(self):
        self.fnum = 0
        self.block = 0
        self.key_on = False
        self.feedback = 0
        self.connection = 0
        self.instrument = None
        self.shift = 0
        self.volume = 127
        self.slots = (_Slot(self), _Slot(self))

    def refresh(self):
        self.shift = (9 - self.feedback) if self.feedback else 0
        for slot in self.slots:
            slot.refresh()


class YM3812:
    """Nine channels of two-operator FM, mono, at 49716 Hz.

    The API deliberately mirrors opn2.YM2612 — set_instrument, note_on,
    note_off, render — so a score can move between the two chips without
    the renderer caring which one it is talking to.
    """

    def __init__(self, clock: float = NTSC_CLOCK, logger=None):
        self.clock = clock
        self.native_rate = clock / SAMPLE_DIVIDER
        self.logger = logger
        self.channels = [_Channel() for _ in range(CHANNELS)]
        self.tremolo_depth = 0          # 0 = 1.0 dB, 1 = 4.8 dB
        self.vibrato_depth = 0          # 0 = 7 cents, 1 = 14 cents
        self._lfo = 0
        #: -1 rather than 0, so the first write of any register is real
        #: even when its value happens to be zero.
        self._shadow = [-1] * 256

    def close(self):
        pass

    # -- registers ---------------------------------------------------------
    def write(self, address: int, value: int):
        """One register write, exactly as a driver would issue it.

        Everything else on this class goes through here. That is what makes
        a .vgm possible: the format stores register writes, so a chip
        driven by setting Python attributes has nothing to record. It also
        means the emulator and the log can never disagree about what was
        played, because they are the same writes.
        """
        address &= 0xFF
        value &= 0xFF
        # Shadow the register file and drop writes that change nothing.
        # Every hardware driver does this; here it also keeps a .vgm from
        # carrying a redundant Total Level write on every single note.
        if self._shadow[address] == value:
            return
        self._shadow[address] = value
        if self.logger is not None:
            self.logger(address, value)
        self._decode(address, value)

    def _decode(self, address: int, value: int):
        high = address & 0xF0
        offset = address & 0x1F

        if address == REG_DEPTH_RHYTHM:
            self.tremolo_depth = (value >> 7) & 1
            self.vibrato_depth = (value >> 6) & 1
            return
        if address < 0x20:
            return                       # test/timer registers: no audio effect

        if 0xA0 <= address <= 0xA8:
            chan = self.channels[address - 0xA0]
            chan.fnum = (chan.fnum & 0x300) | value
            chan.refresh()
            return
        if 0xB0 <= address <= 0xB8:
            chan = self.channels[address - 0xB0]
            chan.fnum = (chan.fnum & 0xFF) | ((value & 3) << 8)
            chan.block = (value >> 2) & 7
            keyed = bool(value & 0x20)
            was = chan.key_on
            chan.key_on = keyed
            chan.refresh()
            if keyed and not was:
                for slot in chan.slots:
                    slot.key_on()
            elif was and not keyed:
                for slot in chan.slots:
                    slot.key_off()
            return
        if 0xC0 <= address <= 0xC8:
            chan = self.channels[address - 0xC0]
            chan.feedback = (value >> 1) & 7
            chan.connection = value & 1
            chan.refresh()
            return

        located = _OFFSET_TO_SLOT.get(offset)
        if located is None:
            return                       # a gap in the slot map; the chip
        channel, index = located         # ignores these too
        slot = self.channels[channel].slots[index]
        op = slot.op

        if high == REG_AM_VIB_EGT_KSR_MULT:
            op.tremolo = (value >> 7) & 1
            op.vibrato = (value >> 6) & 1
            op.sustaining = bool((value >> 5) & 1)
            op.key_scale_rate = (value >> 4) & 1
            op.multiple = value & 15
        elif high == REG_KSL_TL:
            op.key_scale_level = (value >> 6) & 3
            op.total_level = value & 63
        elif high == REG_AR_DR:
            op.attack = (value >> 4) & 15
            op.decay = value & 15
        elif high == REG_SL_RR:
            op.sustain_level = (value >> 4) & 15
            op.release = value & 15
        elif high in (0xE0, 0xF0):
            op.waveform = value & 3
        else:
            return
        slot.refresh()

    # -- voice control -----------------------------------------------------
    def set_instrument(self, channel: int, instrument: OPLInstrument):
        chan = self.channels[channel]
        chan.instrument = instrument
        modulator_offset, carrier_offset = _CHANNEL_OFFSETS[channel]
        for offset, op in ((modulator_offset, instrument.modulator),
                           (carrier_offset, instrument.carrier)):
            self.write(REG_AM_VIB_EGT_KSR_MULT + offset,
                       (op.tremolo << 7) | (op.vibrato << 6)
                       | ((1 if op.sustaining else 0) << 5)
                       | (op.key_scale_rate << 4) | (op.multiple & 15))
            self.write(REG_KSL_TL + offset,
                       ((op.key_scale_level & 3) << 6) | (op.total_level & 63))
            self.write(REG_AR_DR + offset,
                       ((op.attack & 15) << 4) | (op.decay & 15))
            self.write(REG_SL_RR + offset,
                       ((op.sustain_level & 15) << 4) | (op.release & 15))
            self.write(REG_WAVEFORM + offset, op.waveform & 3)
        self.write(REG_FEEDBACK_CONNECTION + channel,
                   ((instrument.feedback & 7) << 1) | (instrument.connection & 1))
        self._write_levels(channel, chan.volume, 127)

    def _write_levels(self, channel: int, volume: int, velocity: int):
        """Push Total Level for whatever reaches the output on this channel.

        Velocity, channel volume and the patch's calibration trim all
        compose here, and all of them land on the carrier only when the
        operators are in FM: attenuating a modulator changes how bright the
        voice is, not how loud, so a fader that touched it would not be a
        fader. In additive mode both operators are heard, so both move.
        """
        chan = self.channels[channel]
        instrument = chan.instrument
        if instrument is None:
            return
        extra = (_velocity_steps(velocity) + _velocity_steps(volume)
                 + instrument.trim)
        modulator_offset, carrier_offset = _CHANNEL_OFFSETS[channel]
        targets = ((modulator_offset, instrument.modulator),
                   (carrier_offset, instrument.carrier)) if chan.connection \
            else ((carrier_offset, instrument.carrier),)
        for offset, op in targets:
            level = max(0, min(63, op.total_level + extra))
            self.write(REG_KSL_TL + offset,
                       ((op.key_scale_level & 3) << 6) | level)

    def note_on(self, channel: int, note: str, octave: int, velocity: int = 127):
        chan = self.channels[channel]
        fnum, block = freq_to_fnum_block(note_to_freq(note, octave), self.clock)
        self._write_levels(channel, chan.volume, velocity)
        self.write(REG_FNUM_LOW + channel, fnum & 0xFF)
        # Key-off before key-on, so a retrigger restarts the envelope the
        # way the hardware does rather than sliding the pitch of a note
        # that is already sounding.
        self.write(REG_KEYON_BLOCK_FNUM + channel,
                   ((block & 7) << 2) | ((fnum >> 8) & 3))
        self.write(REG_KEYON_BLOCK_FNUM + channel,
                   0x20 | ((block & 7) << 2) | ((fnum >> 8) & 3))

    def note_off(self, channel: int):
        chan = self.channels[channel]
        self.write(REG_KEYON_BLOCK_FNUM + channel,
                   ((chan.block & 7) << 2) | ((chan.fnum >> 8) & 3))

    def set_volume(self, channel: int, volume: int):
        """Channel volume, 0-127, on the FM bank's linear-in-amplitude scale.

        The OPL2 has no channel volume register — the only way down is the
        carrier's Total Level, so that is what this moves. In FM the
        modulator is left alone deliberately: attenuating it would change
        how bright the voice is, not how loud, and a fader that alters the
        timbre is not a fader. In additive mode both operators reach the
        output, so both move together.
        """
        chan = self.channels[channel]
        chan.volume = max(0, min(127, volume))
        self._write_levels(channel, chan.volume, 127)

    def set_pitch(self, channel: int, frequency: float):
        """Retune without retriggering — the OPL's own portamento."""
        chan = self.channels[channel]
        fnum, block = freq_to_fnum_block(frequency, self.clock)
        self.write(REG_FNUM_LOW + channel, fnum & 0xFF)
        self.write(REG_KEYON_BLOCK_FNUM + channel,
                   (0x20 if chan.key_on else 0) | ((block & 7) << 2)
                   | ((fnum >> 8) & 3))

    def silence(self):
        for index in range(CHANNELS):
            self.note_off(index)

    # -- rendering ---------------------------------------------------------
    def render(self, n_samples: int):
        """n mono samples, as floats in -1..1.

        Written as one flat loop with everything bound to locals. The
        readable version of this — a method call per operator per sample —
        ran at about a fiftieth of real time, which is the difference
        between usable and not.
        """
        logsin = _LOGSIN
        exp = _EXP
        out = []
        append = out.append

        wants_lfo = any(slot.am or slot.op.vibrato
                        for chan in self.channels for slot in chan.slots)
        live = [chan for chan in self.channels
                if chan.slots[0].state != _Slot.OFF
                or chan.slots[1].state != _Slot.OFF]

        ATTACK, DECAY, SUSTAIN, RELEASE, OFF = (
            _Slot.ATTACK, _Slot.DECAY, _Slot.SUSTAIN, _Slot.RELEASE, _Slot.OFF)

        for _ in range(n_samples):
            self._lfo += 1
            if wants_lfo:
                tremolo = self._tremolo()
                vibrato = self._vibrato()
            else:
                tremolo = 0.0
                vibrato = 0.0

            total = 0
            for chan in live:
                modulator, carrier = chan.slots
                if modulator.state == OFF and carrier.state == OFF:
                    continue

                if vibrato:
                    base = (chan.fnum << chan.block) >> 1
                    scaled = int(base * vibrato)
                    modulator.phase = (modulator.phase + (
                        (scaled * _MULTIPLE[modulator.op.multiple & 15]) >> 1)) & 0xFFFFF
                    carrier.phase = (carrier.phase + (
                        (scaled * _MULTIPLE[carrier.op.multiple & 15]) >> 1)) & 0xFFFFF
                else:
                    modulator.phase = (modulator.phase + modulator.increment) & 0xFFFFF
                    carrier.phase = (carrier.phase + carrier.increment) & 0xFFFFF

                # Modulator, then carrier. Unrolled on purpose: the two
                # differ only in what feeds their phase, and looping over
                # them meant carrying the modulator's output forward in a
                # variable that outlived its branch — correct by accident
                # and unreadable on purpose-inspection.
                shift = chan.shift
                feedback = ((modulator.previous + modulator.out) >> shift) \
                    if shift else 0

                slot = modulator
                if slot.state == OFF:
                    slot.previous, slot.out = slot.out, 0
                else:
                    phase = ((slot.phase >> 9) + feedback) & 0x3FF
                    wave = slot.wave
                    negate = phase & 0x200 if wave == 0 else 0
                    muted = False
                    if wave == 1:
                        muted = phase & 0x200
                    elif wave == 2:
                        phase &= 0x1FF
                    elif wave == 3:
                        muted = phase & 0x100
                        phase &= 0x1FF
                    if muted:
                        slot.previous, slot.out = slot.out, 0
                    else:
                        index = phase & 0xFF
                        if phase & 0x100:
                            index ^= 0xFF      # the ROM holds a quarter period
                        level = logsin[index] + (int(
                            slot.env + slot.tl4 + slot.ksl
                            + (tremolo if slot.am else 0.0)) << 3)
                        if level > 0x1FFF:
                            level = 0x1FFF
                        value = ((exp[(level & 0xFF) ^ 0xFF] | 0x400) << 1) \
                            >> (level >> 8)
                        slot.previous, slot.out = slot.out, \
                            (-value if negate else value)

                slot = carrier
                modulation = 0 if chan.connection else modulator.out
                if slot.state == OFF:
                    slot.previous, slot.out = slot.out, 0
                else:
                    phase = ((slot.phase >> 9) + modulation) & 0x3FF
                    wave = slot.wave
                    negate = phase & 0x200 if wave == 0 else 0
                    muted = False
                    if wave == 1:
                        muted = phase & 0x200
                    elif wave == 2:
                        phase &= 0x1FF
                    elif wave == 3:
                        muted = phase & 0x100
                        phase &= 0x1FF
                    if muted:
                        slot.previous, slot.out = slot.out, 0
                    else:
                        index = phase & 0xFF
                        if phase & 0x100:
                            index ^= 0xFF
                        level = logsin[index] + (int(
                            slot.env + slot.tl4 + slot.ksl
                            + (tremolo if slot.am else 0.0)) << 3)
                        if level > 0x1FFF:
                            level = 0x1FFF
                        value = ((exp[(level & 0xFF) ^ 0xFF] | 0x400) << 1) \
                            >> (level >> 8)
                        slot.previous, slot.out = slot.out, \
                            (-value if negate else value)

                total += (modulator.out + carrier.out) if chan.connection \
                    else carrier.out

                # -- envelopes, once both operators have produced a sample
                for slot in chan.slots:
                    state = slot.state
                    if state == OFF:
                        continue
                    if state == ATTACK:
                        if slot.step:
                            slot.env -= (slot.env + 1.0) * slot.step * 0.125
                        if slot.env <= 0.0:
                            slot.env = 0.0
                            slot.state = DECAY
                            slot._restep()
                    elif state == DECAY:
                        slot.env += slot.step
                        if slot.env >= slot.target:
                            slot.env = slot.target
                            slot.state = SUSTAIN
                            slot._restep()
                    else:
                        slot.env += slot.step
                        if slot.env >= _ENV_MAX:
                            slot.env = _ENV_MAX
                            slot.state = OFF

            # Nine channels at 4084 each would clip; dividing by twice the
            # chip's full-scale reading puts one channel at full tilt near
            # 0.5 and leaves a full mix somewhere to go.
            append(total / 8192.0)

        import audio
        return audio.from_floats(out, 1)

    def _tremolo(self) -> float:
        """A ~3.7 Hz triangle, 1.0 or 4.8 dB deep, in envelope units."""
        period = self.native_rate / 3.7
        position = (self._lfo % period) / period
        triangle = 2.0 * position if position < 0.5 else 2.0 * (1.0 - position)
        return triangle * (25.6 if self.tremolo_depth else 5.3)

    def _vibrato(self) -> float:
        """A ~6.1 Hz sine, 7 or 14 cents deep, as a frequency multiplier."""
        period = self.native_rate / 6.1
        cents = 14.0 if self.vibrato_depth else 7.0
        return 2.0 ** (cents * math.sin(2 * math.pi * (self._lfo % period) / period)
                       / 1200.0)


def _velocity_steps(velocity: int) -> int:
    """MIDI-ish 0-127 -> extra Total Level steps, linear in amplitude.

    Same reasoning as opn2.level_to_attenuation: halving the number should
    cost about 6 dB, not make everything below 100 inaudible.
    """
    if velocity <= 0:
        return 63
    if velocity >= 127:
        return 0
    db = -20.0 * math.log10(velocity / 127.0)
    return max(0, min(63, int(round(db / 0.75))))
