"""corpus.py — a collection of modules, read for looking things up.

A zip of modules teaches nothing by being loaded: no weights change. What a
small model can use is an index it can search and short, exact episodes it
can read — 8 to 16 rows of one pattern, the rows around them, the state the
song is in when they start, where they come from and who wrote them. This
module builds both, for every format libopenmpt plays (IT, XM, S3M, MOD),
and never writes to the originals.

Three things are kept apart, because mixing them is how a reading goes
wrong:

    native          the cell as its own format writes it, `C-5 01 v64 H93`,
                    formatted by libopenmpt in the module's notation
    normalized      libopenmpt's numbers: note, instrument, volume command,
                    effect and parameter. Its effect numbers are its own
                    (EFFECT_NAMES): an IT `H` is 5, an XM `4` is 5 too, and
                    neither is the letter
    interpretation  what a card says the cells do; a card is marked as an
                    interpretation and carries its confidence

Which patterns are *played* is a question for the engine, not for the order
list. A pattern can sit in the order list behind a jump and never play; one
can be stored and not listed at all; a loop plays rows twice. `trace` plays
the song in libopenmpt at a low rate and writes down every (order, pattern,
row) it passes with the speed and tempo there, stopping at the song's end or
at a time limit for songs that loop forever. `reached` is what the trace saw,
and a recommendation is only ever made from that.

The state at the start of an episode (each channel's instrument, note,
volume and effect memory) is replayed from the cells along the trace. It is
a row-level record — the last non-zero parameter of each effect on each
channel — and formats differ in which effects share a memory (IT's E/F/G
with compatible Gxx off, for one); the record says what was last written,
not which memory a given player will use.
"""

import ctypes
import hashlib
import json
import os
from typing import Dict, List, Optional

from . import openmpt

INDEX_FORMAT = "schism-corpus-index/1"
EPISODE_FORMAT = "schism-corpus-episode/1"

#: libopenmpt's effect numbers. 1-31 for IT's A-Z were checked by writing
#: each letter and reading it back (tests/test_corpus.py); the rest are its
#: enum's names for effects other formats have.
EFFECT_NAMES = {
    1: "arpeggio", 2: "portamento up", 3: "portamento down",
    4: "tone portamento", 5: "vibrato", 6: "tone portamento + volume slide",
    7: "vibrato + volume slide", 8: "tremolo", 9: "set panning",
    10: "sample offset", 11: "volume slide", 12: "position jump",
    13: "set volume", 14: "pattern break", 15: "retrigger", 16: "set speed",
    17: "set tempo", 18: "tremor", 19: "extended (MOD/XM E)",
    20: "extended (S3M/IT S)", 21: "channel volume",
    22: "channel volume slide", 23: "global volume",
    24: "global volume slide", 25: "key off", 26: "fine vibrato",
    27: "panbrello", 28: "extra-fine portamento", 29: "panning slide",
    30: "set envelope position", 31: "MIDI macro", 32: "smooth MIDI macro",
    33: "note delay and cut", 34: "parameter extension"}
#: the IT letter of each effect number, as measured
IT_LETTER = {16: "A", 12: "B", 14: "C", 11: "D", 3: "E", 2: "F", 4: "G",
             5: "H", 18: "I", 1: "J", 7: "K", 6: "L", 21: "M", 22: "N",
             10: "O", 29: "P", 15: "Q", 8: "R", 20: "S", 17: "T", 26: "U",
             23: "V", 24: "W", 9: "X", 27: "Y", 31: "Z"}
#: the effects that move the play position
FLOW = {12, 14}
#: libopenmpt command indices
NOTE, INSTRUMENT, VOLCMD, EFFECT, VOLUME, PARAM = range(6)

TRACE_RATE = 8000
#: frames rendered between position readings: 4 ms at 8 kHz, shorter than
#: the shortest row a module can have (speed 1 at tempo 255, 9.8 ms)
TRACE_CHUNK = 32
TRACE_LIMIT_S = 1200.0


class CorpusError(ValueError):
    pass


