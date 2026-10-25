"""anchors.py — where a phrase ends, so a reference is listened to from the
start of a phrase and a cadence beside a cadence, not bar 9 beside bar 9.

Each bar line of a reference (and of the piece) gets a phrase-end score
from what happens around it:

    settling   the last beat before it is quieter in onsets than the
               part's usual beat (a held note, a rest: the phrase
               breathes)
    change     the two bars after it differ from the two before (other
               pitch classes and density in a score; other level and
               density in audio)
    arrival    the downbeat after it is struck harder than most (more
               notes start together; a stronger onset)
    prior      a bar line at a multiple of four bars gets a little more,
               eight a little more again: most chip music phrases in fours
               — a prior, said as one, never the only reason

A bar line whose score is a local peak and at least ANCHOR_MIN is an
anchor; "clear" when it also stands CLEAR_MARGIN above its neighbours,
"weak" otherwise. A person's marks (`mark`) replace the computed ones and
say so. In the piece, the sections of the plan are anchors already (their
functions: statement, cadence ...); phrase ends inside them are scored the
same way from its notes.

`phrase_span` picks where a reference fragment of n bars starts: at the
start of a phrase near the region asked for — or, for a cadence, so that
it ends on the strongest anchor. Without an anchor it says so and keeps
the region's bars.
"""

import math
from typing import Dict, List, Optional

from . import features as F
from . import references as REF
from .state import AgenticError

#: a bar line scoring this much or more (and a local peak) is an anchor
ANCHOR_MIN = 0.45
#: and a clear one when it stands this far above both neighbours
CLEAR_MARGIN = 0.12
#: audio anchors are scored over this much of a long reference (s)
AUDIO_LIMIT_S = 240.0
#: a section function that ends a phrase rather than starts one
ENDINGS = ("cadence", "ending", "close", "outro", "coda")
WEIGHTS = {"settling": 0.4, "change": 0.35, "arrival": 0.25}


# -- scoring bar lines ------------------------------------------------------------------
def _pitched(notes: List[dict]) -> List[dict]:
    return [n for n in notes if n.get("pitch") is not None and
            n.get("end", n["start"]) - n["start"] > 0.05]


def _profile_notes(notes, a: float, b: float):
    ns = [n for n in notes if a <= n["start"] < b]
    pcs = [0.0] * 12
    for n in ns:
        pcs[int(n["pitch"]) % 12] += 1.0
    total = sum(pcs) or 1.0
    return [v / total for v in pcs], len(ns)


def _scores_from_notes(notes: List[dict], starts: List[float],
                       beats_per_bar: int) -> List[dict]:
    notes = _pitched(notes)
    out = []
    if len(starts) < 3 or not notes:
        return out
    bar_s = [b - a for a, b in zip(starts, starts[1:])]
    per_beat = len(notes) / max(1e-6, (starts[-1] - starts[0])
                                / (sum(bar_s) / len(bar_s)) * beats_per_bar)
    onsets_at = []
    for k in range(1, len(starts) - 1):
        s = starts[k]
        onsets_at.append(sum(1 for n in notes if abs(n["start"] - s) < 0.06))
    top_arrival = sorted(onsets_at)[int(0.9 * (len(onsets_at) - 1))] or 1
    for k in range(1, len(starts) - 1):
        s = starts[k]
        beat = bar_s[k - 1] / beats_per_bar
        last = sum(1 for n in notes if s - beat <= n["start"] < s - 0.03)
        settling = 1.0 - min(1.0, last / max(per_beat, 1e-6))
        a0 = starts[max(0, k - 2)]
        b1 = starts[min(len(starts) - 1, k + 2)]
        p0, c0 = _profile_notes(notes, a0, s)
        p1, c1 = _profile_notes(notes, s, b1)
        dist = 0.5 * sum(abs(x - y) for x, y in zip(p0, p1))
        dens = abs(c1 - c0) / max(1.0, 0.5 * (c0 + c1))
        change = min(1.0, 0.7 * dist + 0.3 * dens)
        arrival = min(1.0, onsets_at[k - 1] / top_arrival)
        out.append({"bar_line": k, "t": round(s, 3),
                    "parts": {"settling": round(settling, 3),
                              "change": round(change, 3),
                              "arrival": round(arrival, 3)}})
    return out


