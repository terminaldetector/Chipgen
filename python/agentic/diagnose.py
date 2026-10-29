"""diagnose.py — from an observation to where it is, what causes it, and
what could fix it.

An observation (ear.py) says what is wrong and roughly where. A diagnosis
is a card that adds:

    located     the voice, its instrument, its notes in the range with
                their source lines, the worst notes by measurement, and
                for the worst one the chain down to the register writes
                the chip received (trace.explain)
    causes      each with its status: "established" when a measurement
                shows it directly (a voice's share of the energy in the
                buried voice's band), "plausible" when it is an inference
                that would explain it (two voices in one octave), and an
                auditory model's opinion only as "heard by <model>" — the
                three are never merged
    hypotheses  patches that could fix it (patch.py specs), each with the
                effect expected *before* rendering — computed from the
                measured shares and the realised change where that is
                arithmetic, marked as an assumption where it is not, and
                left empty where nothing supports a number — and what it
                risks. loop.py tries them in that order and measures.
    excluded    patches considered and refused, with the reason (a
                declared register, a chip limit, a velocity at its top)

Hypotheses are about arrangement and level: where a voice plays, how loud,
how bright. None removes or adds a note, changes a pitch class, moves an
onset or touches the melody's pitches. Dissonance, syncopation, echo and
dense texture are the music's; they are not faults to be measured away,
and loop.py's guards check every candidate leaves them in place.
"""

import math
from typing import List, Optional

from . import ear as EAR
from . import patch as P
from . import trace
from .state import AgenticError

#: Roles whose pitches are the music's line: never transposed by a fix.
MELODIC_ROLES = ("lead", "melody", "counter")
#: The most a fix may cut an accompanying voice, or raise the target (dB).
MAX_CUT_DB = 6.0
MAX_BOOST_DB = 6.0
#: Aim this far past the line, so a fix is not a coin toss on rounding.
AIM_PAST_DB = 1.0
#: Assumed share of a voice's energy in another's band that an octave move
#: takes out of it. An assumption, said so on the card; the render decides.
REGISTER_MOVED = 0.5
#: A darker contributor: its loudest modulator this many TL steps down.
DARKEN_STEPS = 8
#: Contributors below this share of the band are not worth a hypothesis.
MIN_SHARE = 0.10


def _db_gain_from_share(share: float, change_db: float) -> float:
    """How much a voice's margin rises when a contributor holding `share`
    of the background in its band changes by `change_db`: the rest of the
    background stays, that part scales."""
    rest = (1.0 - share) + share * 10 ** (change_db / 10.0)
    return round(-10.0 * math.log10(max(rest, 1e-9)), 2)


def _cut_for(share: float, need_db: float) -> float:
    """The cut (positive dB) that would give `need_db` from one
    contributor, within [1.5, MAX_CUT_DB], in half-dB steps."""
    left = 10 ** (-need_db / 10.0) - (1.0 - share)
    if left <= 0:
        cut = MAX_CUT_DB
    else:
        cut = -10.0 * math.log10(left / share)
    return round(max(1.5, min(MAX_CUT_DB, cut)) * 2) / 2.0


def _notes_in(timeline, voice: str, rng: dict) -> List[dict]:
    return [n for n in timeline.sounding(rng["t0"], rng["t1"], voice)
            if n["start"] >= rng["t0"] - 1e-6]


def _span(notes) -> Optional[List[int]]:
    pitches = [n["pitch"] for n in notes if n.get("pitch") is not None]
    return [min(pitches), max(pitches)] if pitches else None


