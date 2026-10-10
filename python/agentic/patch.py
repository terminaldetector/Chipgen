"""patch.py — the smallest change to the native material, and what it
actually changed.

A patch says what it touches the way the spec asks for it — section and
rows, voice, instrument, parameter, time — and changes only that, in the
tracker cells and directives the piece is kept in:

    level       a voice's notes in a range made louder or quieter, in dB:
                FM by velocity (the chip attenuates carriers in 0.75 dB
                steps from it), PSG and noise by their attenuation (2 dB
                steps), the DAC by its 0-1 volume
    transpose   a voice's notes in a range moved by semitones (an octave,
                usually: a register change, not a new melody)
    param       one operator field of a voice's FM instrument, for the
                whole piece or in a range only. Carrier levels are
                refused: a key-on below velocity 127 rewrites them from
                the bank patch (opn2.note_on), so an override there would
                come and go with the dynamics — use `level` for loudness
    vol         the channel's `vol` from one row to another, restored after
    instrument  the patch an FM voice plays, for the whole piece (from the
                built-in bank or a bank the project loaded; a forge
                instrument's detune layer comes with it)

and the musical ones, for when the timing, the line or the texture
explains a problem rather than a level:

    articulation  notes in a range shortened to a share of their written
                  length (`gate`: a key-off written inside the note, on the
                  row grid), or `legato`: a key-off between two notes a row
                  apart removed so they touch
    density       an accompanying voice thinned (every other onset taken
                  out) or doubled (a repeat of each note half-way to the
                  next, where the grid has room)
    rhythm        an accompanying voice's on-beat onsets pushed a row
                  early (`anticipate`), or its off-beat onsets delayed by
                  a share of a row with the cell's delay command (`swing`)
    voicing       a pad's notes moved to the nearest tone of the chord the
                  section's harmony names there, so it moves by the
                  smallest step (voice leading); onsets kept
    pan           an FM voice's place in the stereo field (the YM2612's
                  left/right bits; the PSG has none)
    echo          a voice's notes copied to a free FM channel a few rows
                  later, quieter, panned the other way: a new voice of
                  role "echo"

The melody's pitches and onsets are not the musical ops' to change:
density and rhythm refuse a melodic voice, voicing works on pads.

The realised change is reported next to the asked one: the steps the chip
has, the cells that could not move (a velocity already at 127, a PSG
already silent, a note that would leave the register), and every edit.

A range inside a section that plays more than once is changed in that
occurrence only (scope "occurrence"): the section is copied for that slot
of the order and the copy edited, and the report says so. Scope "section"
changes every occurrence.
"""

import copy
import math
import re
from typing import List, Optional, Tuple

import events as E
import fx
import tracker
from .state import AgenticError

_NOTE = re.compile(r"^([A-Ga-g][-#b]?)(\d)(?::(.+))?$")
FM_DB_PER_STEP = 0.75
PSG_DB_PER_STEP = 2.0
#: The lowest notes a voice plays at their written pitch (sanity.FLOORS).
_FLOOR = {"psg": 2 * 12 + 9}    # A-2


def _split(cell: str) -> Tuple[str, str]:
    token, codes = fx.split_cell(cell.strip())
    return token.strip(), cell.strip()[len(token.strip()):]


def _pitch(token: str) -> Optional[Tuple[int, Optional[str]]]:
    m = _NOTE.match(token)
    if not m:
        return None
    parsed = tracker.parse_note(f"{m.group(1)}{m.group(2)}")
    if parsed is None:
        return None
    note, octave, _ = parsed
    return octave * 12 + E.NOTE_NAMES.index(note), m.group(3)


def _name(pitch: int) -> str:
    name = E.NOTE_NAMES[pitch % 12]
    return f"{name}{'-' if len(name) == 1 else ''}{pitch // 12}"


def _velocity_for(velocity: int, db: float) -> int:
    """The FM velocity that moves the note by `db` (the chip's own curve,
    opn2.level_to_attenuation, linear in amplitude)."""
    amp = (velocity / 127.0) * (10 ** (db / 20.0))
    return max(1, min(127, int(round(amp * 127.0))))


def _fm_db(velocity: int) -> float:
    import opn2
    return -opn2.level_to_attenuation(velocity) * FM_DB_PER_STEP


# -- where a range lives in the content ----------------------------------------
def _slots(content: dict) -> List[dict]:
    out, row = [], 0
    lengths = {s["id"]: len(s["rows"]) for s in content["sections"]}
    for slot, sid in enumerate(content["order"]):
        out.append({"slot": slot, "section": sid, "row0": row,
                    "row1": row + lengths[sid]})
        row += lengths[sid]
    return out