def _scores_from_audio(stereo, rate: float, starts: List[float],
                       beats_per_bar: int) -> List[dict]:
    mono = F.mono_of(stereo)
    env, hop, _low = F.onset_envelope(mono, rate)
    times = F.peaks(env, hop)
    if len(starts) < 3:
        return []
    bar_s = [b - a for a, b in zip(starts, starts[1:])]
    beats = (starts[-1] - starts[0]) / (sum(bar_s) / len(bar_s)) * \
        beats_per_bar
    per_beat = len(times) / max(1.0, beats)

    def level(a: float, b: float) -> float:
        i, j = int(a * rate), int(b * rate)
        seg = mono[i:j]
        if not seg:
            return -90.0
        p = sum(v * v for v in seg) / len(seg)
        return 10 * math.log10(p) if p > 1e-12 else -90.0

    def strength(t: float) -> float:
        i = int(t / hop)
        return max(env[max(0, i - 3):i + 4] or [0.0])

    hits = [strength(starts[k]) for k in range(1, len(starts) - 1)]
    top = sorted(hits)[int(0.9 * (len(hits) - 1))] or 1.0
    out = []
    for k in range(1, len(starts) - 1):
        s = starts[k]
        beat = bar_s[k - 1] / beats_per_bar
        last = sum(1 for t in times if s - beat <= t < s - 0.03)
        settling = 1.0 - min(1.0, last / max(per_beat, 1e-6))
        a0 = starts[max(0, k - 2)]
        b1 = starts[min(len(starts) - 1, k + 2)]
        c0 = sum(1 for t in times if a0 <= t < s)
        c1 = sum(1 for t in times if s <= t < b1)
        dens = abs(c1 - c0) / max(1.0, 0.5 * (c0 + c1))
        dl = abs(level(s, b1) - level(a0, s)) / 6.0
        change = min(1.0, 0.5 * dens + 0.5 * min(1.0, dl))
        arrival = min(1.0, hits[k - 1] / top)
        out.append({"bar_line": k, "t": round(s, 3),
                    "parts": {"settling": round(settling, 3),
                              "change": round(change, 3),
                              "arrival": round(arrival, 3)}})
    return out


def _pick(scored: List[dict], basis: str) -> List[dict]:
    """Weighted scores, the prior, local peaks -> anchors."""
    for x in scored:
        s = sum(WEIGHTS[k] * v for k, v in x["parts"].items())
        prior = 0.08 if x["bar_line"] % 4 == 0 else 0.0
        prior += 0.06 if x["bar_line"] % 8 == 0 else 0.0
        x["prior"] = round(prior, 2)
        x["score"] = round(min(1.0, s + prior), 3)
    out = []
    for i, x in enumerate(scored):
        left = scored[i - 1]["score"] if i > 0 else 0.0
        right = scored[i + 1]["score"] if i + 1 < len(scored) else 0.0
        if x["score"] < ANCHOR_MIN or x["score"] < left or \
                x["score"] < right:
            continue
        margin = x["score"] - max(left, right)
        out.append({"bar": x["bar_line"], "t": x["t"], "kind": "phrase_end",
                    "score": x["score"], "parts": x["parts"],
                    "prior": x["prior"], "basis": basis,
                    "certainty": "clear" if margin >= CLEAR_MARGIN and
                    x["score"] >= ANCHOR_MIN + 0.1 else "weak"})
    return out


