"""revise.py — a composition changed because of what the hearing plugins
measured in its render, and measured again after.

    render     the mix and each accompanying voice and the lead alone,
               played from the top (Chipgen knows its sources: a voice is
               rendered alone instead of separated from the mix)
    hear       Essentia on those files (rhythm, timbre, tonality, and the
               sections cut where the score's own sections start)
    find       a defect, from the detectors below, with the readings it
               rests on and what the score says beside them
    correct    each correction the detector names, in turn, applied to
               the musical state with the existing patch ops
    re-hear    the same analyses on the new render; the target reading
               checked, the side readings and Chipgen's own guards
               checked
    keep       the first correction that meets its target and breaks no
               guard is committed as a revision with the evidence; if
               none does, nothing is committed and each one's readings
               are reported

The detectors are deliberately narrow — each names the reading that
shows the defect and the reading that would show it gone:

    monotonous_rhythm   an accompanying voice (bass first) whose onsets
                        are one interval throughout (Essentia's onsets on
                        the voice's own render: one inter-onset interval
                        takes 80% or more, under 0.6 bits of entropy, no
                        onset on a sixteenth between the eighths).
                        Corrections: the rhythm op's anticipation over the
                        piece; where every note keys off, its syncopation
                        (the same push onto a key-off row) over the piece,
                        then over its second half. Target: entropy
                        of the intervals up by 0.5 bits.
    flat_sections       a section the plan calls a development, contrast,
                        bridge or breakdown that measures as alike as
                        VARIED (0.7) or more to the statement (Essentia's
                        section similarity, cut at the score's
                        boundaries). Corrections: the accompaniment thinned
                        in that section, voice by voice, then together.
                        Target: its similarity to the statement down by
                        0.05 at least.
    narrow_lead         the lead's predominant pitch spans under 7
                        semitones (5th-95th percentile) over 8 bars or
                        more. Reported with a proposal only: the melody is
                        the composer's to change, not a measurement's.

Nothing is called an improvement because the score changed: the report says what changed, what was measured, and
what is still uncertain.
"""

import os
import time
from typing import List, Optional

from .. import ear as EAR
from .. import loop as L
from .. import render as R
from .. import timeline as T
from ..state import AgenticError
from . import (analyze_rhythm, analyze_sections, analyze_timbre,
               analyze_tonality, check_target, evaluate_revision, read,
               status)

VARIED = 0.7
CONTRASTING = ("development", "contrast", "bridge", "breakdown")
#: how far the mix may move in the side readings before a correction is
#: refused: loudness (LU) and the tempo estimate (share)
SIDE = {"loudness_lu": 2.0, "tempo_share": 0.03}


def render_wav(tl, path: str, cache, voices=None, spec: str = "all") -> dict:
    """The range played from the top, written as a stereo WAV."""
    rng = tl.range(spec)
    r = R.render_range(tl, rng, voices=voices, cache=cache, method="full")
    data = r.segment(rng["t0"], rng["t1"], mono=False)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(EAR.wav_bytes(data, R.RATE, stereo=True))
    return {"file": path, "voices": list(voices) if voices else "mix",
            "range": spec, "seconds": round(rng["t1"] - rng["t0"], 3)}


def _roles(tl) -> dict:
    return {v: (i.get("role") or "") for v, i in
            tl.content["instruments"].items()}


def _score_side(tl, voice: str) -> dict:
    """What the score says the voice plays: its onsets, the gaps between
    them in rows, its register."""
    notes = sorted((n for n in tl.notes if n["voice"] == voice and
                    n.get("pitch") is not None), key=lambda n: n["start"])
    gaps = sorted({b["row"] - a["row"] for a, b in zip(notes, notes[1:])
                   if b["row"] > a["row"]})
    pitches = [n["pitch"] for n in notes]
    return {"notes": len(notes), "row_gaps_between_onsets": gaps[:12],
            "lowest": min(pitches) if pitches else None,
            "highest": max(pitches) if pitches else None}


