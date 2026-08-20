"""
vgm_player.py — play a .vgm back through the emulators.

The mirror image of vgm.py. Since a VGM is nothing but "write this
register, wait this long", and chipgen already owns a YM2612 and an
SN76489 that take register writes, replaying one is a short loop.

Two things this buys:

  * verification. tests/ replays chipgen's own export and checks the audio
    matches what the sequencer rendered directly. If the two ever diverge,
    the exported file is lying about the music, and that is exactly the bug
    class you would otherwise ship silently.
  * import. Any Genesis VGM — a rip from a real game, a DefleMask or
    Furnace export — renders here too. The engine stops being a one-way
    door.

Unsupported chips are skipped rather than fatal: a VGM whose header lists
a YM2151 alongside the Genesis pair still plays its Genesis half.
"""

import gzip
import struct

import audio as _audio
import opn2
import sn76489
import vgm as vgm_mod


class UnsupportedCommand(Exception):
    pass


#: command byte -> extra bytes to consume, for commands we step over
_SKIP_SIZES = {
    0x4F: 1, 0x51: 2, 0x54: 2, 0x55: 2, 0x56: 2, 0x57: 2, 0x58: 2, 0x59: 2,
    0x5A: 2, 0x5B: 2, 0x5C: 2, 0x5D: 2, 0x5E: 2, 0x5F: 2,
    0xA0: 2, 0xB0: 2, 0xB1: 2, 0xB2: 2, 0xB3: 2, 0xB4: 2, 0xB5: 2, 0xB6: 2,
    0xB7: 2, 0xB8: 2, 0xB9: 2, 0xBA: 2, 0xBB: 2, 0xBC: 2, 0xBD: 2, 0xBE: 2,
    0xBF: 2,
    0xC0: 3, 0xC1: 3, 0xC2: 3, 0xC3: 3, 0xC4: 3, 0xC5: 3, 0xC6: 3, 0xC7: 3,
    0xC8: 3, 0xD0: 3, 0xD1: 3, 0xD2: 3, 0xD3: 3, 0xD4: 3, 0xD5: 3, 0xD6: 3,
    0xE0: 4, 0xE1: 4,
}


def load(path_or_bytes) -> bytes:
    if isinstance(path_or_bytes, (bytes, bytearray)):
        raw = bytes(path_or_bytes)
    else:
        with open(path_or_bytes, "rb") as fh:
            raw = fh.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    if raw[:4] != b"Vgm ":
        raise ValueError("not a VGM file (bad magic)")
    return raw


def render(path_or_bytes, target_rate: int = 44100, max_seconds: float = 600.0,
           fm_gain: float = 1.0, psg_gain: float = 1.0):
    """Replay a VGM and return mixed stereo audio at target_rate."""
    raw = load(path_or_bytes)
    header = vgm_mod.read_header(raw)

    ym_clock = header["ym2612_clock"] or opn2.NTSC_CHIP_CLOCK
    psg_clock = header["psg_clock"] or sn76489.NTSC_PSG_CLOCK
    ym = opn2.YM2612(clock=float(ym_clock))
    psg = sn76489.SN76489(clock=float(psg_clock))

    fm_rate = ym.native_rate
    psg_rate = psg.native_rate
    fm_chunks, psg_chunks = [], []
    fm_pending = psg_pending = 0.0
    elapsed = 0.0

    pos = header["data_offset"]
    end = min(len(raw), header["eof_offset"])
    pcm_bank = bytearray()
    pcm_pos = 0

    def wait(samples44100: int):
        nonlocal fm_pending, psg_pending, elapsed
        seconds = samples44100 / float(vgm_mod.DEFAULT_SAMPLE_RATE)
        elapsed += seconds
        fm_pending += seconds * fm_rate
        psg_pending += seconds * psg_rate

    def flush():
        nonlocal fm_pending, psg_pending
        n = int(fm_pending)
        if n > 0:
            fm_chunks.append(ym.render(n))
            fm_pending -= n
        n = int(psg_pending)
        if n > 0:
            psg_chunks.append(psg.render(n))
            psg_pending -= n

    while pos < end and elapsed < max_seconds:
        cmd = raw[pos]
        pos += 1

        if cmd == vgm_mod.CMD_END:
            break
        if cmd == vgm_mod.CMD_PSG:
            flush()
            psg.write(raw[pos]); pos += 1
        elif cmd in (vgm_mod.CMD_YM2612_PORT0, vgm_mod.CMD_YM2612_PORT1):
            flush()
            port = 0 if cmd == vgm_mod.CMD_YM2612_PORT0 else 2
            ym.write(port, raw[pos], raw[pos + 1]); pos += 2
        elif cmd == vgm_mod.CMD_WAIT_LONG:
            wait(struct.unpack_from("<H", raw, pos)[0]); pos += 2
        elif cmd == vgm_mod.CMD_WAIT_735:
            wait(735)
        elif cmd == vgm_mod.CMD_WAIT_882:
            wait(882)
        elif 0x70 <= cmd <= 0x7F:
            wait((cmd & 0x0F) + 1)
        elif 0x80 <= cmd <= 0x8F:
            # DAC byte straight from the PCM bank, then an inline wait
            flush()
            if pcm_pos < len(pcm_bank):
                ym.write(0, 0x2A, pcm_bank[pcm_pos])
                pcm_pos += 1
            wait(cmd & 0x0F)
        elif cmd == 0x67:                      # data block
            pos += 1                           # 0x66 marker
            block_type = raw[pos]; pos += 1
            size = struct.unpack_from("<I", raw, pos)[0] & 0x7FFFFFFF; pos += 4
            if block_type == 0x00:             # YM2612 PCM
                pcm_bank += raw[pos:pos + size]
            pos += size
        elif cmd == 0xE0:                      # seek in PCM bank
            pcm_pos = struct.unpack_from("<I", raw, pos)[0]; pos += 4
        elif cmd in _SKIP_SIZES:
            pos += _SKIP_SIZES[cmd]
        else:
            raise UnsupportedCommand(
                f"VGM command 0x{cmd:02X} at offset 0x{pos - 1:X} is not handled")

    flush()
    ym.close()
    psg.close()

    fm_audio = _audio.resample(_audio.concat(fm_chunks, 2), fm_rate, target_rate)
    psg_audio = _audio.resample(_audio.concat(psg_chunks, 1), psg_rate, target_rate)
    n = max(len(fm_audio), len(psg_audio))
    out = _audio.zeros(n, 2)
    if len(fm_audio):
        _audio.add_stereo_into(out, fm_audio, fm_gain)
    if len(psg_audio):
        _audio.add_mono_into_stereo(out, psg_audio, psg_gain)
    peak = _audio.peak(out)
    if peak > 1.0:
        out = _audio.scale(out, 0.98 / peak)
    return out


def main(argv):
    import wavio
    if not argv:
        print("usage: python3 vgm_player.py IN.vgm [OUT.wav]")
        return 2
    src = argv[0]
    dst = argv[1] if len(argv) > 1 else src.rsplit(".", 1)[0] + ".wav"
    buf = render(src)
    wavio.write(dst, buf)
    print(f"{src} -> {dst}: {wavio.describe(buf)}")
    return 0


if __name__ == "__main__":
    import sys
    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    sys.exit(main(sys.argv[1:]))
