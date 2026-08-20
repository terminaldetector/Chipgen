"""
sn76489.py — Python control layer for the Sega PSG (SN76489).

Native rate = chip_clock / 16 (the chip's internal working rate; each
channel's counter decrements once per tick at this rate). For NTSC:
3,579,545 / 16 = 223,721.5625 Hz.

Like opn2.py, every byte written to the chip can be mirrored to a
`logger` callback, which is what lets vgm.py record the exact bus traffic
of a performance rather than reconstructing it afterwards.
"""

import ctypes

import audio as _audio
import core_loader
from opn2 import _NOTE_OFFSETS  # reuse the same note-name table

NTSC_PSG_CLOCK = 3_579_545
PAL_PSG_CLOCK = 3_546_893
NATIVE_RATE = NTSC_PSG_CLOCK / 16  # ~223721.5625 Hz

#: The chip's 4-bit attenuator, in dB. Register value 15 is a hard mute,
#: everything else is -2 dB per step. Handy for writing volume ramps that
#: sound linear rather than lurching.
ATTENUATION_DB = tuple(-2.0 * v for v in range(15)) + (float("-inf"),)


def note_to_freq(note: str, octave: int) -> float:
    semitones = _NOTE_OFFSETS[note] + (octave - 4) * 12
    return 440.0 * (2.0 ** (semitones / 12.0))


def freq_to_tone_n(freq: float, clock: float = NTSC_PSG_CLOCK) -> int:
    """freq -> 10-bit tone register value N (freq = clock / (32*N))."""
    n = round(clock / (32.0 * freq))
    return max(1, min(1023, n))


def tone_n_to_freq(n: int, clock: float = NTSC_PSG_CLOCK) -> float:
    """Inverse of freq_to_tone_n — what the chip will ACTUALLY play.

    The register is 10 bits, so high notes quantise hard: above about C6
    consecutive semitones start landing on the same N. Worth checking
    before blaming the tuning on the emulator.
    """
    return clock / (32.0 * max(1, n))


class _Lib:
    _lib = None

    @classmethod
    def get(cls):
        if cls._lib is None:
            lib = core_loader.load("libpsg")
            if lib is None:
                return None
            lib.psg_new.restype = ctypes.c_void_p
            lib.psg_new.argtypes = []
            lib.psg_free.argtypes = [ctypes.c_void_p]
            lib.psg_reset.argtypes = [ctypes.c_void_p]
            lib.psg_write.argtypes = [ctypes.c_void_p, ctypes.c_ubyte]
            lib.psg_render.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                        ctypes.POINTER(ctypes.c_short)]
            cls._lib = lib
        return cls._lib


class SN76489:
    """Sega PSG: 3 square/tone channels (0-2) + 1 noise channel (3)."""

    NOISE_RATE_LOW, NOISE_RATE_MED, NOISE_RATE_HIGH, NOISE_RATE_TONE2 = 0, 1, 2, 3

    def __init__(self, clock: float = NTSC_PSG_CLOCK, logger=None):
        self.clock = clock
        self.native_rate = clock / 16
        #: called as logger(byte) for every byte written to the chip
        self.logger = logger

        self._lib = _Lib.get()
        if self._lib is not None:
            self._chip = self._lib.psg_new()
            self._py = None
            self.backend = core_loader.NATIVE
        else:
            from fallback.psg import PyPSG
            core_loader.warn_once_about_fallback("SN76489")
            self._chip = None
            self._py = PyPSG(clock=clock)
            self.backend = core_loader.FALLBACK

        self._volume = [15, 15, 15, 15]
        self._note = [None, None, None]

    def close(self):
        if self._chip:
            self._lib.psg_free(self._chip)
            self._chip = None
        self._py = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def write(self, byte: int):
        byte &= 0xFF
        if self.logger is not None:
            self.logger(byte)
        if self._py is not None:
            self._py.write(byte)
            return
        self._lib.psg_write(self._chip, byte)

    # -- tone channels (0, 1, 2) ------------------------------------------------
    def tone_on(self, channel: int, note: str, octave: int, volume: int = 0,
                clock: float = None):
        assert 0 <= channel <= 2
        if clock is not None:
            self.clock = clock
        n = freq_to_tone_n(note_to_freq(note, octave), self.clock)
        self.write(0x80 | (channel << 5) | (0 << 4) | (n & 0xF))
        self.write((n >> 4) & 0x3F)
        self._note[channel] = (note, octave)
        self.set_volume(channel, volume)

    def set_volume(self, channel: int, volume: int):
        """volume: 0 = loudest, 15 = silent. Channel 3 is the noise voice."""
        assert 0 <= channel <= 3
        volume = max(0, min(15, volume))
        self._volume[channel] = volume
        self.write(0x80 | (channel << 5) | (1 << 4) | (volume & 0xF))

    def get_volume(self, channel: int) -> int:
        return self._volume[channel]

    def tone_off(self, channel: int):
        self.set_volume(channel, 15)
        self._note[channel] = None

    def set_tone_register(self, channel: int, n: int):
        """Raw 10-bit counter reload. Below 2 the channel outputs DC, which
        is the basis of the PSG sample-playback trick."""
        assert 0 <= channel <= 2
        n = max(0, min(1023, int(n)))
        self.write(0x80 | (channel << 5) | (0 << 4) | (n & 0xF))
        self.write((n >> 4) & 0x3F)

    # -- noise channel (3) --------------------------------------------------------
    def noise_on(self, white: bool, rate: int, volume: int = 0):
        mode_bit = 0x04 if white else 0x00
        data = mode_bit | (rate & 0x03)
        self.write(0x80 | (3 << 5) | (0 << 4) | data)
        self.set_volume(3, volume)

    def noise_off(self):
        self.set_volume(3, 15)

    def silence(self):
        for channel in range(4):
            self.set_volume(channel, 15)

    # -- rendering ----------------------------------------------------------------
    def render(self, n_samples: int):
        """Render n_samples of native-rate (~223721.56 Hz) mono audio.
        Returns float32 in [-1, 1] — an audio.Buffer if numpy is absent."""
        if n_samples <= 0:
            return _audio.zeros(0, 1)
        if self._py is not None:
            return self._py.render(n_samples)
        buf = (ctypes.c_short * n_samples)()
        self._lib.psg_render(self._chip, n_samples, buf)
        return _audio.from_int16(buf, 1)
