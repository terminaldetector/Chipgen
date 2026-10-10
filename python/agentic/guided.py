"""guided.py — improve a passage toward what references do, by listening
side by side, tracing the gap to the notes and instruments that cause it,
and trying a few changes that could close it.

One round, on one range of the piece:

    listen       the range, each reference's matching bars and the chosen
                 sketches, aligned on bars and matched in loudness
                 (listen.compare): observations heard by a verified model,
                 measured gaps on the dimensions each reference is for,
                 and assumptions the references' sources make plausible
    trace        each gap followed to what makes it: the voice, its notes
                 in the range (how many, how long), its instrument and
                 program, its pan; the link is "assumed" until a change
                 tests it
    hypotheses   changes that carry over the reference's principle, not
                 its notes — a vibrato on held notes, a timbre that moves
                 inside the note, a denser or swung accompaniment, voices
                 spread in the stereo field, an echo voice, shorter notes —
                 several per gap, each with the reading it should move
    test         each rendered over the range only; its readings against
                 the original's and the reference's on the same scale; the
                 loop's guards (with what the op may change), the guard
                 that refuses any figure of a reference's melody, and, with
                 a verified model, the original against the candidate
    keep         the change that closes the most of its gap (a third at
                 least) with every guard kept is committed; the rest are
                 kept as rejected sketches, each with its reason. An
                 instrument gap goes to instrument.design, which keeps or
                 rolls back on its own measurements

The report says what was heard (and by what), what was measured, which
parameter changed and why, what moved, and which hypotheses were rolled
back — and, when no model listened, says that nothing was heard.
"""

import os
import time
from typing import Dict, List, Optional

from . import ear as EAR
from . import features as F
from . import instrument as INS
from . import listen as LI
from . import loop as L
from . import references as REF
from . import render as R
from . import sketches as SK
from . import timeline as T
from .state import AgenticError, content as state_content

#: The share of a gap a change must close to be kept.
MIN_CLOSED = 1.0 / 3.0
#: Notes in a row of the same intervals and rhythm that count as a figure
#: of someone else's melody.
COPY_NGRAM = 5


# -- the guard against copying ------------------------------------------------------
def _figures(notes: List[dict], n: int = COPY_NGRAM) -> Dict[tuple, dict]:
    """Every run of `n` notes of one voice as (intervals, rhythm): the
    intervals in semitones, the time between onsets as a ratio to the
    first gap (quantized to eighths), so a transposed or slower copy is
    the same figure."""
    by: Dict[str, List[dict]] = {}
    for x in notes:
        if x.get("pitch") is not None:
            by.setdefault(x["voice"], []).append(x)
    out = {}
    for v, ns in by.items():
        ns = sorted(ns, key=lambda x: x["start"])
        for i in range(len(ns) - n + 1):
            run = ns[i:i + n]
            gaps = [b["start"] - a["start"] for a, b in zip(run, run[1:])]
            if gaps[0] <= 1e-6:
                continue
            key = (tuple(b["pitch"] - a["pitch"] for a, b in
                         zip(run, run[1:])),
                   tuple(round(g / gaps[0] * 8) for g in gaps))
            if len(set(key[0])) == 1 and key[0][0] == 0:
                continue            # a repeated note is nobody's figure
            out.setdefault(key, {"voice": v, "t": round(run[0]["start"], 3)})
    return out


def copy_guard(project, base_tl, cand_tl, rng: dict) -> dict:
    """Refuse a change that brings in a figure of a reference's melody:
    the candidate's melodic and echo voices over the range, every run of
    COPY_NGRAM notes, against every voice of every reference with a
    source. Only figures the change brought in count (one the piece
    already had is not the change's)."""
    roles = ("lead", "melody", "counter", "echo")

    def ours(tl):
        voices = [v for v, i in tl.content["instruments"].items()
                  if i.get("role") in roles]
        return [n for n in tl.sounding(rng["t0"], rng["t1"])
                if n["voice"] in voices and n.get("pitch") is not None]
    before = _figures(ours(base_tl))
    after = _figures(ours(cand_tl))
    new = {k: v for k, v in after.items() if k not in before}
    hits = []
    for ref in REF.all_(project):
        theirs = _figures(REF.notes(project, ref["id"]))
        for key, where in new.items():
            if key in theirs:
                hits.append(f"{where['voice']} at {where['t']} s plays a "
                            f"figure of {ref['id']} {theirs[key]['voice']} "
                            f"at {theirs[key]['t']} s")
    return {"name": "no_reference_copy", "ok": not hits,
            "detail": "no run of %d notes the change brought in is a "
                      "reference's" % COPY_NGRAM if not hits
            else "; ".join(hits[:3])}


