"""loop.py — one local correction, start to finish, with what it cost.

    observe      an observation of the ear on a range (ear.py)
    locate       the voice, its notes, source lines and register writes
                 (diagnose.locate)
    hypothesize  patches with the effect each is expected to have
                 (diagnose.diagnose), tried in that order
    patch        patch.apply on a copy of the content, never the project
    render       the range only, the chip's state chased in, through the
                 render cache: a voice the patch did not touch is not
                 rendered again
    compare      the candidate's measurements against the base's. The
                 margins are ratios, so a louder mix does not raise them;
                 the A/B audio is written loudness-matched, with the
                 original levels reported next to the gain applied
    evaluate     the target's gain and the guards: nothing clips that did
                 not, every note keeps its onset and pitch class, the
                 melody keeps its pitches, no other voice is newly buried
                 or unheard, an accompanying voice stays present, the bass
                 is not put above another voice where it was not, the mix
                 level moves less than 4 dB
    commit       the best candidate becomes the project's next revision;
                 if none improves, nothing is committed and the decision
                 says so (a rollback of the attempt)
    learn        memory.py turns the decision into a LearningRecord

Budget: 3 candidates x 2 rounds — the spec's starting point, not a tuned
optimum. A round tries the card's hypotheses in order, skipping any whose
content was already seen; a candidate that reaches the goal with every
guard passed ends the search (sufficient, and cheaper); otherwise the
round's best becomes the base of the next round, diagnosed afresh. It
stops when the goal is met, when a round improves nothing, or when the
budget is spent. Every candidate, kept or not, is in the decision.
"""

import os
import time
from typing import Dict, List, Optional

from . import diagnose as D
from . import ear as EAR
from . import patch as P
from . import render as R
from . import timeline as T
from .state import AgenticError, content_hash

#: An accompanying voice's in-band margin a fix may not push it below
#: (a heuristic for "still present", not a listening result).
ACCOMPANIMENT_FLOOR_DB = -9.0
#: The mix level a fix may move, either way (dB).
MIX_LEVEL_LIMIT_DB = 4.0
#: The least gain that counts as an improvement (dB).
MIN_GAIN_DB = 0.5


class Budget:
    def __init__(self, candidates: int = 3, rounds: int = 2,
                 max_renders: int = 80):
        if not (1 <= candidates <= 6 and 1 <= rounds <= 4):
            raise AgenticError("bad_budget", "budget: 1-6 candidates, 1-4 "
                               "rounds")
        self.candidates = candidates
        self.rounds = rounds
        self.max_renders = max_renders

    @classmethod
    def parse(cls, text: str) -> "Budget":
        try:
            c, r = (int(v) for v in str(text).lower().split("x"))
        except ValueError:
            raise AgenticError("bad_budget", f"budget is CANDIDATESxROUNDS, "
                               f"e.g. 3x2, not {text!r}") from None
        return cls(c, r)

    def to_dict(self) -> dict:
        return {"candidates": self.candidates, "rounds": self.rounds,
                "max_renders": self.max_renders}


# -- what is being improved ---------------------------------------------------
def metric_for(obs: dict):
    """(name, reading(hearing) -> float, higher is better, goal(value))."""
    kind = obs["kind"]
    if kind in ("buried", "too_quiet", "unheard_notes"):
        target = obs["voices"][0]

        def reading(h):
            return (h["measurements"]["voices"].get(target) or {}).get(
                "band_margin_db")
        return ("band_margin_db of " + target, reading,
                lambda v: v is not None and v >= EAR.BURIED_BODY_DB)
    if kind == "clipping":
        def peak(h):
            return -h["measurements"]["mix"]["peak_db"]
        return ("mix headroom (-peak dB)", peak,
                lambda v: v is not None and v > 0.18)
    raise AgenticError("no_metric", f"the loop corrects buried, "
                       f"unheard_notes and clipping observations, not "
                       f"{kind!r}", kind=kind)


# -- guards: what a fix may not do --------------------------------------------
def _onsets(tl, rng) -> Dict[str, list]:
    out = {}
    for n in tl.sounding(rng["t0"], rng["t1"]):
        if n["start"] < rng["t0"] - 1e-6:
            continue
        pc = n["pitch"] % 12 if n.get("pitch") is not None else n["name"]
        out.setdefault(n["voice"], []).append((n["row"], pc))
    return out