def _section(content: dict, sid: str) -> dict:
    for s in content["sections"]:
        if s["id"] == sid:
            return s
    raise AgenticError("no_section", f"no section {sid!r}")


def _targets(content: dict, rng: dict, scope: str):
    """[(section dict, first row, last row exclusive)] the range covers,
    copying a repeated section for its slot when the scope is the
    occurrence. -> (targets, notes about copies)."""
    if scope not in ("occurrence", "section"):
        raise AgenticError("bad_scope", f"scope is occurrence or section, "
                           f"not {scope!r}")
    notes = []
    targets = []
    for s in _slots(content):
        a, b = max(rng["row0"], s["row0"]), min(rng["row1"], s["row1"])
        if a >= b:
            continue
        sid = s["section"]
        uses = content["order"].count(sid)
        if scope == "occurrence" and uses > 1:
            base = _section(content, sid)
            k = 2
            while any(x["id"] == f"{sid}.{k}" for x in content["sections"]):
                k += 1
            clone = copy.deepcopy(base)
            clone["id"] = f"{sid}.{k}"
            clone["copied_from"] = sid
            content["sections"].insert(content["sections"].index(base) + 1,
                                       clone)
            content["order"][s["slot"]] = clone["id"]
            notes.append(f"section {sid} plays {uses} times; slot "
                         f"{s['slot']} now plays a copy, {clone['id']}, so "
                         f"the change is in that occurrence only")
            sid = clone["id"]
        targets.append((_section(content, sid), a - s["row0"],
                        b - s["row0"], s["slot"]))
    return targets, notes


def _column(content: dict, voice: str) -> Tuple[str, int]:
    ins = content["instruments"].get(voice)
    column = ins["channel"] if ins else voice
    if column not in content["columns"]:
        raise AgenticError("no_voice", f"no voice or column {voice!r} in "
                           f"this piece", voice=voice)
    return column, content["columns"].index(column)


# -- the operations ------------------------------------------------------------------
def level(content: dict, voice: str, rng: dict, db: float,
          scope: str = "occurrence") -> Tuple[dict, dict]:
    """Every note of `voice` in the range `db` louder (or quieter)."""
    new = copy.deepcopy(content)
    column, k = _column(new, voice)
    targets, notes = _targets(new, rng, scope)
    edits, stuck, realised = [], 0, []
    for section, a, b, slot in targets:
        for r in range(a, b):
            cell = section["rows"][r][k]
            token, codes = _split(cell)
            if column.startswith("fm"):
                p = _pitch(token)
                if p is None:
                    continue
                pitch, param = p
                vel = int(param) if param else 127
                new_vel = _velocity_for(vel, db)
                if new_vel == vel:
                    stuck += 1
                    continue
                realised.append(_fm_db(new_vel) - _fm_db(vel))
                new_cell = f"{_name(pitch)}:{new_vel}{codes}"
            elif column.startswith("psg"):
                p = _pitch(token)
                if p is None:
                    continue
                pitch, param = p
                att = int(param) if param else 0
                new_att = max(0, min(15, att - int(round(db / PSG_DB_PER_STEP))))
                if new_att == att:
                    stuck += 1
                    continue
                realised.append(-(new_att - att) * PSG_DB_PER_STEP)
                new_cell = f"{_name(pitch)}:{new_att}{codes}" \
                    if new_att else f"{_name(pitch)}{codes}"
            elif column == "noise":
                m = re.match(r"^([wpWP])([0-3])(?::(\d+))?$", token)
                if not m:
                    continue
                att = int(m.group(3) or 0)
                new_att = max(0, min(15, att - int(round(db / PSG_DB_PER_STEP))))
                if new_att == att:
                    stuck += 1
                    continue
                realised.append(-(new_att - att) * PSG_DB_PER_STEP)
                new_cell = f"{m.group(1)}{m.group(2)}" + (
                    f":{new_att}" if new_att else "") + codes
            elif column == "dac":
                if token in tracker.HOLD_TOKENS or \
                        token.lower() in tracker.OFF_TOKENS:
                    continue
                name, _, vol = token.partition(":")
                old = float(vol) if vol else 1.0
                value = max(0.0, min(1.0, old * 10 ** (db / 20.0)))
                if abs(value - old) < 1e-6:
                    stuck += 1
                    continue
                realised.append(20 * math.log10(max(value, 1e-6) / old))
                new_cell = f"{name}:{value:.3f}{codes}"
            else:
                continue
            edits.append({"section": section["id"], "slot": slot, "row": r,
                          "column": column, "from": cell, "to": new_cell})
            section["rows"][r][k] = new_cell
    if not edits:
        raise AgenticError("nothing_to_change",
                           f"no note of {voice} in {rng['spec']} could move "
                           f"{db:+.1f} dB ({stuck} already at the limit)",
                           voice=voice)
    mean = sum(realised) / len(realised)
    delta = {"op": "level", "voice": voice, "column": column,
             "range": rng["spec"], "rows": [rng["row0"], rng["row1"]],
             "scope": scope, "asked_db": db,
             "realised_db": {"mean": round(mean, 2),
                             "min": round(min(realised), 2),
                             "max": round(max(realised), 2)},
             "units": {"fm": "velocity -> carrier TL, 0.75 dB steps",
                       "psg": "attenuation, 2 dB steps",
                       "noise": "attenuation, 2 dB steps",
                       "dac": "volume 0-1"}[
                 "fm" if column.startswith("fm") else
                 "psg" if column.startswith("psg") else column],
             "cells": len(edits), "at_limit": stuck, "copies": notes,
             "edits": edits[:12], "edits_total": len(edits)}
    return new, delta