def _boundaries(tl) -> List[float]:
    t0 = tl.range("all")["t0"]
    return [round(s["t0"] - t0, 3) for s in tl.slots()[1:]]


def hear(tl, folder: str, cache) -> dict:
    """Render and analyse: the mix, each accompanying voice, the lead."""
    roles = _roles(tl)
    files = {"mix": render_wav(tl, os.path.join(folder, "mix.wav"), cache)}
    for v, role in roles.items():
        if role in ("bass", "pad", "chords", "arp", "drums", "lead",
                    "counter"):
            files[v] = render_wav(tl, os.path.join(folder, f"{v}.wav"),
                                  cache, voices=[v])
    mix = files["mix"]["file"]
    bpm = float(tl.content["structure"]["bpm"])
    obs = {"mix": {"rhythm": analyze_rhythm(mix, bpm_hint=bpm),
                   "timbre": analyze_timbre(mix),
                   "tonality": analyze_tonality(mix)}}
    bounds = _boundaries(tl)
    if bounds:
        obs["mix"]["sections"] = analyze_sections(mix, boundaries=bounds)
    for v, role in roles.items():
        if v not in files:
            continue
        f = files[v]["file"]
        obs[v] = {"role": role, "rhythm": analyze_rhythm(f, bpm_hint=bpm)}
        if role == "lead":
            obs[v]["tonality"] = analyze_tonality(f)
    return {"files": files, "observations": obs, "boundaries": bounds}


# -- detectors -------------------------------------------------------------------------------
def _monotonous(tl, heard) -> List[dict]:
    out = []
    order = sorted(heard["observations"].items(),
                   key=lambda kv: kv[1].get("role") != "bass")
    for v, o in order:
        if v == "mix" or o.get("role") in ("lead", "drums") or \
                "rhythm" not in o:
            continue
        r = o["rhythm"]
        if r.get("status") != "ok":
            continue
        dom = read(r, "measured.ioi_in_beats.dominant_share")
        ent = read(r, "measured.ioi_in_beats.entropy_bits")
        syn = read(r, "estimated.syncopation_share") or 0.0
        n = read(r, "measured.onsets") or 0
        if dom is None or n < 8:
            continue
        if dom >= 0.8 and ent <= 0.6 and syn < 0.05:
            hist = read(r, "measured.ioi_in_beats.histogram") or []
            out.append({
                "defect": "monotonous_rhythm", "voice": v,
                "evidence": [
                    f"{v} alone: {n} onsets; one interval "
                    f"({hist[0]['beats'] if hist else '?'} beat) takes "
                    f"{dom:.0%} of them",
                    f"interval entropy {ent:.2f} bits",
                    f"syncopated onsets (2nd/4th sixteenth) "
                    f"{syn:.0%}"],
                "basis": "measured: Essentia onsets on the voice's own "
                         "render; the sixteenth positions rest on the "
                         "estimated beats",
                "score_side": _score_side(tl, v),
                "target": {"analysis": "rhythm", "file": v,
                           "path": "measured.ioi_in_beats.entropy_bits",
                           "direction": "up", "by": 0.5},
                "corrections": [
                    [{"op": "rhythm", "voice": v, "range": "all",
                      "mode": "anticipate"}],
                    [{"op": "rhythm", "voice": v, "range": "all",
                      "mode": "syncopate"}],
                    [{"op": "rhythm", "voice": v,
                      "range": _second_half(tl), "mode": "syncopate"}]]})
    return out


def _second_half(tl) -> str:
    bars = tl.range("all")["bars"]
    last = bars[1] if isinstance(bars, list) else int(bars)
    return f"bars {last // 2 + 1}-{last}"


