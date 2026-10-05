"""structure.py — the shape of a module, from what plays: the short schema a
seed library keeps instead of the notes.

A model that is to write its own music does not need another composer's
notes; it needs to know how such a piece is put together. So this reads,
from the trace of what plays (corpus.trace):

    form        the patterns in the order they play, lettered by content
                (two patterns with the same cells are one letter), with the
                rows, seconds, speed and tempo of each visit
    transitions what happens between two visits: a jump, a break, a step
                back, a change of tempo, and whether the rows before it are
                busier than the rest of the pattern (a fill, as a guess)
    channels    per channel: how often it plays, its instruments, its note
                range, its commands, and a role guessed from those, with
                the rule that guessed it
    density     notes per row and per second, per visit and overall
    commands    which effects the song uses, by name, and how often
    tempo map   every change of speed or tempo, and when

Note numbers are what the pattern writes, not what is heard: a sample can
be tuned anywhere, so "low" and "high" here are the written register. A
role is a guess and says so; listening decides.

`fingerprints` cuts patterns into 8-row windows and keeps, for each window
with eight notes or more, the row, channel and interval to its first note
— the reference corpus's own measure of how distinct its fragments are,
used here to check that no fragment crosses from one split to another
(`splits`).
"""

import hashlib
import statistics
from collections import Counter
from typing import Dict, Iterable, List, Optional

from . import corpus

#: the effects that move the play position (libopenmpt's numbers)
JUMP, BREAK = 12, 14
SPECIAL = 20


def _grid(module: corpus.Module, patterns: Iterable[int]) -> Dict[int, list]:
    """Every cell of the given patterns, read once: grid[p][row][ch] =
    (note, instrument, volcmd, volume, effect, param)."""
    c = module.song.command
    out = {}
    for p in patterns:
        if not 0 <= p < module.n_patterns:
            continue
        rows = []
        for r in range(module.rows(p)):
            rows.append([(c(p, r, ch, corpus.NOTE), c(p, r, ch, corpus.INSTRUMENT),
                          c(p, r, ch, corpus.VOLCMD), c(p, r, ch, corpus.VOLUME),
                          c(p, r, ch, corpus.EFFECT), c(p, r, ch, corpus.PARAM))
                         for ch in range(module.channels)])
        out[p] = rows
    return out


def _letters():
    n = 0
    while True:
        q, text = n, ""
        while True:
            text = chr(65 + q % 26) + text
            q = q // 26 - 1
            if q < 0:
                break
        yield text
        n += 1


def _content_key(rows) -> str:
    h = hashlib.sha1()
    for row in rows:
        for cell in row:
            h.update(bytes(v & 0xFF for v in cell))
    return h.hexdigest()[:12]


def _visits(rows) -> List[dict]:
    """Consecutive trace rows at the same order position: one visit (a
    pattern loop inside it stays in it)."""
    out = []
    for o, p, r, speed, tempo, at in rows:
        if out and out[-1]["order"] == o:
            out[-1]["rows_played"] += 1
            out[-1]["_rows"].append(r)
            continue
        out.append({"order": o, "pattern": p, "at": at, "speed": speed,
                    "tempo": tempo, "rows_played": 1, "_rows": [r]})
    return out


def _role(stats: dict, low_median: Optional[float]) -> (str, str):
    if not stats["notes"]:
        return "unused", "no notes in what plays"
    if stats["fixed_pitch_share"] >= 0.8 and stats["instruments_used"] >= 2:
        return ("percussion", "80% or more of its notes are of instruments "
                "it only ever plays at one or two pitches, and it uses two "
                "instruments or more")
    if stats["density"] < 0.04:
        return "sparse (effects, accents)", "a note on fewer than 4% of rows"
    if low_median is not None and stats["median_note"] <= low_median:
        return "bass", ("the lowest written register of the melodic "
                        "channels (median note), with notes on 4% of rows "
                        "or more")
    if stats["together_share"] >= 0.6 and (stats["mean_length_rows"] or 0) >= 4:
        return ("chords / pad", "60% or more of its notes start on a row "
                "where two other channels also start one, and they last 4 "
                "rows or more on average")
    return "melodic", "none of the rules above"


