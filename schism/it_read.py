"""it_read.py — an Impulse Tracker `.it` file back into a Module.

Two jobs. The first is the round trip: write, read, compare, which catches
the writer disagreeing with itself. The second is the corpus: a collection
of real modules becomes text only if they can be read, and real modules are
older than this project and stranger than its writer — packed with IT 2.14
compression, in sample mode, with the old instrument layout, with patterns
that overrun their length.

So this reader is strict about structure it cannot make sense of and
forgiving about data it can skip, and it says which it did:

  * the old instrument layout (Cmwt below 0x200) is refused by name
  * a compressed sample keeps its header and its `compressed` flag; its
    data is not unpacked here (the patterns are the corpus, and a
    transcription does not need the audio). `stored_frames` says how much
    audio stayed in the file, and the writer refuses to write such a module
    rather than write the sample empty
  * a stereo sample is read as both channels (the file keeps all of the
    left, then all of the right)
"""

import struct
import sys
from array import array

from .model import (NOTE_CUT, NOTE_FADE, NOTE_OFF, Cell, Envelope,
                    Instrument, Module, Pattern, Sample)


class ITReadError(ValueError):
    pass


def _cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", "replace")


def _envelope(raw: bytes, kind: str):
    flags, count, lb, le, slb, sle = raw[:6]
    if not flags & 1 and count == 0:
        return None
    count = min(count, 25)
    nodes = []
    filt = kind == "pitch" and bool(flags & 0x80)
    for i in range(count):
        value, tick = struct.unpack_from("<bH", raw, 6 + 3 * i)
        if kind == "volume":
            value = max(0, min(64, value))
        elif filt:
            value += 32
        nodes.append((tick, value))
    return Envelope(nodes=nodes,
                    loop=(lb, le) if flags & 2 else None,
                    sustain=(slb, sle) if flags & 4 else None,
                    enabled=bool(flags & 1), filter=filt)


def _instrument(blob: bytes) -> Instrument:
    if blob[:4] != b"IMPI":
        raise ITReadError("an instrument header does not start with IMPI")
    (filename, _zero, nna, dct, dca, fadeout, pps, ppc, gbv, dfp, rv, rp,
     _trk, _nos, _x, name, ifc, ifr, mch, mpr, bank) = struct.unpack_from(
        "<12sBBBBHbBBBBBHBB26sBBBBH", blob, 4)
    table = blob[0x40:0x130]
    note_map = [(table[2 * i], table[2 * i + 1]) for i in range(120)]
    return Instrument(
        name=_cstr(name), filename=_cstr(filename), nna=nna, dct=dct,
        dca=dca, fadeout=fadeout, pitch_pan_separation=pps,
        pitch_pan_center=ppc, global_volume=gbv,
        default_pan=None if dfp & 0x80 else dfp,
        random_volume=rv, random_pan=rp,
        cutoff=(ifc & 0x7F) if ifc & 0x80 else None,
        resonance=(ifr & 0x7F) if ifr & 0x80 else None,
        midi_channel=mch, midi_program=mpr, midi_bank=bank,
        note_map=note_map,
        volume_envelope=_envelope(blob[0x130:0x182], "volume"),
        pan_envelope=_envelope(blob[0x182:0x1D4], "pan"),
        pitch_envelope=_envelope(blob[0x1D4:0x226], "pitch"))


def _sample(blob: bytes, whole: bytes, load_data: bool) -> Sample:
    if blob[:4] != b"IMPS":
        raise ITReadError("a sample header does not start with IMPS")
    (filename, _zero, gvl, flags, vol, name, cvt, dfp, length, lbeg, lend,
     c5, sbeg, send, pointer, vis, vid, vir, vit) = struct.unpack_from(
        "<12sBBBB26sBBIIIIIIIBBBB", blob, 4)
    sample = Sample(
        name=_cstr(name), filename=_cstr(filename),
        bits=16 if flags & 2 else 8, c5speed=c5, volume=vol,
        global_volume=gvl,
        default_pan=(dfp & 0x7F) if dfp & 0x80 else None,
        loop=(lbeg, lend) if flags & 16 else None,
        loop_pingpong=bool(flags & 64),
        sustain_loop=(sbeg, send) if flags & 32 else None,
        sustain_pingpong=bool(flags & 128),
        vibrato_speed=vis, vibrato_depth=vid, vibrato_rate=vir,
        vibrato_wave=vit)
    if not flags & 1 or length == 0:
        sample.data = array("h" if sample.bits == 16 else "b")
        return sample
    if flags & 8:
        sample.compressed = True            # IT 2.14/2.15 packing
        sample.data = None
        sample.stored_frames = length
        return sample
    if not load_data:
        sample.data = None
        sample.stored_frames = length
        return sample
    width = 2 if sample.bits == 16 else 1
    channels = 2 if flags & 4 else 1       # stereo: all left, then all right
    raw = whole[pointer:pointer + length * width * channels]
    if len(raw) != length * width * channels:
        raise ITReadError(f"sample {sample.name!r} runs past the end of "
                          f"the file")
    parts = []
    for channel in range(channels):
        pcm = array("h" if width == 2 else "b")
        pcm.frombytes(raw[channel * length * width:
                          (channel + 1) * length * width])
        if width == 2 and sys.byteorder == "big":
            pcm.byteswap()
        if not cvt & 1:                     # unsigned: recentre
            offset = 32768 if width == 2 else 128
            pcm = array(pcm.typecode, (v - offset for v in pcm))
        parts.append(pcm)
    sample.data = parts[0]
    if channels == 2:
        sample.right = parts[1]
    return sample