def _flat_sections(tl, heard) -> List[dict]:
    sec = heard["observations"]["mix"].get("sections")
    if not sec or sec.get("status") != "ok":
        return []
    sim = read(sec, "measured.similarity") or []
    slots = tl.slots()
    plan = {p["id"]: p for p in tl.content["structure"].get("plan") or []}
    fn = [(plan.get(s["section"]) or {}).get("function") for s in slots]
    statement = next((i for i, f in enumerate(fn) if f == "statement"), 0)
    accomp = [v for v, r in _roles(tl).items()
              if r in ("bass", "pad", "chords", "arp")]
    out = []
    for i, f in enumerate(fn):
        if f not in CONTRASTING or i >= len(sim) or i == statement:
            continue
        s = sim[i][statement]
        if s < VARIED:
            continue
        spec = f"slot {i}"
        parts = (read(sec, f"measured.sections.{i}") or {})
        corrections = [[{"op": "density", "voice": v, "range": spec,
                         "mode": "thin"}] for v in accomp]
        if len(accomp) > 1:
            corrections.append([{"op": "density", "voice": v,
                                 "range": spec, "mode": "thin"}
                                for v in accomp])
        out.append({
            "defect": "flat_sections", "section": slots[i]["section"],
            "slot": i, "function": f,
            "evidence": [f"slot {i} ({slots[i]['section']}, planned as "
                         f"{f}) measures {s:.2f} alike to the statement "
                         f"(slot {statement}); {VARIED} or more reads as a "
                         f"variation, not a contrast",
                         f"its density {parts.get('onsets_per_s')} "
                         f"onsets/s, level {parts.get('rms_db')} dB, "
                         f"centroid {parts.get('centroid_hz')} Hz"],
            "basis": "measured: Essentia section similarity (timbre, "
                     "chroma, density, level) cut at the score's section "
                     "starts",
            "target": {"analysis": "sections", "file": "mix",
                       "path": f"measured.similarity.{i}.{statement}",
                       "direction": "down", "by": 0.05},
            "corrections": corrections})
    return out


def _narrow_lead(tl, heard) -> List[dict]:
    out = []
    bars = tl.range("all")["bars"]
    nbars = bars[1] if isinstance(bars, list) else int(bars)
    for v, o in heard["observations"].items():
        if o.get("role") != "lead" or "tonality" not in o:
            continue
        rng = read(o["tonality"],
                   "estimated.predominant_pitch.range_semitones_p5_p95")
        if rng is not None and rng < 7 and nbars >= 8:
            out.append({"defect": "narrow_lead", "voice": v,
                        "evidence": [f"{v}'s predominant pitch spans "
                                     f"{rng} semitones over {nbars} bars"],
                        "basis": "estimate: PredominantPitchMelodia on the "
                                 "lead's own render",
                        "score_side": _score_side(tl, v),
                        "proposal": "move one phrase of the lead an octave "
                                    "or widen its contour; not applied — "
                                    "the melody is the composer's",
                        "corrections": []})
    return out


DETECTORS = [_monotonous, _flat_sections, _narrow_lead]


def find(tl, heard) -> List[dict]:
    out = []
    for det in DETECTORS:
        out += det(tl, heard)
    return out


# -- the round -------------------------------------------------------------------------------
def _compact(o: dict) -> dict:
    """A hearing result cut to what a report needs."""
    keep = {}
    for name, res in o.items():
        if not isinstance(res, dict) or "observations" not in res:
            continue
        if res.get("status") != "ok":
            keep[name] = {"status": res.get("status"),
                          "why": res.get("why")}
            continue
        if name == "rhythm":
            keep[name] = {k: read(res, k) for k in (
                "estimated.tempo.bpm", "measured.onsets_per_beat",
                "measured.ioi_in_beats.entropy_bits",
                "measured.ioi_in_beats.dominant_share",
                "estimated.syncopation_share",
                "measured.bars.distinct_patterns")}
        elif name == "timbre":
            keep[name] = {k: read(res, k) for k in (
                "measured.loudness_lufs", "measured.centroid_hz.mean",
                "measured.attack_ms.median")}
        elif name == "tonality":
            keep[name] = {k: read(res, k) for k in (
                "estimated.key.most_named", "estimated.key.profiles_agree",
                "estimated.predominant_pitch.range_semitones_p5_p95")}
        elif name == "sections":
            keep[name] = {"letters": read(res, "estimated.form_letters"),
                          "similarity": read(res, "measured.similarity")}
    return keep