def _pitches(tl, rng, voice) -> list:
    return [(n["row"], n["pitch"]) for n in tl.sounding(rng["t0"], rng["t1"],
                                                        voice)
            if n["start"] >= rng["t0"] - 1e-6]


def _bass_above(tl, rng) -> int:
    """Rows where a bass-role voice sounds above another pitched voice."""
    basses = [v for v, ins in tl.content["instruments"].items()
              if ins.get("role") == "bass"]
    count = 0
    for r in range(rng["row0"], rng["row1"]):
        t = tl.rows[r]["t0"] + 1e-4
        sounding = [n for n in tl.notes if n["start"] <= t < n["end"]
                    and n.get("pitch") is not None]
        for b in basses:
            low = [n["pitch"] for n in sounding if n["voice"] == b]
            others = [n["pitch"] for n in sounding if n["voice"] != b]
            if low and others and low[0] > min(others):
                count += 1
    return count


def _faults(hearing) -> set:
    return {(o["kind"], v) for o in hearing["observations"]
            for v in (o["voices"] or ["mix"])}


def guards(base_tl, cand_tl, base_h, cand_h, rng, obs) -> List[dict]:
    target = obs["voices"][0] if obs.get("voices") else None
    out = []
    bp = base_h["measurements"]["mix"]["peak_db"]
    cp = cand_h["measurements"]["mix"]["peak_db"]
    clipped = cand_h["measurements"]["mix"]["limiter"] or cp >= -0.17
    out.append({"name": "no_clipping",
                "ok": not clipped or cp <= bp,
                "detail": f"mix peak {bp:.2f} -> {cp:.2f} dBFS"})
    a, b = _onsets(base_tl, rng), _onsets(cand_tl, rng)
    out.append({"name": "notes_kept", "ok": a == b,
                "detail": "every note keeps its onset row and pitch class; "
                          "none added or removed" if a == b else
                "a note's onset, pitch class or count changed"})
    melodic = [v for v, ins in base_tl.content["instruments"].items()
               if ins.get("role") in D.MELODIC_ROLES]
    same = all(_pitches(base_tl, rng, v) == _pitches(cand_tl, rng, v)
               for v in melodic)
    names = ", ".join(melodic) or "no melodic voice"
    out.append({"name": "melody_kept", "ok": same,
                "detail": f"pitches of {names} unchanged" if same else
                "a melodic voice's pitch changed"})
    new = _faults(cand_h) - _faults(base_h)
    new = {f for f in new if not (f[1] == target
                                  and f[0] in ("buried", "unheard_notes"))}
    out.append({"name": "no_new_faults", "ok": not new,
                "detail": "none" if not new else
                ", ".join(f"{k} on {v}" for k, v in sorted(new))})
    weak = []
    for v, m in cand_h["measurements"]["voices"].items():
        if v == target:
            continue
        role = base_tl.content["instruments"].get(v, {}).get("role", "")
        before = (base_h["measurements"]["voices"].get(v) or {}).get(
            "band_margin_db")
        after = m.get("band_margin_db")
        if before is None or after is None:
            continue
        floor = ACCOMPANIMENT_FLOOR_DB if before >= ACCOMPANIMENT_FLOOR_DB \
            else before - 1.0
        if after < floor:
            weak.append(f"{v} ({role}) {before:+.1f} -> {after:+.1f} dB")
    out.append({"name": "voices_present", "ok": not weak,
                "detail": "every other voice stays within its band floor "
                          f"({ACCOMPANIMENT_FLOOR_DB:g} dB)" if not weak
                else "; ".join(weak)})
    above0, above1 = _bass_above(base_tl, rng), _bass_above(cand_tl, rng)
    out.append({"name": "bass_under", "ok": above1 <= above0,
                "detail": f"rows with the bass above another voice: "
                          f"{above0} -> {above1}"})
    d = cand_h["measurements"]["mix"]["rms_db"] - \
        base_h["measurements"]["mix"]["rms_db"]
    out.append({"name": "mix_level", "ok": abs(d) <= MIX_LEVEL_LIMIT_DB,
                "detail": f"mix RMS {d:+.2f} dB"})
    return out


