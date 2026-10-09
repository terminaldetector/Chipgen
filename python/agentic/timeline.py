"""timeline.py — one clock for the whole piece.

The state keeps the music as tracker rows; the chips play events; a
listener hears seconds. Every question the loop asks crosses those three
("what plays in bar 6?", "which note is sounding at 12.4 s?", "which rows
does section B cover the second time it plays?"), so they are answered in
one place, from one parse:

    rows      every grid row in play order: its section, which time the
              order is playing that section (slot), the row inside the
              section, the bar, the tick and the second it starts on, and
              the index of its first event
    notes     every note of every voice: start and end in seconds and rows,
              pitch, velocity, the instrument it sounded with, the event
              indices that started and ended it
    events    each chip event with the second it happens

The tracker text is generated from the state (`trk`), parsed once with the
tracker's row clock (`tracker.loads(text, rows=...)`), and nothing is
re-derived elsewhere: a range, a render window, a trace and a patch all
read this.
"""

import re
from typing import Dict, List, Optional

import events as E
import levels
import tracker
from .state import AgenticError, rows_per_bar

#: Events that start a sound on a voice, and those that end one.
_ONS = (E.FMNoteOn, E.PSGToneOn, E.PSGNoiseOn)
_OFFS = (E.FMNoteOff, E.PSGToneOff, E.PSGNoiseOff)


def _note_pitch(note: str, octave: int) -> int:
    return octave * 12 + E.NOTE_NAMES.index(note)


def pitch_name(pitch: int) -> str:
    name = E.NOTE_NAMES[pitch % 12]
    return f"{name}{'-' if len(name) == 1 else ''}{pitch // 12}"


# -- tracker text from the state ------------------------------------------------
def trk(content: dict, title: str = "") -> str:
    """The piece as tracker text: preamble, one pattern per section (with a
    `mark` naming it), and the order. Deterministic: the same content gives
    the same text."""
    s = content["structure"]
    lines = []
    if title:
        lines.append(f"title {title}")
    lines += [f"bpm {s['bpm']:g}", f"lpb {s['lpb']}", f"ticks {s['ticks']:g}"]
    for voice, ins in sorted(content["instruments"].items(),
                             key=lambda kv: kv[1]["channel"]):
        channel = ins["channel"]
        if ins.get("patch") and channel.startswith("fm"):
            lines.append(f"inst {channel} {ins['patch']}")
            for key, value in sorted(ins.get("overrides", {}).items()):
                op, field = key.split(".")
                lines.append(f"op {channel} {op} {field} {int(value)}")
        if ins.get("pan") and channel.startswith("fm"):
            lines.append(f"pan {channel} {ins['pan']}")
        if ins.get("vol") is not None:
            lines.append(f"vol {channel} {int(ins['vol'])}")
    lines.append("cols " + " ".join(content["columns"]))
    for section in content["sections"]:
        lines.append("")
        lines.append(f"pattern {section['id']}")
        lines.append(f"mark {section['id']}")
        by_row = {}
        for d in section.get("directives", []):
            by_row.setdefault(int(d["before_row"]), []).append(d["text"])
        for i, row in enumerate(section["rows"]):
            lines.extend(by_row.get(i, []))
            lines.append("  ".join(cell or "..." for cell in row))
        lines.extend(by_row.get(len(section["rows"]), []))
    if content["order"]:
        lines.append("")
        lines.append("order " + " ".join(content["order"]))
    return "\n".join(lines) + "\n"


