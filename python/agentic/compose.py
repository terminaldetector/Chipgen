"""compose.py — the next section, written from what came before it.

`continue_composition` walks the plan (structure.plan) from the project's
composition cursor. For each planned section it builds the context window
the spec asks for — the previous section, this one, and the next:

    previous   its last note per voice, its bass rhythm, its motif
               instances, the state its directives leave behind
    this       function (statement, answer, variation, development,
               cadence), energy, harmony, the motif to work with
    next       its first chord and function: where the transition goes

then writes the rows, checks the transition, commits a revision, hears the
boundary and the new section with the Tool ear (rows that sound across the
join are rendered with the state chased in), records what it found, and
moves the cursor. Every step is a checkpoint: a session that stops resumes
at the next planned section, and the same plan and seed give the same
music whether or not it was interrupted (tests C check exactly that).

`compose_section` is the built-in composer: plain rules, deterministic
for a seed. It develops motifs (motif.py) rather than copying them, fits
the strong beats of the line to the harmony and leaves the weak beats'
passing and neighbour notes alone, voice-leads the pad, walks the bass
into the next chord, and fills drums before a change of function. An
agent can write its own rows instead (`rows=` in compose_range); the same
checks and the same Ear run on them.
"""

import random
import time
from typing import Dict, List, Optional

import harmony

from . import ear as EAR
from . import motif as M
from . import timeline as T
from .state import AgenticError, content, rows_per_bar

_QUALITY = {"": "maj", "M": "maj", "maj": "maj", "m": "min", "min": "min",
            "-": "min", "7": "dom7", "dom7": "dom7", "m7": "min7",
            "min7": "min7", "maj7": "maj7", "M7": "maj7", "dim": "dim",
            "o": "dim", "aug": "aug", "+": "aug", "sus2": "sus2",
            "sus4": "sus4", "sus": "sus4", "5": "5", "6": "maj6",
            "m6": "min6", "m7b5": "m7b5", "dim7": "dim7", "o7": "dim7",
            "add9": "add9"}
_TEMPLATES = dict(harmony.TEMPLATES)

#: One bar of 16 rows: (onset, length) for a motif's rhythm.
RHYTHMS = [[(0, 3), (3, 3), (6, 2), (8, 4), (12, 4)],
           [(0, 2), (2, 2), (4, 4), (8, 2), (10, 2), (12, 4)],
           [(0, 4), (4, 2), (6, 2), (8, 6), (14, 2)],
           [(0, 3), (3, 1), (4, 4), (8, 3), (11, 1), (12, 4)]]
#: Scale-degree steps for a motif's line (first is 0).
CONTOURS = [[0, -1, -1, 2, -1, 1], [0, 1, 1, -2, -1, 1],
            [0, 2, -1, -1, 3, -1], [0, -2, 1, 1, -1, -1]]
#: A variation's transformation, when the plan does not name one.
VARIATIONS = [[{"op": "shift", "degrees": 2}], [{"op": "invert"}],
              [{"op": "retrograde"}], [{"op": "shift", "degrees": -2}]]
FUNCTIONS = ("statement", "answer", "variation", "development", "cadence")


def chord(symbol: str):
    """'Am' -> (root pc, quality, pitch classes)."""
    s = symbol.strip()
    if not s or s[0].upper() not in M._PC:
        raise AgenticError("bad_chord", f"{symbol!r} is not a chord "
                           f"(e.g. Am, F, G7, Bdim)")
    root = M._PC[s[0].upper()]
    i = 1
    while i < len(s) and s[i] in "#b":
        root += 1 if s[i] == "#" else -1
        i += 1
    q = _QUALITY.get(s[i:])
    if q is None:
        raise AgenticError("bad_chord", f"{symbol!r}: quality {s[i:]!r} is "
                           f"not one of {', '.join(sorted(_QUALITY))}")
    root %= 12
    return root, q, tuple(sorted((root + x) % 12 for x in _TEMPLATES[q]))