# -- what a gap means, and what could close it ---------------------------------------
#: reading -> (dimension, what kind of change carries the principle over)
PRINCIPLES = {
    "voice.vibrato_depth_cents": ("modulation", "instrument"),
    "voice.vibrato_rate_hz": ("modulation", "instrument"),
    "voice.vibrato_delay_ms": ("modulation", "instrument"),
    "voice.vibrato_share": ("modulation", "instrument"),
    "voice.brightness_development": ("timbre", "instrument"),
    "voice.brightness_body": ("timbre", "instrument"),
    "onsets_per_beat": ("rhythm", "texture"),
    "notes_per_beat": ("arrangement", "texture"),
    "offbeat_share": ("rhythm", "feel"),
    "stereo.side_over_mid_db": ("space", "space"),
    "stereo.correlation": ("space", "space"),
    "tail_ms": ("space", "space"),
    "attack_rise_ms": ("rhythm", "articulation"),
}


def trace_gap(tl, rng: dict, obs: dict) -> dict:
    """What in the piece makes the reading what it is: per voice, its
    notes in the range and their lengths, its role, instrument, program
    and pan. The link from the gap to a voice is an assumption here."""
    from synthesis import registry
    voices = {}
    for v, ins in tl.content["instruments"].items():
        ns = [n for n in tl.sounding(rng["t0"], rng["t1"], v)
              if n["start"] >= rng["t0"] - 1e-6]
        if not ns:
            continue
        lengths = sorted(n["end"] - n["start"] for n in ns)
        compiled = registry.lookup("ym2612", ins.get("patch") or "")
        program = (compiled.style.get("program") if compiled is not None
                   else None)
        voices[v] = {"role": ins.get("role"), "channel": ins["channel"],
                     "patch": ins.get("patch"), "pan": ins.get("pan") or
                     ("C" if ins["channel"].startswith("fm") else "mono"),
                     "notes": len(ns),
                     "median_length_s": round(lengths[len(lengths) // 2], 3),
                     "program": {k: program.get(k) for k in
                                 ("vibrato", "articulation")
                                 if program and program.get(k)}
                     if program else None}
    reading = obs.get("feature", "")
    link = {"voice.": "the voice whose notes were read: " +
            ", ".join(obs.get("voices") or []),
            "onsets_per_beat": "the voices that set the pulse: " +
            ", ".join(v for v, i in voices.items()
                      if i["role"] in ("drums", "hats", "bass", "arp")),
            "stereo.": "every FM voice's pan (" + ", ".join(
                f"{v} {i['pan']}" for v, i in voices.items()
                if i["channel"].startswith("fm")) + "); the PSG is mono",
            "tail_ms": "how long notes ring into the gaps: the release "
                       "rates and the note lengths"}
    why = next((t for k, t in link.items() if reading.startswith(k)),
               "not traced")
    return {"reading": reading, "voices": voices,
            "link": why, "basis": "assumed until a change tests it"}


def _fm_voices(tl, roles=None) -> List[str]:
    return [v for v, i in tl.content["instruments"].items()
            if i["channel"].startswith("fm")
            and (roles is None or i.get("role") in roles)]


def hypotheses(tl, rng: dict, obs: dict, trace: dict) -> List[dict]:
    """Changes that carry over the principle behind a gap, each a patch
    chain with the reading it should move and which way."""
    reading = obs["feature"]
    gap = obs.get("gap") or 0.0
    spec = rng["spec"]
    out = []
    voices = trace["voices"]
    if reading in ("onsets_per_beat", "notes_per_beat"):
        pulse = [v for v, i in voices.items()
                 if i["role"] in ("hats", "drums", "arp", "bass")]
        if gap > 0:              # the reference is busier
            for v in [x for x in pulse if voices[x]["role"] == "hats"] + \
                    [x for x in pulse if voices[x]["role"] == "arp"]:
                out.append({"id": f"dense_{v}", "chain": [
                    {"op": "density", "voice": v, "range": spec,
                     "mode": "double"}],
                    "principle": "a busier pulse from the accompaniment, "
                                 "not from the melody",
                    "expect": "onsets per beat up"})
        else:
            for v in [x for x in pulse if voices[x]["role"] in
                      ("hats", "arp")]:
                out.append({"id": f"thin_{v}", "chain": [
                    {"op": "density", "voice": v, "range": spec,
                     "mode": "thin"}],
                    "principle": "air in the accompaniment",
                    "expect": "onsets per beat down"})
    elif reading == "offbeat_share":
        pulse = [v for v, i in voices.items()
                 if i["role"] in ("hats", "bass", "arp")]
        if gap > 0:             # the reference sits more between the beats
            for v in [x for x in pulse if voices[x]["role"] == "bass"]:
                out.append({"id": f"anticipate_{v}", "chain": [
                    {"op": "rhythm", "voice": v, "range": spec,
                     "mode": "anticipate"}],
                    "principle": "the bass pushing ahead of the beat",
                    "expect": "more onsets between the eighths"})
            for v in [x for x in pulse if voices[x]["role"] == "hats"]:
                out.append({"id": f"swing_{v}", "chain": [
                    {"op": "rhythm", "voice": v, "range": spec,
                     "mode": "swing", "share": 0.33}],
                    "principle": "the off-beats late by a third of a row",
                    "expect": "more onsets between the eighths; the "
                              "same notes"})
    elif reading.startswith("stereo.") or reading == "tail_ms":
        fm = _fm_voices(tl)
        pads = [v for v in fm if voices.get(v, {}).get("role") in
                ("pad", "chords", "arp")]
        leads = [v for v in fm if voices.get(v, {}).get("role") in
                 ("lead", "melody")]
        if pads:
            out.append({"id": "spread", "chain": [
                {"op": "pan", "voice": pads[0], "side": "L"}],
                "principle": "voices placed apart in the stereo field",
                "expect": "side over mid up, correlation down"})
        if leads:
            out.append({"id": "echo", "chain": [
                {"op": "echo", "voice": leads[0], "range": spec,
                 "rows": 3, "db": -9.0}],
                "principle": "an echo voice: a quieter copy of the lead a "
                             "few rows later on the other side",
                "expect": "side over mid up; sound in the gaps (tails)"})
            if pads:
                out.append({"id": "spread+echo", "chain": [
                    {"op": "pan", "voice": pads[0], "side": "L"},
                    {"op": "echo", "voice": leads[0], "range": spec,
                     "rows": 3, "db": -9.0}],
                    "principle": "both: the pad to one side, the lead's "
                                 "echo to the other",
                    "expect": "side over mid up the most"})
    elif reading == "attack_rise_ms":
        for v in [x for x, i in voices.items() if i["role"] in
                  ("pad", "chords")]:
            out.append({"id": f"gate_{v}", "chain": [
                {"op": "articulation", "voice": v, "range": spec,
                 "gate": 0.6}],
                "principle": "shorter accompaniment notes leave room for "
                             "the attacks",
                "expect": "rise times of the mix's onsets shorter"})
    return out


# -- testing one ---------------------------------------------------------------------------
def _reading(profile: dict, path: str):
    return F.get(profile, path)


def _test(project, base_tl, rng, hyp: dict, obs: dict, ours_f: dict,
          ref_f: dict, ref_reading: float, cache, tool, base_h,
          folder: str, ear) -> dict:
    out = {"id": hyp["id"], "chain": hyp["chain"],
           "principle": hyp["principle"], "expect": hyp["expect"],
           "reading": obs["feature"]}
    try:
        content, deltas, tl = L._apply_chain(base_tl.content, hyp["chain"],
                                             base_tl)
    except AgenticError as error:
        out.update(accepted=False, why=f"patch refused: {error}")
        return out
    spec = rng["spec"]
    cand_f = LI.of_timeline(tl, spec, f"candidate {hyp['id']}", "ours",
                            hyp["id"], cache=cache)
    cand_m = LI.measure(project, cand_f)
    base_m = ours_f["_measured"]
    path = obs["feature"]
    if path in ("onsets_per_beat", "offbeat_share") and \
            obs.get("counted_from") == "audio":
        path += "_audio"                    # as the reference was counted
    before = _reading(base_m, path)
    after = _reading(cand_m, path)
    h = tool.hear(tl, tl.range(spec))
    checks = L.guards(base_tl, tl, base_h, h, rng, obs | {"voices": []},
                      hyp["chain"])
    checks.append(copy_guard(project, base_tl, tl, rng))
    closed = of_gap = None
    step_note = ""
    if before is not None and after is not None and ref_reading is not None:
        gap0 = abs(ref_reading - before)
        gap1 = abs(ref_reading - after)
        # the share of the gap closed, as reported; and the step a change
        # is held to, the share of the gap or of the reading itself,
        # whichever is smaller: a passage with a quarter of the
        # reference's onsets is not expected to reach it in one step, but
        # doubling them is a step
        of_gap = (gap0 - gap1) / gap0 if gap0 > 1e-9 else 0.0
        scale = min(gap0, abs(before)) if abs(before) > 1e-9 else gap0
        closed = (gap0 - gap1) / scale if scale > 1e-9 else 0.0
        if (ref_reading - before) * (ref_reading - after) < 0 and \
                gap1 > gap0:
            closed = -abs(closed)           # overshot past it, further
            of_gap = -abs(of_gap)
        if scale < gap0 - 1e-9:
            step_note = (f" (a step of {closed:.0%} of the reading itself, "
                         f"the measure where the gap is larger than it)")
    failed = [g for g in checks if not g["ok"]]
    # the original's voices were written once, with the listening reel
    ab = LI.write([dict(ref_f), dict(ours_f, label="original", stems=None),
                   dict(cand_f, label=f"candidate {hyp['id']}")],
                  folder, f"{obs['id']}_{hyp['id']}", mp3=True)
    out.update(deltas=[L._compact_delta(d) for d in deltas],
               before=before, after=after, reference=ref_reading,
               closed=round(closed, 3) if closed is not None else None,
               closed_of_gap=round(of_gap, 3) if of_gap is not None
               else None,
               guards=checks, ab_reel=ab["reel"].get("mp3")
               or ab["reel"]["wav"], ab_index=ab["path"],
               _content=content, _timeline=tl)
    if failed:
        out.update(accepted=False, why="guard failed: " + "; ".join(
            f"{g['name']} ({g['detail']})" for g in failed))
    elif closed is None:
        out.update(accepted=False, why="the reading could not be measured "
                                       "on the candidate")
    elif closed < MIN_CLOSED:
        out.update(accepted=False, why=f"{obs['feature']} {before:g} -> "
                                       f"{after:g} (the reference: "
                                       f"{ref_reading:g}): {of_gap:.0%} of "
                                       f"the gap closed{step_note}, under "
                                       f"{MIN_CLOSED:.0%}")
    else:
        out.update(accepted=True, why=f"{obs['feature']} {before:g} -> "
                                      f"{after:g} (the reference: "
                                      f"{ref_reading:g}): {of_gap:.0%} of "
                                      f"the gap closed{step_note}, every "
                                      f"guard kept")
    return out


def _complete_goal(goal: Dict[str, float],
                   voices: List[dict]) -> Optional[str]:
    """An instrument goal made whole from the reference voices' readings.
    The rate goes with a vibrato's depth, so the transfer carries the
    swing, not just the number that was missing. The delay does not: it
    is the length of the reference's own held notes (the Gunstar lead's
    800 ms is longer than a beat of the étude). Which notes swing is what
    carries over (the share), and the design fits the delay to this
    part's own note lengths from it (instrument._delay_for_share). ->
    why a delay was dropped, or None."""
    for theirs in voices:
        if "vibrato_rate_hz" not in goal and \
                isinstance(theirs.get("vibrato_rate_hz"), (int, float)) and \
                "vibrato_depth_cents" in goal:
            goal["vibrato_rate_hz"] = theirs["vibrato_rate_hz"]
    if "vibrato_share" in goal and "vibrato_delay_ms" in goal:
        return (f"the reference's vibrato delay "
                f"({goal.pop('vibrato_delay_ms'):g} ms) is not a goal: it "
                f"is the length of its own notes; the share of notes that "
                f"swing is, and the delay is fitted to this part's notes "
                f"from it")
    return None


# -- one round --------------------------------------------------------------------------------
def improve_toward(project, spec: str, references: List[str],
                   bars: Optional[int] = None, dims=None, ear=None,
                   sketch_ids=(), voice: Optional[str] = None,
                   folder: Optional[str] = None,
                   cache: Optional[R.Cache] = None,
                   max_gaps: int = 3, instrument_budget: int = 6) -> dict:
    """One guided round on the range `spec` toward the references (ids).
    -> the report."""
    started = time.time()
    cache = cache or R.Cache(project.path("renders"))
    ear = ear or EAR.ToolEar(cache)
    tool = EAR.measurer(ear, cache)
    folder = folder or project.path("out", "guided",
                                    time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(folder, exist_ok=True)
    base_rev = project.head
    base_tl = T.build(state_content(project.state))
    rng = base_tl.range(spec)
    n_bars = bars or (rng["bars"][1] - rng["bars"][0] + 1)
    fragments = [LI.of_project(project, spec, stems=True, cache=cache)]
    for rid in references:
        ref = REF.get(project, rid)
        reg = ref["regions"][0]
        fragments.append(LI.of_reference(project, rid, reg["bars"][0],
                                         n_bars))
    for sid in sketch_ids:
        fragments.append(LI.of_sketch(project, sid, spec, cache=cache))
    heard = LI.compare(project, fragments, os.path.join(folder, "listen"),
                       "compare", ear=ear, voice=voice, dims=dims)
    ours_f = fragments[0]
    ours_f["_measured"] = LI.measure(project, ours_f)
    voice = heard["voice"]
    report = {"range": spec, "bars": rng["bars"], "base_rev": base_rev,
              "references": references, "sketches": list(sketch_ids),
              "listening": heard["listening"],
              "model_listening": heard["model_listening"],
              "listen_index": heard["index"], "reel": heard["reel"],
              "observations": heard["observations"],
              "not_compared": heard.get("not_compared", []),
              "worked_on": [],
              "statement": EAR.statement(ear)}
    base_h = tool.hear(base_tl, rng)
    measured = [o for o in heard["observations"]
                if o["basis"] == "measured" and o["against_kind"] ==
                "reference" and o["feature"] in PRINCIPLES]
    # the largest gaps first, one per reading
    measured.sort(key=lambda o: -abs(o.get("gap") or 0.0) /
                  max(abs(o.get("ours") or 0.0), 1.0))
    seen, chosen = set(), []
    instrument_goal: Dict[str, float] = {}
    instrument_obs = []
    kinds_taken = set()
    for o in measured:
        if o["feature"] in seen:
            continue
        seen.add(o["feature"])
        kind = PRINCIPLES[o["feature"]][1]
        if kind == "instrument":
            # every instrument gap goes to one design: one sound goal
            instrument_goal[o["feature"].split(".", 1)[1]] = o["theirs"]
            instrument_obs.append(o)
        elif kind not in kinds_taken:
            # one gap of each kind: two density gaps would try the same
            # changes twice, while a gap of another kind tries others
            kinds_taken.add(kind)
            chosen.append(o)
    goal_note = _complete_goal(instrument_goal, [
        (heard["readings"].get(o["against"]) or {}).get("voice") or {}
        for o in instrument_obs])
    for o in chosen[:max_gaps]:
        dim, kind = PRINCIPLES[o["feature"]]
        ref_f = next(f for f in fragments if f["label"] == o["against"])
        trace = trace_gap(base_tl, rng, o)
        hyps = hypotheses(base_tl, rng, o, trace)
        work = {"observation": o["id"], "dimension": dim,
                "statement": o["statement"], "basis": o["basis"],
                "against": o["against"], "trace": trace,
                "hypotheses": [], "outcome": None,
                "measured_on": project.head}
        if project.head != base_rev:
            # the observation was made on the original; the changes are
            # measured on the passage as the round has left it so far
            work["note"] = (f"before is the passage as the earlier changes "
                            f"of this round left it (revision "
                            f"{project.head}), not the original the "
                            f"observation was made on")
        if not hyps:
            work.update(outcome="no_hypothesis", why="no change this layer "
                        "knows carries that principle over here")
            report["worked_on"].append(work)
            continue
        tried = []
        for h in hyps:
            t = _test(project, base_tl, rng, h, o, ours_f, ref_f,
                      o["theirs"], cache, tool, base_h, folder, ear)
            tried.append(t)
        best = None
        for t in tried:
            if t.get("accepted") and (best is None or
                                      t["closed"] > best["closed"]):
                best = t
        verdict = None
        if best is not None:
            verdict = L._listen_ab(ear, base_tl, best["_timeline"], spec,
                                   cache, {"voices": [], "kind": dim,
                                           "statement": o["statement"]})
            if EAR.vetoes(verdict):
                best["accepted"] = False
                best["why"] += f"; but {verdict['model']} preferred the " \
                               f"original"
                best = None
        for t in tried:
            if t is not best and t.get("_content") is not None:
                t["sketch"] = SK.save(
                    project, f"{o['id']}: {t['id']}", content=t["_content"],
                    status="rejected", reason=t["why"], chain=t["chain"],
                    base_rev=project.head, section=spec,
                    measured={"reading": o["feature"],
                              "before": t.get("before"),
                              "after": t.get("after"),
                              "reference": t.get("reference")},
                    why=t["principle"])
        if best is not None:
            parent = project.head
            rev = project.commit(best["_content"], {
                "kind": "guided", "observation": o["id"],
                "chain": best["chain"], "principle": best["principle"],
                "reference": o["against"]})
            did = project.add_decision({
                "kind": "guided", "observation": None,
                "voices": sorted({c.get("voice") for c in best["chain"]
                                  if c.get("voice")}),
                "range": {"spec": spec}, "outcome": "committed",
                "chain": best["chain"], "before": best["before"],
                "after": best["after"], "reference": best["reference"],
                "base_rev": parent,
                "committed_rev": rev, "rounds": [],
                "stopped": "hypotheses tried", "why": best["why"],
                "listening_verdict": verdict,
                "judged_by": "measured" if not (verdict or {}).get(
                    "available") else "measured, then heard by model"})
            work.update(outcome="committed", committed_rev=rev,
                        decision=did, chosen=best["id"], why=best["why"])
            base_tl = best["_timeline"]
            base_h = tool.hear(base_tl, base_tl.range(spec))
            ours_f = LI.of_timeline(base_tl, spec, "ours (after)", "ours",
                                    f"r{rev}", cache=cache)
            ours_f["_measured"] = LI.measure(project, ours_f)
        else:
            work.update(outcome="rolled_back",
                        why="no change closed a third of the gap with "
                            "every guard kept; the passage stays as it was")
        work["listening_verdict"] = verdict
        work["hypotheses"] = [{k: v for k, v in t.items()
                               if not k.startswith("_")} for t in tried]
        report["worked_on"].append(work)
    if instrument_goal and voice:
        rec = INS.design(project, voice, instrument_goal, spec, cache=cache,
                         ear=ear, max_candidates=instrument_budget,
                         why="from the references: " + "; ".join(
                             o["statement"] for o in instrument_obs))
        report["worked_on"].append({
            "observation": ",".join(o["id"] for o in instrument_obs),
            "dimension": "instrument", "statement": "; ".join(
                o["statement"] for o in instrument_obs),
            "basis": "measured", "goal": instrument_goal,
            "goal_note": goal_note,
            "outcome": rec["outcome"], "why": rec["why"],
            "committed_rev": rec.get("committed_rev"),
            "chosen": rec.get("chosen"),
            "instrument": {k: rec.get(k) for k in
                           ("patch_before", "patch_after", "chosen",
                            "before", "cost", "decision")},
            "hypotheses": rec["candidates"]})
    report["head"] = project.head
    report["cost"] = {"runtime_s": round(time.time() - started, 2),
                      "renders": cache.misses}
    project.save()
    return report


# -- continuing: the previous section, the join, the new one, the sketches ------------------
def hear_continuation(project, section_id: str, ear=None, sketch_ids=None,
                      folder: Optional[str] = None,
                      cache: Optional[R.Cache] = None) -> dict:
    """After a section is written: the previous section, the join (the
    last bar before and the first bar of the new one) and the new section,
    with the chosen sketches' versions of it, side by side. -> how the new
    section moves on from the previous one, what happens at the join, how
    it differs from each sketch, and whether anything listened."""
    cache = cache or R.Cache(project.path("renders"))
    ear = ear or EAR.ToolEar(cache)
    tl = T.build(state_content(project.state))
    slots = tl.slots()
    k = next((i for i, s in enumerate(slots)
              if s["section"] == section_id), None)
    if k is None:
        raise AgenticError("no_section", f"{section_id!r} does not play")
    new = slots[k]
    spec = f"rows {new['row0']}-{new['row1'] - 1}"
    fragments = [LI.of_timeline(tl, spec, f"new section {section_id}",
                                "ours", f"r{project.head}", cache=cache)]
    out = {"section": section_id, "range": spec}
    if k > 0:
        prev = slots[k - 1]
        fragments.append(LI.of_timeline(
            tl, f"rows {prev['row0']}-{prev['row1'] - 1}",
            f"previous section {prev['section']}", "previous",
            prev["section"], cache=cache))
        rpb = tl.rows_per_bar
        a = max(prev["row0"], new["row0"] - rpb)
        b = min(new["row1"], new["row0"] + rpb)
        before = LI.of_timeline(tl, f"rows {a}-{new['row0'] - 1}",
                                "the bar before", "join", "before",
                                cache=cache)
        after = LI.of_timeline(tl, f"rows {new['row0']}-{b - 1}",
                               "the bar after", "join", "after", cache=cache)
        mb = F.fragment(before["samples"], R.RATE, before["beat_s"])
        ma = F.fragment(after["samples"], R.RATE, after["beat_s"])
        from . import compose as C
        out["join"] = {
            "loudness_change_lufs": round(ma["loudness"]["lufs"]
                                          - mb["loudness"]["lufs"], 2),
            "onsets_per_beat": [mb.get("onsets_per_beat"),
                                ma.get("onsets_per_beat")],
            "centroid_hz": [mb["balance"].get("centroid_hz"),
                            ma["balance"].get("centroid_hz")],
            "score": C.transition(tl.content, prev["section"], section_id),
            "method": "the bar on each side of the join measured alone "
                      "(features.py); the score's transition check"}
        fragments.append(dict(before, label="the join: the bar before"))
        fragments.append(dict(after, label="the join: the bar after"))
    out["sketches"] = []
    for sid in sketch_ids or []:
        s = SK.get(project, sid)
        drafted = next((x for x in SK.content_of(project, sid)["sections"]
                        if x["id"] == section_id), None)
        if drafted is None:
            out["sketches"].append({"id": sid, "compared": False,
                                    "why": f"it has no {section_id}"})
            continue
        # the sketch's rows for this section, played by the piece as it
        # is now (its instruments, its other sections): the comparison is
        # of the composition, not of what changed elsewhere since
        c = state_content(project.state)
        if len(drafted["rows"]) != len(next(
                x for x in c["sections"] if x["id"] == section_id)["rows"]):
            out["sketches"].append({"id": sid, "compared": False,
                                    "why": f"its {section_id} is not as "
                                           f"long as the piece's"})
            continue
        c["sections"] = [dict(drafted) if x["id"] == section_id else x
                         for x in c["sections"]]
        try:
            stl = T.build(c)
        except AgenticError as error:
            out["sketches"].append({"id": sid, "compared": False,
                                    "why": f"it does not build with the "
                                           f"piece as it is now: {error}"})
            continue
        fragments.append(LI.of_timeline(
            stl, spec, f"sketch {sid} ({s['label']}), its {section_id} played "
            f"with the piece's instruments now", "sketch", sid,
            cache=cache))
        out["sketches"].append({"id": sid, "label": s["label"],
                                "compared": True})
    folder = folder or project.path("out", "continue", section_id)
    rep = LI.compare(project, [f for f in fragments if f["kind"] != "join"]
                     if len(fragments) > 1 else fragments, folder,
                     f"continue_{section_id}", ear=ear)
    joins = []
    j = out.get("join") or {}
    if abs(j.get("loudness_change_lufs") or 0.0) >= 3.0:
        joins.append(f"the join changes loudness by "
                     f"{j['loudness_change_lufs']:+.1f} LU from the bar "
                     f"before to the bar after")
    cs = j.get("centroid_hz") or [None, None]
    if cs[0] and cs[1] and abs(cs[1] - cs[0]) / cs[0] >= 0.25:
        joins.append(f"the join changes the spectral centroid "
                     f"{cs[0]:.0f} -> {cs[1]:.0f} Hz")
    for issue in (j.get("score") or {}).get("issues", []):
        joins.append(f"the score at the join: {issue}")
    join_obs = [{"id": f"J{k + 1}", "basis": "measured",
                 "dimension": "development", "feature": "join",
                 "against": "the bar before", "against_kind": "join",
                 "statement": text, "confidence": 0.7}
                for k, text in enumerate(joins)]
    out.update(observations=join_obs + rep["observations"],
               reel=rep["reel"], index=rep["index"],
               listening=rep["listening"],
               model_listening=rep["model_listening"],
               readings=rep["readings"])
    return out
