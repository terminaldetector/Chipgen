"""trace.py — from a moment in the audio back to what made it.

A rendered MP3 is the end of a chain: register writes made by events made
from notes in rows of a section. Anything wrong in the sound happened at
one of those links, and an audio-to-MIDI guess at the notes would be a
guess where the notes are known. So this follows the chain back:

    audio time -> the notes sounding (voice, pitch, velocity, row, section,
                  source line of the tracker text)
               -> the instrument each sounded with (patch, overrides)
               -> the register writes the chip actually received around
                  that time, decoded (channel, operator, field, value)

The writes come from the render itself: a `TraceWriter` stands where the
VGM writer would and records every byte the emulated chips are sent, with
the second it was sent at. That is the trace of what was played, not of
what the score asked for — a write the driver dropped is not in it.
"""

from typing import Optional

from sequencer import Sequencer

from . import render as render_mod

_YM_FIELDS = {0x30: "dt_mul", 0x40: "tl", 0x50: "ks_ar", 0x60: "am_d1r",
              0x70: "d2r", 0x80: "sl_rr", 0x90: "ssg"}
#: register slot (address bits 2-3) -> operator in block-diagram numbering
_SLOT_TO_OP = (1, 3, 2, 4)


def decode_ym(port: int, addr: int, data: int) -> dict:
    """One YM2612 register write, named."""
    high = port >= 2
    if not high and addr < 0x30:
        if addr == 0x28:
            ch = data & 0x07
            channel = (ch & 3) + (3 if ch & 4 else 0)
            ops = data >> 4
            return {"channel": f"fm{channel}", "what": "key",
                    "value": "on" if ops else "off", "slots": ops}
        if addr == 0x22:
            return {"channel": None, "what": "lfo", "value": data}
        if addr == 0x27:
            return {"channel": "fm2", "what": "ch3_mode", "value": data >> 6}
        if addr == 0x2B:
            return {"channel": "fm5", "what": "dac_enable",
                    "value": bool(data & 0x80)}
        if addr == 0x2A:
            return {"channel": "fm5", "what": "dac", "value": data}
        return {"channel": None, "what": f"reg_{addr:02x}", "value": data}
    group = addr & 0xF0
    low = addr & 0x03
    channel = (low + (3 if high else 0)) if low != 3 else None
    name = f"fm{channel}" if channel is not None else None
    if group in _YM_FIELDS:
        op = _SLOT_TO_OP[(addr >> 2) & 3]
        field = _YM_FIELDS[group]
        value = data
        if field == "tl":
            value = data & 0x7F
        return {"channel": name, "what": f"op{op}.{field}", "value": value}
    if 0xA0 <= addr <= 0xA2:
        return {"channel": name, "what": "freq_lo", "value": data}
    if 0xA4 <= addr <= 0xA6:
        return {"channel": name, "what": "block_freq_hi", "value": data}
    if 0xA8 <= addr <= 0xAE:
        return {"channel": "fm2", "what": "ch3_op_freq", "value": data}
    if 0xB0 <= addr <= 0xB2:
        return {"channel": name, "what": "fb_alg",
                "value": {"feedback": (data >> 3) & 7, "algorithm": data & 7}}
    if 0xB4 <= addr <= 0xB6:
        return {"channel": name, "what": "pan_ams_pms",
                "value": {"left": bool(data & 0x80), "right": bool(data & 0x40),
                          "ams": (data >> 4) & 3, "pms": data & 7}}
    return {"channel": name, "what": f"reg_{addr:02x}", "value": data}


class TraceWriter:
    """Stands in for vgm.VGMWriter: records each write with its time."""

    def __init__(self, start_s: float = 0.0, keep_dac: bool = False):
        self.t = start_s
        self.writes = []
        self.keep_dac = keep_dac
        self.dac_bytes = 0
        self.opl_clock = 0
        self.nes_clock = 0
        self._psg_latch = 0

    def ym_logger(self, port: int, addr: int, data: int):
        if port < 2 and addr == 0x2A and not self.keep_dac:
            self.dac_bytes += 1
            return
        row = decode_ym(port, addr, data)
        row["t"] = self.t
        row["chip"] = "ym2612"
        row["raw"] = [port, addr, data]
        self.writes.append(row)

    def psg_logger(self, byte: int):
        if byte & 0x80:
            channel = (byte >> 5) & 3
            self._psg_latch = channel
            kind = "volume" if byte & 0x10 else "tone_lo"
            value = byte & 0x0F
        else:
            channel = self._psg_latch
            kind = "tone_hi"
            value = byte & 0x3F
        self.writes.append({"t": self.t, "chip": "sn76489",
                            "channel": "noise" if channel == 3
                            else f"psg{channel}",
                            "what": kind, "value": value, "raw": [byte]})

    def opl_logger(self, addr: int, data: int):
        self.writes.append({"t": self.t, "chip": "ym3812", "channel": None,
                            "what": f"reg_{addr:02x}", "value": data,
                            "raw": [addr, data]})

    def nes_logger(self, addr: int, data: int):
        self.writes.append({"t": self.t, "chip": "rp2a03", "channel": None,
                            "what": f"reg_{addr:04x}", "value": data,
                            "raw": [addr, data]})

    def advance(self, seconds: float):
        if seconds > 0:
            self.t += seconds

    def set_loop_point(self):
        pass