def structure(module: corpus.Module, played: Optional[dict] = None) -> dict:
    """The module's schema (see the module's docstring)."""
    played = played or corpus.trace(module)
    rows = played["rows"]
    reached = played["reached"]
    grid = _grid(module, reached)
    letters = _letters()
    letter_of, key_of = {}, {}
    for p in reached:
        key = _content_key(grid[p])
        key_of[p] = key
        if key not in letter_of:
            letter_of[key] = next(letters)
    visits = _visits(rows)
    form = []
    for i, v in enumerate(visits):
        p = v["pattern"]
        cells = grid.get(p)
        notes = 0
        if cells:
            for r in v["_rows"]:
                if r < len(cells):
                    notes += sum(1 for cell in cells[r] if 1 <= cell[0] <= 120)
        nxt = visits[i + 1] if i + 1 < len(visits) else None
        seconds = round((nxt["at"] if nxt else played["seconds"]) - v["at"], 3)
        form.append({"order": v["order"], "pattern": p,
                     "letter": letter_of.get(key_of.get(p)),
                     "at": v["at"], "seconds": seconds,
                     "rows_played": v["rows_played"],
                     "speed": v["speed"], "tempo": v["tempo"],
                     "notes": notes,
                     "notes_per_row": round(notes / v["rows_played"], 2)})
    transitions = []
    for i in range(len(visits) - 1):
        a, b = visits[i], visits[i + 1]
        kinds = []
        cells = grid.get(a["pattern"])
        last = a["_rows"][-1]
        if cells and last < len(cells):
            effects = {cell[4] for cell in cells[last]}
            if JUMP in effects:
                kinds.append("jump (Bxx)")
            if BREAK in effects:
                kinds.append("break (Cxx)")
        if b["order"] < a["order"]:
            kinds.append("back to an earlier order")
        if (b["speed"], b["tempo"]) != (a["speed"], a["tempo"]):
            kinds.append(f"speed/tempo {a['speed']}/{a['tempo']} -> "
                         f"{b['speed']}/{b['tempo']}")
        if len(set(a["_rows"])) < len(a["_rows"]):
            kinds.append("rows repeated inside (pattern loop)")
        fill = None
        if cells and len(a["_rows"]) >= 16:
            n = len(a["_rows"])
            tail = a["_rows"][-max(4, n // 4):]
            head = a["_rows"][:-len(tail)]

            def per_row(rs):
                return sum(sum(1 for cell in cells[r] if 1 <= cell[0] <= 120)
                           for r in rs if r < len(cells)) / max(1, len(rs))
            t, h = per_row(tail), per_row(head)
            if h > 0 and t >= 1.5 * h:
                fill = round(t / h, 2)
        transitions.append({"from_order": a["order"], "to_order": b["order"],
                            "at": b["at"], "kinds": kinds or ["next order"],
                            "fill_guess": fill})
    # channels, over every row that plays (a row played twice counts twice)
    per = []
    onsets_by_row = []
    for o, p, r, *_ in rows:
        cells = grid.get(p)
        if cells is None or r >= len(cells):
            onsets_by_row.append(set())
            continue
        onsets_by_row.append({ch for ch, cell in enumerate(cells[r])
                              if 1 <= cell[0] <= 120})
    for ch in range(module.channels):
        notes, instruments, effects, volcmds = [], Counter(), Counter(), 0
        pitches_of = {}
        together = 0
        lengths, started = [], None
        for k, (o, p, r, *_) in enumerate(rows):
            cells = grid.get(p)
            if cells is None or r >= len(cells):
                continue
            note, ins, volcmd, _, effect, _ = cells[r][ch]
            if note and started is not None:
                # a note, a key-off, a cut or a fade ends the one before
                lengths.append(k - started)
                started = None
            if 1 <= note <= 120:
                started = k
                notes.append(note)
                if ins:
                    instruments[ins] += 1
                pitches_of.setdefault(ins, set()).add(note)
                if len(onsets_by_row[k] - {ch}) >= 2:
                    together += 1
            if effect:
                effects[corpus.EFFECT_NAMES.get(effect, str(effect))] += 1
            if volcmd:
                volcmds += 1
        fixed = 0
        if notes:
            # notes whose instrument this channel plays at one or two
            # pitches only (percussion is usually written that way)
            fixed_instruments = {i for i, ps in pitches_of.items()
                                 if len(ps) <= 2}
            for k, (o, p, r, *_) in enumerate(rows):
                cells = grid.get(p)
                if cells is None or r >= len(cells):
                    continue
                note, ins = cells[r][ch][0], cells[r][ch][1]
                if 1 <= note <= 120 and ins in fixed_instruments:
                    fixed += 1
        stats = {"channel": ch + 1, "notes": len(notes),
                 "density": round(len(notes) / max(1, len(rows)), 3),
                 "instruments_used": len(instruments),
                 "instruments": [i for i, _ in instruments.most_common(6)],
                 "median_note": statistics.median(notes) if notes else None,
                 "note_range": [min(notes), max(notes)] if notes else None,
                 "fixed_pitch_share": round(fixed / len(notes), 2)
                 if notes else 0.0,
                 "mean_length_rows": round(sum(lengths) / len(lengths), 1)
                 if lengths else None,
                 "together_share": round(together / len(notes), 2)
                 if notes else 0.0,
                 "effects": dict(effects.most_common(8)),
                 "volume_column": volcmds}
        per.append(stats)
    melodic = [s for s in per if s["notes"] and s["density"] >= 0.04
               and not (s["fixed_pitch_share"] >= 0.8
                        and s["instruments_used"] >= 2)]
    low_median = min((s["median_note"] for s in melodic), default=None)
    for s in per:
        s["role_guess"], s["role_rule"] = _role(
            s, low_median if len(melodic) > 1 else None)
    commands = Counter()
    for s in per:
        commands.update(s["effects"])
    tempo_map, last = [], None
    for o, p, r, speed, tempo, at in rows:
        if (speed, tempo) != last:
            tempo_map.append({"at": at, "order": o, "row": r,
                              "speed": speed, "tempo": tempo})
            last = (speed, tempo)
    total_notes = sum(s["notes"] for s in per)
    return {"format": "schism-structure/1",
            "form": " ".join(v["letter"] or "?" for v in form),
            "letters": len(letter_of), "visits": form,
            "transitions": transitions, "channels": per,
            "density": {"notes": total_notes,
                        "notes_per_row": round(total_notes / max(1, len(rows)),
                                               2),
                        "notes_per_second": round(
                            total_notes / max(0.001, played["seconds"]), 2)},
            "commands": dict(commands.most_common()),
            "tempo_map": tempo_map[:200],
            "seconds": played["seconds"],
            "caveats": ["note numbers are the written register, not the "
                        "heard one: a sample can be tuned anywhere",
                        "roles and fills are guesses by the rules given "
                        "with each; listening decides",
                        "letters compare cells exactly: a pattern with one "
                        "cell changed is another letter"]}


def fingerprints(module: corpus.Module, patterns: Iterable[int],
                 min_notes: int = 8, size: int = 8) -> Counter:
    """8-row windows (rows 0-7, 8-15, ... of each pattern) with eight notes
    or more, each as (row in the window, channel, interval to its first
    note): instruments, effects and volumes left out, transposition
    folded."""
    out = Counter()
    grid = _grid(module, patterns)
    for p, cells in grid.items():
        for start in range(0, len(cells) - size + 1, size):
            notes = [(r - start, ch, cell[0])
                     for r in range(start, start + size)
                     for ch, cell in enumerate(cells[r])
                     if 1 <= cell[0] <= 120]
            if len(notes) < min_notes:
                continue
            first = notes[0][2]
            out[tuple((r, ch, n - first) for r, ch, n in notes)] += 1
    return out


def splits(groups: Dict[str, List[str]], shares=(("test", 0.15),
                                                 ("validation", 0.15))) -> dict:
    """Whole groups (an author's compositions) to test, validation and
    train, in an order fixed by a hash of the group's name, so the split is
    the same on every machine and no composition is cut in two."""
    total = sum(len(v) for v in groups.values())
    order = sorted(groups, key=lambda g: hashlib.sha256(
        g.encode("utf-8")).hexdigest())
    plan = {name: [] for name, _ in shares}
    plan["train"] = []
    i = 0
    for name, share in shares:
        want = max(1, round(share * total))
        while i < len(order) and sum(len(groups[g]) for g in plan[name]) < want:
            plan[name].append(order[i])
            i += 1
    plan["train"].extend(order[i:])
    return {split: {"groups": names,
                    "items": sorted(x for g in names for x in groups[g])}
            for split, names in plan.items()}


def leaks(prints_by_item: Dict[str, Counter], plan: dict) -> dict:
    """Fingerprints found in more than one split (a fragment of one
    composition, transposed or not, that another split also has)."""
    where = {}
    for split, entry in plan.items():
        for item in entry["items"]:
            for fp in prints_by_item.get(item, ()):
                where.setdefault(fp, set()).add(split)
    crossing = [fp for fp, s in where.items() if len(s) > 1]
    examples = []
    for fp in crossing[:5]:
        holders = sorted(item for item, c in prints_by_item.items()
                         if fp in c)
        examples.append({"items": holders, "notes": len(fp)})
    return {"fingerprints": len(where), "crossing_splits": len(crossing),
            "examples": examples}
