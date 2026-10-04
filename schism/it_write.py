"""it_write.py — a Module as the bytes of an Impulse Tracker `.it` file.

The layout, from ITTECH.TXT, in the order it is written:

    0x000  header                      192 bytes, with channel pan and volume
           order list                  one byte each, then 255
           parapointers                u32 offsets: instruments, samples, patterns
           [MIDI configuration]        4896 bytes, if the module carries one
           [song message]
           instruments                 554 bytes each
           sample headers              80 bytes each
           patterns                    packed
           sample data                 signed PCM, 8 or 16 bit, little-endian

Everything is checked rather than clamped: a value the format cannot hold
raises `ITWriteError` naming it, because a clamp turns a mistake into a
module that plays something else. libopenmpt reads the result in the tests
and reports back what it parsed, which is the only check that does not
share this file's assumptions.
"""

import struct
import sys
from array import array

from .model import (EFFECT_LETTERS, MAX_CHANNELS, MAX_ROWS, MIN_ROWS,
                   NOTE_CUT, NOTE_FADE, NOTE_OFF, SCHISM_MAX_PATTERNS,
                   SCHISM_MAX_SAMPLES)

MIDI_CONFIG_BYTES = (9 + 16 + 128) * 32

#: Bit 7 of a pitch envelope's flags says it moves the filter instead of
#: the pitch. (Bit 3 is "carry", which is something else: an earlier
#: version of this writer set it, and a filter envelope played as a pitch
#: sweep in both libopenmpt and Schism. The test that should have noticed
#: measured brightness, which a rising pitch also raises; it now measures
#: pitch too.)
FILTER_FLAG = 0x80

#: The byte a note fade (~~~) is stored as. ITTECH gives 120..253 to it,
#: and libopenmpt reads 121..252 as a fade but 253 as no note at all —
#: measured, see tests/test_it.py. 246 is inside both.
FADE_BYTE = 246


class ITWriteError(ValueError):
    pass


def _check(name, value, low, high):
    if not low <= value <= high:
        raise ITWriteError(f"{name} is {value}, but the format holds "
                           f"{low}..{high}")
    return value


def _text(value: str, size: int) -> bytes:
    """ASCII, null-padded. Non-ASCII becomes '?': the format has no
    encoding field, and what a tracker shows for a byte above 127 is up to
    its font."""
    raw = value.encode("ascii", "replace")[:size - 1]
    return raw.ljust(size, b"\0")


# -- patterns ---------------------------------------------------------------
def pack_pattern(pattern, channels: int) -> bytes:
    """IT's packed pattern data.

    A cell names its channel and then only the fields it has. Two layers
    of saving: a channel that repeats the previous mask sends no mask, and a
    field equal to that channel's last value sends a flag instead of the
    value. A pattern of mostly empty rows costs one zero byte per row.
    """
    data = bytearray()
    last = [[0, 0, -1, 0, 0] for _ in range(channels)]  # note ins vol fx param
    primed = [0] * channels                              # fields written once
    last_mask = [0] * channels

    for row in pattern.cells:
        for channel in range(min(channels, len(row))):
            cell = row[channel]
            if cell.empty():
                continue
            note = cell.note
            if 1 <= note <= 120:
                note -= 1                      # the file counts from zero
            elif note == NOTE_FADE:
                note = FADE_BYTE
            elif note not in (0, NOTE_OFF, NOTE_CUT):
                raise ITWriteError(f"note {note} is not 1..120 or a special")

            fields = []                        # (bit, repeat bit, value)
            if cell.note:
                fields.append((1, 0x10, (note,)))
            if cell.instrument:
                fields.append((2, 0x20, (cell.instrument,)))
            if cell.volume >= 0:
                fields.append((4, 0x40, (cell.volume,)))
            if cell.effect or cell.param:
                fields.append((8, 0x80, (cell.effect, cell.param)))

            mask = 0
            body = bytearray()
            slot = {1: 0, 2: 1, 4: 2, 8: 3}
            for bit, repeat, value in fields:
                index = slot[bit]
                previous = (last[channel][3:5] if bit == 8
                            else [last[channel][index]])
                if primed[channel] & bit and list(value) == list(previous):
                    mask |= repeat
                else:
                    mask |= bit
                    body += bytes(value)
                    primed[channel] |= bit
                    if bit == 8:
                        last[channel][3], last[channel][4] = value
                    else:
                        last[channel][index] = value[0]

            if mask == last_mask[channel]:
                data.append(channel + 1)
            else:
                data.append((channel + 1) | 0x80)
                data.append(mask)
                last_mask[channel] = mask
            data += body
        data.append(0)                         # end of row
    return struct.pack("<HHI", len(data), len(pattern.cells), 0) + bytes(data)