def transpose(content: dict, voice: str, rng: dict, semitones: int,
              scope: str = "occurrence") -> Tuple[dict, dict]:
    """`voice`'s notes in the range moved by `semitones`."""
    new = copy.deepcopy(content)
    column, k = _column(new, voice)
    if not column.startswith(("fm", "psg")):
        raise AgenticError("not_pitched", f"{voice} ({column}) has no "
                           f"pitch to move", voice=voice)
    targets, notes = _targets(new, rng, scope)
    edits = []
    for section, a, b, slot in targets:
        for r in range(a, b):
            cell = section["rows"][r][k]
            token, codes = _split(cell)
            p = _pitch(token)
            if p is None:
                continue
            pitch, param = p
            moved = pitch + semitones
            if column.startswith("psg") and moved < _FLOOR["psg"]:
                raise AgenticError(
                    "below_floor", f"{_name(pitch)} on {column} would go "
                    f"to {_name(moved)}, under the PSG's floor A-2 where "
                    f"the divider clamps and the note sounds sharp",
                    voice=voice)
            if not 0 <= moved // 12 <= 7:
                raise AgenticError("out_of_range", f"{_name(pitch)} "
                                   f"{semitones:+d} leaves octaves 0-7",
                                   voice=voice)
            new_cell = _name(moved) + (f":{param}" if param else "") + codes
            edits.append({"section": section["id"], "slot": slot, "row": r,
                          "column": column, "from": cell, "to": new_cell})
            section["rows"][r][k] = new_cell
    if not edits:
        raise AgenticError("nothing_to_change", f"{voice} plays no note in "
                           f"{rng['spec']}", voice=voice)
    return new, {"op": "transpose", "voice": voice, "column": column,
                 "range": rng["spec"], "rows": [rng["row0"], rng["row1"]],
                 "scope": scope, "semitones": semitones,
                 "cells": len(edits), "copies": notes,
                 "pitch_classes_kept": semitones % 12 == 0,
                 "edits": edits[:12], "edits_total": len(edits)}


def _instrument(content: dict, voice: str):
    ins = content["instruments"].get(voice)
    if not ins or not ins.get("patch") or not ins["channel"].startswith("fm"):
        raise AgenticError("no_fm_instrument", f"{voice} has no FM "
                           f"instrument to change", voice=voice)
    import instruments
    patch = instruments.BANK.get(ins["patch"])
    if patch is None:
        raise AgenticError("unknown_patch", f"{ins['patch']!r} is not in "
                           f"the bank", voice=voice)
    return ins, patch


def carriers(content: dict, voice: str) -> List[int]:
    _ins, patch = _instrument(content, voice)
    order = (1, 3, 2, 4)
    return sorted(order[i] for i in patch.carrier_indices())


_FIELDS = {"tl": (0, 127, "total_level"), "ar": (0, 31, "attack_rate"),
           "d1r": (0, 31, "decay_rate"), "d2r": (0, 31, "sustain_rate"),
           "sl": (0, 15, "sustain_level"), "rr": (0, 15, "release_rate"),
           "mul": (0, 15, "multiple"), "dt": (0, 7, "detune")}


