"""
fallback/psg.py — the Sega PSG in pure Python.

Same model as core/psg.c: three 10-bit down-counters driving toggle
flip-flops, one 16-bit LFSR with taps on bits 0 and 3 for white noise and
bit 0 alone for periodic, the published 4-bit / -2 dB attenuation table.

The difference is arithmetic, not semantics. psg.c steps every one of the
chip's 223,721 ticks per second because in C that is free. Doing that in
Python would cost roughly a second of CPU per second of audio, so instead
each output sample integrates the square wave over the ticks it spans:

    a tone channel toggles every N ticks, so over a window of T ticks the
    output is high for a computable fraction of the time

which is both faster (a handful of toggles per sample rather than five
ticks x four channels) and better behaved, because integrating over the
window is a box filter — the hard edges alias far less than point-sampling
them would. The register semantics, the LFSR, and the volume table are
identical to the C core.
"""

from array import array

import audio as _audio

NTSC_PSG_CLOCK = 3_579_545

#: 4-bit attenuator, -2 dB per step, 15 = silence. Same table as psg.c.
VOLUME_TABLE = (32767, 26028, 20675, 16422, 13045, 10362, 8231, 6568,
                5193, 4125, 3277, 2603, 2067, 1642, 1304, 0)

#: Noise counter reload for modes 0-2; mode 3 follows tone channel 2.
NOISE_RELOAD = (0x10, 0x20, 0x40)


class PyPSG:
    """Drop-in for the ctypes SN76489 handle used by sn76489.SN76489."""

    def __init__(self, clock: float = NTSC_PSG_CLOCK):
        self.clock = float(clock)
        self.rate = self.clock / 16.0
        self.reset()

    def reset(self):
        self.tone_reg = [0, 0, 0]
        self.vol_reg = [0x0F] * 4
        self.noise_reg = 0
        self.tone_phase = [0.0, 0.0, 0.0]   # ticks until the next toggle
        self.tone_output = [1, 1, 1]
        self.noise_phase = 0.0
        self.noise_ff = 0
        self.lfsr = 0x8000
        self._latched_channel = 0
        self._latched_type = 0

    # -- bus ---------------------------------------------------------------
    def write(self, byte: int):
        byte &= 0xFF
        if byte & 0x80:
            channel = (byte >> 5) & 0x03
            type_ = (byte >> 4) & 0x01
            data = byte & 0x0F
            self._latched_channel = channel
            self._latched_type = type_
            if type_:
                self.vol_reg[channel] = data
            elif channel == 3:
                self.noise_reg = data & 0x07
                self.lfsr = 0x8000
            else:
                self.tone_reg[channel] = (self.tone_reg[channel] & 0x3F0) | data
        else:
            data6 = byte & 0x3F
            if self._latched_type:
                self.vol_reg[self._latched_channel] = data6 & 0x0F
            elif self._latched_channel == 3:
                self.noise_reg = data6 & 0x07
                self.lfsr = 0x8000
            else:
                channel = self._latched_channel
                self.tone_reg[channel] = (self.tone_reg[channel] & 0x00F) | (data6 << 4)

    # -- LFSR --------------------------------------------------------------
    def _shift_lfsr(self):
        if self.noise_reg & 0x04:                 # white: parity of bits 0 and 3
            v = self.lfsr & 0x0009
            v ^= v >> 8
            v ^= v >> 4
            v ^= v >> 2
            v ^= v >> 1
            feedback = v & 1
        else:                                     # periodic: bit 0 straight back
            feedback = self.lfsr & 1
        self.lfsr = ((self.lfsr >> 1) | (feedback << 15)) & 0xFFFF

    # -- rendering ---------------------------------------------------------
    def render(self, n_samples: int):
        """n_samples of mono float audio at self.rate."""
        if n_samples <= 0:
            return _audio.zeros(0, 1)

        data = array("f", bytes(4 * n_samples))

        ticks_per_sample = 1.0    # this core's rate IS the chip tick rate
        levels = [VOLUME_TABLE[v] / 32768.0 for v in self.vol_reg]
        periods = []
        for ch in range(3):
            reg = self.tone_reg[ch]
            # N <= 1 holds the output high instead of oscillating. That is
            # not a guard clause, it is the documented behaviour that makes
            # PSG sample playback possible at all.
            periods.append(float(reg) if reg > 1 else 0.0)

        noise_mode = self.noise_reg & 0x03
        noise_period = float(NOISE_RELOAD[noise_mode]) if noise_mode < 3 else \
            float(self.tone_reg[2] if self.tone_reg[2] > 0 else 1)

        for i in range(n_samples):
            total = 0.0
            for ch in range(3):
                period = periods[ch]
                if period <= 0.0:
                    total += levels[ch]           # DC high
                    continue
                total += levels[ch] * self._integrate_tone(ch, period,
                                                           ticks_per_sample)
            total += levels[3] * self._integrate_noise(noise_period,
                                                       ticks_per_sample)
            data[i] = total * 0.25                # /4, same headroom as psg.c
        return _audio.from_floats(data, 1)

    def _integrate_tone(self, ch: int, period: float, window: float) -> float:
        """Mean of the +/-1 square over one output sample."""
        remaining = window
        phase = self.tone_phase[ch]
        level = self.tone_output[ch]
        accumulated = 0.0
        while remaining > 0.0:
            if phase <= 0.0:
                phase = period
                level ^= 1
            span = phase if phase < remaining else remaining
            accumulated += span if level else -span
            phase -= span
            remaining -= span
        self.tone_phase[ch] = phase
        self.tone_output[ch] = level
        return accumulated / window

    def _integrate_noise(self, period: float, window: float) -> float:
        remaining = window
        phase = self.noise_phase
        accumulated = 0.0
        while remaining > 0.0:
            if phase <= 0.0:
                phase = period
                previous = self.noise_ff
                self.noise_ff ^= 1
                if previous == 0 and self.noise_ff == 1:
                    self._shift_lfsr()
            span = phase if phase < remaining else remaining
            accumulated += span if (self.lfsr & 1) else -span
            phase -= span
            remaining -= span
        self.noise_phase = phase
        return accumulated / window
