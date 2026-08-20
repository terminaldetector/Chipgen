"""
opn2.py — Python control layer for the YM2612 (Nuked-OPN2 core).

Wraps core/libopn2.so via ctypes and exposes musically meaningful methods
(set_instrument, note_on, set_pan, set_volume, DAC playback...) instead of
raw register writes, while still allowing raw register access for anyone
who wants it.

Native output rate derivation (NTSC Sega Genesis):
    chip input clock  = 53,693,175 / 7   =  7,670,453.57 Hz
    internal clock     = chip clock / 6   =  1,278,408.93 Hz   (per Nuked-OPN2 docs:
                                                                 1 call to OPN2_Clock
                                                                 = 1 internal clock
                                                                 = 6 chip-input clocks)
    audio sample rate  = internal / 24    =     53,267.04 Hz   (24 operator slots:
                                                                 6 channels x 4 ops,
                                                                 round-robin pipeline)

Every register write can be mirrored to a `logger` callback. That is how
vgm.py records a track: the exact byte stream that reached the chip is
also the VGM data block, so an exported .vgm and the rendered WAV are the
same performance by construction, not two implementations that have to be
kept in sync.
"""

import ctypes
import math

import audio as _audio
import core_loader

NTSC_CHIP_CLOCK = 53_693_175 / 7          # Hz, input clock to the YM2612
PAL_CHIP_CLOCK = 53_203_424 / 7           # Hz, PAL Mega Drive variant
NATIVE_RATE = NTSC_CHIP_CLOCK / 6 / 24    # ~53267.04 Hz, see module docstring
PAL_NATIVE_RATE = PAL_CHIP_CLOCK / 6 / 24

_NOTE_OFFSETS = {"C": -9, "C#": -8, "Db": -8, "D": -7, "D#": -6, "Eb": -6,
                 "E": -5, "F": -4, "F#": -3, "Gb": -3, "G": -2, "G#": -1,
                 "Ab": -1, "A": 0, "A#": 1, "Bb": 1, "B": 2}

#: Register offsets for the four operators, in the order the chip lays them
#: out. The YM2612 interleaves operators across its 24-slot pipeline, so the
#: offsets ascend as op1, op3, op2, op4 — NOT op1..op4. FMInstrument stores
#: its operator list in this same register order, which means list index 1
#: is chip operator 3 and list index 2 is chip operator 2. Anything that
#: reasons about the algorithm (which operator is a carrier, say) has to go
#: through _LIST_TO_OP below rather than assume list index + 1.
_OP_OFFSETS = (0x00, 0x04, 0x08, 0x0C)
_LIST_TO_OP = (1, 3, 2, 4)
_OP_TO_LIST = {op: i for i, op in enumerate(_LIST_TO_OP)}

#: Which chip operators are carriers (reach the output directly) for each of
#: the 8 FM algorithms. Modulators shape timbre, carriers set level — so a
#: volume control has to attenuate only these, or it detunes the sound
#: instead of quietening it.
_CARRIERS_BY_ALGORITHM = {
    0: (4,), 1: (4,), 2: (4,), 3: (4,),
    4: (2, 4), 5: (2, 3, 4), 6: (2, 3, 4), 7: (1, 2, 3, 4),
}

#: LFO rates for register 0x22, in Hz (from the YM2612 application manual).
LFO_FREQUENCIES = (3.98, 5.56, 6.02, 6.37, 6.88, 9.63, 48.1, 72.2)

#: Which Genesis revision to emulate.
#:   "ym2612" — the discrete chip in Model 1 and Model 2 VA2. Its
#:              time-shared DAC runs through a resistor ladder that never
#:              settles to zero, so every output carries a small fixed
#:              level between channel slots. That is the "ladder effect":
#:              gritty, slightly dirty, and the reason a hard-panned
#:              channel still bleeds a quiet square into the other side.
#:   "ym3438" — the integrated ASIC in later models. No ladder, clean
#:              muting, clean silence.
#: Both are correct hardware; pick the one you are writing for.
CHIP_TYPES = ("ym2612", "ym3438")
DEFAULT_CHIP_TYPE = "ym2612"