def register_trace(timeline, rng: dict, voices=None,
                   preroll_s: float = render_mod.DEFAULT_PREROLL_S) -> dict:
    """The register writes the chips received over `rng` (chased in the
    same way a range render is), with times in the piece's seconds."""
    rows = timeline.rows
    row_s = rows[0]["t1"] - rows[0]["t0"]
    back = int(-(-preroll_s // max(row_s, 1e-6)))
    w0 = max(0, rng["row0"] - back)
    w1 = min(len(rows), rng["row1"] + 1)
    keep = render_mod._columns(timeline, voices)
    event_list, info = render_mod.chased_events(timeline, w0, w1, keep)
    start = rows[w0]["t0"]
    writer = TraceWriter(start_s=start)
    seq = Sequencer(ticks_per_second=timeline.meta.ticks_per_second,
                    target_rate=render_mod.RATE)
    seq._run(event_list, writer=writer, want_audio=False)
    # the chase writes the state at the window's start in one instant; the
    # writes that are the range's own come after its first second
    # times are summed from the renderer's sample counts, so a write on a
    # row boundary can land a fraction of a sample early: 1 ms of slack
    inside = [w for w in writer.writes
              if rng["t0"] - 0.001 <= w["t"] < rng["t1"] - 0.001]
    return {"range_s": [rng["t0"], rng["t1"]], "writes": inside,
            "chased_at_s": round(start, 4), "dac_bytes": writer.dac_bytes,
            "chase": info}


def instrument_of(timeline, voice: str) -> Optional[dict]:
    """A voice's instrument as the state defines it: the bank patch, the
    overrides, and the operator values those give."""
    ins = timeline.content["instruments"].get(voice)
    if ins is None:
        return None
    out = {"voice": voice, "channel": ins["channel"],
           "patch": ins.get("patch"), "overrides": ins.get("overrides", {})}
    if ins.get("patch") and ins["channel"].startswith("fm"):
        import instruments
        patch = instruments.BANK.get(ins["patch"])
        if patch is not None:
            fm = patch.fm if hasattr(patch, "fm") else patch
            out["algorithm"] = getattr(fm, "algorithm", None)
            out["feedback"] = getattr(fm, "feedback", None)
            ops = {}
            for i, op in enumerate(getattr(fm, "operators", [])):
                number = _SLOT_TO_OP[i]
                ops[number] = {"tl": op.total_level, "ar": op.attack_rate,
                               "d1r": op.decay_rate, "d2r": op.sustain_rate,
                               "sl": op.sustain_level, "rr": op.release_rate,
                               "mul": op.multiple, "dt": op.detune}
            for key, value in out["overrides"].items():
                number, field = key.split(".")
                ops.setdefault(int(number), {})[field] = int(value)
            out["operators"] = {str(k): v for k, v in sorted(ops.items())}
            out["carriers"] = sorted(
                _SLOT_TO_OP[i] for i in fm.carrier_indices()) \
                if hasattr(fm, "carrier_indices") else None
    return out


def explain(timeline, t0: float, t1: float, voice: str = None,
            writes: bool = True, limit: int = 40) -> dict:
    """What makes the sound between t0 and t1: the notes sounding, their
    rows and source lines, their instruments, and (if `writes`) the
    register writes of their channels in that time, the first `limit`."""
    # a note that ends where the window starts is not sounding in it (the
    # sums of row lengths put its end a hair past the next onset)
    notes = [n for n in timeline.sounding(t0, t1, voice)
             if n["end"] > t0 + 1e-3]
    lines = timeline.text.splitlines()
    out_notes = []
    for n in notes:
        row = timeline.rows[n["row"]] if n.get("row") is not None else None
        out_notes.append({
            "voice": n["voice"], "column": n["column"], "note": n["name"],
            "velocity": n["velocity"],
            "start_s": round(n["start"], 4), "end_s": round(n["end"], 4),
            "section": n["section"], "slot": n["slot"],
            "row_in_section": row["row"] if row else None,
            "source_line": row["line"] if row else None,
            "source": lines[row["line"] - 1].strip() if row else None,
            "instrument": n["instrument"]})
    voices = sorted({n["voice"] for n in notes})
    out = {"range_s": [round(t0, 4), round(t1, 4)], "notes": out_notes,
           "instruments": {v: instrument_of(timeline, v) for v in voices
                           if v in timeline.content["instruments"]}}
    if writes and notes:
        rng = timeline.range(f"t {t0}-{t1}")
        trace = register_trace(timeline, rng, voices=voices)
        columns = {n["column"] for n in notes}
        relevant = [w for w in trace["writes"]
                    if w.get("channel") in columns]
        out["register_writes"] = [
            {k: (round(v, 4) if k == "t" else v) for k, v in w.items()
             if k != "raw"} for w in relevant[:limit]]
        out["register_writes_total"] = len(relevant)
    return out