# -- one candidate --------------------------------------------------------------
def _apply_chain(content, chain, timeline):
    deltas = []
    current = content
    tl = timeline
    for spec in chain:
        current, delta = P.apply(current, spec, tl)
        deltas.append(delta)
        tl = T.build(current)
    return current, deltas, tl


def _compact_delta(delta: dict) -> dict:
    keep = {k: v for k, v in delta.items() if k not in ("edits", "units")}
    edits = delta.get("edits") or []
    if edits:
        keep["edits"] = edits[:4]
    return keep


class Search:
    """The candidates of one observation's correction, measured."""

    def __init__(self, timeline, obs: dict, hearing: dict,
                 cache: Optional[R.Cache] = None,
                 budget: Optional[Budget] = None, remembered=None,
                 ear=None):
        self.base_tl = timeline
        self.obs = obs
        self.base_h = hearing
        self.cache = cache or R.Cache()
        # candidates are measured: the metric and the guards are numbers,
        # whichever ear made the observation (a listening model hears the
        # chosen candidate against the original afterwards, in `improve`)
        self.ear = EAR.measurer(ear, self.cache) if ear is not None \
            else EAR.ToolEar(self.cache)
        self.budget = budget or Budget()
        self.remembered = remembered or []
        self.rng = timeline.range(D._spec(obs))
        self.metric_name, self.read, self.goal = metric_for(obs)
        self.seen = {content_hash(timeline.content)}
        self.operations = 0
        self.renders0 = self.cache.misses
        self.seconds0 = self.cache.render_seconds

    def _hear(self, tl):
        self.operations += 1
        return self.ear.hear(tl, tl.range(self.rng["spec"]))

    def candidate(self, base_content, base_chain, hyp: dict) -> dict:
        chain = base_chain + (hyp["patch"] if isinstance(hyp["patch"], list)
                              else [hyp["patch"]])
        out = {"hypothesis": hyp.get("id"), "kind": hyp.get("kind"),
               "patch": hyp["patch"], "chain": chain,
               "expected_db": hyp.get("expect_db"),
               "expected": hyp.get("hypothesis"), "basis": hyp.get("basis")}
        renders = self.cache.misses
        started = time.time()
        try:
            self.operations += 1
            content, deltas, tl = _apply_chain(self.base_tl.content, chain,
                                               self.base_tl)
        except AgenticError as error:
            out.update(accepted=False, why=f"patch refused: {error}",
                       error=error.to_dict())
            return out
        h = content_hash(content)
        if h in self.seen:
            out.update(accepted=False, why="this content was tried already "
                                           "(a repeated state)",
                       content_hash=h)
            return out
        self.seen.add(h)
        hearing = self._hear(tl)
        value = self.read(hearing)
        start = self.read(self.base_h)
        checks = guards(self.base_tl, tl, self.base_h, hearing, self.rng,
                        self.obs)
        failed = [g for g in checks if not g["ok"]]
        gain = None if value is None or start is None else \
            round(value - start, 2)
        out.update({
            "content_hash": h, "deltas": [_compact_delta(d) for d in deltas],
            "metric": {"name": self.metric_name, "before": start,
                       "after": value, "gain_db": gain},
            "goal_met": bool(self.goal(value)),
            "guards": checks,
            "mix_rms_db": hearing["measurements"]["mix"]["rms_db"],
            "cost": {"renders": self.cache.misses - renders,
                     "seconds": round(time.time() - started, 2)}})
        if failed:
            out.update(accepted=False,
                       why="guard failed: " + "; ".join(
                           f"{g['name']} ({g['detail']})" for g in failed))
        elif gain is None or gain < MIN_GAIN_DB:
            out.update(accepted=False,
                       why=f"gain {gain} dB is under {MIN_GAIN_DB} dB")
        else:
            out.update(accepted=True,
                       why=f"{self.metric_name} {start:+.2f} -> "
                           f"{value:+.2f} dB, every guard passed")
        out["_content"] = content
        out["_timeline"] = tl
        out["_hearing"] = hearing
        return out

    def run(self) -> dict:
        started = time.time()
        rounds = []
        base_content, base_chain = self.base_tl.content, []
        base_value = self.read(self.base_h)
        best = None
        current_h = self.base_h
        current_tl = self.base_tl
        tried = set()
        stop = None
        first_card = None
        for k in range(self.budget.rounds):
            self.operations += 1
            card = D.diagnose(current_tl, self.obs, current_h,
                              remembered=self.remembered if k == 0 else None,
                              tried=tried, explain_writes=(k == 0))
            if first_card is None:
                first_card = card
            hyps = card.get("hypotheses", [])
            if not hyps:
                stop = "no hypothesis left"
                break
            tried_now = []
            round_best = None
            for hyp in hyps[:self.budget.candidates]:
                if self.cache.misses - self.renders0 >= self.budget.max_renders:
                    stop = "render budget spent"
                    break
                tried.add(D.signature(hyp["patch"]))
                c = self.candidate(base_content, base_chain, hyp)
                tried_now.append(c)
                if c["accepted"] and (round_best is None or
                                      c["metric"]["after"] >
                                      round_best["metric"]["after"]):
                    round_best = c
                if c["accepted"] and c["goal_met"]:
                    break
            rounds.append({"round": k + 1,
                           "from": [D.signature(s) for s in base_chain]
                           or ["base"],
                           "candidates": tried_now,
                           "best": round_best["hypothesis"] if round_best
                           else None})
            if round_best is None or (
                    best is not None and round_best["metric"]["after"]
                    <= best["metric"]["after"] + 1e-9):
                stop = stop or "no candidate improved"
                break
            best = round_best
            if best["goal_met"]:
                stop = "goal met"
                break
            # the next round starts from the best so far, heard afresh
            base_content = best["_content"]
            base_chain = best["chain"]
            current_tl = best["_timeline"]
            current_h = best["_hearing"]
            if stop:
                break
        else:
            stop = stop or "budget spent"
        return {"rounds": rounds, "best": best, "base_value": base_value,
                "stopped": stop or "budget spent", "card": first_card,
                "cost": {"renders": self.cache.misses - self.renders0,
                         "render_seconds": round(self.cache.render_seconds
                                                 - self.seconds0, 2),
                         "runtime_s": round(time.time() - started, 2),
                         "operations": self.operations,
                         "candidates": sum(len(r["candidates"])
                                           for r in rounds),
                         "retries": max(0, sum(len(r["candidates"])
                                               for r in rounds) - 1),
                         "model_tokens": 0, "audio_model_seconds": 0.0}}