def param(content: dict, voice: str, key: str, delta: int = None,
          value: int = None, rng: dict = None,
          scope: str = "occurrence") -> Tuple[dict, dict]:
    """`op<N>.<field>` of `voice`'s instrument moved by `delta` (or set to
    `value`): for the whole piece (an instrument override), or, given a
    range, only there — an `op` directive at the range's first row and one
    restoring the instrument's value after its last (the chip keeps an
    operator write until the next one; the range render chases it in)."""
    new = copy.deepcopy(content)
    ins, patch = _instrument(new, voice)
    m = re.match(r"^op([1-4])\.(\w+)$", key)
    if not m or m.group(2) not in _FIELDS:
        raise AgenticError("bad_param", f"{key!r} is not op1-4.<"
                           f"{'|'.join(_FIELDS)}>", key=key)
    op, field = int(m.group(1)), m.group(2)
    if field == "tl" and op in carriers(content, voice):
        raise AgenticError(
            "carrier_level", f"op{op} is a carrier of {ins['patch']}: its "
            f"level is rewritten from the bank patch at every key-on below "
            f"velocity 127 (opn2.note_on), so an override would come and go "
            f"with the dynamics. Change the level with `level` instead",
            voice=voice, key=key)
    lo, hi, attr = _FIELDS[field]
    slot = (1, 3, 2, 4).index(op)
    base = getattr(patch.operators[slot], attr)
    current = int(ins.get("overrides", {}).get(f"{op}.{field}", base))
    target = current + delta if delta is not None else value
    if target is None:
        raise AgenticError("bad_param", "give delta or value")
    clamped = max(lo, min(hi, int(target)))
    if clamped == current:
        raise AgenticError("nothing_to_change", f"{key} of {voice} is "
                           f"already {current} (limit {lo}-{hi})", key=key)
    out = {"op": "param", "voice": voice, "instrument": ins["patch"],
           "parameter": key, "from": current, "to": clamped,
           "bank_value": base, "asked": target, "clamped": clamped != target,
           "units": "TL steps of 0.75 dB on that operator's output"
           if field == "tl" else f"{field} register value"}
    channel = ins["channel"]
    if rng is None:
        ins.setdefault("overrides", {})[f"{op}.{field}"] = clamped
        out.update(scope="piece (every note of this voice)",
                   native=f"op {channel} {op} {field} {clamped} after "
                          f"inst {channel} {ins['patch']}")
    else:
        targets, notes = _targets(new, rng, scope)
        placed = []
        for section, a, b, slot in targets:
            section.setdefault("directives", []).append(
                {"before_row": a, "text": f"op {channel} {op} {field} "
                                          f"{clamped}"})
            section["directives"].append(
                {"before_row": b, "text": f"op {channel} {op} {field} "
                                          f"{current}"})
            placed.append({"section": section["id"], "slot": slot,
                           "rows": [a, b]})
        out.update(scope=scope, range=rng["spec"],
                   rows=[rng["row0"], rng["row1"]], copies=notes,
                   placed=placed,
                   native=f"op {channel} {op} {field} {clamped} at the "
                          f"range's start, {current} after it")
    if field == "tl":
        out["operator_db"] = round(-(clamped - current) * FM_DB_PER_STEP, 2)
    return new, out


def vol(content: dict, voice: str, rng: dict, value: int,
        scope: str = "occurrence") -> Tuple[dict, dict]:
    """The channel's `vol` at `value` over the range, restored after."""
    new = copy.deepcopy(content)
    column, _k = _column(new, voice)
    ins = new["instruments"].get(voice, {})
    restore = int(ins.get("vol", 127 if column.startswith("fm") else 15))
    targets, notes = _targets(new, rng, scope)
    placed = []
    for section, a, b, slot in targets:
        section.setdefault("directives", []).append(
            {"before_row": a, "text": f"vol {column} {int(value)}"})
        section["directives"].append(
            {"before_row": b, "text": f"vol {column} {restore}"})
        placed.append({"section": section["id"], "slot": slot,
                       "rows": [a, b]})
    return new, {"op": "vol", "voice": voice, "column": column,
                 "range": rng["spec"], "value": value, "restored_to": restore,
                 "scope": scope, "copies": notes, "placed": placed}


