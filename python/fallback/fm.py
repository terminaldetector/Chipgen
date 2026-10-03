"""
fallback/fm.py — an operator-level YM2612, in pure Python.

READ THIS FIRST: this is an APPROXIMATION, and the project's whole pitch
is that it does not approximate. core/ym3438.c (Nuked-OPN2) is a
cycle-accurate model derived from die photographs; this is a
per-sample-per-operator model written so that chipgen still makes FM sound
in a sandbox with no C compiler. Use it as a floor, not as the thing.

What IS taken from the real chip, unchanged:

  * the log-sin and exp ROMs, generated from the same formulas the ROM
    tables tabulate, so the operator waveform and the attenuation curve
    are the chip's, not a math.sin() stand-in;
  * phase increment `(fnum << block) >> 1` scaled by MUL, the DT1 detune
    table, and the key-code derivation, so it plays in tune with the
    native core rather than merely in tune with itself;
  * the algorithm routing table lifted straight out of ym3438.c, including
    the one-sample pipeline delays on op1 and op2 feedback paths;
  * envelope rates via the standard `2*R + key-scale` formula, with attack
    as an exponential approach and decay/release linear in attenuation;
  * the >>5, clamp-to-9-bits channel accumulator, so levels match the
    native core closely enough to swap backends mid-project.

What is NOT here, and will sound different:

  * cycle accuracy of any kind — no bus timing, no busy flag, no 24-slot
    pipeline, no DAC ladder;
  * the LFO (register 0x22 is accepted and ignored), so AMS/PMS do nothing;
  * SSG-EG;
  * the envelope counter's exact 4-phase increment pattern; rates are
    right on average, individual steps are smoothed.

Speed is roughly 3-8x slower than realtime per active channel on CPython.
Silent channels cost nothing, which is what keeps it usable.
"""

import math
from array import array

import audio as _audio

NTSC_CHIP_CLOCK = 53_693_175 / 7

# --------------------------------------------------------------------------
# Chip ROMs, generated rather than transcribed (verified against ym3438.c)
# --------------------------------------------------------------------------
#: -log2(sin(x)) * 256 over the first quarter of a sine, 256 steps.
LOGSIN = tuple(
    int(round(-math.log(math.sin((i + 0.5) * math.pi / 512.0), 2) * 256.0))
    for i in range(256)
)
#: 2^(x/256) * 1024 - 1024. Indexed with the attenuation's low byte INVERTED.
EXP = tuple(int(round((2.0 ** (i / 256.0)) * 1024.0)) - 1024 for i in range(256))

#: DT1 detune, indexed [dt & 3][key code 0-31]. dt bit 2 is the sign.
DETUNE = (
    (0,) * 32,
    (0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2,
     2, 3, 3, 3, 4, 4, 4, 5, 5, 6, 6, 7, 8, 8, 8, 8),
    (1, 1, 1, 1, 2, 2, 2, 2, 2, 3, 3, 3, 4, 4, 4, 5,
     5, 6, 6, 7, 8, 8, 9, 10, 11, 12, 13, 14, 16, 16, 16, 16),
    (2, 2, 2, 2, 2, 3, 3, 3, 4, 4, 4, 5, 5, 6, 6, 7,
     8, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 20, 22, 22, 22, 22),
)

#: Straight out of ym3438.c's fm_algorithm[4][6][8]. First index is the
#: operator's PIPELINE position, which runs op1, op3, op2, op4 — the same
#: interleave as the register offsets. Rows are, in order:
#:   0: modulate with op1's output from 1 sample ago
#:   1: modulate with op1's output from 2 samples ago
#:   2: modulate with op2's output from the previous sample
#:   3: modulate with the operator two pipeline positions back (mod2)
#:   4: same source, into mod1
#:   5: this operator is a carrier
FM_ALGORITHM = (
    ((1,) * 8, (1,) * 8, (0,) * 8, (0,) * 8, (0,) * 8,
     (0, 0, 0, 0, 0, 0, 0, 1)),
    ((0, 1, 0, 0, 0, 1, 0, 0), (0,) * 8, (1, 1, 1, 0, 0, 0, 0, 0),
     (0,) * 8, (0,) * 8, (0, 0, 0, 0, 0, 1, 1, 1)),
    ((0,) * 8, (0,) * 8, (0,) * 8, (1, 0, 0, 1, 1, 1, 1, 0),
     (0,) * 8, (0, 0, 0, 0, 1, 1, 1, 1)),
    ((0, 0, 1, 0, 0, 1, 0, 0), (0,) * 8, (0, 0, 0, 1, 0, 0, 0, 0),
     (1, 1, 0, 1, 1, 0, 0, 0), (0, 0, 1, 0, 0, 0, 0, 0), (1,) * 8),
)