class Timeline:
    """The parsed piece and its clock (see the module docstring)."""

    def __init__(self, content: dict, text: str, events, meta, rows,
                 event_seconds, notes):
        self.content = content
        self.text = text
        self.events = events
        self.meta = meta
        self.rows = rows
        self.event_seconds = event_seconds
        self.notes = notes
        self.rows_per_bar = rows_per_bar(content)
        last = rows[-1] if rows else None
        self.duration = (last["t1"] if last else 0.0)

    # -- lookups -------------------------------------------------------------
    def row_at(self, seconds: float) -> Optional[dict]:
        lo, hi = 0, len(self.rows) - 1
        if hi < 0 or seconds < 0:
            return None
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.rows[mid]["t0"] <= seconds:
                lo = mid
            else:
                hi = mid - 1
        row = self.rows[lo]
        return row if row["t0"] <= seconds < row["t1"] else None

    def slots(self) -> List[dict]:
        """Each time the order plays a section: slot, section, rows, time."""
        out = {}
        for r in self.rows:
            entry = out.setdefault(r["slot"], {
                "slot": r["slot"], "section": r["section"],
                "row0": r["index"], "t0": r["t0"]})
            entry["row1"] = r["index"] + 1
            entry["t1"] = r["t1"]
        return [out[k] for k in sorted(out)]

    def sounding(self, t0: float, t1: float, voice: str = None) -> List[dict]:
        return [n for n in self.notes
                if n["start"] < t1 and n["end"] > t0
                and (voice is None or n["voice"] == voice)]

    def voices(self) -> List[str]:
        seen = []
        for n in self.notes:
            if n["voice"] not in seen:
                seen.append(n["voice"])
        return seen

    def range(self, spec: str) -> dict:
        return parse_range(self, spec)

    def summary(self) -> dict:
        return {"duration_s": round(self.duration, 3),
                "rows": len(self.rows), "bars": -(-len(self.rows)
                                                   // self.rows_per_bar),
                "slots": [{k: (round(v, 3) if isinstance(v, float) else v)
                           for k, v in s.items()} for s in self.slots()],
                "voices": self.voices(), "notes": len(self.notes),
                "ticks_per_row": self.meta.ticks_per_row(),
                "ticks_per_second": self.meta.ticks_per_second}


# -- building ---------------------------------------------------------------------
def build(content: dict, title: str = "") -> Timeline:
    text = trk(content, title)
    rows: List[dict] = []
    try:
        events, meta = tracker.loads(text, rows=rows)
    except tracker.TrackerError as error:
        message = str(error)
        m = re.match(r"line (\d+)", message)
        line = int(m.group(1)) if m else None
        source = text.splitlines()[line - 1] if line else None
        raise AgenticError("score_error", message, line=line,
                           source=source) from None
    # seconds of every event: ticks through every Tempo change
    tps = float(meta.ticks_per_second)
    tick = 0
    seconds = 0.0
    event_seconds = []
    tick_seconds = {}             # tick -> seconds, at each event
    for ev in events:
        event_seconds.append(seconds)
        tick_seconds.setdefault(tick, seconds)
        if isinstance(ev, E.Wait):
            tick += ev.ticks
            seconds += ev.ticks / tps
        elif isinstance(ev, E.Tempo):
            tps = max(1.0, float(ev.ticks_per_second))
    tick_seconds.setdefault(tick, seconds)
    end_seconds = seconds

    def at_tick(t: int) -> float:
        if t in tick_seconds:
            return tick_seconds[t]
        # between two known ticks: linear at the rate then in force
        known = max(k for k in tick_seconds if k <= t)
        return tick_seconds[known] + (t - known) / tps

    # rows -> section, slot, row-in-section, bar, seconds
    order = content["order"]
    lengths = {s["id"]: len(s["rows"]) for s in content["sections"]}
    expected = sum(lengths[name] for name in order)
    if len(rows) != expected:
        raise AgenticError(
            "row_count", f"the tracker played {len(rows)} rows where the "
            f"order adds up to {expected}: an `end` directive or a pattern "
            f"error cut the piece", played=len(rows), expected=expected)
    per_bar = rows_per_bar(content)
    out_rows = []
    i = 0
    for slot, name in enumerate(order):
        for r in range(lengths[name]):
            raw = rows[i]
            t0 = at_tick(raw["tick"])
            t1 = at_tick(raw["tick"] + raw["ticks"])
            out_rows.append({"index": i, "slot": slot, "section": name,
                             "row": r, "bar": i // per_bar,
                             "bar_in_section": r // per_bar,
                             "line": raw["line"], "tick": raw["tick"],
                             "t0": t0, "t1": t1, "event": raw["event"]})
            i += 1
    for k, r in enumerate(out_rows):
        r["event_end"] = (out_rows[k + 1]["event"] if k + 1 < len(out_rows)
                          else len(events))

    def row_of_event(index: int) -> Optional[dict]:
        lo, hi = 0, len(out_rows) - 1
        if hi < 0 or index < out_rows[0]["event"]:
            return None
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if out_rows[mid]["event"] <= index:
                lo = mid
            else:
                hi = mid - 1
        return out_rows[lo]

    # notes per voice, with the instrument each sounded with
    instrument = {}
    voice_name = {ins["channel"]: voice
                  for voice, ins in content["instruments"].items()}
    open_notes: Dict[str, dict] = {}
    notes = []

    def close(column, index):
        note = open_notes.pop(column, None)
        if note is None:
            return
        note["end"] = event_seconds[index] if index < len(events) \
            else end_seconds
        note["off_event"] = index
        end_row = row_of_event(index)
        note["end_row"] = end_row["index"] if end_row else len(out_rows)
        notes.append(note)

    for index, ev in enumerate(events):
        if isinstance(ev, E.FMInstrumentSelect):
            instrument[f"fm{ev.channel}"] = ev.instrument
            continue
        column = levels._voice_of(ev)
        if column is None:
            continue
        if isinstance(ev, _OFFS):
            close(column, index)
            continue
        if isinstance(ev, _ONS) or isinstance(ev, E.DACSample):
            close(column, index)
            row = row_of_event(index)
            if isinstance(ev, E.DACSample):
                pitch, velocity, label = None, None, ev.name
            elif isinstance(ev, E.PSGNoiseOn):
                pitch, velocity, label = None, None, "noise"
            else:
                pitch = _note_pitch(ev.note, ev.octave)
                velocity = getattr(ev, "velocity", None)
                if velocity is None:
                    velocity = getattr(ev, "volume", None)
                label = pitch_name(pitch)
            note = {"voice": voice_name.get(column, column),
                    "column": column, "pitch": pitch, "name": label,
                    "velocity": velocity, "start": event_seconds[index],
                    "on_event": index,
                    "row": row["index"] if row else None,
                    "slot": row["slot"] if row else None,
                    "section": row["section"] if row else None,
                    "instrument": instrument.get(column)}
            if isinstance(ev, E.DACSample):
                # a one-shot: it ends when its sample does, not at a key-up
                import sanity
                note["end"] = note["start"] + (sanity._dac_sample_seconds(
                    ev.name, ev.rate) or 0.0)
                note["off_event"] = None
                note["end_row"] = None
                notes.append(note)
            else:
                open_notes[column] = note
    for column in list(open_notes):
        close(column, len(events))
    notes.sort(key=lambda n: (n["start"], n["column"]))
    for n in notes:
        if n.get("end_row") is None and n["end"] is not None:
            r = None
            for row in out_rows:
                if row["t0"] >= n["end"]:
                    r = row["index"]
                    break
            n["end_row"] = r if r is not None else len(out_rows)
    return Timeline(content, text, events, meta, out_rows, event_seconds,
                    notes)


# -- ranges -----------------------------------------------------------------------
_RANGE = re.compile(
    r"^(?:(?P<all>all)"
    r"|(?P<sec>[A-Za-z_][\w']*)(?:#(?P<occ>\d+))?(?::bars?\s*(?P<sb0>\d+)(?:-(?P<sb1>\d+))?)?"
    r"|slot\s*(?P<slot>\d+)"
    r"|bars?\s*(?P<b0>\d+)(?:-(?P<b1>\d+))?"
    r"|rows?\s*(?P<r0>\d+)(?:-(?P<r1>\d+))?"
    r"|t\s*(?P<t0>[\d.]+)-(?P<t1>[\d.]+))$")


def parse_range(tl: Timeline, spec: str) -> dict:
    """A musical range from text. Forms: `all`, `B` (the first time section
    B plays), `B#2` (the second), `B:bars 2-3` (bars of that occurrence,
    from 1), `slot 4` (the fifth entry of the order, from 0), `bars 5-8`
    (from 1), `rows 32-63` (from 0, inclusive), `t 3.2-5.0` (seconds).
    -> {row0, row1 (exclusive), t0, t1, label, slots}."""
    m = _RANGE.match(spec.strip())
    if not m:
        raise AgenticError(
            "bad_range", f"{spec!r} is not a range; use all, B, B#2, "
            f"B:bars 2-3, slot 4, bars 5-8, rows 32-63 or t 3.2-5.0",
            spec=spec)
    rows = tl.rows
    if not rows:
        raise AgenticError("empty_piece", "the piece has no rows yet")
    if m.group("all"):
        r0, r1 = 0, len(rows)
    elif m.group("sec"):
        name = m.group("sec")
        occurrence = int(m.group("occ") or 1)
        slots = [s for s in tl.slots() if s["section"] == name]
        if not slots:
            raise AgenticError("no_section", f"section {name!r} does not "
                               f"play; the order is "
                               f"{tl.content['order']}", section=name)
        if not 1 <= occurrence <= len(slots):
            raise AgenticError("no_occurrence", f"{name} plays "
                               f"{len(slots)} time(s), not {occurrence}",
                               section=name)
        s = slots[occurrence - 1]
        r0, r1 = s["row0"], s["row1"]
        if m.group("sb0"):
            b0 = int(m.group("sb0"))
            b1 = int(m.group("sb1") or b0)
            r0, r1 = (s["row0"] + (b0 - 1) * tl.rows_per_bar,
                      min(s["row1"], s["row0"] + b1 * tl.rows_per_bar))
    elif m.group("slot") is not None:
        k = int(m.group("slot"))
        slots = tl.slots()
        if not 0 <= k < len(slots):
            raise AgenticError("no_slot", f"the order has {len(slots)} "
                               f"entries; slot {k} is not one", slot=k)
        r0, r1 = slots[k]["row0"], slots[k]["row1"]
    elif m.group("b0"):
        b0 = int(m.group("b0"))
        b1 = int(m.group("b1") or b0)
        r0, r1 = (b0 - 1) * tl.rows_per_bar, b1 * tl.rows_per_bar
    elif m.group("r0"):
        r0 = int(m.group("r0"))
        r1 = int(m.group("r1") or r0) + 1
    else:
        t0, t1 = float(m.group("t0")), float(m.group("t1"))
        if t0 >= tl.duration or t1 <= t0:
            raise AgenticError("out_of_piece", f"{spec!r}: the piece lasts "
                               f"{tl.duration:.3f} s", spec=spec)
        first = tl.row_at(t0) or rows[0]
        last = tl.row_at(max(t0, t1 - 1e-6)) or rows[-1]
        r0, r1 = first["index"], last["index"] + 1
    if r0 >= len(rows) or r1 <= r0 or r0 < 0:
        raise AgenticError(
            "out_of_piece", f"{spec!r} lies outside the piece: it has "
            f"{len(rows)} rows, {-(-len(rows) // tl.rows_per_bar)} bars",
            spec=spec)
    clamped = r1 > len(rows)
    r1 = min(r1, len(rows))
    slots = sorted({rows[i]["slot"] for i in range(r0, r1)})
    return {"spec": spec, "row0": r0, "row1": r1, "clamped": clamped,
            "t0": round(rows[r0]["t0"], 6), "t1": round(rows[r1 - 1]["t1"], 6),
            "bars": [rows[r0]["bar"] + 1, rows[r1 - 1]["bar"] + 1],
            "slots": slots,
            "sections": sorted({rows[i]["section"] for i in range(r0, r1)})}
