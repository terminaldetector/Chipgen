"""motif.py — the piece's ideas, where they return, and how they changed.

A motif is a short line of one voice: notes as (onset in rows from the
motif's start, pitch, length in rows). What makes it the same idea when
it comes back is kept apart from where it is:

    steps    scale-degree steps in the piece's key, so a sequence a third
             higher is the same motif though its semitones differ
    rhythm   onsets and lengths in rows

Transformations, each returning the new notes and a description of what
was done (the description is the edge in the graph):

    shift(k)        diatonic sequence: every degree + k
    transpose(n)    chromatic: every pitch + n semitones
    invert()        diatonic inversion around the first note
    retrograde()    the pitches backwards on the same rhythm positions
    augment()       every onset and length x2; diminish() /2
    fragment(a, n)  n notes from the a-th
    displace(r)     every onset r rows later

Recognition goes the other way: given a voice's notes, `find` reports
where a motif occurs and under which transformation (identity, shift,
transposition, inversion, retrograde, augmentation, diminution, a
fragment of three or more notes), with how many of its steps match. A
variation that changed a note to fit a new chord still matches most of
its steps, and is reported as varied, not exact.

The graph lives in the state, `state["motifs"]`:
    motifs     {id: {voice, notes, key, origin}}
    instances  [{motif, section, voice, row, transform, match}]
    open       ideas stated and not yet developed, for the composer
"""

from typing import Dict, List, Optional, Tuple

from .state import AgenticError

SCALES = {"major": (0, 2, 4, 5, 7, 9, 11),
          "minor": (0, 2, 3, 5, 7, 8, 10),
          "harmonic minor": (0, 2, 3, 5, 7, 8, 11),
          "dorian": (0, 2, 3, 5, 7, 9, 10),
          "mixolydian": (0, 2, 4, 5, 7, 9, 10),
          "phrygian": (0, 1, 3, 5, 7, 8, 10),
          "lydian": (0, 2, 4, 6, 7, 9, 11)}
_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def parse_key(text: str) -> Tuple[int, str, tuple]:
    """'A minor', 'Am', 'C', 'F# dorian' -> (tonic pc, mode, scale)."""
    t = (text or "C major").strip()
    if not t:
        t = "C major"
    head, _, rest = t.partition(" ")
    name = head[0].upper()
    if name not in _PC:
        raise AgenticError("bad_key", f"{text!r} is not a key (e.g. "
                           f"'A minor', 'Am', 'D dorian')")
    pc = _PC[name]
    i = 1
    while i < len(head) and head[i] in "#b":
        pc += 1 if head[i] == "#" else -1
        i += 1
    suffix = head[i:].lower()
    mode = rest.strip().lower() or ("minor" if suffix in ("m", "min")
                                    else "major")
    if mode not in SCALES:
        raise AgenticError("bad_key", f"mode {mode!r} is not one of "
                           f"{', '.join(SCALES)}")
    return pc % 12, mode, SCALES[mode]


class Key:
    def __init__(self, text: str):
        self.text = text
        self.tonic, self.mode, self.scale = parse_key(text)

    def degree(self, pitch: int) -> Tuple[int, int]:
        """pitch -> (absolute degree, alteration in semitones)."""
        rel = pitch - self.tonic
        octave, within = divmod(rel, 12)
        idx = max(i for i, s in enumerate(self.scale) if s <= within)
        return octave * 7 + idx, within - self.scale[idx]

    def pitch(self, degree: int, alteration: int = 0) -> int:
        octave, idx = divmod(degree, 7)
        return self.tonic + octave * 12 + self.scale[idx] + alteration

    def in_scale(self, pitch: int) -> bool:
        return (pitch - self.tonic) % 12 in self.scale


# -- notes of a voice -----------------------------------------------------------
def voice_notes(section: dict, column_index: int) -> List[list]:
    """[[row, pitch, length]] of one column of a section's rows (FM and PSG
    cells; a hold extends, an off or the next note ends)."""
    import tracker
    from . import patch as P
    out = []
    rows = section["rows"]
    for r, row in enumerate(rows):
        token, _codes = P._split(row[column_index])
        p = P._pitch(token)
        if out and out[-1][2] is None and (
                p is not None or token.lower() in tracker.OFF_TOKENS):
            out[-1][2] = r - out[-1][0]
        if p is not None:
            out.append([r, p[0], None])
    if out and out[-1][2] is None:
        out[-1][2] = len(rows) - out[-1][0]
    return out


def make(mid: str, voice: str, notes: List[list], key: str,
         origin: dict = None) -> dict:
    notes = [list(n) for n in notes]
    base = notes[0][0] if notes else 0
    for n in notes:
        n[0] -= base
    return {"id": mid, "voice": voice, "notes": notes, "key": key,
            "length_rows": (notes[-1][0] + notes[-1][2]) if notes else 0,
            "origin": origin or {}}


def steps(notes, key: Key) -> List[int]:
    d = [key.degree(n[1])[0] for n in notes]
    return [b - a for a, b in zip(d, d[1:])]