def instrument(content: dict, voice: str, patch: str) -> Tuple[dict, dict]:
    """`voice` plays `patch` for the whole piece (an FM voice; the patch
    must be in an installed bank — the built-in one or one the project
    loaded). The voice's operator overrides belonged to the old patch and
    are dropped, and the report lists them."""
    import instruments
    new = copy.deepcopy(content)
    ins = new["instruments"].get(voice)
    if ins is None:
        raise AgenticError("no_voice", f"no voice {voice!r} in this piece",
                           voice=voice)
    if not ins["channel"].startswith("fm"):
        raise AgenticError("not_fm", f"{voice} plays {ins['channel']}, "
                           f"which has no patches to choose", voice=voice)
    if patch not in instruments.BANK:
        raise AgenticError("unknown_patch", f"no installed bank has "
                           f"{patch!r}; load the bank that has it "
                           f"(load_bank)", patch=patch)
    old = ins.get("patch")
    if old == patch:
        raise AgenticError("nothing_to_change", f"{voice} already plays "
                           f"{patch}", voice=voice)
    dropped = ins.pop("overrides", None) or {}
    ins["patch"] = patch
    from . import banks
    return new, {"op": "instrument", "voice": voice, "from": old,
                 "to": patch, "overrides_dropped": dropped,
                 "layers_on": banks.layer_columns(new, voice),
                 "scope": "piece (every note of this voice)",
                 "native": f"inst {ins['channel']} {patch}"}


# -- the musical operations -----------------------------------------------------------
_MELODIC = ("lead", "melody", "counter")
_HOLD = tracker.HOLD_TOKENS


def _is_hold(cell: str) -> bool:
    return cell.strip() in _HOLD


def _is_off(cell: str) -> bool:
    return cell.strip().lower() in tracker.OFF_TOKENS


def _onset(column: str, cell: str) -> bool:
    """A cell that starts a sound in this column."""
    token, _codes = _split(cell)
    if _is_hold(token) or _is_off(token):
        return False
    if column.startswith(("fm", "psg")):
        return _pitch(token) is not None
    return True                         # noise wN/pN, dac sample names


def _accompaniment(content: dict, voice: str, op: str):
    role = content["instruments"].get(voice, {}).get("role", "")
    if role in _MELODIC:
        raise AgenticError("melodic_voice", f"{op} changes onsets; {voice} "
                           f"is the {role}, whose notes are the melody and "
                           f"not this op's to move", voice=voice, op=op)


def articulation(content: dict, voice: str, rng: dict, gate: float = None,
                 legato: bool = False,
                 scope: str = "occurrence") -> Tuple[dict, dict]:
    """Notes of `voice` in the range shortened to `gate` of their written
    length (a key-off inside the note, on the row grid), or, with
    `legato`, the one-row gaps between notes closed."""
    if (gate is None) == (not legato):
        raise AgenticError("bad_patch", "articulation takes gate (0-1) or "
                           "legato=true")
    if gate is not None and not 0.1 <= gate < 1.0:
        raise AgenticError("bad_patch", "gate is a share of the written "
                           "length, 0.1-0.99")
    new = copy.deepcopy(content)
    column, k = _column(new, voice)
    targets, notes = _targets(new, rng, scope)
    edits, stuck, lengths = [], 0, []
    for section, a, b, slot in targets:
        rows = section["rows"]
        for r in range(a, b):
            if not _onset(column, rows[r][k]):
                continue
            end = r + 1
            while end < len(rows) and _is_hold(rows[end][k]):
                end += 1
            length = end - r
            if gate is not None:
                cut = r + max(1, int(round(length * gate)))
                if cut >= end:
                    stuck += 1          # one row: the grid cannot shorten it
                    continue
                edits.append({"section": section["id"], "slot": slot,
                              "row": cut, "column": column,
                              "from": rows[cut][k], "to": "==="})
                rows[cut][k] = "==="
                lengths.append([length, cut - r])
            elif end < len(rows) and _is_off(rows[end][k]) and \
                    end + 1 < len(rows) and _onset(column, rows[end + 1][k]):
                edits.append({"section": section["id"], "slot": slot,
                              "row": end, "column": column,
                              "from": rows[end][k], "to": "..."})
                rows[end][k] = "..."
                lengths.append([length, length + 1])
    if not edits:
        raise AgenticError("nothing_to_change", f"no note of {voice} in "
                           f"{rng['spec']} could change its length "
                           f"({stuck} are one row long)", voice=voice)
    return new, {"op": "articulation", "voice": voice, "column": column,
                 "range": rng["spec"], "rows": [rng["row0"], rng["row1"]],
                 "scope": scope, "gate": gate, "legato": legato,
                 "lengths_rows": lengths[:16], "cells": len(edits),
                 "too_short": stuck, "copies": notes,
                 "edits": edits[:12], "edits_total": len(edits),
                 "native": "a key-off (===) written inside the note"
                 if gate is not None else "a key-off removed: the notes "
                                          "touch"}