def _cell_name(pitch: int) -> str:
    return T.pitch_name(pitch)


def _nearest(pitch_classes, near: int, lo: int, hi: int,
             avoid=()) -> int:
    best = None
    for p in range(lo, hi + 1):
        if p % 12 in pitch_classes and p % 12 not in avoid:
            d = abs(p - near)
            if best is None or d < best[0]:
                best = (d, p)
    if best is None:
        return _nearest(pitch_classes, near, lo, hi) if avoid else near
    return best[1]


def _roles(c: dict) -> Dict[str, List[str]]:
    out = {}
    for v, ins in c["instruments"].items():
        out.setdefault(ins.get("role", ""), []).append(v)
    return out


# -- the context window ------------------------------------------------------------
def window(c: dict, plan: List[dict], index: int, motifs: dict) -> dict:
    """What the composer of plan[index] may read: the section before it
    (as written), the entry itself, and the one after."""
    prev = None
    if index > 0:
        sid = plan[index - 1]["id"]
        prev = next((s for s in c["sections"] if s["id"] == sid), None)
    last = {}
    if prev is not None:
        for v, ins in c["instruments"].items():
            col = ins["channel"]
            if col not in c["columns"]:
                continue
            notes = M.voice_notes(prev, c["columns"].index(col))
            if notes:
                last[v] = {"pitch": notes[-1][1], "row": notes[-1][0],
                           "length": notes[-1][2],
                           "onsets": [n[0] for n in notes]}
    nxt = plan[index + 1] if index + 1 < len(plan) else None
    return {"index": index, "entry": plan[index],
            "previous": {"id": prev["id"], "function":
                         prev.get("intent", {}).get("function"),
                         "rows": len(prev["rows"]), "last": last,
                         "harmony": prev.get("harmony")} if prev else None,
            "next": {"id": nxt["id"], "function": nxt.get("function"),
                     "first_chord": (nxt.get("harmony") or [None])[0]}
            if nxt else None,
            "motifs": sorted(motifs.get("motifs", {})),
            "open": list(motifs.get("open", []))}