def intervals(notes) -> List[int]:
    return [b[1] - a[1] for a, b in zip(notes, notes[1:])]


def rhythm(notes) -> List[Tuple[int, int]]:
    base = notes[0][0] if notes else 0
    return [(n[0] - base, n[2]) for n in notes]


# -- transformations ------------------------------------------------------------
def shift(notes, key: Key, k: int):
    out = []
    for r, p, n in notes:
        d, alt = key.degree(p)
        out.append([r, key.pitch(d + k, alt), n])
    return out, {"op": "shift", "degrees": k}


def transpose(notes, semitones: int):
    return [[r, p + semitones, n] for r, p, n in notes], \
        {"op": "transpose", "semitones": semitones}


def invert(notes, key: Key):
    if not notes:
        return [], {"op": "invert"}
    axis = key.degree(notes[0][1])[0]
    out = []
    for r, p, n in notes:
        d, alt = key.degree(p)
        out.append([r, key.pitch(2 * axis - d, -alt), n])
    return out, {"op": "invert", "axis_degree": axis}


def retrograde(notes):
    pitches = [p for _r, p, _n in notes][::-1]
    return [[r, pitches[i], n] for i, (r, _p, n) in enumerate(notes)], \
        {"op": "retrograde"}


def augment(notes):
    return [[r * 2, p, n * 2] for r, p, n in notes], {"op": "augment"}