def note_to_freq(note: str, octave: int) -> float:
    """'C', 4 -> 261.63 Hz (A4 = 440 Hz reference, 12-tone equal temperament)."""
    semitones = _NOTE_OFFSETS[note] + (octave - 4) * 12
    return 440.0 * (2.0 ** (semitones / 12.0))


def freq_to_fnum_block(freq: float, clock: float = NTSC_CHIP_CLOCK):
    """Pick a block (0-7) that keeps fnum in the valid 11-bit range and
    return (fnum, block).

    The datasheet gives

        F-Number = (144 * f * 2^20 / clock) / 2^(block - 1)

    which folds to 144 * f * 2^(21 - block) / clock. Getting the exponent
    wrong by one does not produce an obviously broken sound — it produces
    a perfectly in-tune performance one octave away from the one that was
    written, which is exactly the kind of bug that survives listening
    tests. Sanity check: A4 = 440 Hz must land on fnum 1082, block 4, the
    value every published Genesis note table lists.
    """
    best = None
    for block in range(8):
        fnum = round(freq * (2 ** (21 - block)) * 144 / clock)
        if 0 <= fnum <= 2047:
            # Prefer the block that puts fnum comfortably mid-range —
            # gives headroom for pitch bends/vibrato in either direction.
            score = abs(fnum - 1024)
            if best is None or score < best[0]:
                best = (score, fnum, block)
    if best is None:
        raise ValueError(f"frequency {freq} Hz out of representable range")
    return best[1], best[2]


def level_to_attenuation(level: int, maximum: int = 127) -> int:
    """MIDI-ish 0..127 fader -> extra Total Level steps (0.75 dB each).

    Linear in amplitude, not in dB: halving `level` costs about 6 dB, which
    is what a musician expects a volume control to do. A straight
    `127 - level` would be linear in dB and make everything below about 100
    inaudible.
    """
    if level <= 0:
        return 127
    if level >= maximum:
        return 0
    db = -20.0 * math.log10(level / float(maximum))
    return max(0, min(127, int(round(db / 0.75))))


def fnum_block_to_freq(fnum: int, block: int,
                        clock: float = NTSC_CHIP_CLOCK) -> float:
    """Inverse of freq_to_fnum_block — the pitch the chip will actually play."""
    return fnum * clock / (144.0 * (2 ** (21 - block)))


class _Lib:
    """Lazily-loaded, correctly-typed ctypes handle to libopn2."""
    _lib = None

    @classmethod
    def get(cls):
        if cls._lib is None:
            lib = core_loader.load("libopn2")
            if lib is None:
                return None
            lib.opn2_new.restype = ctypes.c_void_p
            lib.opn2_new.argtypes = []
            lib.opn2_free.argtypes = [ctypes.c_void_p]
            lib.opn2_reset.argtypes = [ctypes.c_void_p]
            lib.opn2_write.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_ubyte]
            lib.opn2_render.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                         ctypes.POINTER(ctypes.c_short)]
            lib.opn2_set_chip_type.argtypes = [ctypes.c_int]
            lib.opn2_get_chip_type.restype = ctypes.c_int
            cls._lib = lib
        return cls._lib