# -- envelopes and instruments ----------------------------------------------
def _envelope(env, kind: str) -> bytes:
    """82 bytes. `kind` is 'volume', 'pan' or 'pitch'."""
    if env is None or not env.nodes:
        return bytes(82)
    nodes = list(env.nodes)
    _check(f"{kind} envelope node count", len(nodes), 1, 25)
    if nodes[0][0] != 0:
        raise ITWriteError(f"the {kind} envelope must start at tick 0, not "
                           f"{nodes[0][0]}")
    ticks = [t for t, _ in nodes]
    if any(b <= a for a, b in zip(ticks, ticks[1:])):
        raise ITWriteError(f"the {kind} envelope's ticks must increase, got "
                           f"{ticks}")
    _check(f"{kind} envelope last tick", ticks[-1], 0, 65535)
    flags = 1 if env.enabled else 0
    loop = env.loop or (0, 0)
    sustain = env.sustain or (0, 0)
    for label, pair in (("loop", env.loop), ("sustain", env.sustain)):
        if pair is not None:
            _check(f"{kind} envelope {label} start", pair[0], 0, len(nodes) - 1)
            _check(f"{kind} envelope {label} end", pair[1], pair[0],
                   len(nodes) - 1)
    if env.loop is not None:
        flags |= 2
    if env.sustain is not None:
        flags |= 4
    if kind == "pitch" and env.filter:
        flags |= FILTER_FLAG

    out = bytearray([flags, len(nodes), loop[0], loop[1], sustain[0],
                     sustain[1]])
    for tick, value in nodes:
        if kind == "volume":
            _check("volume envelope value", value, 0, 64)
            raw = value
        elif kind == "pan":
            _check("pan envelope value", value, -32, 32)
            raw = value
        elif env.filter:
            # the file stores the filter envelope as a signed offset from 32
            _check("filter envelope value", value, 0, 64)
            raw = value - 32
        else:
            _check("pitch envelope value", value, -32, 32)
            raw = value
        out += struct.pack("<bH", raw, tick)
    out += bytes(3 * (25 - len(nodes)))
    out.append(0)
    assert len(out) == 82
    return bytes(out)


def instrument_bytes(ins) -> bytes:
    _check("fadeout", ins.fadeout, 0, 256)
    _check("NNA", ins.nna, 0, 3)
    _check("DCT", ins.dct, 0, 3)
    _check("DCA", ins.dca, 0, 2)
    _check("pitch-pan separation", ins.pitch_pan_separation, -32, 32)
    _check("pitch-pan centre", ins.pitch_pan_center, 0, 119)
    _check("instrument global volume", ins.global_volume, 0, 128)
    if len(ins.note_map) != 120:
        raise ITWriteError(f"an instrument's note map has 120 entries, this "
                           f"has {len(ins.note_map)}")
    pan = 0x80 | 32 if ins.default_pan is None else \
        _check("instrument default pan", ins.default_pan, 0, 64)
    cutoff = 0 if ins.cutoff is None else \
        0x80 | _check("filter cutoff", ins.cutoff, 0, 127)
    resonance = 0 if ins.resonance is None else \
        0x80 | _check("filter resonance", ins.resonance, 0, 127)

    table = bytearray()
    for note, sample in ins.note_map:
        _check("note-map note", note, 0, 119)
        _check("note-map sample", sample, 0, 255)
        table += bytes((note, sample))

    out = struct.pack(
        "<4s12sBBBBHbBBBBBHBB26sBBBBH",
        b"IMPI", _text(ins.filename, 13)[:12], 0,
        ins.nna, ins.dct, ins.dca, ins.fadeout,
        ins.pitch_pan_separation, ins.pitch_pan_center,
        ins.global_volume, pan,
        _check("random volume", ins.random_volume, 0, 100),
        _check("random pan", ins.random_pan, 0, 64),
        0x0214, 0, 0,
        _text(ins.name, 26),
        cutoff, resonance, ins.midi_channel, ins.midi_program, ins.midi_bank)
    out += bytes(table)
    out += _envelope(ins.volume_envelope, "volume")
    out += _envelope(ins.pan_envelope, "pan")
    out += _envelope(ins.pitch_envelope, "pitch")
    out += bytes(4)
    assert len(out) == 554, len(out)
    return out