# -- A/B audio ------------------------------------------------------------------
def write_ab(folder: str, name: str, tl_a, tl_b, spec: str,
             cache: Optional[R.Cache] = None, mp3: bool = True) -> dict:
    """The range from two versions as WAV (and MP3), loudness-matched: B is
    scaled to A's RMS, and both down together if that would clip. The
    original levels are reported next to the gain applied."""
    import audio
    import wavio
    cache = cache or R.Cache()
    out = {"range": spec, "files": {}}
    levels = {}
    data = {}
    for label, tl in (("A", tl_a), ("B", tl_b)):
        rng = tl.range(spec)
        r = R.render_range(tl, rng, cache=cache)
        stereo = r.segment(rng["t0"], rng["t1"], mono=False)
        rms = R.rms_db(stereo)
        peak = max((abs(v) for v in stereo), default=0.0)
        levels[label] = {"rms_db": round(rms, 2),
                         "peak_db": round(EAR._db(peak), 2)}
        data[label] = stereo
    gain_b = levels["A"]["rms_db"] - levels["B"]["rms_db"]
    peak_b = levels["B"]["peak_db"] + gain_b
    common = min(0.0, -0.3 - max(levels["A"]["peak_db"], peak_b))
    gains = {"A": common, "B": gain_b + common}
    for label in ("A", "B"):
        factor = 10 ** (gains[label] / 20.0)
        samples = data[label]
        scaled = samples.__class__(samples.typecode,
                                   (v * factor for v in samples))
        buf = audio.from_floats(scaled, channels=2)
        wav = os.path.join(folder, f"{name}_{label}.wav")
        wavio.write(wav, buf, R.RATE)
        out["files"][label] = {"wav": wav}
        if mp3:
            import mp3 as mp3mod
            path = os.path.join(folder, f"{name}_{label}.mp3")
            info = mp3mod.encode(buf, R.RATE, path, encoder="auto")
            out["files"][label]["mp3"] = path
            out["files"][label]["encoder"] = info["encoder"]
    out["original_levels"] = levels
    out["gain_applied_db"] = {k: round(v, 2) for k, v in gains.items()}
    out["matched"] = "RMS of the range (A is the reference)"
    return out