def density(content: dict, voice: str, rng: dict, mode: str = "thin",
            scope: str = "occurrence") -> Tuple[dict, dict]:
    """An accompanying voice with every other onset taken out (`thin`) or
    each note repeated half-way to the next where there is room
    (`double`)."""
    if mode not in ("thin", "double"):
        raise AgenticError("bad_patch", "density mode is thin or double")
    _accompaniment(content, voice, "density")
    new = copy.deepcopy(content)
    column, k = _column(new, voice)
    targets, notes = _targets(new, rng, scope)
    edits = []
    for section, a, b, slot in targets:
        rows = section["rows"]
        onsets = [r for r in range(a, b) if _onset(column, rows[r][k])]
        if mode == "thin":
            for i, r in enumerate(onsets):
                if i % 2 == 1:
                    prev_sounding = i > 0
                    to = "===" if prev_sounding and \
                        column.startswith(("fm", "psg")) else "..."
                    edits.append({"section": section["id"], "slot": slot,
                                  "row": r, "column": column,
                                  "from": rows[r][k], "to": to})
                    rows[r][k] = to
        else:
            percussive = not column.startswith(("fm", "psg"))
            for i, r in enumerate(onsets):
                nxt = onsets[i + 1] if i + 1 < len(onsets) else b
                mid = r + (nxt - r) // 2
                if nxt - r < 2 or mid <= r:
                    continue
                free = _is_hold(rows[mid][k]) or (
                    percussive and _is_off(rows[mid][k]))
                if not free:
                    continue
                edits.append({"section": section["id"], "slot": slot,
                              "row": mid, "column": column,
                              "from": rows[mid][k], "to": rows[r][k]})
                was_off = _is_off(rows[mid][k])
                rows[mid][k] = rows[r][k]
                # a hit that replaced a key-off ends where the old one
                # ended: the burst stays as short as the others
                if was_off and mid + 1 < len(rows) and \
                        _is_hold(rows[mid + 1][k]):
                    rows[mid + 1][k] = "==="
    if not edits:
        raise AgenticError("nothing_to_change", f"{voice} has no onset in "
                           f"{rng['spec']} to {mode}", voice=voice)
    return new, {"op": "density", "voice": voice, "column": column,
                 "mode": mode, "range": rng["spec"],
                 "rows": [rng["row0"], rng["row1"]], "scope": scope,
                 "cells": len(edits), "copies": notes,
                 "onsets_changed": len(edits) * (1 if mode == "double"
                                                 else -1),
                 "edits": edits[:12], "edits_total": len(edits)}