ATTACK, DECAY, SUSTAIN, RELEASE, OFF = 0, 1, 2, 3, 4
MAX_ATTENUATION = 1023.0
#: The envelope generator advances once every three output samples.
EG_DIVIDER = 3


def _eg_step(rate: int) -> float:
    """Attenuation units added per envelope tick for an effective rate 0-63.

    The hardware picks from a table of 4-step increment patterns; this is
    that table's average, which gets the decay TIME right while smoothing
    the individual steps. Capped at 8, the hardware maximum for a
    non-attack phase.
    """
    if rate <= 0:
        return 0.0
    rate = min(63, rate)
    return min(8.0, (4 + (rate & 3)) / 8.0 * (2.0 ** ((rate >> 2) - 11)))


class _Operator:
    __slots__ = ("detune", "multiple", "total_level", "attack_rate",
                 "decay_rate", "sustain_rate", "release_rate", "sustain_level",
                 "rate_scaling", "phase", "increment", "level", "state",
                 "output")

    def __init__(self):
        self.detune = 0
        self.multiple = 1
        self.total_level = 0
        self.attack_rate = 31
        self.decay_rate = 0
        self.sustain_rate = 0
        self.release_rate = 15
        self.sustain_level = 0
        self.rate_scaling = 0
        self.phase = 0
        self.increment = 0
        self.level = MAX_ATTENUATION
        self.state = OFF
        self.output = 0


class _Channel:
    __slots__ = ("ops", "algorithm", "feedback", "fnum", "block", "keycode",
                 "pan_left", "pan_right", "keyed", "op1_history", "op2_previous")

    def __init__(self):
        self.ops = [_Operator() for _ in range(4)]
        self.algorithm = 0
        self.feedback = 0
        self.fnum = 0
        self.block = 0
        self.keycode = 0
        self.pan_left = True
        self.pan_right = True
        self.keyed = False
        self.op1_history = [0, 0]
        self.op2_previous = 0

    def active(self) -> bool:
        return self.keyed or any(op.state != OFF for op in self.ops)