# -- the whole correction, on a project -------------------------------------------
def rescope(project, oid: str, spec: str) -> str:
    """A copy of observation `oid` limited to the range `spec`, stored as
    a new observation. The composition loop hears a new section with the
    bar before it (for the join); a correction must touch the new
    section's rows only, not the context it was heard in."""
    obs = project.observation(oid)
    tl = T.build({k: project.state[k] for k in
                  ("structure", "instruments", "sections", "order",
                   "columns")})
    rng = tl.range(spec)
    new = {k: v for k, v in obs.items() if k not in ("id", "at", "card")}
    new["where"] = dict(obs["where"], t0=round(rng["t0"], 4),
                        t1=round(rng["t1"], 4),
                        rows=[rng["row0"], rng["row1"] - 1],
                        bars=rng["bars"], sections=rng["sections"])
    new["from_observation"] = oid
    new["scope"] = f"{spec} (heard over rows {obs['where']['rows'][0]}-" \
                   f"{obs['where']['rows'][1]})"
    return project.add_observation(new)


def _listen_ab(ear, tl_a, tl_b, spec: str, cache, obs: dict) -> dict:
    """The verified listening model's verdict on the original (A) against
    the chosen candidate (B) over the range; {"available": False, why}
    when nothing can listen."""
    if not EAR.listening(ear)["available"]:
        return EAR.ab_verdict(ear, [], [], "")
    clips = []
    for tl in (tl_a, tl_b):
        rng = tl.range(spec)
        r = R.render_range(tl, rng, cache=cache)
        clips.append(r.segment(rng["t0"], rng["t1"], mono=False))
    voices = list(obs.get("voices") or [])
    goal = (obs.get("statement") or obs["kind"]) + \
        " — the change should correct this without harming the rest"
    return EAR.ab_verdict(ear, clips[0], clips[1], goal,
                          voices=voices or tl_a.voices())