class Operator:
    """One FM operator's parameters (there are 4 per channel).

    detune: 0-7 (3-bit DT1, 4 = no detune)
    multiple: 0-15 (frequency multiplier, 0 means x0.5)
    total_level: 0 (loudest) - 127 (silent)
    attack_rate, decay_rate, sustain_rate, release_rate: 0-31 (rate) /
        release_rate is stored as 4-bit (0-15) per hardware
    sustain_level: 0 (highest) - 15 (lowest, i.e. deepest decay target)
    ssg_eg: 0 or 8-15, SSG-EG envelope mode (0 = normal ADSR, off)
    """
    __slots__ = ("detune", "multiple", "total_level", "attack_rate",
                 "decay_rate", "sustain_rate", "release_rate",
                 "sustain_level", "ssg_eg", "rate_scaling", "am_enable")

    def __init__(self, detune=0, multiple=1, total_level=20, attack_rate=31,
                 decay_rate=5, sustain_rate=0, release_rate=7,
                 sustain_level=2, ssg_eg=0, rate_scaling=0, am_enable=0):
        self.detune = detune
        self.multiple = multiple
        self.total_level = total_level
        self.attack_rate = attack_rate
        self.decay_rate = decay_rate
        self.sustain_rate = sustain_rate
        self.release_rate = release_rate
        self.sustain_level = sustain_level
        self.ssg_eg = ssg_eg
        self.rate_scaling = rate_scaling
        self.am_enable = am_enable

    def copy(self) -> "Operator":
        return Operator(**{slot: getattr(self, slot) for slot in self.__slots__})


class FMInstrument:
    """A full 4-operator YM2612 patch: algorithm + feedback + 4 Operators.

    `operators` is in register order (op1, op3, op2, op4) — see _OP_OFFSETS.
    """
    __slots__ = ("algorithm", "feedback", "operators", "name")

    def __init__(self, algorithm: int, feedback: int, operators, name: str = ""):
        assert 0 <= algorithm <= 7
        assert 0 <= feedback <= 7
        assert len(operators) == 4
        self.algorithm = algorithm
        self.feedback = feedback
        self.operators = list(operators)
        self.name = name

    def carrier_indices(self):
        """Positions in `operators` that reach the output for this algorithm."""
        return tuple(_OP_TO_LIST[op] for op in _CARRIERS_BY_ALGORITHM[self.algorithm])

    def copy(self) -> "FMInstrument":
        return FMInstrument(self.algorithm, self.feedback,
                            [op.copy() for op in self.operators], self.name)