# -- samples ----------------------------------------------------------------
def _channel_bytes(data, bits: int) -> bytes:
    if bits == 16:
        pcm = array("h", data)
        if sys.byteorder == "big":
            pcm.byteswap()
        return pcm.tobytes()
    if bits == 8:
        return array("b", data).tobytes()
    raise ITWriteError(f"samples are 8 or 16 bit, not {bits}")


def sample_data_bytes(sample) -> bytes:
    """The sample's bytes. A stereo sample is all of the left channel and
    then all of the right: both players read that, and neither reads
    interleaved frames."""
    data = sample.data
    if data is None or len(data) == 0:
        return b""
    out = _channel_bytes(data, sample.bits)
    if sample.right is not None:
        if len(sample.right) != len(data):
            raise ITWriteError(
                f"sample {sample.name!r}: the right channel has "
                f"{len(sample.right)} frames and the left {len(data)}")
        out += _channel_bytes(sample.right, sample.bits)
    return out


def sample_header_bytes(sample, data_offset: int) -> bytes:
    frames = sample.frames
    flags = 0
    if frames:
        flags |= 1
    if sample.bits == 16:
        flags |= 2
    if sample.right is not None:
        flags |= 4
    loop = sample.loop or (0, 0)
    sustain = sample.sustain_loop or (0, 0)
    if sample.loop is not None:
        _check("loop start", loop[0], 0, frames)
        _check("loop end", loop[1], loop[0] + 1, frames)
        flags |= 16
        if sample.loop_pingpong:
            flags |= 64
    if sample.sustain_loop is not None:
        _check("sustain loop start", sustain[0], 0, frames)
        _check("sustain loop end", sustain[1], sustain[0] + 1, frames)
        flags |= 32
        if sample.sustain_pingpong:
            flags |= 128
    pan = 0 if sample.default_pan is None else \
        0x80 | _check("sample default pan", sample.default_pan, 0, 64)
    return struct.pack(
        "<4s12sBBBB26sBBIIIIIIIBBBB",
        b"IMPS", _text(sample.filename, 13)[:12], 0,
        _check("sample global volume", sample.global_volume, 0, 64),
        flags,
        _check("sample volume", sample.volume, 0, 64),
        _text(sample.name, 26),
        1,                                  # Cvt: signed samples
        pan,
        frames, loop[0], loop[1],
        _check("C5 speed", sample.c5speed, 1, 4294967295),
        sustain[0], sustain[1],
        data_offset if frames else 0,
        _check("sample vibrato speed", sample.vibrato_speed, 0, 64),
        _check("sample vibrato depth", sample.vibrato_depth, 0, 64),
        _check("sample vibrato rate", sample.vibrato_rate, 0, 64),
        _check("sample vibrato waveform", sample.vibrato_wave, 0, 3))