def rhythm(content: dict, voice: str, rng: dict, mode: str = "anticipate",
           share: float = 0.33,
           scope: str = "occurrence") -> Tuple[dict, dict]:
    """An accompanying voice's on-beat onsets a row early (`anticipate`,
    where that row is free), or its off-beat onsets delayed by `share`
    of a row (`swing`, the cell's delay command)."""
    if mode not in ("anticipate", "swing"):
        raise AgenticError("bad_patch", "rhythm mode is anticipate or swing")
    _accompaniment(content, voice, "rhythm")
    new = copy.deepcopy(content)
    column, k = _column(new, voice)
    lpb = int(new["structure"]["lpb"])
    ticks_per_row = int(round(float(new["structure"]["ticks"]) * 60.0
                              / float(new["structure"]["bpm"]) / lpb))
    targets, notes = _targets(new, rng, scope)
    edits = []
    for section, a, b, slot in targets:
        rows = section["rows"]
        for r in range(a, b):
            cell = rows[r][k]
            if not _onset(column, cell):
                continue
            if mode == "anticipate":
                if r % lpb == 0 and r - 1 >= a and \
                        _is_hold(rows[r - 1][k]) and r % (2 * lpb) != 0:
                    edits.append({"section": section["id"], "slot": slot,
                                  "row": r, "column": column, "from": cell,
                                  "to": "...", "moved_to": r - 1})
                    rows[r - 1][k] = cell
                    rows[r][k] = "..."
            else:
                half = max(1, lpb // 2)
                if r % half == 0 and (r // half) % 2 == 1:
                    ticks = max(1, min(ticks_per_row - 1,
                                       int(round(share * ticks_per_row))))
                    token, _codes = _split(cell)
                    if any(c.upper().startswith("C") for c in _codes):
                        continue        # a delay is there already
                    to = f"{cell}/C{ticks:02X}"
                    edits.append({"section": section["id"], "slot": slot,
                                  "row": r, "column": column, "from": cell,
                                  "to": to})
                    rows[r][k] = to
    if not edits:
        raise AgenticError("nothing_to_change", f"{voice} has no onset in "
                           f"{rng['spec']} that {mode} can move",
                           voice=voice)
    return new, {"op": "rhythm", "voice": voice, "column": column,
                 "mode": mode, "range": rng["spec"],
                 "rows": [rng["row0"], rng["row1"]], "scope": scope,
                 "share_of_row": share if mode == "swing" else None,
                 "cells": len(edits), "copies": notes,
                 "edits": edits[:12], "edits_total": len(edits)}


def voicing(content: dict, voice: str, rng: dict,
            scope: str = "occurrence") -> Tuple[dict, dict]:
    """A pad's notes moved to the nearest tone of the chord the section's
    harmony names at each (within its register), so the line moves by the
    smallest steps; onsets and lengths kept."""
    from . import compose as C
    ins = content["instruments"].get(voice, {})
    if ins.get("role") not in ("pad", "chords", "arp"):
        raise AgenticError("not_a_pad", f"voicing re-voices a pad, chords "
                           f"or arp; {voice} is {ins.get('role')!r}",
                           voice=voice)
    new = copy.deepcopy(content)
    column, k = _column(new, voice)
    lo, hi = (ins.get("register") or [36, 84])[:2]
    targets, notes = _targets(new, rng, scope)
    edits, prev = [], None
    for section, a, b, slot in targets:
        harmony = section.get("harmony") or []
        rows = section["rows"]
        if not harmony:
            continue
        per = max(1, len(rows) // len(harmony))
        for r in range(a, b):
            token, codes = _split(rows[r][k])
            p = _pitch(token)
            if p is None:
                continue
            pitch, param = p
            root, _q, pcs = C.chord(harmony[min(len(harmony) - 1, r // per)])
            near = prev if prev is not None else pitch
            choices = [x for x in range(lo, hi + 1) if x % 12 in pcs]
            if not choices:
                prev = pitch
                continue
            best = min(choices, key=lambda x: (abs(x - near),
                                               abs(x - pitch)))
            if best != pitch:
                cell = _name(best) + (f":{param}" if param else "") + codes
                edits.append({"section": section["id"], "slot": slot,
                              "row": r, "column": column,
                              "from": rows[r][k], "to": cell,
                              "chord": harmony[min(len(harmony) - 1,
                                                   r // per)]})
                rows[r][k] = cell
            prev = best
    if not edits:
        raise AgenticError("nothing_to_change", f"{voice}'s notes in "
                           f"{rng['spec']} already move by the nearest "
                           f"chord tones", voice=voice)
    return new, {"op": "voicing", "voice": voice, "column": column,
                 "range": rng["spec"], "rows": [rng["row0"], rng["row1"]],
                 "scope": scope, "cells": len(edits), "copies": notes,
                 "edits": edits[:12], "edits_total": len(edits)}


def pan(content: dict, voice: str, side: str) -> Tuple[dict, dict]:
    """An FM voice panned L, R or C for the whole piece."""
    side = side.upper()
    if side not in ("L", "R", "C"):
        raise AgenticError("bad_patch", "pan side is L, R or C")
    new = copy.deepcopy(content)
    ins = new["instruments"].get(voice)
    if ins is None:
        raise AgenticError("no_voice", f"no voice {voice!r}", voice=voice)
    if not ins["channel"].startswith("fm"):
        raise AgenticError("no_pan", f"{voice} plays {ins['channel']}: the "
                           f"SN76489 is mono on the Mega Drive, only the "
                           f"YM2612's channels pan", voice=voice)
    old = ins.get("pan") or "C"
    if old == side:
        raise AgenticError("nothing_to_change", f"{voice} is already "
                           f"{side}", voice=voice)
    ins["pan"] = side
    return new, {"op": "pan", "voice": voice, "from": old, "to": side,
                 "scope": "piece", "native": f"pan {ins['channel']} {side} "
                 f"(register 0xB4: the left and right output bits)"}


def echo(content: dict, voice: str, rng: dict, rows: int = 3,
         db: float = -9.0, column: Optional[str] = None,
         scope: str = "occurrence") -> Tuple[dict, dict]:
    """`voice`'s notes in the range copied `rows` later to a free FM
    channel, `db` quieter (velocity), with the same patch, panned the
    other way: a new voice of role "echo". Notes that would fall past the
    section's end are left out."""
    if not 1 <= rows <= 16:
        raise AgenticError("bad_patch", "an echo is 1-16 rows late")
    new = copy.deepcopy(content)
    src_col, k = _column(new, voice)
    ins = new["instruments"][voice]
    if not src_col.startswith("fm") or not ins.get("patch"):
        raise AgenticError("not_fm", f"an echo copies an FM voice; {voice} "
                           f"plays {src_col}", voice=voice)
    used = {i["channel"] for i in new["instruments"].values()}
    from . import banks
    used |= set(banks.layers(new))
    free = [f"fm{c}" for c in range(6) if f"fm{c}" not in used
            and not (f"fm{c}" == "fm5" and "dac" in new["columns"])]
    column = column or (free[0] if free else None)
    if column is None or column not in free:
        raise AgenticError("no_channel", f"no free FM channel for the echo "
                           f"(free: {free or 'none'}; fm5 is the DAC's when "
                           f"the piece has drums)", voice=voice)
    name = f"{voice}_echo"
    while name in new["instruments"]:
        name += "_"
    side = {"L": "R", "R": "L"}.get((ins.get("pan") or "C").upper(), "R")
    new["instruments"][name] = {"channel": column, "patch": ins["patch"],
                                "role": "echo", "pan": side,
                                "register": ins.get("register"),
                                "echo_of": voice}
    if column not in new["columns"]:
        new["columns"].append(column)
        for s in new["sections"]:
            for row in s["rows"]:
                row.append("...")
    e = new["columns"].index(column)
    targets, notes = _targets(new, rng, scope)
    edits = []
    for section, a, b, slot in targets:
        srows = section["rows"]
        for r in range(a, b):
            cell = srows[r][k]
            token, codes = _split(cell)
            d = r + rows
            if d >= len(srows):
                break
            if _is_off(token):
                if _is_hold(srows[d][e]):
                    srows[d][e] = "==="
                continue
            p = _pitch(token)
            if p is None:
                continue
            pitch, param = p
            vel = _velocity_for(int(param) if param else 127, db)
            srows[d][e] = f"{_name(pitch)}:{vel}"
            edits.append({"section": section["id"], "slot": slot, "row": d,
                          "column": column, "from": "...",
                          "to": srows[d][e]})
        # the copy ends where the section does
        if edits and len(srows) and not _is_off(srows[-1][e]):
            last = len(srows) - 1
            if _is_hold(srows[last][e]):
                srows[last][e] = "==="
    if not edits:
        raise AgenticError("nothing_to_change", f"{voice} has no note in "
                           f"{rng['spec']} to echo", voice=voice)
    return new, {"op": "echo", "voice": voice, "new_voice": name,
                 "column": column, "range": rng["spec"],
                 "rows": [rng["row0"], rng["row1"]], "delay_rows": rows,
                 "db": db, "pan": side, "scope": scope,
                 "cells": len(edits), "copies": notes,
                 "edits": edits[:12], "edits_total": len(edits)}


OPS = {"level": level, "transpose": transpose, "param": param, "vol": vol,
       "instrument": instrument, "articulation": articulation,
       "density": density, "rhythm": rhythm, "voicing": voicing, "pan": pan,
       "echo": echo}

#: what each op may change, for the guards (loop.guards): "onsets" (the
#: voice's onsets and their count), "pitches" (pitch classes, onsets
#: kept), "new_voice" (a voice added), "lengths" (note ends only)
CHANGES = {"articulation": "lengths", "density": "onsets",
           "rhythm": "onsets", "voicing": "pitches", "echo": "new_voice"}


def apply(content: dict, spec: dict, timeline=None) -> Tuple[dict, dict]:
    """A patch described as data: {"op": ..., ...}. Ranges are given as
    text and resolved on `timeline`."""
    op = spec.get("op")
    if op not in OPS:
        raise AgenticError("bad_patch", f"op is one of {', '.join(OPS)}, "
                           f"not {op!r}", op=op)
    args = dict(spec)
    args.pop("op")
    for meta in ("id", "why", "hypothesis", "expected"):
        args.pop(meta, None)
    if "range" in args:
        if timeline is None:
            raise AgenticError("bad_patch", "a range needs the timeline")
        args["rng"] = timeline.range(args.pop("range"))
    try:
        return OPS[op](content, **args)
    except TypeError as error:
        raise AgenticError("bad_patch", f"{op}: {error}", op=op) from None