# -- the composer -----------------------------------------------------------------
class Composer:
    """The built-in rules. One instance per section; deterministic."""

    def __init__(self, c: dict, ctx: dict, key: str, motifs: dict,
                 seed: int = 0):
        self.c = c
        self.ctx = ctx
        self.entry = ctx["entry"]
        self.key = M.Key(key)
        self.key_text = key
        self.motifs = motifs
        self.rng = random.Random(f"{seed}:{self.entry['id']}")
        self.rpb = rows_per_bar(c)
        #: rows in a beat: the tracker's lpb, whatever the metre
        self.beat = max(1, int(c["structure"].get("lpb", 4)))
        self.bars = int(self.entry.get("bars", 2))
        self.n = self.rpb * self.bars
        self.harmony = list(self.entry.get("harmony") or ["C"])
        if self.n % len(self.harmony):
            raise AgenticError("bad_harmony", f"{len(self.harmony)} chords "
                               f"do not divide {self.n} rows evenly")
        self.rpc = self.n // len(self.harmony)
        self.chords = [chord(h) for h in self.harmony]
        self.energy = float(self.entry.get("energy", 0.6))
        self.function = self.entry.get("function", "statement")
        if self.function not in FUNCTIONS:
            raise AgenticError("bad_function", f"function is one of "
                               f"{', '.join(FUNCTIONS)}, not "
                               f"{self.function!r}")
        self.notes = {}
        self.report = {"fitted": 0, "motif": None, "transform": None,
                       "instances": []}

    def chord_at(self, row: int):
        return self.chords[min(len(self.chords) - 1, row // self.rpc)]

    def _register(self, voice, default):
        return self.c["instruments"][voice].get("register") or default

    # the line
    def _new_motif(self, voice) -> List[list]:
        lo, hi = self._register(voice, [60, 79])
        rhythm = self.rng.choice(RHYTHMS)
        contour = self.rng.choice(CONTOURS)
        scale = self.rpb / 16.0
        prev = (self.ctx["previous"] or {}).get("last", {}).get(voice)
        near = prev["pitch"] if prev else (lo + hi) // 2
        root, _q, pcs = self.chords[0]
        start = _nearest(pcs, near, lo, hi, avoid=(root,))
        degree = self.key.degree(start)[0]
        notes = []
        for (onset, length), step in zip(rhythm, contour):
            degree += step
            notes.append([int(round(onset * scale)),
                          self.key.pitch(degree),
                          max(1, int(round(length * scale)))])
        return notes

    def _octave(self, notes, voice, near: int, keep: int = None) -> int:
        """The octave shift that puts a line inside the register, its first
        note near `near`; `keep` (the shift used so far) wins when it fits,
        so a sequence keeps rising instead of folding back."""
        lo, hi = self._register(voice, [60, 79])
        best = None
        for octave in range(-3, 4):
            pitches = [p + 12 * octave for _r, p, _n in notes]
            outside = sum(1 for p in pitches if not lo <= p <= hi)
            cost = outside * 100 + abs(pitches[0] - near) + \
                (0 if octave == keep else 3)
            if best is None or cost < best[0]:
                best = (cost, octave)
        return best[1]

    def _fit(self, notes):
        """Structural notes onto the chord: those on a strong beat, those
        held a beat or longer, and the last. Short notes between them stay
        as written — passing and neighbour notes are the line's own."""
        beat = self.beat
        out = []
        for i, (r, p, n) in enumerate(notes):
            root, _q, pcs = self.chord_at(r)
            strong = r % (2 * beat) == 0 or n >= beat
            last = i == len(notes) - 1
            if (strong or last) and p % 12 not in pcs:
                fitted = min((q for q in range(p - 2, p + 3)
                              if q % 12 in pcs),
                             key=lambda q: (abs(q - p),
                                            0 if self.key.in_scale(q) else 1),
                             default=p)
                if fitted != p:
                    self.report["fitted"] += 1
                p = fitted
            out.append([r, p, n])
        return out

    def _cadence_end(self, notes, row_end):
        """The phrase's last note on a chord tone, held to the bar's end."""
        if not notes:
            return notes
        r, p, n = notes[-1]
        root, _q, pcs = self.chord_at(r)
        target = root if self.function == "cadence" else None
        if target is not None:
            p = min((q for q in range(p - 6, p + 7) if q % 12 == target),
                    key=lambda q: abs(q - p))
        notes[-1] = [r, p, max(n, row_end - r)]
        return notes

    def lead(self, voice: str):
        g = self.motifs
        mid = self.entry.get("motif")
        motif = g.get("motifs", {}).get(mid) if mid else None
        if motif is None and g.get("motifs"):
            # the plan names none: develop the first open idea, else the
            # first one stated
            name = (g.get("open") or sorted(g["motifs"]))[0]
            motif = g["motifs"][name]
        if motif is None or self.function == "statement" and not mid:
            # a new idea, fitted to the first bar's chords before it is
            # kept: the motif is what is heard, not the raw sketch
            raw = self._new_motif(voice)
            notes = self._fit([list(x) for x in raw])
            mid = f"m{len(g.get('motifs', {})) + 1}"
            motif = M.make(mid, voice, notes, self.key_text,
                           origin={"section": self.entry["id"], "row": 0})
            self.report["new_motif"] = motif
        base = motif["notes"]
        bar = self.rpb
        if self.function in ("statement", "answer"):
            first = [] if self.function == "statement" else \
                [{"op": "shift", "degrees": -1}]
            answer = first + [{"op": "shift", "degrees": -1}]
            parts = [(0, first), (bar, answer)]
        elif self.function == "variation":
            tr = self.entry.get("transform") or self.rng.choice(VARIATIONS)
            parts = [(0, tr), (bar, tr + [{"op": "shift", "degrees": -1}])]
        elif self.function == "development":
            size = min(3, len(base))
            frag = [{"op": "fragment", "start": 0, "count": size}]
            span = base[size - 1][0] + base[size - 1][2] if size else bar
            step = max(4, min(bar // 2, span))
            # a sequence moves away from the register's nearer edge
            lo, hi = self._register(voice, [60, 79])
            prev = (self.ctx["previous"] or {}).get("last", {}).get(voice)
            up = not prev or prev["pitch"] <= (lo + hi) // 2
            parts = []
            k = 0
            for at in range(0, self.n - step + 1, step):
                parts.append((at, frag + ([{"op": "shift", "degrees": k}]
                                          if k else [])))
                k += 1 if up else -1
        else:   # cadence
            parts = [(0, []), (bar, [{"op": "fragment", "start": 0,
                                      "count": min(3, len(base))}])]
        lo, hi = self._register(voice, [60, 79])
        prev = (self.ctx["previous"] or {}).get("last", {}).get(voice)
        near = prev["pitch"] if prev else (lo + hi) // 2
        built = []
        for at, tr in parts:
            if at >= self.n:
                continue
            notes = M.apply(base, self.key, tr) if tr else \
                [list(x) for x in base]
            built.append((at, tr, notes))
        # one octave for the whole line, so a sequence keeps its direction;
        # a part that would leave the register alone moves by octaves
        whole = [x for _at, _tr, notes in built for x in notes]
        octave = self._octave(whole, voice, near) if whole else 0
        line = []
        for at, tr, notes in built:
            o = octave
            if any(not lo <= p + 12 * o <= hi for _r, p, _n in notes):
                o = self._octave(notes, voice, line[-1][1] if line
                                 else near, octave)
            notes = [[r + at, p + 12 * o, n] for r, p, n in notes]
            notes = [[r, p, min(n, self.n - r)] for r, p, n in notes
                     if r < self.n]
            line.extend(notes)
            record = M.simplify(list(tr) + ([{"op": "octave",
                                               "octaves": o}] if o else []))
            self.report["instances"].append(
                {"motif": motif["id"], "row": at, "transform": record})
        line.sort()
        # rows hold one note: a later onset cuts an earlier one
        for a, b in zip(line, line[1:]):
            a[2] = min(a[2], b[0] - a[0])
        line = [x for x in line if x[2] > 0]
        line = self._fit(line)
        if self.function in ("cadence",) or self.ctx["next"] is None:
            line = self._cadence_end(line, self.n)
        self.report["motif"] = motif["id"]
        self.notes[voice] = line
        return line

    def bass(self, voice: str):
        lo, hi = self._register(voice, [36, 52])
        prev = (self.ctx["previous"] or {}).get("last", {}).get(voice)
        near = prev["pitch"] if prev else lo + 7
        eighth = max(1, self.beat // 2)
        step = eighth if self.energy >= 0.4 else self.rpc
        out = []
        for k, (root, _q, _pcs) in enumerate(self.chords):
            a = k * self.rpc
            r_pitch = _nearest((root,), near, lo, hi)
            near = r_pitch
            if k + 1 < len(self.chords):
                nxt = self.chords[k + 1][0]
            else:
                nf = (self.ctx["next"] or {}).get("first_chord")
                nxt = chord(nf)[0] if nf else None
            for r in range(a, a + self.rpc, step):
                p = r_pitch
                if self.energy > 0.7 and (r - a) % (2 * step) == step:
                    p = r_pitch + 12 if r_pitch + 12 <= hi else r_pitch
                last = r + step >= a + self.rpc
                if last and nxt is not None and nxt != root and \
                        self.rpc >= 4:
                    target = _nearest((nxt,), r_pitch, lo, hi)
                    below = target - 2 if self.key.in_scale(target - 2) \
                        else target - 1
                    above = target + 2 if self.key.in_scale(target + 2) \
                        else target + 1
                    p = below if abs(below - r_pitch) <= \
                        abs(above - r_pitch) else above
                    p = max(lo, min(hi, p))
                length = max(1, step // 2) if step == eighth else step
                out.append([r, p, length])
        self.notes[voice] = out
        return out

    def pad(self, voice: str):
        lo, hi = self._register(voice, [52, 72])
        prev = (self.ctx["previous"] or {}).get("last", {}).get(voice)
        near = prev["pitch"] if prev else (lo + hi) // 2
        out = []
        for k, (root, _q, pcs) in enumerate(self.chords):
            p = _nearest(pcs, near, lo, hi, avoid=(root,))
            near = p
            out.append([k * self.rpc, p, self.rpc])
        self.notes[voice] = out
        return out

    def arp(self, voice: str):
        lo, hi = self._register(voice, [60, 84])
        out = []
        step = 1 if self.energy > 0.7 else 2
        for r in range(0, self.n, step):
            root, _q, pcs = self.chord_at(r)
            tones = sorted(p for p in range(lo, min(hi, lo + 12) + 1)
                           if p % 12 in pcs)
            out.append([r, tones[(r // step) % len(tones)], 1])
        self.notes[voice] = out
        return out

    def drums(self, voice: str, column: str):
        beat = self.beat
        cells = {}
        fill = self.ctx["next"] is not None and \
            self.ctx["next"].get("function") != self.function
        for r in range(self.n):
            in_bar = r % self.rpb
            last_bar = r >= self.n - self.rpb
            if column == "dac":
                if in_bar % (2 * beat) == 0:
                    cells[r] = "kick"
                elif in_bar % beat == 0 and (in_bar // beat) % 2 == 1:
                    cells[r] = "snare:0.8"
                if fill and last_bar and in_bar >= 3 * beat and \
                        in_bar % max(1, beat // 2) == 0:
                    cells[r] = "snare:0.7"
            elif column == "noise" and self.energy >= 0.4:
                half = max(1, beat // 2)
                if in_bar % half == 0:
                    cells[r] = "w1:6" if in_bar % beat else "w1:3"
                elif in_bar % half == 1:
                    cells[r] = "==="        # a hat, not a hiss
        self.notes[voice] = cells
        return cells

    # the rows
    def rows(self) -> List[List[str]]:
        cols = self.c["columns"]
        grid = [["..."] * len(cols) for _ in range(self.n)]
        vel = max(60, min(127, int(80 + 47 * self.energy)))
        for voice, notes in self.notes.items():
            ins = self.c["instruments"][voice]
            col = ins["channel"]
            k = cols.index(col)
            if isinstance(notes, dict):
                for r, cell in notes.items():
                    grid[r][k] = cell
                continue
            for i, (r, p, n) in enumerate(notes):
                if col.startswith("fm"):
                    v = vel if ins.get("role") != "pad" else \
                        max(50, vel - 30)
                    grid[r][k] = f"{_cell_name(p)}:{v}"
                elif col.startswith("psg"):
                    att = 4 if ins.get("role") == "arp" else 2
                    grid[r][k] = f"{_cell_name(p)}:{att}"
                end = r + n
                nxt = notes[i + 1][0] if i + 1 < len(notes) else None
                if end < self.n and (nxt is None or nxt > end):
                    grid[end][k] = "==="
        # the last section lets every voice go: release inside the piece
        if self.ctx["next"] is None:
            for k, col in enumerate(cols):
                if col.startswith(("fm", "psg")) or col == "noise":
                    if grid[-1][k] == "...":
                        grid[-1][k] = "==="
        return grid


def compose_section(c: dict, ctx: dict, key: str, motifs: dict,
                    seed: int = 0) -> (dict, dict):
    """The section plan[ctx.index] as rows. -> (section, report)."""
    comp = Composer(c, ctx, key, motifs, seed)
    roles = _roles(c)
    for v in roles.get("bass", []):
        comp.bass(v)
    for role in ("lead", "melody"):
        for v in roles.get(role, []):
            comp.lead(v)
    for v in roles.get("pad", []) + roles.get("chords", []):
        comp.pad(v)
    for v in roles.get("arp", []):
        comp.arp(v)
    for v in roles.get("drums", []) + roles.get("hats", []):
        comp.drums(v, c["instruments"][v]["channel"])
    e = ctx["entry"]
    section = {"id": e["id"], "name": e.get("name", comp.function),
               "rows": comp.rows(), "directives": [],
               "harmony": comp.harmony,
               "intent": {"function": comp.function, "energy": comp.energy},
               "composed_by": "builtin",
               "context": {"previous": (ctx["previous"] or {}).get("id"),
                           "next": (ctx["next"] or {}).get("id")}}
    return section, comp.report


# -- checks ----------------------------------------------------------------------
def transition(c: dict, prev_id: str, sid: str) -> dict:
    """The join between two sections, from the score: the lead's leap, the
    bass's approach, notes that run on across it, state left behind."""
    tl = T.build(c)
    slots = {s["section"]: s for s in tl.slots()}
    if prev_id not in slots or sid not in slots:
        raise AgenticError("no_section", f"{prev_id} or {sid} is not in "
                           f"the order")
    boundary = slots[sid]["row0"]
    out = {"between": [prev_id, sid], "row": boundary, "issues": [],
           "notes": []}
    for v, ins in c["instruments"].items():
        role = ins.get("role", "")
        before = [n for n in tl.notes if n["voice"] == v
                  and n.get("row") is not None and n["row"] < boundary]
        after = [n for n in tl.notes if n["voice"] == v
                 and n.get("row") is not None and n["row"] >= boundary]
        crossing = [n for n in before if n.get("end_row") is not None
                    and n["end_row"] > boundary]
        if crossing and role not in ("pad",):
            out["issues"].append({"kind": "runs_on", "voice": v,
                                  "note": crossing[0]["name"],
                                  "statement": f"{v}'s {crossing[0]['name']} "
                                               f"keeps sounding into {sid}"})
        if before and after and before[-1].get("pitch") is not None and \
                after[0].get("pitch") is not None:
            leap = after[0]["pitch"] - before[-1]["pitch"]
            if role in ("lead", "melody"):
                entry = {"kind": "leap", "voice": v, "semitones": leap}
                (out["issues"] if abs(leap) > 7 else out["notes"]).append(
                    dict(entry, statement=f"{v} moves {leap:+d} semitones "
                                          f"across the join"))
            if role == "bass":
                d = abs(leap) % 12
                prepared = d in (0, 1, 2, 5, 7, 10, 11)
                out["notes"].append({"kind": "bass_approach", "voice": v,
                                     "semitones": leap,
                                     "prepared": prepared})
    carried = []
    for s in c["sections"]:
        if s["id"] != prev_id:
            continue
        opened = {}
        for d in sorted(s.get("directives", []),
                        key=lambda d: d["before_row"]):
            head = d["text"].split()
            if head and head[0] in ("vol", "op", "pan", "porta", "vib",
                                    "fade", "trem", "pitch"):
                target = " ".join(head[:2] if head[0] != "op" else head[:4])
                opened.setdefault(target, []).append(d["text"])
        # the patches set a value and restore it: an odd count is a
        # change the section leaves to the next one
        carried = [texts[-1] for texts in opened.values()
                   if len(texts) % 2 == 1]
    out["state_left"] = carried
    out["ok"] = not out["issues"]
    return out


def hanging(c: dict) -> List[dict]:
    """Notes that run past the end of the piece without a key-off, and
    notes (not pads) that run across a section join."""
    tl = T.build(c)
    out = []
    last_row = len(tl.rows)
    for n in tl.notes:
        if n["column"] == "dac":
            continue
        if (n.get("end_row") or last_row) >= last_row:
            out.append({"voice": n["voice"], "note": n["name"],
                        "start_s": round(n["start"], 3),
                        "why": "no key-off before the end of the piece"})
    slots = tl.slots()
    joins = [s["row0"] for s in slots[1:]]
    for n in tl.notes:
        role = c["instruments"].get(n["voice"], {}).get("role", "")
        if n["column"] == "dac" or role == "pad" or n.get("row") is None:
            continue
        for j in joins:
            if n["row"] < j < (n.get("end_row") or last_row):
                out.append({"voice": n["voice"], "note": n["name"],
                            "start_s": round(n["start"], 3),
                            "why": f"runs across the join at row {j}"})
    return out


def harmony_check(c: dict, section_id: str) -> dict:
    """Each chord span of a section against the plan's chord, read back
    from the notes by harmony.label (weighted by how long each pitch
    class sounds); and whether the bass plays the root at the span's
    start."""
    tl = T.build(c)
    sec = next(s for s in c["sections"] if s["id"] == section_id)
    slot = next(s for s in tl.slots() if s["section"] == section_id)
    n = len(sec["rows"])
    chords_ = sec.get("harmony") or []
    if not chords_:
        return {"spans": [], "root_match": None}
    rpc = n // len(chords_)
    basses = [v for v, ins in c["instruments"].items()
              if ins.get("role") == "bass"]
    spans = []
    for k, sym in enumerate(chords_):
        a = slot["row0"] + k * rpc
        b = a + rpc
        weights = {}
        bass_root = None
        for i, note in enumerate(tl.notes):
            if note.get("pitch") is None or note.get("row") is None:
                continue
            end = note.get("end_row") or b
            if note["voice"] in basses:
                # a staccato bass still names the root until its next note
                later = [x["row"] for x in tl.notes[i + 1:]
                         if x["voice"] == note["voice"]
                         and x.get("row") is not None]
                end = max(end, min(later) if later else b)
            overlap = min(end, b) - max(note["row"], a)
            if overlap > 0:
                weights[note["pitch"] % 12] = weights.get(
                    note["pitch"] % 12, 0) + overlap
            if note["voice"] in basses and note["row"] == a:
                bass_root = note["pitch"] % 12
        strong = harmony.strong(weights)
        labels = harmony.label(strong, bass=bass_root)
        root, _q, pcs = chord(sym)
        top = labels[0] if labels else None
        spans.append({"chord": sym, "read": (harmony.NOTE_NAMES[top[0]]
                                             + ("" if top[1] == "maj"
                                                else top[1])) if top else None,
                      "root_match": bool(top and top[0] == root),
                      "bass_root": bass_root == root if basses else None})
    matches = [s["root_match"] for s in spans]
    roots = [s["bass_root"] for s in spans if s["bass_root"] is not None]
    return {"spans": spans,
            "root_match": round(sum(matches) / len(matches), 3),
            "bass_on_root": round(sum(roots) / len(roots), 3)
            if roots else None}


# -- the loop over the plan ----------------------------------------------------------
def continue_composition(project, goal: Optional[dict] = None,
                         stop_after: Optional[int] = None, seed: int = None,
                         hear: bool = True, cache=None,
                         focus="melodic", after=None) -> dict:
    """Compose the plan's remaining sections from the cursor, one
    checkpoint per section. `goal` {"add": [plan entries]} extends the
    plan first. `stop_after` ends the session after that many sections
    (as an interruption would). The ear hears each new section with the
    bar before it; `focus` "melodic" measures the melodic voices against
    the rest (7 renders for five voices instead of 11 — a correction then
    hears every voice before it commits anything), None measures every
    voice. `after(project, step)` runs once a section is written and
    heard, before the next is composed — the place for a correction, so
    the next window reads the corrected music and a lesson learned on
    one section is there for the next. -> what was done and where it
    stopped."""
    from . import render as R
    started = time.time()
    st = project.state
    cur = st.setdefault("composition", {"next": 0, "seed": 0, "log": []})
    if seed is not None and cur["next"] == 0:
        cur["seed"] = int(seed)
    plan = st["structure"]["plan"]
    if goal and goal.get("add"):
        new = content(st)
        for e in goal["add"]:
            if "id" not in e:
                raise AgenticError("bad_goal", "each added section needs "
                                   "an id")
            new["structure"]["plan"].append(e)
        project.commit(new, {"kind": "plan", "added":
                             [e["id"] for e in goal["add"]]})
        plan = st["structure"]["plan"]
    cache = cache or R.Cache(project.path("renders"))
    ear = EAR.ToolEar(cache)
    done = []
    for i in range(cur["next"], len(plan)):
        if stop_after is not None and len(done) >= stop_after:
            project.save()
            return {"status": "stopped", "next": cur["next"],
                    "remaining": [e["id"] for e in plan[i:]],
                    "done": done,
                    "runtime_s": round(time.time() - started, 2)}
        t0 = time.time()
        g = M.graph(st)
        ctx = window(content(st), plan, i, g)
        section, report = compose_section(content(st), ctx,
                                          st["structure"].get("key")
                                          or "C major", g, cur["seed"])
        new = content(st)
        new["sections"] = [s for s in new["sections"]
                           if s["id"] != section["id"]] + [section]
        if section["id"] not in new["order"]:
            new["order"].append(section["id"])
        T.build(new)        # refuses rows that do not parse or play
        rev = project.commit(new, {"kind": "compose", "section":
                                   section["id"], "function":
                                   section["intent"]["function"],
                                   "context": section["context"]})
        if report.get("new_motif"):
            M.add_motif(st, report["new_motif"])
        lead = next((v for v, ins in st["instruments"].items()
                     if ins.get("role") in ("lead", "melody")), None)
        for inst in report["instances"]:
            M.add_instance(st, inst["motif"], section["id"], lead,
                           inst["row"], inst["transform"],
                           role=section["intent"]["function"])
        step = {"section": section["id"], "rev": rev,
                "function": section["intent"]["function"],
                "motif": report.get("motif"),
                "fitted_to_harmony": report.get("fitted")}
        if ctx["previous"]:
            step["transition"] = transition(content(st),
                                            ctx["previous"]["id"],
                                            section["id"])
        if hear:
            tl = T.build(content(st))
            slot = next(s for s in tl.slots()
                        if s["section"] == section["id"])
            a = max(0, slot["row0"] - tl.rows_per_bar)
            spec = f"rows {a}-{slot['row1'] - 1}"
            voices = None
            if focus == "melodic":
                from .diagnose import MELODIC_ROLES
                voices = [v for v, ins in st["instruments"].items()
                          if ins.get("role") in MELODIC_ROLES] or None
            elif focus:
                voices = list(focus)
            h = ear.hear(tl, tl.range(spec), focus=voices)
            ids = [project.add_observation(o) for o in h["observations"]]
            step["heard"] = {"range": spec, "observations": ids,
                             "section_range": f"rows {slot['row0']}-"
                                              f"{slot['row1'] - 1}",
                             "focus": voices or "every voice",
                             "statement": h["statement"],
                             "cost": h["cost"]}
        if after is not None:
            step["after"] = after(project, step)
        step["seconds"] = round(time.time() - t0, 2)
        cur["log"].append(step)
        cur["next"] = i + 1
        project.save()          # the checkpoint
        done.append(step)
    return {"status": "complete", "next": cur["next"], "done": done,
            "remaining": [],
            "runtime_s": round(time.time() - started, 2)}
