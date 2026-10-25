"""joins.py — what happens where one section hands over to the next, read
from the score: the audit's H02 says a heard "transition" had nothing to
be measured against. Each join (the last bar of one slot against the
first bar of the next) gets

    density     notes started per beat, either side, and the jump
    register    the lead's median pitch either side, in semitones
    level       the mean velocity of the notes either side (the score's
                level, not the rendered one)
    gap         the longest stretch around the bar line where nothing
                sounds (a hard stop reads as a gap of a beat or more)
    harmony     the pitch classes sounding in the last beat and the first
                beat, and how many they share

and a `flags` list naming the jumps past their limits — a reading for a
person or a model to weigh, not a verdict: a dramatic entry is often
meant. Nothing is rendered; `measure` runs in milliseconds.
"""

from typing import List, Optional

#: jumps past these are flagged
LIMITS = {"density_ratio": 2.0, "register_semitones": 7.0,
          "velocity": 25.0, "gap_beats": 1.0}


def _median(values):
    values = sorted(values)
    return values[len(values) // 2] if values else None


def _side(tl, a: float, b: float, lead: Optional[str]) -> dict:
    notes = [n for n in tl.notes if a <= n["start"] < b and
             n.get("pitch") is not None]
    beat = tl.duration / max(1, len(tl.rows)) * \
        int(tl.content["structure"]["lpb"])
    lead_notes = [n for n in notes if lead is None or n["voice"] == lead]
    vel = [n.get("velocity") for n in notes
           if isinstance(n.get("velocity"), (int, float))]
    return {"notes": len(notes),
            "per_beat": round(len(notes) / max(1e-6, (b - a) / beat), 2),
            "lead_pitch": _median([n["pitch"] for n in lead_notes]),
            "velocity": round(sum(vel) / len(vel), 1) if vel else None}


def _pcs(tl, a: float, b: float) -> set:
    return {int(n["pitch"]) % 12 for n in tl.sounding(a, b)
            if n.get("pitch") is not None}


def _gap(tl, t: float, around: float) -> float:
    """The longest silence (no note sounding) within `around` s of t."""
    spans = sorted((max(t - around, n["start"]), min(t + around, n["end"]))
                   for n in tl.sounding(t - around, t + around))
    longest, at = 0.0, t - around
    for s, e in spans:
        longest = max(longest, s - at)
        at = max(at, e)
    return round(max(longest, t + around - at), 3)


def measure(tl, lead: Optional[str] = None) -> List[dict]:
    """Every join of the piece's order. `lead`: the voice whose register is
    followed (the instrument whose role is lead, if not given)."""
    if lead is None:
        lead = next((v for v, ins in tl.content["instruments"].items()
                     if ins.get("role") == "lead"), None)
    slots = tl.slots()
    rows_per_bar = tl.rows_per_bar
    lpb = int(tl.content["structure"]["lpb"])
    out = []
    for prev, nxt in zip(slots, slots[1:]):
        t = nxt["t0"]
        bar = rows_per_bar * (t - prev["t0"]) / max(1, prev["row1"] -
                                                    prev["row0"])
        beat = bar / (rows_per_bar / lpb)
        before = _side(tl, max(prev["t0"], t - bar), t, lead)
        after = _side(tl, t, min(nxt["t1"], t + bar), lead)
        last = _pcs(tl, t - beat, t)
        first = _pcs(tl, t, t + beat)
        gap = _gap(tl, t, beat)
        j = {"from": prev["section"], "to": nxt["section"],
             "t": round(t, 3), "bar": nxt["row0"] // rows_per_bar + 1,
             "before": before, "after": after,
             "density_ratio": round(max(after["per_beat"], 0.01) /
                                    max(before["per_beat"], 0.01), 2),
             "register_semitones": (after["lead_pitch"] -
                                    before["lead_pitch"])
             if None not in (after["lead_pitch"], before["lead_pitch"])
             else None,
             "velocity": round(after["velocity"] - before["velocity"], 1)
             if None not in (after["velocity"], before["velocity"])
             else None,
             "gap_beats": round(gap / beat, 2),
             "harmony": {"last_beat": sorted(last),
                         "first_beat": sorted(first),
                         "shared": len(last & first)}}
        flags = []
        r = j["density_ratio"]
        if r >= LIMITS["density_ratio"] or r <= 1 / LIMITS["density_ratio"]:
            flags.append(f"density x{r:g}")
        if j["register_semitones"] is not None and \
                abs(j["register_semitones"]) >= LIMITS["register_semitones"]:
            flags.append(f"the lead jumps {j['register_semitones']:+d} "
                         f"semitones")
        if j["velocity"] is not None and \
                abs(j["velocity"]) >= LIMITS["velocity"]:
            flags.append(f"velocity {j['velocity']:+g}")
        if j["gap_beats"] >= LIMITS["gap_beats"]:
            flags.append(f"silence of {j['gap_beats']:g} beats at the join")
        if last and first and not (last & first):
            flags.append("no pitch class shared across the bar line")
        j["flags"] = flags
        out.append(j)
    return out