def _tempo_relation(est: float, score_bpm: float) -> Optional[str]:
    """How an estimated tempo stands to the score's: "same", "half",
    "double" (the beat tracker's octave errors) or None (neither)."""
    for name, f in (("same", 1.0), ("half", 0.5), ("double", 2.0)):
        if abs(est - score_bpm * f) <= SIDE["tempo_share"] * score_bpm * f:
            return name
    return None


def _side(before: dict, after: dict, score_bpm: float) -> List[dict]:
    out = []
    la = read(before["timbre"], "measured.loudness_lufs")
    lb = read(after["timbre"], "measured.loudness_lufs")
    if la is not None and lb is not None:
        out.append({"reading": "mix loudness (LUFS)", "before": la,
                    "after": lb, "limit": SIDE["loudness_lu"],
                    "ok": abs(lb - la) <= SIDE["loudness_lu"]})
    ta = read(before["rhythm"], "estimated.tempo.bpm")
    tb = read(after["rhythm"], "estimated.tempo.bpm")
    if ta and tb:
        ra, rb = _tempo_relation(ta, score_bpm), _tempo_relation(tb,
                                                                 score_bpm)
        row = {"reading": "tempo estimate (BPM) against the score's "
                          f"{score_bpm:g}", "before": ta, "after": tb,
               "before_is": ra, "after_is": rb,
               "limit": f"{SIDE['tempo_share']:.0%} of the score's tempo, "
                        f"its half or its double",
               "ok": rb is not None}
        if rb and rb != ra:
            row["note"] = (f"the estimate moved to the score's {rb} "
                           f"tempo: the beat tracker reads the pulse "
                           f"differently, the score's tempo is unchanged")
        ca = read(before["rhythm"], "estimated.tempo.beat_confidence")
        cb = read(after["rhythm"], "estimated.tempo.beat_confidence")
        row["beat_confidence"] = [ca, cb]
        out.append(row)
    return out