def improve(project, oid: str, budget: Optional[Budget] = None,
            cache: Optional[R.Cache] = None, memory=None,
            use_memory: bool = True, ab: bool = True,
            hearing: Optional[dict] = None, ear=None) -> dict:
    """Correct observation `oid` of the project's head: search, commit the
    best or nothing, write the A/B, record the decision (and, with a
    memory, the LearningRecord). -> the decision.

    `ear` is the ear the caller chose (ear.choose). The candidates are
    measured by its measuring part — the metric and the guards are
    numbers. When a listening model that passed the probes is behind it,
    the chosen candidate is played to it against the original, matched in
    loudness; its preference is recorded as "heard by model", apart from
    the measurements, and a confident preference for the original vetoes
    the commit. Without one, the decision says that nothing listened."""
    started = time.time()
    obs = project.observation(oid)
    cache = cache or R.Cache(project.path("renders"))
    tl = T.build({k: project.state[k] for k in
                  ("structure", "instruments", "sections", "order",
                   "columns")})
    rng = tl.range(D._spec(obs))
    ear = ear or EAR.ToolEar(cache)
    tool = EAR.measurer(ear, cache)
    ear_record = {"mode": ear.mode, "statement": EAR.statement(ear),
                  "listening": EAR.listening(ear),
                  "candidates_judged_by": "measurement (Tool ear)"}
    renders0, seconds0 = cache.misses, cache.render_seconds
    if hearing is None:
        hearing = tool.hear(tl, rng)
    remembered, retrieval = [], None
    if memory is not None and use_memory:
        remembered, retrieval = memory.recall(tl, obs, hearing)
    search = Search(tl, obs, hearing, cache, budget, remembered, ear=tool)
    if search.goal(search.read(hearing)):
        # measured again over its own range, the fault is not there (a
        # rescoped observation can be): nothing to correct
        did = project.add_decision({
            "observation": oid, "kind": obs["kind"], "voices": obs["voices"],
            "range": {k: rng[k] for k in ("spec", "t0", "t1", "bars",
                                           "sections")},
            "ear": ear_record,
            "base_rev": project.head, "outcome": "not_needed",
            "before": search.read(hearing), "rounds": [],
            "stopped": "goal already met", "committed_rev": None,
            "why": f"{search.metric_name} is "
                   f"{search.read(hearing):+.2f} dB here, already at the "
                   f"goal; nothing was changed",
            "cost": {"renders": cache.misses - renders0,
                     "render_seconds": round(cache.render_seconds
                                             - seconds0, 2),
                     "runtime_s": round(time.time() - started, 2),
                     "operations": 1, "candidates": 0, "retries": 0,
                     "model_tokens": 0,
                     "audio_model_seconds": 0.0}})
        project.save()
        return project.decision(did)
    result = search.run()
    best = result["best"]
    base_rev = project.head
    verdict = None
    if best is not None:
        verdict = _listen_ab(ear, tl, best["_timeline"], rng["spec"], cache,
                             obs)
    decision = {
        "observation": oid, "kind": obs["kind"], "voices": obs["voices"],
        "range": {k: rng[k] for k in ("spec", "t0", "t1", "bars",
                                       "sections")},
        "ear": ear_record,
        "listening_verdict": verdict,
        "judged_by": "measured" if not (verdict or {}).get("available")
        else "measured, then heard by model",
        "base_rev": base_rev, "base_hash": content_hash(tl.content),
        "budget": search.budget.to_dict(),
        "card": _compact_card(result["card"]) if result["card"] else None,
        "memory": retrieval,
        "rounds": [{**r, "candidates": [_public(c) for c in r["candidates"]]}
                   for r in result["rounds"]],
        "stopped": result["stopped"]}
    if best is not None and EAR.vetoes(verdict):
        decision.update(outcome="rolled_back", committed_rev=None,
                        vetoed_by=verdict["model"],
                        rejected_chain=best["chain"],
                        measured_before=result["base_value"],
                        measured_after=best["metric"]["after"],
                        why=f"the measurement improved "
                            f"({result['base_value']:+.2f} -> "
                            f"{best['metric']['after']:+.2f} dB) but "
                            f"{verdict['model']} preferred the original "
                            f"(confidence {verdict['confidence']:.2f}): "
                            f"\"{verdict.get('statement', '')}\"; not "
                            f"committed, the project stays at revision "
                            f"{base_rev}")
        best = None
    elif best is None:
        decision.update(outcome="rolled_back", committed_rev=None,
                        why="no candidate improved the measurement with "
                            "every guard passed; the project stays at "
                            f"revision {base_rev}")
    else:
        decision.update(outcome="committed", chain=best["chain"],
                        before=result["base_value"],
                        after=best["metric"]["after"],
                        gain_db=round(best["metric"]["after"]
                                      - result["base_value"], 2),
                        goal_met=best["goal_met"],
                        guards=best["guards"],
                        deltas=best["deltas"])
    did = project.add_decision(decision)
    if best is not None:
        rev = project.commit(best["_content"], {
            "kind": "patch", "decision": did, "observation": oid,
            "chain": best["chain"]})
        decision_ref = project.decision(did)
        decision_ref["committed_rev"] = rev
        if ab:
            decision_ref["ab"] = write_ab(project.path("out"), did, tl,
                                          best["_timeline"],
                                          rng["spec"], cache)
    cost = dict(result["cost"])
    cost.update(renders=cache.misses - renders0,
                render_seconds=round(cache.render_seconds - seconds0, 2),
                runtime_s=round(time.time() - started, 2))
    project.decision(did)["cost"] = cost
    if memory is not None:
        record = memory.learn(project, did)
        project.decision(did)["learning_record"] = record["id"]
    project.save()
    return project.decision(did)


def _public(c: dict) -> dict:
    return {k: v for k, v in c.items() if not k.startswith("_")}


def _compact_card(card: dict) -> dict:
    out = dict(card)
    loc = dict(out.get("located") or {})
    loc.pop("notes", None)
    out["located"] = loc
    return out