def _bind():
    L = openmpt._lib
    if L is None:
        raise CorpusError("libopenmpt is not installed (apt install "
                          "libopenmpt0)")
    if getattr(L, "_corpus_bound", False):
        return L
    P, I32 = ctypes.c_void_p, ctypes.c_int32
    for name in ("order", "pattern", "row", "speed", "tempo"):
        fn = getattr(L, f"openmpt_module_get_current_{name}")
        fn.restype, fn.argtypes = I32, [P]
    L._corpus_bound = True
    return L


class Module:
    """A file as libopenmpt reads it, with its hash."""

    def __init__(self, path: str):
        _bind()
        self.path = path
        with open(path, "rb") as handle:
            self.data = handle.read()
        self.sha256 = hashlib.sha256(self.data).hexdigest()
        self.song = openmpt.Song(self.data)
        self.channels = self.song.channels
        self.n_patterns = self.song.patterns

    def close(self):
        self.song.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @property
    def handle(self):
        return self.song._handle

    def rows(self, pattern: int) -> int:
        return self.song.pattern_rows(pattern)

    def normalized(self, pattern, row, channel) -> Dict[str, int]:
        c = self.song.command
        return {"note": c(pattern, row, channel, NOTE),
                "instrument": c(pattern, row, channel, INSTRUMENT),
                "volcmd": c(pattern, row, channel, VOLCMD),
                "volume": c(pattern, row, channel, VOLUME),
                "effect": c(pattern, row, channel, EFFECT),
                "param": c(pattern, row, channel, PARAM)}

    def native(self, pattern, row, channel) -> str:
        return self.song.cell(pattern, row, channel)

    def nonempty(self, pattern) -> bool:
        for row in range(self.rows(pattern)):
            for ch in range(self.channels):
                n = self.normalized(pattern, row, ch)
                if any(n.values()):
                    return True
        return False

    def orders(self) -> List[int]:
        """The stored order list: a pattern number per position; markers
        (`+++` skip, `---` end) come back as numbers past the patterns."""
        return [self.song.order_pattern(i) for i in range(self.song.orders)]


def static(module: Module) -> dict:
    """What the file stores: the order list and which stored patterns it
    names. Not what plays — see `trace`."""
    orders = module.orders()
    listed = sorted({p for p in orders if 0 <= p < module.n_patterns})
    nonempty = [p for p in range(module.n_patterns) if module.nonempty(p)]
    return {"orders": orders, "patterns": module.n_patterns,
            "nonempty": nonempty, "listed": listed,
            "unlisted_nonempty": [p for p in nonempty if p not in listed]}


def trace(module: Module, limit_s: float = TRACE_LIMIT_S,
          rate: int = TRACE_RATE) -> dict:
    """Play the song once in libopenmpt and write down every row it passes.

    -> {"rows": [[order, pattern, row, speed, tempo], ...] in the order they
    played (a row played twice is there twice), "reached": patterns that
    played, "seconds", "stopped": "song end" | "limit"}."""
    L = _bind()
    h = module.handle
    L.openmpt_module_set_repeat_count(h, 0)
    buf = (ctypes.c_float * (TRACE_CHUNK * 2))()
    rows = []
    last = None
    frames = 0
    stopped = "song end"
    limit = int(limit_s * rate)
    # libopenmpt plays a few frames past the end, at the position it would
    # loop back to, before it stops; its own length of the song is where
    # the song ends (measured: 15.36 s by duration, 15.46 s rendered, the
    # extra at order 0 row 0)
    end = int(module.song.duration * rate) + 1
    while True:
        here = (L.openmpt_module_get_current_order(h),
                L.openmpt_module_get_current_pattern(h),
                L.openmpt_module_get_current_row(h))
        speed = L.openmpt_module_get_current_speed(h)
        tempo = L.openmpt_module_get_current_tempo(h)
        got = L.openmpt_module_read_interleaved_float_stereo(
            h, rate, TRACE_CHUNK, buf)
        if not got or frames >= end:
            break
        if here != last:
            rows.append([*here, speed, tempo])
            last = here
        frames += got
        if frames >= limit:
            stopped = "limit"
            break
    reached = sorted({r[1] for r in rows if 0 <= r[1] < module.n_patterns})
    return {"rows": rows, "reached": reached,
            "seconds": round(min(frames, end) / rate, 2), "stopped": stopped,
            "limit_s": limit_s}