class YM2612:
    """One emulated YM2612: 6 FM channels (0-5), channel 6 doubling as the DAC."""

    _OP_OFFSETS = _OP_OFFSETS

    def __init__(self, clock: float = NTSC_CHIP_CLOCK, logger=None,
                 chip_type: str = DEFAULT_CHIP_TYPE):
        self.clock = clock
        self.native_rate = clock / 6 / 24
        #: called as logger(port, addr, data) for every register write
        self.logger = logger
        if chip_type not in CHIP_TYPES:
            raise ValueError(f"chip_type must be one of {CHIP_TYPES}, "
                             f"got {chip_type!r}")
        self.chip_type = chip_type

        self._lib = _Lib.get()
        if self._lib is not None:
            # Nuked-OPN2 keeps the chip type in a file-static, so this is a
            # process-wide setting rather than a per-object one. Setting it
            # per construction is still right — you get the type you asked
            # for — but two YM2612 objects with different types cannot
            # coexist, and pretending otherwise would be worse than saying so.
            self._lib.opn2_set_chip_type(1 if chip_type == "ym2612" else 0)
            self._chip = self._lib.opn2_new()
            self._py = None
            self.backend = core_loader.NATIVE
        else:
            from fallback.fm import PyYM2612
            core_loader.warn_once_about_fallback("YM2612")
            self._chip = None
            # The pure-Python core has no ladder model, so it always
            # behaves like the clean ASIC revision whatever was asked for.
            self._py = PyYM2612(clock=clock)
            self.native_rate = self._py.rate
            self.backend = core_loader.FALLBACK

        self._channel_instrument = [None] * 6
        self._channel_volume = [127] * 6
        self._channel_cents = [0.0] * 6
        self._channel_note = [None] * 6      # (note, octave) currently keyed on
        self._keyed_on = [False] * 6
        self._dac_enabled = False

    def close(self):
        if self._chip:
            self._lib.opn2_free(self._chip)
            self._chip = None
        self._py = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # -- raw register access -------------------------------------------------
    def _port_addr_for(self, channel: int):
        # channels 0-2 live on bank A (ports 0/1), 3-5 on bank B (ports 2/3)
        if channel < 3:
            return 0, 1, channel
        return 2, 3, channel - 3

    def write(self, port: int, addr: int, data: int):
        """Write one register. `port` is the ADDRESS port (0 or 2); data goes
        to port+1, exactly as a Genesis driver would do it."""
        addr &= 0xFF
        data &= 0xFF
        if self.logger is not None:
            self.logger(port, addr, data)
        if self._py is not None:
            self._py.write(port, addr, data)
            return
        self._lib.opn2_write(self._chip, port, addr)
        self._lib.opn2_write(self._chip, port + 1, data)

    # -- instrument / voice ----------------------------------------------------
    def set_instrument(self, channel: int, instrument: FMInstrument):
        addr_port, _, ch = self._port_addr_for(channel)
        carriers = instrument.carrier_indices()
        extra = level_to_attenuation(self._channel_volume[channel])
        for i, (op, off) in enumerate(zip(instrument.operators, self._OP_OFFSETS)):
            base = 0x30 + off + ch
            tl = op.total_level + (extra if i in carriers else 0)
            self.write(addr_port, base, ((op.detune & 0x7) << 4) | (op.multiple & 0xF))
            self.write(addr_port, base + 0x10, min(127, tl) & 0x7F)
            self.write(addr_port, base + 0x20, ((op.rate_scaling & 0x3) << 6) | (op.attack_rate & 0x1F))
            self.write(addr_port, base + 0x30, ((op.am_enable & 0x1) << 7) | (op.decay_rate & 0x1F))
            self.write(addr_port, base + 0x40, op.sustain_rate & 0x1F)
            self.write(addr_port, base + 0x50, ((op.sustain_level & 0xF) << 4) | (op.release_rate & 0xF))
            self.write(addr_port, base + 0x60, op.ssg_eg & 0xF)
        self.write(addr_port, 0xB0 + ch, ((instrument.feedback & 0x7) << 3) | (instrument.algorithm & 0x7))
        self.write(addr_port, 0xB4 + ch, 0xC0)  # pan L+R on, AMS/PMS off
        self._channel_instrument[channel] = instrument

    def set_pan(self, channel: int, left: bool = True, right: bool = True,
                ams: int = 0, pms: int = 0):
        """Register 0xB4: stereo output enables + per-channel LFO depth.

        Panning on this chip is two mute switches, not a continuous law —
        there is no 30%-left. Both off is silence, which is occasionally
        what you want as a hard gate.
        """
        addr_port, _, ch = self._port_addr_for(channel)
        value = ((0x80 if left else 0x00) | (0x40 if right else 0x00)
                 | ((ams & 0x3) << 4) | (pms & 0x7))
        self.write(addr_port, 0xB4 + ch, value)

    def set_lfo(self, enable: bool, freq: int = 0):
        """Register 0x22: the one global LFO, shared by all six channels."""
        self.write(0, 0x22, (0x08 if enable else 0x00) | (freq & 0x7))

    def set_volume(self, channel: int, volume: int):
        """0-127 channel fader. Rewrites carrier Total Levels only."""
        volume = max(0, min(127, volume))
        self._channel_volume[channel] = volume
        instrument = self._channel_instrument[channel]
        if instrument is None:
            return
        addr_port, _, ch = self._port_addr_for(channel)
        extra = level_to_attenuation(volume)
        for i in instrument.carrier_indices():
            op = instrument.operators[i]
            tl = min(127, op.total_level + extra)
            self.write(addr_port, 0x40 + self._OP_OFFSETS[i] + ch, tl & 0x7F)

    def set_pitch_offset(self, channel: int, cents: float):
        """Detune the channel; takes effect immediately if a note is sounding."""
        self._channel_cents[channel] = float(cents)
        sounding = self._channel_note[channel]
        if sounding is not None:
            self._write_frequency(channel, sounding[0], sounding[1])

    def _write_frequency(self, channel: int, note: str, octave: int):
        freq = note_to_freq(note, octave)
        cents = self._channel_cents[channel]
        if cents:
            freq *= 2.0 ** (cents / 1200.0)
        fnum, block = freq_to_fnum_block(freq, self.clock)
        addr_port, _, ch = self._port_addr_for(channel)
        # Block/high bits first: the chip latches the low byte, so writing
        # 0xA4 before 0xA0 is what makes both halves take effect together.
        self.write(addr_port, 0xA4 + ch, ((block & 0x7) << 3) | (fnum >> 8))
        self.write(addr_port, 0xA0 + ch, fnum & 0xFF)

    def _key_code(self, channel: int) -> int:
        return channel if channel < 3 else channel + 1  # 0,1,2,4,5,6

    def note_on(self, channel: int, note: str, octave: int, velocity: int = 127,
                clock: float = None, retrigger: bool = True):
        """Key on. `velocity` (1-127) attenuates carriers for this note only.

        `retrigger` matters on real hardware: writing key-on to an already-on
        channel does nothing at all — no new attack. So a repeated note needs
        a key-off first, and we do that for you unless you ask otherwise
        (legato/tie is `retrigger=False`).
        """
        if clock is not None:
            self.clock = clock
        if retrigger and self._keyed_on[channel]:
            self.write(0, 0x28, 0x00 | self._key_code(channel))

        instrument = self._channel_instrument[channel]
        if instrument is not None and velocity < 127:
            addr_port, _, ch = self._port_addr_for(channel)
            extra = level_to_attenuation(self._channel_volume[channel]) \
                + level_to_attenuation(velocity)
            for i in instrument.carrier_indices():
                tl = min(127, instrument.operators[i].total_level + extra)
                self.write(addr_port, 0x40 + self._OP_OFFSETS[i] + ch, tl & 0x7F)
            self._velocity_dirty = True

        self._write_frequency(channel, note, octave)
        self.write(0, 0x28, 0xF0 | self._key_code(channel))
        self._channel_note[channel] = (note, octave)
        self._keyed_on[channel] = True

    def note_off(self, channel: int):
        self.write(0, 0x28, 0x00 | self._key_code(channel))
        self._keyed_on[channel] = False
        self._channel_note[channel] = None
        if getattr(self, "_velocity_dirty", False):
            # Restore the patch's own carrier levels so the next note is not
            # stuck at the last note's velocity.
            self._velocity_dirty = False
            self.set_volume(channel, self._channel_volume[channel])

    # -- DAC / PCM (channel 6) --------------------------------------------------
    def set_dac_enable(self, enable: bool):
        """Register 0x2B bit 7: hand FM channel 6 over to the 8-bit DAC.

        Channel 6 stops being an FM voice while this is on — that trade is
        the whole reason Genesis drum tracks sound the way they do.
        """
        self._dac_enabled = bool(enable)
        self.write(0, 0x2B, 0x80 if enable else 0x00)

    def write_dac(self, sample: int):
        """One unsigned 8-bit PCM sample to register 0x2A."""
        self.write(0, 0x2A, max(0, min(255, int(sample))))

    @property
    def dac_enabled(self) -> bool:
        return self._dac_enabled

    # -- rendering -------------------------------------------------------------
    def render(self, n_samples: int):
        """Render n_samples of native-rate (~53267 Hz) stereo audio.
        Returns float32 (n_samples, 2) in [-1, 1] — an audio.Buffer if
        numpy is not installed."""
        if n_samples <= 0:
            return _audio.zeros(0, 2)
        if self._py is not None:
            return self._py.render(n_samples)
        buf = (ctypes.c_short * (n_samples * 2))()
        self._lib.opn2_render(self._chip, n_samples, buf)
        return _audio.from_int16(buf, 2)
