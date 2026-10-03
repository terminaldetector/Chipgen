"""levels.py — who is audible, measured one channel at a time.

A mix is a sum, and a sum cannot tell you that the pad is 40 dB under the
drums. This renders the module once per channel with every other channel
muted (the mute is a flag in the file header, so there is nothing to
re-synthesise) and reports each channel's own loudness against the whole.

The question it answers is the one this project's chip sibling was built
around — "I wrote an instrument and cannot hear it" — and in Impulse
Tracker the usual causes are quiet and silent in the same way: a sample
volume of 8, an instrument global volume of 0, an envelope that starts at
0 and never rises, a filter closed on a note. The render succeeds either
way.

Needs libopenmpt (see openmpt.py).
"""

import math

from . import it_write, openmpt


def _db(value: float) -> float:
    return 20.0 * math.log10(value) if value > 1e-9 else -120.0


def _rms(frames) -> float:
    if not len(frames):
        return 0.0
    total = 0.0
    for start in range(0, len(frames), 65536):
        chunk = frames[start:start + 65536]
        total += sum(v * v for v in chunk)
    return math.sqrt(total / len(frames))


def _muted_but(blob: bytes, keep: int) -> bytes:
    """The module with every channel but `keep` (0-based) muted in the
    header. Channel pan bytes live at 0x40; bit 7 is the mute flag."""
    data = bytearray(blob)
    for channel in range(64):
        if channel != keep:
            data[0x40 + channel] |= 0x80
        else:
            data[0x40 + channel] &= 0x7F
    return bytes(data)


def voice_levels(module, sample_rate: int = 22050):
    """-> list of dicts, one per channel that holds a note, loudest first.

    Keys: channel (1-based), rms_db, peak_db, share_db (this channel's
    energy against the full mix's, so 0 would be a channel that is the
    whole song), notes, and `audible` (above -40 dB of the loudest).
    Rendered at a low rate: the question is loudness, not fidelity.
    """
    blob = it_write.build(module)
    with openmpt.Song(blob) as song:
        full = song.render(sample_rate)
    full_energy = _rms(full) ** 2 or 1e-12
    notes = [0] * 64
    for order in module.orders:
        for row in module.patterns[order].cells:
            for c, cell in enumerate(row):
                if 1 <= cell.note <= 120:
                    notes[c] += 1
    rows = []
    for channel in range(module.channels_used()):
        if not notes[channel]:
            continue
        with openmpt.Song(_muted_but(blob, channel)) as solo:
            frames = solo.render(sample_rate)
        rms = _rms(frames)
        rows.append({"channel": channel + 1, "notes": notes[channel],
                     "rms_db": _db(rms), "peak_db": _db(openmpt.peak(frames)),
                     "share_db": 10.0 * math.log10(max(rms * rms, 1e-12)
                                                   / full_energy)})
    loudest = max((r["rms_db"] for r in rows), default=-120.0)
    for r in rows:
        r["audible"] = r["rms_db"] > loudest - 40.0
    rows.sort(key=lambda r: -r["rms_db"])
    return rows


def report(module) -> str:
    rows = voice_levels(module)
    lines = [f"{'ch':>3} {'notes':>6} {'rms dB':>8} {'peak dB':>8} "
             f"{'vs mix':>7}"]
    for r in rows:
        flag = "" if r["audible"] else "   <-- under -40 dB of the loudest"
        lines.append(f"{r['channel']:3d} {r['notes']:6d} {r['rms_db']:8.1f} "
                     f"{r['peak_db']:8.1f} {r['share_db']:7.1f}{flag}")
    return "\n".join(lines)