def _first_visit(rows, order, row):
    for i, (o, _, r, _, _) in enumerate(rows):
        if o == order and r == row:
            return i
    return None


def entry_state(module: Module, rows, upto: int) -> dict:
    """Each channel's state when the song reaches rows[upto]: the last
    instrument, note and volume written, and the last non-zero parameter of
    each effect (by libopenmpt's numbering, with its name). Replayed from
    the cells of the rows played before it."""
    channels = {}
    for o, pattern, row, _, _ in rows[:upto]:
        if not 0 <= pattern < module.n_patterns:
            continue
        for ch in range(module.channels):
            n = module.normalized(pattern, row, ch)
            if not any(n.values()):
                continue
            state = channels.setdefault(ch, {"instrument": 0, "note": 0,
                                             "volume": None, "memory": {}})
            if n["instrument"]:
                state["instrument"] = n["instrument"]
            if 1 <= n["note"] <= 120:
                state["note"] = n["note"]
            if n["volcmd"]:
                state["volume"] = [n["volcmd"], n["volume"]]
            if n["effect"] and n["param"]:
                state["memory"][n["effect"]] = n["param"]
    out = {}
    for ch, state in sorted(channels.items()):
        out[str(ch + 1)] = {
            "instrument": state["instrument"], "note": state["note"],
            "volume": state["volume"],
            "memory": {EFFECT_NAMES.get(e, f"effect {e}"): f"{p:02X}"
                       for e, p in sorted(state["memory"].items())}}
    speed, tempo = (rows[upto][3], rows[upto][4]) if upto < len(rows) \
        else (None, None)
    return {"speed": speed, "tempo": tempo, "channels": out}


def episode(path: str, order: int, row: int, rows: int = 12,
            context: int = 2, channels: Optional[List[int]] = None,
            source: Optional[dict] = None) -> dict:
    """`rows` rows starting at (order, row) as the song first plays them,
    `context` rows either side, the state the song is in when they start,
    and the instruments they use."""
    if not 4 <= rows <= 32:
        raise CorpusError("an episode is 4-32 rows (8-16 is the useful size)")
    with Module(path) as module:
        played = trace(module)["rows"]
        start = _first_visit(played, order, row)
        if start is None:
            raise CorpusError(
                f"the song never plays order {order} row {row}; reached "
                f"patterns: {sorted({r[1] for r in played})}")
        pattern = played[start][1]
        want = channels or list(range(module.channels))
        lines = []
        lo = max(0, start - context)
        hi = min(len(played), start + rows + context)
        used = set()
        for i in range(lo, hi):
            o, p, r, speed, tempo = played[i]
            cells = []
            for ch in want:
                n = module.normalized(p, r, ch)
                if not any(n.values()):
                    continue
                if n["instrument"]:
                    used.add(n["instrument"])
                cells.append({"channel": ch + 1,
                              "native": module.native(p, r, ch),
                              "normalized": dict(
                                  n, effect_name=EFFECT_NAMES.get(n["effect"])
                                  if n["effect"] else None)})
            lines.append({"order": o, "pattern": p, "row": r,
                          "speed": speed, "tempo": tempo,
                          "context": not start <= i < start + rows,
                          "cells": cells})
        state = entry_state(module, played, start)
        names = {i: module.song.instrument_name(i - 1) for i in sorted(used)}
        out = {"format": EPISODE_FORMAT,
               "source": dict(source or {}, file=os.path.basename(path),
                              sha256=module.sha256),
               "address": {"order": order, "pattern": pattern, "row": row,
                           "rows": rows, "channels": [c + 1 for c in want]},
               "entry_state": state, "lines": lines,
               "instruments": {str(k): v for k, v in names.items()},
               "kept_apart": "native = the format's own text; normalized = "
                             "libopenmpt's numbers (its effect numbering, "
                             "named in effect_name); an interpretation "
                             "belongs on a card"}
        if module.data[:4] == b"IMPM":
            out["it_instruments"] = _it_headers(module.data, used)
        return out