# -- the file ---------------------------------------------------------------
def build(module) -> bytes:
    m = module
    _check("order count", len(m.orders), 0, 255)
    _check("instrument count", len(m.instruments), 0, 255)
    if len(m.samples) > SCHISM_MAX_SAMPLES:
        raise ITWriteError(
            f"{len(m.samples)} samples, but Schism Tracker plays at most "
            f"{SCHISM_MAX_SAMPLES} (the next one is silent and a file with "
            f"more does not load); a pitched instrument costs one sample per "
            f"octave, so narrow oct= or share instruments")
    if len(m.patterns) > SCHISM_MAX_PATTERNS:
        raise ITWriteError(
            f"{len(m.patterns)} patterns, but Schism Tracker and OpenMPT load "
            f"at most {SCHISM_MAX_PATTERNS}; repeat patterns in the order "
            f"list instead of writing them out again")
    for index in m.orders:
        if index not in (254, 255) and not 0 <= index < len(m.patterns):
            raise ITWriteError(f"the order list names pattern {index}, but "
                               f"there are {len(m.patterns)}")
    for number, pattern in enumerate(m.patterns):
        if not MIN_ROWS <= pattern.rows <= MAX_ROWS:
            raise ITWriteError(f"pattern {number} has {pattern.rows} rows; "
                               f"Impulse Tracker patterns have "
                               f"{MIN_ROWS}-{MAX_ROWS}")
        if len(pattern.cells) != pattern.rows:
            raise ITWriteError(f"pattern {number} says {pattern.rows} rows "
                               f"and holds {len(pattern.cells)}")
    if m.use_instruments is False and m.instruments:
        raise ITWriteError("instruments given, but the module is in sample mode")
    for number, sample in enumerate(m.samples, 1):
        held = getattr(sample, "stored_frames", 0)
        if sample.data is None and held:
            why = ("is stored compressed (IT 2.14/2.15 packing), which this "
                   "reader does not unpack" if sample.compressed else
                   "was read without its audio (load_data=False)")
            raise ITWriteError(
                f"sample {number} {sample.name!r} {why}: writing the module "
                f"would leave its {held} frames out. Nothing was written; "
                f"the original file is the only complete copy")

    channels = max(1, m.channels_used())
    _check("channels used", channels, 1, MAX_CHANNELS)

    packed = [pack_pattern(p, channels) for p in m.patterns]
    orders = bytes(m.orders) + bytes([255])
    message = m.message.replace("\r\n", "\n").replace("\n", "\r") \
        .encode("ascii", "replace")[:7999]
    if message:
        message += b"\0"
    if m.midi_config is not None and len(m.midi_config) != MIDI_CONFIG_BYTES:
        raise ITWriteError(f"MIDI configuration is {MIDI_CONFIG_BYTES} bytes, "
                           f"not {len(m.midi_config)}")

    n_ord = len(orders)
    n_ins, n_smp, n_pat = len(m.instruments), len(m.samples), len(m.patterns)
    cursor = 0xC0 + n_ord + 4 * (n_ins + n_smp + n_pat)
    midi_at = cursor
    if m.midi_config is not None:
        cursor += MIDI_CONFIG_BYTES
    message_at = cursor if message else 0
    cursor += len(message)
    instrument_at = cursor
    cursor += 554 * n_ins
    sample_header_at = cursor
    cursor += 80 * n_smp
    pattern_at = []
    for block in packed:
        pattern_at.append(cursor)
        cursor += len(block)
    data_at = []
    blobs = []
    shared = {}                       # id of the data -> where it went
    for sample in m.samples:
        key = (id(sample.data), id(sample.right), sample.bits)
        if sample.data is not None and key in shared:
            data_at.append(shared[key])      # two headers, one set of bytes
            continue
        blob = sample_data_bytes(sample)
        shared[key] = cursor
        data_at.append(cursor)
        blobs.append(blob)
        cursor += len(blob)

    flags = (1 if m.stereo else 0) | (4 if m.use_instruments else 0) \
        | (8 if m.linear_slides else 0) | (16 if m.old_effects else 0) \
        | (32 if m.gxx_link else 0)
    special = (1 if message else 0) | (8 if m.midi_config is not None else 0)

    header = struct.pack(
        "<4s26sBBHHHHHHHHBBBBBBHII",
        b"IMPM", _text(m.title, 26),
        _check("rows per beat", m.rows_per_beat, 0, 255),
        _check("rows per measure", m.rows_per_measure, 0, 255),
        n_ord, n_ins, n_smp, n_pat,
        m.created_with, m.compatible_with, flags, special,
        _check("global volume", m.global_volume, 0, 128),
        _check("mix volume", m.mix_volume, 0, 128),
        _check("initial speed", m.speed, 1, 255),
        _check("initial tempo", m.tempo, 32, 255),
        _check("pan separation", m.pan_separation, 0, 128),
        _check("pitch wheel depth", m.pitch_wheel_depth, 0, 255),
        len(message), message_at, 0)

    pan = bytearray()
    for channel in range(64):
        value = m.channel_pan[channel]
        _check(f"channel {channel + 1} pan", value, 0, 100)
        pan.append(value | (128 if m.channel_mute[channel] else 0))
    volume = bytearray()
    for channel in range(64):
        volume.append(_check(f"channel {channel + 1} volume",
                             m.channel_volume[channel], 0, 64))

    out = bytearray(header) + pan + volume
    assert len(out) == 0xC0, len(out)
    out += orders
    out += b"".join(struct.pack("<I", instrument_at + 554 * i)
                    for i in range(n_ins))
    out += b"".join(struct.pack("<I", sample_header_at + 80 * i)
                    for i in range(n_smp))
    out += b"".join(struct.pack("<I", offset) for offset in pattern_at)
    assert len(out) == midi_at
    if m.midi_config is not None:
        out += m.midi_config
    out += message
    for ins in m.instruments:
        out += instrument_bytes(ins)
    for sample, offset in zip(m.samples, data_at):
        out += sample_header_bytes(sample, offset)
    for block in packed:
        out += block
    for blob in blobs:
        out += blob
    assert len(out) == cursor, (len(out), cursor)
    return bytes(out)


def write(module, path: str) -> int:
    data = build(module)
    with open(path, "wb") as handle:
        handle.write(data)
    return len(data)