def _median(values):
    values = sorted(values)
    return values[len(values) // 2] if values else None


def _name(p):
    from .timeline import pitch_name
    return pitch_name(p) if p is not None else None


def _modulators(content: dict, voice: str) -> List[dict]:
    """The modulators of a voice's FM patch, loudest first (lowest TL,
    overrides included)."""
    ins = content["instruments"].get(voice) or {}
    if not ins.get("patch") or not ins.get("channel", "").startswith("fm"):
        return []
    import instruments
    patch = instruments.BANK.get(ins["patch"])
    if patch is None:
        return []
    carriers = P.carriers(content, voice)
    out = []
    for slot, op in enumerate(patch.operators):
        number = (1, 3, 2, 4)[slot]
        if number in carriers:
            continue
        tl = int(ins.get("overrides", {}).get(f"{number}.tl",
                                              op.total_level))
        out.append({"op": number, "tl": tl})
    return sorted(out, key=lambda m: m["tl"])


# -- locating -------------------------------------------------------------------
def locate(timeline, obs: dict, hearing: dict, explain_writes: bool = True
           ) -> dict:
    """Where the observation lives in the score and in the chip."""
    voice = obs["voices"][0] if obs.get("voices") else None
    rng = timeline.range(_spec(obs))
    out = {"range": {k: rng[k] for k in ("spec", "t0", "t1", "bars",
                                          "sections")}}
    if voice is None:
        return out
    notes = _notes_in(timeline, voice, rng)
    m = hearing["measurements"]["voices"].get(voice, {})
    per_note = {round(n["at_ms"]): n for n in m.get("per_note_band", [])}
    lines = timeline.text.splitlines()
    listed = []
    for n in notes:
        row = timeline.rows[n["row"]] if n.get("row") is not None else None
        at = round((n["start"] - rng["t0"]) * 1000.0)
        listed.append({
            "note": n["name"], "velocity": n["velocity"],
            "start_s": round(n["start"], 3), "section": n["section"],
            "row_in_section": row["row"] if row else None,
            "bar": row["bar"] + 1 if row else None,
            "source_line": row["line"] if row else None,
            "source": lines[row["line"] - 1].strip() if row else None,
            "band_margin_db": (per_note.get(at) or {}).get("band_margin_db")})
    worst = sorted([x for x in listed if x["band_margin_db"] is not None],
                   key=lambda x: x["band_margin_db"])[:3]
    ins = timeline.content["instruments"].get(voice, {})
    out.update({
        "voice": voice, "role": ins.get("role"),
        "instrument": {"channel": ins.get("channel"),
                       "patch": ins.get("patch"),
                       "overrides": ins.get("overrides", {})},
        "notes_total": len(listed), "notes": listed[:6],
        "span": [_name(p) for p in (_span(notes) or [])],
        "worst_notes": worst})
    if worst and explain_writes:
        w = worst[0]
        chain = trace.explain(timeline, w["start_s"],
                              min(rng["t1"], w["start_s"] + 0.05),
                              voice=voice, writes=True, limit=8)
        out["chain_at_worst"] = {
            "t_s": w["start_s"],
            "notes": [{k: n[k] for k in ("voice", "note", "velocity",
                                         "source_line", "source")}
                      for n in chain["notes"]],
            "instrument": (chain["instruments"].get(voice) or {}).get(
                "operators"),
            "register_writes": chain.get("register_writes", []),
            "register_writes_total": chain.get("register_writes_total", 0)}
    return out


def _spec(obs: dict) -> str:
    w = obs["where"]
    return f"rows {w['rows'][0]}-{w['rows'][1]}"


# -- the card ---------------------------------------------------------------------
def diagnose(timeline, obs: dict, hearing: dict, remembered=None,
             tried=(), explain_writes: bool = True) -> dict:
    """A diagnostic card for one observation. `hearing` is the Tool ear's
    report on the range (its measurements are the evidence). `remembered`
    are hypotheses memory.py retrieved, already adapted to these voices;
    they are ranked by what they measured, not by an estimate. `tried` are
    patch signatures to leave out."""
    kind = obs["kind"]
    card = {"observation": obs.get("id"), "kind": kind,
            "statement": obs.get("statement"), "basis": obs.get("basis"),
            "located": locate(timeline, obs, hearing, explain_writes)}
    if kind in ("buried", "too_quiet"):
        _buried(timeline, obs, hearing, card)
    elif kind == "unheard_notes":
        _buried(timeline, obs, hearing, card, unheard=True)
    elif kind == "clipping":
        _clipping(timeline, obs, hearing, card)
    elif kind == "long_hold":
        card["causes"] = [{"status": "established", "statement":
                           obs["statement"], "evidence": obs["evidence"]}]
        card["hypotheses"] = []
        card["question"] = ("a held note can be the music (a pedal, a "
                            "drone): whether to end it is the composer's "
                            "call, so no patch is proposed")
    else:
        card["causes"] = []
        card["hypotheses"] = []
        card["question"] = (f"no measured cause is known for {kind!r}; "
                            f"nothing is proposed")
    for h in remembered or []:
        card.setdefault("hypotheses", []).append(h)
    hyps = [h for h in card.get("hypotheses", [])
            if signature(h["patch"]) not in set(tried)]
    hyps.sort(key=lambda h: (-(h["expect_db"] if h["expect_db"] is not None
                               else -99.0), h.get("cells", 0)))
    seen = set()
    unique = []
    for h in hyps:
        sig = signature(h["patch"])
        if sig in seen:
            continue
        seen.add(sig)
        unique.append(h)
    for i, h in enumerate(unique):
        h["id"] = f"h{i + 1}"
    card["hypotheses"] = unique
    card["not_considered"] = (
        "changing the melody's pitches, the harmony, the rhythm, or "
        "removing notes: the piece's own tensions are not faults")
    return card


def signature(spec) -> str:
    """A patch (or a chain of patches) as a stable string."""
    import json
    if isinstance(spec, list):
        return " + ".join(signature(s) for s in spec)
    keep = {k: v for k, v in sorted(spec.items())
            if k not in ("why", "hypothesis", "expected", "id")}
    return json.dumps(keep, sort_keys=True)


def _try(content, spec, timeline):
    try:
        return P.apply(content, spec, timeline), None
    except AgenticError as error:
        return None, error.to_dict()


def _buried(timeline, obs, hearing, card, unheard: bool = False):
    content = timeline.content
    target = obs["voices"][0]
    rng = timeline.range(_spec(obs))
    m = hearing["measurements"]["voices"].get(target, {})
    margin = m.get("band_margin_db")
    shares = m.get("band_share") or {}
    goal = EAR.BURIED_BODY_DB
    if margin is None:
        card["causes"] = []
        card["hypotheses"] = []
        card["question"] = f"{target} has no pitched notes to measure"
        return
    need = goal + AIM_PAST_DB - margin
    card["measured"] = {"band_margin_db": margin, "goal_db": goal,
                        "need_db": round(need, 2), "band_share": shares}
    causes, hyps, excluded = [], [], []
    target_notes = _notes_in(timeline, target, rng)
    t_span = _span(target_notes)
    t_mid = _median([n["pitch"] for n in target_notes
                     if n.get("pitch") is not None])
    for u, share in shares.items():
        if share < MIN_SHARE:
            continue
        ins = content["instruments"].get(u, {})
        role = ins.get("role", "")
        u_notes = _notes_in(timeline, u, rng)
        u_span = _span(u_notes)
        causes.append({
            "status": "established", "voice": u,
            "statement": f"{u} gives {share:.0%} of the energy in "
                         f"{target}'s band",
            "evidence": {"band_share": share,
                         "method": "each voice alone, energy between 0.8x "
                                   "and 6x each of the target's notes over "
                                   "its body, summed"}})
        if u_span and t_span and u_span[1] >= t_span[0] - 12 \
                and u_span[0] <= t_span[1]:
            causes.append({
                "status": "plausible", "voice": u,
                "statement": f"{u} plays {_name(u_span[0])}-"
                             f"{_name(u_span[1])}, within an octave of "
                             f"{target}'s {_name(t_span[0])}-"
                             f"{_name(t_span[1])}: its fundamentals and "
                             f"low partials fall in {target}'s band",
                "evidence": {"spans": {u: u_span, target: t_span},
                             "method": "note spans from the score"}})
        # 1. lower the contributor
        cut = _cut_for(share, need)
        spec = {"op": "level", "voice": u, "range": rng["spec"], "db": -cut}
        done, error = _try(content, spec, timeline)
        if error:
            excluded.append({"patch": spec, "why": error["message"]})
        else:
            realised = done[1]["realised_db"]["mean"]
            hyps.append({
                "patch": spec, "kind": "balance",
                "hypothesis": f"{u} {abs(realised):.1f} dB quieter in the "
                              f"range leaves {target} more of its band",
                "expect_db": _db_gain_from_share(share, realised),
                "basis": f"arithmetic: {u} holds {share:.0%} of the band; "
                         f"realised {realised:+.2f} dB",
                "risks": [f"{u} ({role or 'no role'}) loses presence"],
                "cells": done[1]["cells"]})
        # 2. move the contributor away by an octave (not a melodic voice)
        pitched = ins.get("channel", "").startswith(("fm", "psg"))
        if pitched and role not in MELODIC_ROLES and u_notes and t_mid:
            u_mid = _median([n["pitch"] for n in u_notes
                             if n.get("pitch") is not None])
            step = -12 if u_mid <= t_mid else 12
            spec = {"op": "transpose", "voice": u, "range": rng["spec"],
                    "semitones": step}
            done, error = _try(content, spec, timeline)
            register = ins.get("register")
            if error:
                excluded.append({"patch": spec, "why": error["message"]})
            elif register and not all(
                    register[0] <= p + step <= register[1]
                    for p in (n["pitch"] for n in u_notes
                              if n.get("pitch") is not None)):
                excluded.append({
                    "patch": spec,
                    "why": f"{u} would leave its declared register "
                           f"{_name(register[0])}-{_name(register[1])}"})
            else:
                hyps.append({
                    "patch": spec, "kind": "register",
                    "hypothesis": f"{u} an octave {'down' if step < 0 else 'up'}"
                                  f" moves its fundamentals out of "
                                  f"{target}'s band; the pitch classes and "
                                  f"the rhythm stay",
                    "expect_db": _db_gain_from_share(
                        share * REGISTER_MOVED, -60.0),
                    "basis": f"assumption: the move takes about "
                             f"{REGISTER_MOVED:.0%} of {u}'s energy out of "
                             f"the band (its upper partials stay)",
                    "risks": [f"{u}'s register and weight change"],
                    "cells": done[1]["cells"]})
        # 3. a darker contributor (FM, its loudest modulator down)
        mods = _modulators(content, u)
        if mods:
            mod = mods[0]
            spec = {"op": "param", "voice": u, "key": f"op{mod['op']}.tl",
                    "delta": DARKEN_STEPS, "range": rng["spec"]}
            done, error = _try(content, spec, timeline)
            if error:
                excluded.append({"patch": spec, "why": error["message"]})
            else:
                hyps.append({
                    "patch": spec, "kind": "timbre",
                    "hypothesis": f"{u}'s loudest modulator (op{mod['op']}, "
                                  f"TL {mod['tl']}) {DARKEN_STEPS * 0.75:g} "
                                  f"dB down: fewer upper partials of {u} "
                                  f"in {target}'s band",
                    "expect_db": None,
                    "basis": "not estimated: how much of the share is "
                             "partials rather than fundamentals is not "
                             "known before the render",
                    "risks": [f"{u}'s character (brightness, bite) changes"],
                    "cells": 0})
    # 4. raise the target itself
    spec = {"op": "level", "voice": target, "range": rng["spec"],
            "db": round(min(MAX_BOOST_DB, max(1.5, need)) * 2) / 2.0}
    done, error = _try(content, spec, timeline)
    if error:
        excluded.append({"patch": spec, "why": error["message"]})
    else:
        realised = done[1]["realised_db"]["mean"]
        hyps.append({
            "patch": spec, "kind": "balance",
            "hypothesis": f"{target} {realised:+.1f} dB (asked "
                          f"{spec['db']:+.1f}; the chip's velocity curve "
                          f"and the 127 ceiling decide what is realised)",
            "expect_db": round(realised, 2),
            "basis": "arithmetic: only the target changes, by its realised "
                     "level",
            "risks": ["the mix gets louder", f"{target} may dominate"],
            "cells": done[1]["cells"]})
    if unheard:
        causes.insert(0, {"status": "established",
                          "statement": obs["statement"],
                          "evidence": obs["evidence"]})
    card["causes"] = causes
    card["hypotheses"] = hyps
    card["excluded"] = excluded


def _clipping(timeline, obs, hearing, card):
    voices = hearing["measurements"]["voices"]
    loudest = sorted(voices.items(), key=lambda kv: -kv[1].get("peak_db",
                                                                -180))
    rng = timeline.range(_spec(obs))
    card["causes"] = [{"status": "established",
                       "statement": obs["statement"],
                       "evidence": obs["evidence"]}]
    hyps, excluded = [], []
    for v, m in loudest[:2]:
        spec = {"op": "level", "voice": v, "range": rng["spec"], "db": -3.0}
        done, error = _try(timeline.content, spec, timeline)
        if error:
            excluded.append({"patch": spec, "why": error["message"]})
            continue
        hyps.append({"patch": spec, "kind": "balance",
                     "hypothesis": f"{v}, the loudest voice alone "
                                   f"({m.get('peak_db')} dBFS peak), 3 dB "
                                   f"down",
                     "expect_db": None,
                     "basis": "not estimated: peaks of a sum are not the "
                              "sum of peaks",
                     "risks": [f"{v} loses presence"],
                     "cells": done[1]["cells"]})
    card["hypotheses"] = hyps
    card["excluded"] = excluded