class PyYM2612:
    """Drop-in for the ctypes YM2612 handle used by opn2.YM2612."""

    def __init__(self, clock: float = NTSC_CHIP_CLOCK):
        self.clock = float(clock)
        self.rate = self.clock / 6.0 / 24.0
        self.channels = [_Channel() for _ in range(6)]
        self.dac_enabled = False
        self.dac_value = 0
        self._eg_phase = 0

    # -- bus ---------------------------------------------------------------
    def write(self, port: int, addr: int, data: int):
        bank = 1 if port >= 2 else 0
        addr &= 0xFF
        data &= 0xFF

        if bank == 0 and addr < 0x30:
            self._write_global(addr, data)
            return
        if addr < 0x30:
            return

        if addr < 0xA0:
            index = addr & 3
            if index == 3:
                return                       # no channel 4 within a bank
            channel = self.channels[bank * 3 + index]
            op = channel.ops[(addr >> 2) & 3]
            group = addr & 0xF0
            if group == 0x30:
                op.detune = (data >> 4) & 7
                op.multiple = data & 0xF
                self._update_increment(channel, op)
            elif group == 0x40:
                op.total_level = data & 0x7F
            elif group == 0x50:
                op.rate_scaling = (data >> 6) & 3
                op.attack_rate = data & 0x1F
            elif group == 0x60:
                op.decay_rate = data & 0x1F
            elif group == 0x70:
                op.sustain_rate = data & 0x1F
            elif group == 0x80:
                op.sustain_level = (data >> 4) & 0xF
                op.release_rate = data & 0xF
            # 0x90 (SSG-EG) is accepted and ignored, see module docstring
            return

        index = addr & 3
        if index == 3:
            return
        channel = self.channels[bank * 3 + index]
        group = addr & 0xFC
        if group == 0xA0:
            channel.fnum = (channel.fnum & 0x700) | data
            self._refresh_pitch(channel)
        elif group == 0xA4:
            channel.fnum = (channel.fnum & 0x0FF) | ((data & 7) << 8)
            channel.block = (data >> 3) & 7
            self._refresh_pitch(channel)
        elif group == 0xB0:
            channel.algorithm = data & 7
            channel.feedback = (data >> 3) & 7
        elif group == 0xB4:
            channel.pan_left = bool(data & 0x80)
            channel.pan_right = bool(data & 0x40)

    def _write_global(self, addr: int, data: int):
        if addr == 0x28:
            index = data & 7
            if index == 3 or index == 7:
                return
            channel = self.channels[index if index < 3 else index - 1]
            self._key(channel, (data >> 4) & 0xF)
        elif addr == 0x2A:
            self.dac_value = data
        elif addr == 0x2B:
            self.dac_enabled = bool(data & 0x80)
        # 0x22 (LFO) and the timer registers are accepted and ignored

    # -- voice state -------------------------------------------------------
    def _key(self, channel: _Channel, mask: int):
        channel.keyed = mask != 0
        for i, op in enumerate(channel.ops):
            on = bool(mask & (1 << i))
            if on and op.state in (OFF, RELEASE):
                op.state = ATTACK
                op.phase = 0
                if op.level >= MAX_ATTENUATION:
                    op.level = MAX_ATTENUATION
            elif not on and op.state != OFF:
                op.state = RELEASE

    def _refresh_pitch(self, channel: _Channel):
        fnum = channel.fnum
        f11 = (fnum >> 10) & 1
        f10 = (fnum >> 9) & 1
        f9 = (fnum >> 8) & 1
        f8 = (fnum >> 7) & 1
        # The chip derives a 5-bit "key code" from block plus the top of the
        # F-number; key scaling and detune both index off it, which is why a
        # patch gets brighter and snappier as you play up the keyboard.
        n3 = (f11 & (f10 | f9 | f8)) | ((1 - f11) & f10 & f9 & f8)
        channel.keycode = (channel.block << 2) | (f11 << 1) | n3
        for op in channel.ops:
            self._update_increment(channel, op)

    def _update_increment(self, channel: _Channel, op: "_Operator"):
        base = (channel.fnum << channel.block) >> 1
        detune = DETUNE[op.detune & 3][channel.keycode]
        if op.detune & 4:
            base -= detune
        else:
            base += detune
        base &= 0x1FFFF
        if op.multiple == 0:
            op.increment = base >> 1
        else:
            op.increment = (base * op.multiple) & 0xFFFFF

    def _effective_rate(self, channel: _Channel, op: "_Operator", rate: int) -> int:
        if rate == 0:
            return 0
        ksv = channel.keycode >> (3 - op.rate_scaling)
        return min(63, rate * 2 + ksv)

    # -- envelope ----------------------------------------------------------
    def _advance_envelope(self, channel: _Channel, op: "_Operator"):
        state = op.state
        if state == OFF:
            return
        if state == ATTACK:
            rate = self._effective_rate(channel, op, op.attack_rate)
            if rate >= 62:
                op.level = 0.0
            else:
                step = min(16.0, _eg_step(rate) * 2.0)
                op.level -= (op.level + 1.0) * step / 16.0
            if op.level <= 0.0:
                op.level = 0.0
                op.state = DECAY
            return
        if state == DECAY:
            op.level += _eg_step(self._effective_rate(channel, op, op.decay_rate))
            threshold = op.sustain_level * 32.0 if op.sustain_level < 15 \
                else MAX_ATTENUATION
            if op.level >= threshold:
                op.level = threshold
                op.state = SUSTAIN
        elif state == SUSTAIN:
            op.level += _eg_step(self._effective_rate(channel, op, op.sustain_rate))
        else:  # RELEASE
            # RR is 4 bits and behaves as if it were the 5-bit rate 2*RR+1.
            rate = self._effective_rate(channel, op, op.release_rate * 2 + 1)
            op.level += _eg_step(rate)
        if op.level >= MAX_ATTENUATION:
            op.level = MAX_ATTENUATION
            if op.state == RELEASE:
                op.state = OFF

    # -- operator ----------------------------------------------------------
    @staticmethod
    def _operator_output(op: "_Operator", modulation: int) -> int:
        attenuation = int(op.level) + (op.total_level << 3)
        if attenuation >= 1023:
            return 0
        phase = ((op.phase >> 10) + modulation) & 0x3FF
        quarter = phase & 0xFF
        if phase & 0x100:
            quarter ^= 0xFF
        level = LOGSIN[quarter] + (attenuation << 2)
        if level > 0x1FFF:
            return 0
        out = ((EXP[(level & 0xFF) ^ 0xFF] | 0x400) << 2) >> (level >> 8)
        return -out if (phase & 0x200) else out

    # -- rendering ---------------------------------------------------------
    def render(self, n_samples: int):
        if n_samples <= 0:
            return _audio.zeros(0, 2)
        data = array("f", bytes(8 * n_samples))

        # 6 channels * 3 (the DAC model's per-clock gain) * 7 (the wrapper's
        # int16 scale) / 32768 -- chosen so this core and the native one
        # produce the same loudness and can be swapped without remixing.
        scale = 21.0 / 32768.0

        for i in range(n_samples):
            self._eg_phase += 1
            tick_eg = self._eg_phase >= EG_DIVIDER
            if tick_eg:
                self._eg_phase = 0

            left = right = 0
            for index, channel in enumerate(self.channels):
                if index == 5 and self.dac_enabled:
                    value = (self.dac_value - 128) << 1   # 8-bit unsigned -> 9-bit signed
                    if channel.pan_left:
                        left += value
                    if channel.pan_right:
                        right += value
                    continue
                if not channel.active():
                    continue
                value = self._channel_sample(channel, tick_eg)
                if channel.pan_left:
                    left += value
                if channel.pan_right:
                    right += value

            data[i * 2] = left * scale
            data[i * 2 + 1] = right * scale
        return _audio.from_floats(data, 2)

    def _channel_sample(self, channel: _Channel, tick_eg: bool) -> int:
        ops = channel.ops
        algorithm = channel.algorithm
        routing = FM_ALGORITHM

        if tick_eg:
            for op in ops:
                self._advance_envelope(channel, op)

        history = channel.op1_history
        op2_previous = channel.op2_previous
        outputs = [0, 0, 0, 0]

        for position in range(4):
            op = ops[position]
            op.phase = (op.phase + op.increment) & 0xFFFFF
            table = routing[position]
            mod1 = mod2 = 0
            if table[0][algorithm]:
                mod2 |= history[0]
            if table[1][algorithm]:
                mod1 |= history[1]
            if table[2][algorithm]:
                mod1 |= op2_previous
            if table[3][algorithm]:
                mod2 |= outputs[position - 2]
            if table[4][algorithm]:
                mod1 |= outputs[position - 2]
            modulation = mod1 + mod2
            if position == 0:
                modulation = (modulation >> (10 - channel.feedback)) \
                    if channel.feedback else 0
            else:
                modulation >>= 1
            outputs[position] = self._operator_output(op, modulation)

        history[1] = history[0]
        history[0] = outputs[0]
        channel.op2_previous = outputs[2]

        total = 0
        for position in range(4):
            if routing[position][5][algorithm]:
                total += outputs[position] >> 5
        # The channel bus is 9 bits wide; overdriving it clips on hardware
        # too, and that clip is part of how loud FM patches sound.
        if total > 255:
            return 255
        if total < -256:
            return -256
        return total