def diminish(notes):
    if any(r % 2 or n % 2 for r, _p, n in notes):
        raise AgenticError("odd_rhythm", "a diminution needs even onsets "
                           "and lengths")
    return [[r // 2, p, n // 2] for r, p, n in notes], {"op": "diminish"}


def fragment(notes, start: int, count: int):
    part = [list(n) for n in notes[start:start + count]]
    if len(part) < 2:
        raise AgenticError("short_fragment", "a fragment needs 2+ notes")
    base = part[0][0]
    return [[r - base, p, n] for r, p, n in part], \
        {"op": "fragment", "start": start, "count": count}


def displace(notes, rows: int):
    return [[r + rows, p, n] for r, p, n in notes], \
        {"op": "displace", "rows": rows}


def simplify(transform: List[dict]) -> List[dict]:
    """Consecutive shifts and octaves merged: [shift -2, shift -1,
    octave 1] reads [shift -3, octave 1] (and a shift of 9 degrees as
    shift 2, octave 1)."""
    out, degrees, semis, octaves = [], 0, 0, 0

    def flush():
        nonlocal degrees, semis, octaves
        if degrees or octaves and not semis:
            out.extend(_split(degrees + 7 * octaves, 7, -3))
        elif semis or octaves:
            out.extend(_split(semis + 12 * octaves, 12, -6))
        degrees = semis = octaves = 0

    for t in transform:
        if t["op"] == "shift":
            degrees += t["degrees"]
        elif t["op"] == "transpose":
            semis += t["semitones"]
        elif t["op"] == "octave":
            octaves += t["octaves"]
        else:
            flush()
            out.append(dict(t))
    flush()
    return out


def apply(notes, key: Key, transform: List[dict]):
    """A chain of transformations as data (the graph's edges)."""
    out = [list(n) for n in notes]
    for t in transform:
        op = t["op"]
        if op == "shift":
            out, _ = shift(out, key, t["degrees"])
        elif op == "transpose":
            out, _ = transpose(out, t["semitones"])
        elif op == "invert":
            out, _ = invert(out, key)
        elif op == "retrograde":
            out, _ = retrograde(out)
        elif op == "augment":
            out, _ = augment(out)
        elif op == "diminish":
            out, _ = diminish(out)
        elif op == "fragment":
            out, _ = fragment(out, t["start"], t["count"])
        elif op == "displace":
            out, _ = displace(out, t["rows"])
        elif op == "octave":
            out, _ = transpose(out, 12 * t["octaves"])
        else:
            raise AgenticError("bad_transform", f"unknown transformation "
                               f"{op!r}")
    return out


# -- recognition ------------------------------------------------------------------
def _ratio(a, b) -> Optional[float]:
    """The factor b's rhythm is of a's (1, 2 or 0.5), or None."""
    ra, rb = rhythm(a), rhythm(b)
    for f in (1.0, 2.0, 0.5):
        if all(abs(x[0] * f - y[0]) < 1e-9 for x, y in zip(ra, rb)):
            return f
    return None


def _mode(values):
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    best = max(counts.items(), key=lambda kv: (kv[1], -abs(kv[0])))
    return best[0], best[1]


def _split(k: int, size: int, low: int) -> List[dict]:
    """A shift of k degrees (size 7) or semitones (size 12) as the step
    inside an octave and the octaves, so `shift 9` reads `shift 2,
    octave +1`."""
    octaves, step = divmod(k - low, size)
    step += low
    ops = []
    if step:
        ops.append({"op": "shift" if size == 7 else "transpose",
                    ("degrees" if size == 7 else "semitones"): step})
    if octaves:
        ops.append({"op": "octave", "octaves": octaves})
    return ops


def compare(motif_notes, candidate, key: Key) -> Optional[dict]:
    """How `candidate` (same number of notes, same rhythm up to x2 or /2)
    relates to the motif: the transformation under which the most of its
    notes agree. A note changed to fit a chord costs one note, not two
    steps: each reading takes the most common offset between the two
    lines (degrees for a diatonic shift, semitones for a transposition,
    the degree sum for an inversion) and counts the notes that keep it."""
    n = len(candidate)
    if n != len(motif_notes) or n < 2:
        return None
    f = _ratio(motif_notes, candidate)
    if f is None:
        return None
    md = [key.degree(x[1])[0] for x in motif_notes]
    mp = [x[1] for x in motif_notes]
    axis = md[0]
    best = None
    for retro in (False, True):
        cp = [x[1] for x in candidate]
        if retro:
            cp = cp[::-1]
        cd = [key.degree(p)[0] for p in cp]
        readings = []
        k, hits = _mode([c - m for c, m in zip(cd, md)])
        readings.append((hits, 0, _split(k, 7, -3)))
        t, hits = _mode([c - m for c, m in zip(cp, mp)])
        readings.append((hits, 1, _split(t, 12, -6)))
        total, hits = _mode([c + m for c, m in zip(cd, md)])
        readings.append((hits, 2, [{"op": "invert"}]
                         + _split(total - 2 * axis, 7, -3)))
        for hits, order, ops in readings:
            ops = ([{"op": "retrograde"}] if retro else []) + ops
            if f == 2.0:
                ops.append({"op": "augment"})
            elif f == 0.5:
                ops.append({"op": "diminish"})
            rank = (hits, -len(ops), -order)
            if best is None or rank > best[0]:
                best = (rank, {"transform": ops, "notes_matched": hits,
                               "notes": n,
                               "similarity": round(hits / n, 3),
                               "exact": hits == n})
    return best[1]


def find(motif: dict, notes: List[list], key: Key,
         min_similarity: float = 0.66, min_fragment: int = 3) -> List[dict]:
    """Where `motif` occurs in `notes` ([[row, pitch, length]]): whole, or
    as a fragment of `min_fragment`+ notes from its start. Every window is
    scored first and the best are kept, non-overlapping (a greedy scan
    from the left let a chance match across a bar line take the notes of
    a real instance). A match needs at least three agreeing notes."""
    m = motif["notes"]
    scored = []
    for size in range(len(m), min_fragment - 1, -1):
        part = m[:size]
        for i in range(0, len(notes) - size + 1):
            window = notes[i:i + size]
            c = compare(part, window, key)
            if not c or c["similarity"] < min_similarity or \
                    c["notes_matched"] < 3:
                continue
            if size < len(m):
                c["transform"] = [{"op": "fragment", "start": 0,
                                   "count": size}] + c["transform"]
            c.update(motif=motif["id"], row=window[0][0], span=(i, i + size))
            scored.append(c)
    scored.sort(key=lambda c: (-c["notes_matched"], -c["similarity"],
                               c["row"]))
    taken, found = set(), []
    for c in scored:
        a, b = c.pop("span")
        if any(j in taken for j in range(a, b)):
            continue
        taken.update(range(a, b))
        found.append(c)
    return sorted(found, key=lambda c: c["row"])


# -- the graph in the state -------------------------------------------------------
def graph(state: dict) -> dict:
    g = state.setdefault("motifs", {})
    g.setdefault("motifs", {})
    g.setdefault("instances", [])
    g.setdefault("open", [])
    return g


def add_motif(state: dict, motif: dict):
    g = graph(state)
    g["motifs"][motif["id"]] = motif
    if motif["id"] not in g["open"]:
        g["open"].append(motif["id"])


def add_instance(state: dict, motif_id: str, section: str, voice: str,
                 row: int, transform: List[dict], match: Optional[dict]
                 = None, role: str = "") -> dict:
    g = graph(state)
    inst = {"motif": motif_id, "section": section, "voice": voice,
            "row": row, "transform": transform, "role": role,
            "match": match}
    g["instances"] = [i for i in g["instances"]
                      if not (i["section"] == section and i["voice"] == voice
                              and i["row"] == row
                              and i["motif"] == motif_id)]
    g["instances"].append(inst)
    if transform and motif_id in g["open"]:
        g["open"].remove(motif_id)
    return inst


def analyse(content: dict, motifs: Dict[str, dict], key_text: str) -> list:
    """Every occurrence of every motif in every section of its voice,
    found from the rows (not from what the composer said it did)."""
    key = Key(key_text)
    out = []
    for section in content["sections"]:
        for mid, motif in motifs.items():
            ins = content["instruments"].get(motif["voice"], {})
            column = ins.get("channel", motif["voice"])
            if column not in content["columns"]:
                continue
            notes = voice_notes(section, content["columns"].index(column))
            for hit in find(motif, notes, key):
                out.append({"section": section["id"], **hit})
    return out