def unpack_pattern(blob: bytes, channels: int = 64) -> Pattern:
    length, rows = struct.unpack_from("<HH", blob, 0)
    data = blob[8:8 + length]
    if len(data) != length:
        raise ITReadError("a pattern runs past the end of the file")
    pattern = Pattern(rows=rows)
    last_mask = [0] * 64
    last = [[0, 0, -1, 0, 0] for _ in range(64)]
    i, row = 0, 0
    while i < len(data) and row < rows:
        variable = data[i]
        i += 1
        if variable == 0:
            row += 1
            continue
        channel = (variable - 1) & 63
        if variable & 0x80:
            last_mask[channel] = data[i]
            i += 1
        mask = last_mask[channel]
        state = last[channel]
        if mask & 1:
            state[0] = data[i]
            i += 1
        if mask & 2:
            state[1] = data[i]
            i += 1
        if mask & 4:
            state[2] = data[i]
            i += 1
        if mask & 8:
            state[3], state[4] = data[i], data[i + 1]
            i += 2
        cell = pattern.cell(row, channel)
        if mask & (1 | 0x10):
            note = state[0]
            if note < 120:
                cell.note = note + 1
            else:                       # 255 off, 254 cut, the rest fade
                cell.note = {255: NOTE_OFF, 254: NOTE_CUT}.get(note, NOTE_FADE)
        if mask & (2 | 0x20):
            cell.instrument = state[1]
        if mask & (4 | 0x40):
            cell.volume = state[2]
        if mask & (8 | 0x80):
            cell.effect, cell.param = state[3], state[4]
    return pattern


def read(data: bytes, load_data: bool = True) -> Module:
    if data[:4] != b"IMPM":
        raise ITReadError("not an Impulse Tracker module (no IMPM)")
    if len(data) < 0xC0:
        raise ITReadError("shorter than an IT header")
    (title, minor, major, n_ord, n_ins, n_smp, n_pat, cwt, cmwt, flags,
     special, gv, mv, speed, tempo, sep, pwd, msg_len, msg_at,
     _res) = struct.unpack_from("<26sBBHHHHHHHHBBBBBBHII", data, 4)
    if cmwt < 0x200 and n_ins:
        raise ITReadError(f"this module uses the old instrument layout "
                          f"(Cmwt {cmwt:#06x}), which this reader does not read")
    module = Module(
        title=_cstr(title), rows_per_beat=minor, rows_per_measure=major,
        stereo=bool(flags & 1), use_instruments=bool(flags & 4),
        linear_slides=bool(flags & 8), old_effects=bool(flags & 16),
        gxx_link=bool(flags & 32), global_volume=gv, mix_volume=mv,
        speed=speed, tempo=tempo, pan_separation=sep, pitch_wheel_depth=pwd,
        created_with=cwt, compatible_with=cmwt)
    pans = data[0x40:0x80]
    module.channel_pan = [p & 0x7F for p in pans]
    module.channel_mute = [bool(p & 0x80) for p in pans]
    module.channel_volume = list(data[0x80:0xC0])
    orders = list(data[0xC0:0xC0 + n_ord])
    while orders and orders[-1] == 255:
        orders.pop()
    module.orders = orders

    at = 0xC0 + n_ord
    ins_at = struct.unpack_from(f"<{n_ins}I", data, at)
    smp_at = struct.unpack_from(f"<{n_smp}I", data, at + 4 * n_ins)
    pat_at = struct.unpack_from(f"<{n_pat}I", data, at + 4 * (n_ins + n_smp))
    at += 4 * (n_ins + n_smp + n_pat)
    if special & 2:                          # edit history: count + 8 bytes each
        (count,) = struct.unpack_from("<H", data, at)
        at += 2 + 8 * count
    if special & 8 or flags & 128:
        module.midi_config = data[at:at + (9 + 16 + 128) * 32]
        at += (9 + 16 + 128) * 32
    if special & 1 and msg_len:
        raw = data[msg_at:msg_at + msg_len].split(b"\0", 1)[0]
        module.message = raw.decode("ascii", "replace").replace("\r", "\n")

    module.instruments = [_instrument(data[o:o + 554]) for o in ins_at]
    module.samples = [_sample(data[o:o + 80], data, load_data) for o in smp_at]
    module.patterns = []
    for offset in pat_at:
        if offset == 0:                      # IT's way of saying "empty, 64 rows"
            module.patterns.append(Pattern(rows=64))
        else:
            module.patterns.append(unpack_pattern(data[offset:]))
    return module


def read_file(path: str, load_data: bool = True) -> Module:
    with open(path, "rb") as handle:
        return read(handle.read(), load_data)