def revise(project, cache, defect: Optional[str] = None,
           commit: bool = True) -> dict:
    """One hearing-driven round on the project's head. -> the report."""
    started = time.time()
    from ..state import content as state_content
    content = state_content(project.state)
    tl = T.build(content)
    base_rev = project.head
    folder = project.path("hearing", f"r{base_rev}")
    plugins = status()
    if not (plugins.get("essentia") or {}).get("available"):
        return {"outcome": "unavailable",
                "why": "Essentia is not available in the hearing "
                       "interpreter: " + str(plugins.get("essentia") or
                                             plugins.get("why")),
                "plugins": plugins}
    heard = hear(tl, folder, cache)
    findings = find(tl, heard)
    report = {"base_rev": base_rev, "renders": heard["files"],
              "observations": {k: _compact(v) for k, v in
                               heard["observations"].items()},
              "findings": findings,
              "plugins": {"essentia": plugins.get("essentia")}}
    if defect:
        findings = [f for f in findings if f["defect"] == defect]
    actionable = [f for f in findings if f["corrections"]]
    if not actionable:
        report["outcome"] = "no correctable defect found" if not findings \
            else "found, nothing to apply"
        report["seconds"] = round(time.time() - started, 1)
        return report
    finding = actionable[0]
    report["working_on"] = finding
    target = finding["target"]
    attempts = []
    chosen = None
    ear = EAR.ToolEar(cache)
    rng = tl.range("all")
    bpm = float(content["structure"]["bpm"])
    h_base = ear.hear(tl, rng)
    for k, chain in enumerate(finding["corrections"]):
        att = {"chain": chain}
        attempts.append(att)
        try:
            new_content, deltas, new_tl = L._apply_chain(content, chain, tl)
        except AgenticError as error:
            att.update(applied=False, why=f"{error.code}: {error}")
            continue
        att["applied"] = True
        att["score_changes"] = [L._compact_delta(d) for d in deltas]
        if finding.get("voice"):
            att["score_side"] = {"before": _score_side(tl, finding["voice"]),
                                 "after": _score_side(new_tl,
                                                      finding["voice"])}
        cand = os.path.join(folder, f"candidate{k + 1}")
        which = target["file"]
        voices = None if which == "mix" else [which]
        before_file = heard["files"][which]["file"]
        after = render_wav(new_tl, os.path.join(cand, f"{which}.wav"),
                           cache, voices=voices)
        after_mix = render_wav(new_tl, os.path.join(cand, "mix.wav"), cache)
        att["render"] = {"target": after["file"], "mix": after_mix["file"]}
        if target["analysis"] == "sections":
            bounds = heard["boundaries"]
            res = {"sections": (heard["observations"]["mix"]["sections"],
                                analyze_sections(after["file"],
                                                 boundaries=bounds))}
            att["target"] = check_target(target, res)
            ev = evaluate_revision(heard["files"]["mix"]["file"],
                                   after_mix["file"],
                                   analyses=("rhythm", "timbre"),
                                   bpm_hint=bpm)
        else:
            ev = evaluate_revision(before_file, after["file"], target,
                                   analyses=("rhythm",), bpm_hint=bpm)
            att["target"] = ev.get("target")
            att["target_deltas"] = ev["observations"]["deltas"]
            ev = evaluate_revision(heard["files"]["mix"]["file"],
                                   after_mix["file"],
                                   analyses=("rhythm", "timbre"),
                                   bpm_hint=bpm)
        att["mix_deltas"] = ev["observations"]["deltas"]
        mix_after = {"rhythm": analyze_rhythm(after_mix["file"],
                                              bpm_hint=bpm),
                     "timbre": analyze_timbre(after_mix["file"])}
        att["side_readings"] = _side(heard["observations"]["mix"],
                                     mix_after,
                                     float(content["structure"]["bpm"]))
        h_new = ear.hear(new_tl, new_tl.range("all"))
        g = L.guards(tl, new_tl, h_base, h_new, rng,
                     {"voices": [finding.get("voice")] if
                      finding.get("voice") else []}, chain=chain)
        att["guards"] = [{"name": x["name"], "ok": x["ok"],
                          "detail": x.get("detail")} for x in g]
        ok = bool(att["target"].get("met")) and \
            all(s["ok"] for s in att["side_readings"]) and \
            all(x["ok"] for x in g)
        att["accepted"] = ok
        if ok:
            chosen = (att, new_content)
            break
    report["attempts"] = attempts
    if chosen is None:
        report["outcome"] = "rolled back: no correction met its target " \
                            "without breaking a guard"
    elif not commit:
        report["outcome"] = "would commit (commit=False)"
    else:
        att, new_content = chosen
        why = (f"hearing: {finding['defect']} — " + "; ".join(
            finding["evidence"]) + f"; {target['path']} "
            f"{att['target']['before']} -> {att['target']['after']}")
        rev = project.commit(new_content, {"kind": "patch", "by": "agent",
                                           "chain": att["chain"],
                                           "why": why[:400]})
        did = project.add_decision({
            "observation": None, "kind": f"hearing:{finding['defect']}",
            "voices": [finding["voice"]] if finding.get("voice") else [],
            "range": {"spec": "all"}, "outcome": "committed",
            "chain": att["chain"], "base_rev": base_rev,
            "committed_rev": rev, "rounds": [], "by": "agent", "why": why,
            "before": att["target"]["before"],
            "after": att["target"]["after"],
            "evidence": finding["evidence"]})
        project.save()
        report.update(outcome="committed", rev=rev, decision=did)
    report["uncertain"] = [
        "the sixteenth positions and bars rest on Essentia's estimated "
        "beats and an assumed four beats a bar",
        "a reading that moved is a measured change, not a listener's "
        "verdict; no listening model was asked here"]
    report["seconds"] = round(time.time() - started, 1)
    return report