def _it_headers(data: bytes, used) -> dict:
    """The IT instrument headers an episode uses: envelopes, NNA, filter."""
    from . import it_read
    from .forge import program
    try:
        mod = it_read.read(data, load_data=False)
    except it_read.ITReadError as error:
        return {"unreadable": str(error)}
    out = {}
    for number in sorted(used):
        if 1 <= number <= len(mod.instruments):
            info = program.explain_instrument(mod, number)
            info.pop("samples", None)
            out[str(number)] = info
    return out


def _windows(module: Module, pattern: int, size: int = 16):
    """Per window of `size` rows: notes, distinct effects, instruments."""
    out = []
    for start in range(0, module.rows(pattern), size):
        notes, effects, instruments = 0, set(), set()
        for row in range(start, min(module.rows(pattern), start + size)):
            for ch in range(module.channels):
                n = module.normalized(pattern, row, ch)
                if 1 <= n["note"] <= 120:
                    notes += 1
                if n["effect"] and n["effect"] not in FLOW:
                    effects.add(n["effect"])
                if n["instrument"]:
                    instruments.add(n["instrument"])
        out.append({"row": start, "notes": notes,
                    "effects": sorted(EFFECT_NAMES.get(e, str(e))
                                      for e in effects),
                    "instruments": len(instruments)})
    return out


def index_module(path: str, source: Optional[dict] = None,
                 candidates: int = 3, limit_s: float = TRACE_LIMIT_S) -> dict:
    """One module's index entry: hash, format, what it stores, what plays,
    and a few reached windows worth reading (by variety of effects, not by
    density of notes)."""
    with Module(path) as module:
        stored = static(module)
        played = trace(module, limit_s)
        first = {}
        for o, p, r, _, _ in played["rows"]:
            first.setdefault((p, r), o)
        windows = []
        for p in played["reached"]:
            for w in _windows(module, p):
                order = first.get((p, w["row"]))
                if order is None or w["notes"] == 0:
                    continue
                windows.append(dict(w, pattern=p, order=order))
        windows.sort(key=lambda w: (-len(w["effects"]), -w["instruments"],
                                    w["order"]))
        return {
            "file": os.path.basename(path), "sha256": module.sha256,
            "source": dict(source or {}),
            "format": module.song.metadata("type"),
            "tracker": module.song.metadata("tracker"),
            "title": module.song.metadata("title"),
            "channels": module.channels, "stored": stored,
            "played": {"reached": played["reached"],
                       "listed_never_played": [
                           p for p in stored["listed"]
                           if p not in played["reached"]
                           and p in stored["nonempty"]],
                       "rows_played": len(played["rows"]),
                       "seconds": played["seconds"],
                       "stopped": played["stopped"]},
            "candidates": windows[:candidates],
            "selection": "windows of 16 rows in patterns the song plays, "
                         "ranked by how many kinds of effect and instrument "
                         "they use; an episode reads one with its context"}


def index(paths, manifest: Optional[dict] = None, limit_s=TRACE_LIMIT_S):
    """Index entries for many files; `manifest` maps a file name to its
    source record (author, origin, licence) which is carried along."""
    out = []
    for path in paths:
        source = (manifest or {}).get(os.path.basename(path), {})
        try:
            out.append(index_module(path, source, limit_s=limit_s))
        except (openmpt.OpenMPTError, OSError, CorpusError) as error:
            out.append({"file": os.path.basename(path), "error": str(error)})
    return out


def write_index(entries, path: str) -> str:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"format": INDEX_FORMAT,
                                 "modules": len(entries)}) + "\n")
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path