# -- a reference's anchors ----------------------------------------------------------------
def of_reference(project, rid: str, refresh: bool = False) -> dict:
    """The reference's phrase anchors (computed once and kept on its
    record), or a person's marks. -> {"anchors", "basis", "method",
    "over_s"}. `bar` is the bar that ends the phrase: the next phrase
    starts on bar + 1."""
    ref = REF.get(project, rid)
    have = ref.get("anchors")
    if have and (have.get("basis") == "manual" or not refresh):
        return have
    starts = ref["grid"]["bars"]
    bpb = int(ref["grid"].get("beats_per_bar", 4))
    notes = REF.notes(project, rid)
    if _pitched(notes):
        scored = _scores_from_notes(notes, starts, bpb)
        basis = "the source's notes"
        over = ref["seconds"]
    else:
        over = min(ref["seconds"], AUDIO_LIMIT_S)
        cut = [s for s in starts if s <= over]
        stereo = REF.read(project, rid, 0.0, over)
        scored = _scores_from_audio(stereo, REF.RATE, cut, bpb)
        basis = "the audio"
    rec = {"anchors": _pick(scored, basis), "basis": basis,
           "over_s": round(over, 1), "bar_lines": len(scored),
           "grid": ref["grid"]["source"],
           "method": "per bar line: settling of the last beat, change "
                     "between the two bars either side, arrival on the "
                     "downbeat, a prior for fours and eights; local peaks "
                     f"of {ANCHOR_MIN:g} or more"}
    if ref["grid"].get("uncertain_after_s") is not None:
        rec["grid_uncertain_after_s"] = ref["grid"]["uncertain_after_s"]
    ref["anchors"] = rec
    return rec


def mark(project, rid: str, bars: List[int], note: str = "") -> dict:
    """A person's phrase ends (the bars that end a phrase), kept in place
    of the computed ones."""
    ref = REF.get(project, rid)
    starts = ref["grid"]["bars"]
    bad = [b for b in bars if not 1 <= int(b) < len(starts)]
    if bad:
        raise AgenticError("bad_anchor", f"bars {bad}: {rid} has "
                           f"{len(starts)} bars; a phrase end is a bar "
                           f"before the last", reference=rid)
    rec = {"anchors": [{"bar": int(b), "t": round(starts[int(b)], 3),
                        "kind": "phrase_end", "score": 1.0,
                        "basis": "manual", "certainty": "clear"}
                       for b in sorted(set(int(b) for b in bars))],
           "basis": "manual", "note": note,
           "method": "marked by a person"}
    ref["anchors"] = rec
    return rec


# -- the piece's anchors ------------------------------------------------------------------
def of_piece(tl) -> dict:
    """The piece's sections (from the plan, with their functions) and the
    phrase ends inside them, from its notes."""
    rpb = tl.rows_per_bar
    starts = [r["t0"] for r in tl.rows[::rpb]]
    plan = {p["id"]: p for p in
            tl.content.get("structure", {}).get("plan", []) or []}
    sections = []
    for s in tl.slots():
        p = plan.get(s["section"], {})
        sections.append({"section": s["section"], "slot": s["slot"],
                         "bar": s["row0"] // rpb + 1, "t": round(s["t0"], 3),
                         "function": p.get("function")
                         or (s.get("intent") or {}).get("function")})
    bpb = int(tl.content["structure"].get("beats_per_bar", 4))
    scored = _scores_from_notes(tl.notes, starts + [tl.duration], bpb)
    return {"sections": sections,
            "anchors": _pick(scored, "the piece's notes")}


def function_of(tl, rng: dict) -> Optional[str]:
    """The plan's function of the section the range starts in."""
    plan = {p["id"]: p for p in
            tl.content.get("structure", {}).get("plan", []) or []}
    for s in tl.slots():
        if s["row0"] <= rng["row0"] < s["row1"]:
            p = plan.get(s["section"]) or {}
            sec = next((x for x in tl.content.get("sections", [])
                        if x["id"] == s["section"]), {})
            return p.get("function") or (sec.get("intent") or {}).get(
                "function")
    return None


# -- where a reference fragment starts ---------------------------------------------------------
def phrase_span(project, rid: str, n_bars: int,
                function: Optional[str] = None,
                near_bar: Optional[int] = None) -> dict:
    """The first bar of an n-bar reference fragment, by phrase: the start
    of the phrase nearest `near_bar` (the region's first bar), clear
    anchors before weak ones; for an ending function, the span that ends
    on the strongest anchor. -> {"first_bar", "aligned_by", "anchor",
    "certainty", "why"}."""
    ref = REF.get(project, rid)
    total = len(ref["grid"]["bars"])
    near = near_bar or (ref["regions"][0]["bars"][0] if ref.get("regions")
                        else 1)
    rec = of_reference(project, rid)
    anchors = [a for a in rec["anchors"]
               if rec.get("grid_uncertain_after_s") is None or
               a["t"] < rec["grid_uncertain_after_s"]]
    fallback = {"first_bar": near, "aligned_by": "bars",
                "anchor": None, "certainty": None,
                "why": f"no phrase anchor in {rid} ({rec['basis']}): the "
                       f"region's bars"}
    if not anchors:
        return fallback
    rank = {"clear": 0, "weak": 1}
    if function in ENDINGS:
        options = [a for a in anchors if a["bar"] - n_bars + 1 >= 1]
        if not options:
            return fallback
        best = min(options, key=lambda a: (rank[a["certainty"]],
                                           -a["score"],
                                           abs(a["bar"] - near)))
        first = best["bar"] - n_bars + 1
        why = (f"{function}: the {n_bars} bars that end on {rid}'s "
               f"{best['certainty']} phrase end at bar {best['bar']}")
    else:
        starts = [{"bar": 1, "certainty": "clear", "score": 1.0,
                   "t": 0.0, "basis": "the start"}] + \
            [dict(a, bar=a["bar"] + 1) for a in anchors]
        options = [a for a in starts if a["bar"] + n_bars - 1 <= total]
        if not options:
            return fallback
        best = min(options, key=lambda a: (rank[a["certainty"]],
                                           abs(a["bar"] - near)))
        first = best["bar"]
        why = (f"from the start of a phrase: bar {first}, after "
               + ("the start of the piece" if first == 1 else
                  f"a {best['certainty']} phrase end at bar {first - 1}")
               + (f" (the region starts at bar {near})" if first != near
                  else ""))
    return {"first_bar": int(first), "aligned_by": "phrase anchors",
            "anchor": {k: best.get(k) for k in ("bar", "t", "score",
                                                 "certainty", "basis")},
            "certainty": best["certainty"], "basis": rec["basis"],
            "why": why}


# -- the window an ear is sent ----------------------------------------------------------------
#: the fewest bars a choice is listened to over
WINDOW_MIN_BARS = 2


def window_for(tl, spec: str, o: dict) -> dict:
    """The bars an ear is played to choose about an observation: the
    phrase (or phrases) around where it was heard, inside the round's range
    — not the whole range again. Played from the top as ever; only the clip
    is shorter. -> {"spec", "bars", "why"}; the range itself when the
    observation says nowhere."""
    rng = tl.range(spec)
    r0 = rng["row0"] // tl.rows_per_bar + 1
    r1 = (rng["row1"] - 1) // tl.rows_per_bar + 1
    where = (o.get("where") or {}).get("bars") or o.get("bars")
    if not where or r1 - r0 + 1 <= WINDOW_MIN_BARS:
        return {"spec": spec, "bars": [r0, r1],
                "why": "the whole range (the observation names no bars)"
                if not where else "the range is already short"}
    b0, b1 = max(r0, int(where[0])), min(r1, int(where[-1]))
    piece = of_piece(tl)
    ends = sorted({a["bar"] for a in piece["anchors"]} |
                  {s["bar"] - 1 for s in piece["sections"] if s["bar"] > 1})
    start = max([e + 1 for e in ends if e + 1 <= b0] + [r0])
    end = min([e for e in ends if e >= b1] + [r1])
    if end - start + 1 < WINDOW_MIN_BARS:
        end = min(r1, start + WINDOW_MIN_BARS - 1)
        start = max(r0, end - WINDOW_MIN_BARS + 1)
    return {"spec": f"bars {start}-{end}", "bars": [start, end],
            "why": f"the phrase around bars {b0}-{b1}, where it was heard "
                   f"(of bars {r0}-{r1})"}
