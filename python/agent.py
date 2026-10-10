"""agent.py — the agent's one door into the agentic loop.

    python3 python/agent.py <operation> [--root DIR] [key=value ...]
    python3 python/agent.py <operation> --args '{"project_id": "p1", ...}'

or from Python:

    from agent import Agent
    a = Agent("work/agentic")
    a.call("create_musical_state", intent="a lead over bass and pad",
           preset="masked_lead", project_id="demo")
    a.call("hear_range", project_id="demo", range="all")
    a.call("improve", project_id="demo", observation_id="o1")

Operations (the spec's facade, plus `improve`, `trace` and `export`):

    inspect_capabilities   target                       what can be done, how
    create_musical_state   intent, target, preset|...   a new project
    inspect_musical_state  project_id, scope            a short card
    compose_range          project_id, section, rows?   write one section
    render_range           project_id, range, stems?    audio files
    hear_range             project_id, range, mode      observations
    trace                  project_id, t0, t1, voice    audio -> notes -> regs
    diagnose_range         project_id, observation_id   a diagnostic card
    propose_patch          project_id, observation_id   hypotheses with ids
    compare_versions       project_id, rev_a, rev_b     A/B, measured
    commit_patch           project_id, patch_id|patch   apply, guard, commit
    rollback_patch         project_id, revision_id      back, as a revision
    improve                project_id, observation_id   the whole loop, 3x2
    continue_composition   project_id, goal, sections   compose on, resumable
    learn_recipe           project_id, decision_id      a LearningRecord
    load_bank              project_id, path, kind       forge or FM patches
    export                 project_id, format           trk vgm wav mp3 it

References, parallel listening, sketches and instruments:

    add_reference          project_id, path, tags, note a recording, score
                                                        or module, checked
    list_references        project_id                   their cards
    listen_compare         project_id, range, refs      aligned fragments,
                                                        reel, observations
    check_listening        -                            is a model hearing?
    design_instrument      project_id, voice, goal|ref  a voice's sound
    save_sketch            project_id, label, rev?      keep a draft
    section_sketches       project_id, section, seeds   other drafts of it
    list_sketches          project_id                   drafts and why
    choose_sketch          project_id, sketch_id, why   a direction
    reject_sketch          project_id, sketch_id, why   with the reason
    improve_toward_reference project_id, range, refs    one guided round
    hear_continuation      project_id, section          previous, join,
                                                        new, sketches

Every reply is compact JSON: what was done, the ids to ask about next,
and its cost (renders, seconds, the reply's own size). Audio, events and
spectra stay on disk; replies carry paths and numbers. A refusal is
{"error": code, "message": reason} — never a traceback. The memory of
decisions is one file under the root, shared by every project there, so
what one session learned is there for the next.
"""

import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from agentic import banks as BANKS               # noqa: E402
from agentic import capabilities as CAP          # noqa: E402
from agentic import compose as COMP              # noqa: E402
from agentic import diagnose as D                # noqa: E402
from agentic import ear as EAR                   # noqa: E402
from agentic import fixtures                     # noqa: E402
from agentic import guided as G                  # noqa: E402
from agentic import instrument as INS            # noqa: E402
from agentic import listen as LI                 # noqa: E402
from agentic import references as REF            # noqa: E402
from agentic import sketches as SK               # noqa: E402
from agentic import loop as L                    # noqa: E402
from agentic import memory as MEM                # noqa: E402
from agentic import render as R                  # noqa: E402
from agentic import state as S                   # noqa: E402
from agentic import timeline as T                # noqa: E402
from agentic import trace as TR                  # noqa: E402
from agentic.state import AgenticError           # noqa: E402

PRESETS = {"masked_lead": fixtures.masked_lead,
           "masked_lead_b": fixtures.masked_lead_b,
           "etude": fixtures.etude}


def _r(x, n=2):
    return round(x, n) if isinstance(x, float) else x


def _obs_card(o: dict) -> dict:
    w = o.get("where", {})
    return {"id": o.get("id"), "kind": o["kind"], "voices": o["voices"],
            "bars": w.get("bars"), "t": [w.get("t0"), w.get("t1")],
            "statement": o["statement"], "basis": o["basis"],
            "severity": o.get("severity"),
            "confidence": o.get("confidence")}


class Agent:
    """The facade. One instance per root folder of projects."""

    def __init__(self, root: str = "work/agentic", adapter=None):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)
        self.memory = MEM.Memory(os.path.join(self.root, "memory.jsonl"))
        self.adapter = adapter
        self.verification = None

    # -- plumbing -------------------------------------------------------------
    def _project(self, project_id: str) -> S.Project:
        path = os.path.join(self.root, project_id)
        return S.Project.open(path)

    def _timeline(self, project, rev=None):
        c = project.checkout(rev) if rev is not None else S.content(
            project.state)
        return T.build(c)

    def _cache(self, project):
        return R.Cache(project.path("renders"))

    def call(self, op: str, **args) -> dict:
        """Run one operation; errors come back as data."""
        started = time.time()
        fn = getattr(self, "op_" + op, None)
        if fn is None:
            reply = {"error": "unknown_operation",
                     "message": f"{op!r} is not an operation; see "
                                f"`python3 python/agent.py --help`"}
        else:
            try:
                reply = fn(**args)
            except AgenticError as error:
                reply = error.to_dict()
            except TypeError as error:
                reply = {"error": "bad_arguments", "message": str(error)}
        size = len(json.dumps(reply, ensure_ascii=False))
        reply.setdefault("cost", {})
        if isinstance(reply["cost"], dict):
            reply["cost"].update(seconds=round(time.time() - started, 2),
                                 reply_bytes=size,
                                 approx_tokens=size // 4)
        self._log(op, args, reply, started)
        return reply

    def _log(self, op, args, reply, started):
        pid = args.get("project_id")
        if not pid or not os.path.isdir(os.path.join(self.root, pid)):
            return
        line = {"op": op, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                              time.gmtime(started)),
                "args": {k: v for k, v in args.items()
                         if k not in ("rows",)},
                "error": reply.get("error"), "cost": reply.get("cost")}
        with open(os.path.join(self.root, pid, "calls.jsonl"), "a",
                  encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")

    # -- operations -------------------------------------------------------------
    def op_inspect_capabilities(self, target: str = "megadrive") -> dict:
        if self.adapter is not None and self.verification is None:
            self.verification = EAR.verify(self.adapter)
        return CAP.inspect(target, self.adapter, self.verification)

    def op_create_musical_state(self, intent: str = "", target: str =
                                "megadrive", preset: str = None,
                                project_id: str = None, key: str = "",
                                bpm: float = 140.0, plan=None,
                                instruments=None, columns=None,
                                constraints=None, banks=None) -> dict:
        if preset:
            if preset not in PRESETS:
                raise AgenticError("unknown_preset", f"preset is one of "
                                   f"{', '.join(PRESETS)}")
            st = PRESETS[preset]()
            if intent:
                st["identity"]["intent"] = intent
            if plan:
                st["structure"]["plan"] = plan
        else:
            st = S.new_state(intent, target=target, key=key, bpm=bpm,
                             columns=columns, constraints=constraints)
            if instruments:
                st["instruments"] = instruments
            if plan:
                st["structure"]["plan"] = plan
        if project_id:
            st["identity"]["id"] = project_id
        pid = st["identity"]["id"]
        S.Project.create(os.path.join(self.root, pid), st)
        loaded = []
        for b in banks or []:
            spec = b if isinstance(b, dict) else {"path": b}
            got = BANKS.add(self._project(pid), spec["path"],
                            spec.get("kind", "forge"))
            loaded.append({"bank": got["bank"]["path"],
                           "for_megadrive": [x["name"] for x in
                                             got["for_megadrive"]]})
        out = {"project_id": pid, "card": self._card(self._project(pid))}
        if loaded:
            out["banks"] = loaded
        return out

    def _card(self, p) -> dict:
        st = p.state
        tl = self._timeline(p)
        return {"intent": st["identity"]["intent"],
                "target": st["identity"]["target"],
                "constraints": st["identity"]["constraints"],
                "key": st["structure"]["key"], "bpm": st["structure"]["bpm"],
                "plan": [{k: e.get(k) for k in ("id", "function", "bars",
                                                 "energy", "harmony")}
                         for e in st["structure"]["plan"]],
                "sections": [s["id"] for s in st["sections"]],
                "order": st["order"],
                "voices": {v: {k: ins.get(k) for k in
                               ("channel", "patch", "role", "register")}
                           for v, ins in st["instruments"].items()},
                "duration_s": _r(tl.duration) if st["sections"] else 0.0,
                "banks": [b["path"] for b in
                          st["structure"].get("banks", []) or []],
                "head": st["head"], "revisions": len(st["revisions"]),
                "observations": len(st["observations"]),
                "decisions": len(st["decisions"]),
                "motifs": sorted(st.get("motifs", {}).get("motifs", {})),
                "composition": {k: v for k, v in
                                (st.get("composition") or {}).items()
                                if k != "log"}}

    def op_load_bank(self, project_id: str, path: str,
                     kind: str = "forge") -> dict:
        """A forge bank (python/forge.py) or an FM bank file, copied into
        the project, installed, recorded as a revision."""
        return BANKS.add(self._project(project_id), path, kind)

    def op_inspect_musical_state(self, project_id: str,
                                 scope: str = "card") -> dict:
        p = self._project(project_id)
        st = p.state
        if scope == "card":
            return self._card(p)
        if scope == "observations":
            return {"observations": [_obs_card(o) for o in
                                     st["observations"]]}
        if scope == "decisions":
            return {"decisions": [{k: d.get(k) for k in
                                   ("id", "observation", "kind", "outcome",
                                    "before", "after", "gain_db",
                                    "goal_met", "committed_rev", "chain",
                                    "learning_record", "stopped")}
                                  for d in st["decisions"]]}
        if scope == "revisions":
            return {"head": st["head"], "revisions": st["revisions"]}
        if scope == "motifs":
            g = st.get("motifs", {})
            return {"motifs": {k: {"voice": m["voice"], "notes": m["notes"],
                                   "origin": m["origin"]}
                               for k, m in g.get("motifs", {}).items()},
                    "instances": g.get("instances", []),
                    "open": g.get("open", [])}
        if scope == "trk":
            return {"trk": self._timeline(p).text}
        if scope.startswith("section:"):
            sec = p.section(scope.split(":", 1)[1])
            return {"section": sec}
        if scope == "timeline":
            return self._timeline(p).summary()
        raise AgenticError("bad_scope", "scope is card, observations, "
                           "decisions, revisions, motifs, trk, timeline or "
                           "section:<id>")

    def op_compose_range(self, project_id: str, section: str = None,
                         rows=None, harmony=None, function: str = None,
                         seed: int = None) -> dict:
        """Rows from the agent for `section` (new or replacing), checked
        and committed; without rows, the built-in composer writes the
        next planned section."""
        p = self._project(project_id)
        if rows is None:
            r = COMP.continue_composition(p, stop_after=1, seed=seed,
                                          hear=False)
            return {"composed": [x["section"] for x in r["done"]],
                    "status": r["status"], "next": r["next"],
                    "steps": [{k: x.get(k) for k in
                               ("section", "rev", "function", "motif",
                                "transition")} for x in r["done"]]}
        c = S.content(p.state)
        sec = {"id": section, "rows": rows, "directives": [],
               "harmony": harmony or [], "composed_by": "agent",
               "intent": {"function": function or "statement"}}
        c["sections"] = [s for s in c["sections"] if s["id"] != section] + \
            [sec]
        if section not in c["order"]:
            c["order"].append(section)
        problems = S.check_content(c)
        if problems:
            raise AgenticError("bad_content", problems[0]["message"],
                               problems=problems)
        T.build(c)
        rev = p.commit(c, {"kind": "compose", "section": section,
                           "by": "agent"})
        out = {"section": section, "rev": rev}
        idx = c["order"].index(section)
        if idx > 0:
            out["transition"] = COMP.transition(c, c["order"][idx - 1],
                                                section)
        if harmony:
            out["harmony"] = COMP.harmony_check(c, section)
        return out

    def op_render_range(self, project_id: str, range: str = "all",
                        stems: bool = False, fmt: str = "mp3",
                        rev: int = None) -> dict:
        import audio
        import wavio
        p = self._project(project_id)
        tl = self._timeline(p, rev)
        rng = tl.range(range)
        cache = self._cache(p)
        before = cache.misses
        targets = [("mix", None)]
        if stems:
            targets += [(v, [v]) for v in tl.voices()
                        if tl.sounding(rng["t0"], rng["t1"], v)]
        files = {}
        tag = range.replace(" ", "").replace(":", "_").replace("#", "_")
        for name, voices in targets:
            r = R.render_range(tl, rng, voices=voices, cache=cache)
            stereo = r.segment(rng["t0"], rng["t1"], mono=False)
            buf = audio.from_floats(stereo, channels=2)
            path = p.path("out", f"r{rev if rev is not None else p.head}_"
                                 f"{tag}_{name}.{fmt}")
            if fmt == "wav":
                wavio.write(path, buf, R.RATE)
            elif fmt == "mp3":
                import mp3
                mp3.encode(buf, R.RATE, path)
            else:
                raise AgenticError("bad_format", "fmt is wav or mp3")
            files[name] = {"path": path, "rms_db": _r(R.rms_db(stereo)),
                           "method": r.method}
        return {"range": {k: _r(rng[k]) for k in ("spec", "t0", "t1",
                                                   "bars")},
                "files": files, "note": "stems are separate renders; the "
                "mix is not their sum (register-write timing)",
                "cost": {"renders": cache.misses - before}}

    def _ear(self, mode, p):
        if self.adapter is not None and self.verification is None:
            self.verification = EAR.verify(self.adapter)
        return EAR.choose(mode, self.adapter, self.verification,
                          self._cache(p))

    def op_hear_range(self, project_id: str, range: str = "all",
                      mode: str = "auto", focus=None) -> dict:
        p = self._project(project_id)
        tl = self._timeline(p)
        rng = tl.range(range)
        ear = self._ear(mode, p)
        h = ear.hear(tl, rng, focus=focus,
                     intent=p.state["identity"]["intent"])
        ids = [p.add_observation(o) for o in h["observations"]]
        p.save()
        voices = {v: {k: m.get(k) for k in ("role", "band_margin_db",
                                              "body_margin_db",
                                              "attack_margin_db",
                                              "band_share", "notes")}
                  for v, m in h.get("measurements", {}).get(
                      "voices", {}).items()}
        return {"mode": h["mode"], "statement": h["statement"],
                "range": {k: _r(rng[k]) for k in ("spec", "t0", "t1",
                                                   "bars")},
                "observations": [_obs_card(p.observation(i)) for i in ids],
                "mix": h.get("measurements", {}).get("mix"),
                "voices": voices, "listening": EAR.listening(ear),
                "cost": h.get("cost", {})}

    def op_trace(self, project_id: str, t0: float, t1: float,
                 voice: str = None, writes: bool = True) -> dict:
        p = self._project(project_id)
        tl = self._timeline(p)
        return TR.explain(tl, float(t0), float(t1), voice, writes, limit=24)

    def _hearing_for(self, p, tl, obs):
        rng = tl.range(D._spec(obs))
        return EAR.ToolEar(self._cache(p)).hear(tl, rng)

    def op_diagnose_range(self, project_id: str, observation_id: str,
                          memory: bool = True) -> dict:
        p = self._project(project_id)
        obs = p.observation(observation_id)
        tl = self._timeline(p)
        h = self._hearing_for(p, tl, obs)
        remembered, retrieval = ([], None)
        if memory:
            remembered, retrieval = self.memory.recall(tl, obs, h)
        card = D.diagnose(tl, obs, h, remembered=remembered)
        obs["card"] = {"rev": p.head, "hypotheses": card["hypotheses"]}
        p.save()
        out = L._compact_card(card)
        out["memory"] = retrieval
        for hyp in out["hypotheses"]:
            hyp["patch_id"] = f"{observation_id}.{hyp['id']}"
        return out

    def op_propose_patch(self, project_id: str, observation_id: str) -> dict:
        card = self.op_diagnose_range(project_id, observation_id)
        if "error" in card:
            return card
        return {"observation": observation_id,
                "patches": [{"patch_id": h["patch_id"], "patch": h["patch"],
                             "hypothesis": h["hypothesis"],
                             "expect_db": h["expect_db"],
                             "basis": h["basis"], "risks": h["risks"]}
                            for h in card["hypotheses"]],
                "excluded": card.get("excluded", []),
                "not_considered": card.get("not_considered")}

    def op_compare_versions(self, project_id: str, rev_a: int, rev_b: int,
                            range: str = "all", target: str = None,
                            mode: str = "auto", goal: str = "") -> dict:
        p = self._project(project_id)
        tl_a, tl_b = self._timeline(p, rev_a), self._timeline(p, rev_b)
        cache = self._cache(p)
        before = cache.misses
        chosen = self._ear(mode, p)
        ear = EAR.measurer(chosen, cache)
        ha = ear.hear(tl_a, tl_a.range(range))
        hb = ear.hear(tl_b, tl_b.range(range))
        obs = {"voices": [target] if target else []}
        rng = tl_a.range(range)
        checks = L.guards(tl_a, tl_b, ha, hb, rng, obs)
        voices = {}
        for v, m in hb["measurements"]["voices"].items():
            a = ha["measurements"]["voices"].get(v, {})
            voices[v] = {"band_margin_db": [a.get("band_margin_db"),
                                            m.get("band_margin_db")],
                         "rms_db": [a.get("rms_db"), m.get("rms_db")]}
        ab = L.write_ab(p.path("out"), f"r{rev_a}_vs_r{rev_b}", tl_a, tl_b,
                        range, cache)
        verdict = L._listen_ab(chosen, tl_a, tl_b, range, cache,
                               {"voices": [target] if target else [],
                                "kind": "comparison",
                                "statement": goal or "the better version"})
        return {"range": range, "statement": EAR.TOOL_STATEMENT,
                "listening": EAR.listening(chosen),
                "listening_verdict": verdict,
                "voices": voices,
                "mix_rms_db": [ha["measurements"]["mix"]["rms_db"],
                               hb["measurements"]["mix"]["rms_db"]],
                "faults": {"a": [_obs_card(o) for o in ha["observations"]],
                           "b": [_obs_card(o) for o in hb["observations"]]},
                "guards_b_vs_a": checks, "ab": ab,
                "cost": {"renders": cache.misses - before}}

    def op_commit_patch(self, project_id: str, patch_id: str = None,
                        patch=None, check: bool = True,
                        why: str = "") -> dict:
        p = self._project(project_id)
        obs = None
        if patch_id:
            oid, hid = patch_id.split(".", 1)
            obs = p.observation(oid)
            card = obs.get("card")
            if not card:
                raise AgenticError("no_proposal", f"{oid} has no proposed "
                                   f"patches; call propose_patch first")
            if card["rev"] != p.head:
                raise AgenticError("stale_proposal",
                                   f"{patch_id} was proposed at revision "
                                   f"{card['rev']}; the head is {p.head}. "
                                   f"Propose again", rev=card["rev"],
                                   head=p.head)
            hyp = next((h for h in card["hypotheses"] if h["id"] == hid),
                       None)
            if hyp is None:
                raise AgenticError("no_patch", f"no {hid} in {oid}'s card")
            patch = hyp["patch"]
        if patch is None:
            raise AgenticError("bad_patch", "give patch_id or patch")
        chain = patch if isinstance(patch, list) else [patch]
        tl = self._timeline(p)
        new_content, deltas, new_tl = L._apply_chain(tl.content, chain, tl)
        result = {"chain": chain,
                  "deltas": [L._compact_delta(d) for d in deltas]}
        cache = self._cache(p)
        before = cache.misses
        if check:
            spec = D._spec(obs) if obs else "all"
            ear = EAR.ToolEar(cache)
            ha = ear.hear(tl, tl.range(spec))
            hb = ear.hear(new_tl, new_tl.range(spec))
            checks = L.guards(tl, new_tl, ha, hb, tl.range(spec),
                              obs or {"voices": []})
            result["guards"] = checks
            if obs and obs["kind"] in ("buried", "too_quiet",
                                       "unheard_notes", "clipping"):
                name, read, goal = L.metric_for(obs)
                result["metric"] = {"name": name, "before": read(ha),
                                    "after": read(hb),
                                    "goal_met": goal(read(hb))}
            failed = [g for g in checks if not g["ok"]]
            if failed:
                raise AgenticError(
                    "guard_failed", "not committed: " + "; ".join(
                        f"{g['name']} ({g['detail']})" for g in failed),
                    guards=checks, chain=chain)
        base_rev = p.head
        rev = p.commit(new_content, {"kind": "patch", "by": "agent",
                                     "chain": chain, "why": why,
                                     "observation": obs["id"] if obs
                                     else None})
        did = p.add_decision({"observation": obs["id"] if obs else None,
                              "kind": obs["kind"] if obs else "manual",
                              "voices": obs["voices"] if obs else [],
                              "range": {"spec": D._spec(obs) if obs
                                        else "all"},
                              "outcome": "committed", "chain": chain,
                              "base_rev": base_rev,
                              "committed_rev": rev, "rounds": [],
                              "by": "agent", "why": why,
                              **({"before": result["metric"]["before"],
                                  "after": result["metric"]["after"]}
                                 if "metric" in result else {})})
        p.save()
        result.update(rev=rev, decision=did,
                      cost={"renders": cache.misses - before})
        return result

    def op_rollback_patch(self, project_id: str, revision_id: int,
                          why: str = "") -> dict:
        p = self._project(project_id)
        before = p.head
        rev = p.rollback(int(revision_id), {"why": why or "rollback "
                                            "requested"})
        same = p.revision(rev)["hash"] == p.revision(int(revision_id))["hash"]
        return {"rev": rev, "from": before, "to": int(revision_id),
                "content_equal_to_target": same}

    def op_improve(self, project_id: str, observation_id: str,
                   budget: str = "3x2", memory: bool = True,
                   ab: bool = True, mode: str = "auto") -> dict:
        p = self._project(project_id)
        ear = self._ear(mode, p)
        # memory=false is a control run: nothing recalled, nothing learned
        d = L.improve(p, observation_id, budget=L.Budget.parse(budget),
                      cache=self._cache(p),
                      memory=self.memory if memory else None, ab=ab,
                      ear=ear)
        return {"decision": d["id"], "outcome": d["outcome"],
                "before": d.get("before"), "after": d.get("after"),
                "gain_db": d.get("gain_db"), "goal_met": d.get("goal_met"),
                "chain": d.get("chain"), "rev": d.get("committed_rev"),
                "stopped": d["stopped"],
                "candidates": [{"round": r["round"],
                                "patch": c["patch"],
                                "after": (c.get("metric") or {}).get("after"),
                                "accepted": c["accepted"], "why": c["why"]}
                               for r in d["rounds"] for c in r["candidates"]],
                "ab": {k: v.get("mp3") or v.get("wav")
                       for k, v in (d.get("ab") or {}).get("files",
                                                           {}).items()},
                "memory": d.get("memory"),
                "learning_record": d.get("learning_record"),
                "statement": EAR.statement(ear),
                "listening": EAR.listening(ear),
                "listening_verdict": d.get("listening_verdict"),
                "judged_by": d.get("judged_by"),
                "why": d.get("why"), "cost": d["cost"]}

    def op_continue_composition(self, project_id: str, goal=None,
                                sections: int = None, seed: int = None,
                                hear: bool = True, correct: bool = False,
                                budget: str = "3x2",
                                mode: str = "auto",
                                listen: bool = False,
                                sketches=None) -> dict:
        p = self._project(project_id)
        ear = self._ear(mode, p)
        fixes = []
        heard_on = []

        def correct_section(project, step):
            done = []
            heard = step.get("heard") or {}
            for oid in heard.get("observations", []):
                o = project.observation(oid)
                if o["kind"] not in ("buried", "unheard_notes", "clipping"):
                    continue
                # heard with the bar before the section; corrected in the
                # section only
                scoped = L.rescope(project, oid, heard["section_range"])
                d = L.improve(project, scoped, budget=L.Budget.parse(budget),
                              cache=self._cache(project), memory=self.memory,
                              ear=ear)
                done.append({"section": step["section"], "observation": oid,
                             "corrected_as": scoped,
                             "decision": d["id"], "outcome": d["outcome"],
                             "before": d.get("before"),
                             "after": d.get("after"),
                             "chain": d.get("chain"),
                             "remembered": bool((d.get("memory") or {}).get(
                                 "used")),
                             "candidates": d["cost"]["candidates"],
                             "renders": d["cost"]["renders"],
                             "seconds": d["cost"]["runtime_s"],
                             "judged_by": d.get("judged_by"),
                             "why": d.get("why")})
            fixes.extend(done)
            return done

        def after(project, step):
            out = {}
            if correct:
                out["corrections"] = correct_section(project, step)
            if listen:
                ids = sketches if sketches is not None else \
                    [s["id"] for s in SK.chosen(project)]
                h = G.hear_continuation(project, step["section"], ear=ear,
                                        sketch_ids=ids,
                                        cache=self._cache(project))
                # the join and the previous section, and apart from them
                # the sketches: a short list must not cut the comparison
                # with the chosen direction off
                said = {"join": [], "previous": [], "sketch": []}
                for o in h["observations"]:
                    kind = o.get("against_kind")
                    said[kind if kind in said else "previous"].append(
                        o["statement"])
                compared = [s for s in h.get("sketches", [])
                            if s["compared"]]
                summary = {"section": step["section"], "join": h.get("join"),
                           "reel": h["reel"], "listening": h["listening"],
                           "observations": said["join"]
                           + said["previous"][:6],
                           "against_sketches": said["sketch"][:8] or (
                               ["no reading differs from the sketch's by "
                                "more than its threshold"] if compared
                               else []),
                           "sketches": h.get("sketches", [])}
                heard_on.append(summary)
                out["continuation"] = summary
            return out

        r = COMP.continue_composition(p, goal=goal, stop_after=sections,
                                      seed=seed, hear=hear,
                                      cache=self._cache(p),
                                      after=after if (correct or listen)
                                      else None, ear=ear)
        return {"status": r["status"], "next": r["next"],
                "remaining": r["remaining"],
                "sections": [{"section": x["section"], "rev": x["rev"],
                              "function": x["function"],
                              "motif": x.get("motif"),
                              "transition_ok": (x.get("transition") or
                                                {}).get("ok"),
                              "observations": [
                                  _obs_card(p.observation(o))
                                  for o in (x.get("heard") or {}).get(
                                      "observations", [])]}
                             for x in r["done"]],
                "corrections": fixes, "continuations": heard_on,
                "statement": EAR.statement(ear),
                "listening": EAR.listening(ear),
                "cost": {"runtime_s": r["runtime_s"]}}

    def op_learn_recipe(self, project_id: str, decision_id: str) -> dict:
        p = self._project(project_id)
        d = p.decision(decision_id)
        if d.get("learning_record"):
            rec = self.memory.get(d["learning_record"])
            return {"record": rec["id"], "already": True,
                    "change": rec["change"], "result": rec["result"]}
        if d.get("by") == "agent" and not d.get("rounds"):
            raise AgenticError("not_measured", f"{decision_id} was "
                               f"committed by hand without a measured "
                               f"search; use compare_versions first")
        rec = self.memory.learn(p, decision_id)
        d["learning_record"] = rec["id"]
        p.save()
        return {"record": rec["id"], "change": rec["change"],
                "result": rec["result"], "applies_to": rec["applies_to"]}

    def op_export(self, project_id: str, format: str = "trk") -> dict:
        return CAP.export(self._project(project_id), format)

    # -- references, listening, sketches, instruments ------------------------
    def op_add_reference(self, project_id: str, path: str, tags=(),
                         note: str = "", kind: str = None, title: str = "",
                         author: str = "", licence: str = "",
                         url: str = "", regions=None, bank: str = None,
                         seconds: float = REF.DEFAULT_SECONDS,
                         check: bool = True) -> dict:
        """Keep someone else's music in the project as a reference (never
        committed, never copied): what it is for (tags), the user's note,
        where it came from; its grid and its source's claims, checked
        against its audio."""
        p = self._project(project_id)
        r = REF.add(p, path, kind=kind, tags=list(tags or []), note=note,
                    title=title, author=author, licence=licence, url=url,
                    regions=regions, bank=bank, seconds=float(seconds),
                    check=check)
        return {"reference": REF.card(r)}

    def op_list_references(self, project_id: str) -> dict:
        p = self._project(project_id)
        return {"references": [REF.card(r) for r in REF.all_(p)]}

    def op_check_listening(self) -> dict:
        """Whether an audio model that passed the probes is configured —
        and if not, what that means for every reply."""
        if self.adapter is None:
            return {"available": False, "adapter": None,
                    "why": "no audio adapter is configured (agent.py "
                           "--audio-endpoint URL --audio-model NAME, an "
                           "OpenAI-compatible endpoint that takes "
                           "input_audio)",
                    "consequence": "every observation is measured (or "
                                   "assumed, and said so); nothing is "
                                   "reported as heard"}
        if self.verification is None:
            self.verification = EAR.verify(self.adapter)
        v = self.verification
        return {"available": bool(v.get("verified")),
                "adapter": self.adapter.name, "probes": v.get("answers"),
                "why": v.get("why"),
                "consequence": "observations from this model are reported "
                               "as heard by model, apart from the "
                               "measurements" if v.get("verified") else
                               "the adapter did not pass the probes: it is "
                               "not used as an ear"}

    def op_listen_compare(self, project_id: str, range: str,
                          references=(), bars: int = None, sketches=(),
                          dims=None, voice: str = None,
                          mode: str = "auto") -> dict:
        """The range of the piece (its mix and its voices), each
        reference's matching bars, the sketches' same range: aligned on
        bars, loudness-matched, written one by one and as a reel;
        observations with their basis."""
        p = self._project(project_id)
        cache = self._cache(p)
        ear = self._ear(mode, p)
        tl = self._timeline(p)
        rng = tl.range(range)
        n_bars = bars or (rng["bars"][1] - rng["bars"][0] + 1)
        frags = [LI.of_project(p, range, stems=True, cache=cache)]
        for rid in references or []:
            reg = REF.get(p, rid)["regions"][0]
            frags.append(LI.of_reference(p, rid, reg["bars"][0], n_bars))
        for sid in sketches or []:
            frags.append(LI.of_sketch(p, sid, range, cache=cache))
        name = f"cmp_r{p.head}_" + "".join(ch if ch.isalnum() else "_"
                                             for ch in range)
        rep = LI.compare(p, frags, p.path("out", "listen"), name, ear=ear,
                         voice=voice, dims=dims)
        p.save()
        return {"reel": rep["reel"], "index": rep["index"],
                "fragments": [{k: e.get(k) for k in
                               ("n", "label", "bars", "bpm", "seconds",
                                "in_reel", "lufs_as_rendered", "gain_db")}
                              for e in rep["fragments"]],
                "observations": [{k: o.get(k) for k in
                                  ("id", "basis", "dimension", "feature",
                                   "against", "statement", "confidence",
                                   "voices", "bars", "t0", "t1")}
                                 for o in rep["observations"]],
                "matched_voices": rep["matched_voices"],
                "listening": rep["listening"],
                "model_listening": rep["model_listening"],
                "cost": {"renders": cache.misses}}

    def op_design_instrument(self, project_id: str, voice: str,
                             range: str = "all", goal: dict = None,
                             reference: str = None, dims=None,
                             mode: str = "auto", candidates: int = 6) -> dict:
        """Work on a voice's instrument toward a sound goal: given, or read
        off a reference's matching voice (on the dimensions it is tagged
        for). -> the candidates, what each changed and measured, the one
        kept or why none was."""
        p = self._project(project_id)
        cache = self._cache(p)
        ear = self._ear(mode, p)
        if goal is None:
            if reference is None:
                raise AgenticError("no_goal", "give a goal (readings) or a "
                                   "reference to read one from")
            ref = REF.get(p, reference)
            role = p.state["instruments"].get(voice, {}).get("role", "lead")
            matched = LI.match_voice(p, reference, role)
            if not matched.get("voice"):
                raise AgenticError("no_voice_to_match", f"{reference}: "
                                   f"{matched.get('why')}")
            tl = self._timeline(p)
            rng = tl.range(range)
            reg = ref["regions"][0]
            f = LI.of_reference(p, reference, reg["bars"][0],
                                rng["bars"][1] - rng["bars"][0] + 1)
            prof = LI.measure(p, f, reference_voice=matched["voice"])
            goal = INS.goal_from(prof.get("voice") or {},
                                 dims or ref["tags"],
                                 [c for c in ref.get("claims", [])
                                  if matched["voice"] in c.get("voices",
                                                               [])])
            if not goal:
                raise AgenticError("no_goal", f"{reference}'s "
                                   f"{matched['voice']} gives no reading "
                                   f"on {dims or ref['tags']}")
        rec = INS.design(p, voice, goal, range, cache=cache, ear=ear,
                         max_candidates=int(candidates))
        return {"goal": goal, "outcome": rec["outcome"], "why": rec["why"],
                "rev": rec.get("committed_rev"),
                "patch": [rec["patch_before"], rec.get("patch_after")],
                "before": rec["before"],
                "candidates": [{k: c.get(k) for k in
                                ("id", "round", "parameter", "change",
                                 "accepted", "why", "closed", "profile",
                                 "level_matched", "sketch")}
                               for c in rec["candidates"]],
                "listening": rec["listening"],
                "listening_verdict": rec.get("listening_verdict"),
                "decision": rec["decision"], "cost": rec["cost"]}

    def op_save_sketch(self, project_id: str, label: str, rev: int = None,
                       why: str = "") -> dict:
        p = self._project(project_id)
        sid = SK.save(p, label, rev=p.head if rev is None else int(rev),
                      why=why)
        return {"sketch": SK.get(p, sid)}

    def op_section_sketches(self, project_id: str, section: str,
                            seeds=(), energies=()) -> dict:
        p = self._project(project_id)
        ids = SK.section_variants(p, section, seeds=seeds,
                                  energies=energies)
        return {"sketches": [SK.get(p, i) for i in ids]}

    def op_list_sketches(self, project_id: str) -> dict:
        p = self._project(project_id)
        return {"sketches": [{k: s.get(k) for k in
                              ("id", "label", "status", "why", "reason",
                               "chosen_because", "base_rev", "rev",
                               "section", "chain", "measured")}
                             for s in SK.all_(p)]}

    def op_choose_sketch(self, project_id: str, sketch_id: str,
                         why: str = "") -> dict:
        return {"sketch": SK.choose(self._project(project_id), sketch_id,
                                    why)}

    def op_reject_sketch(self, project_id: str, sketch_id: str,
                         why: str) -> dict:
        return {"sketch": SK.reject(self._project(project_id), sketch_id,
                                    why)}

    def op_improve_toward_reference(self, project_id: str, range: str,
                                    references=(), sketches=None,
                                    bars: int = None, dims=None,
                                    voice: str = None, mode: str = "auto",
                                    gaps: int = 3) -> dict:
        """One guided round on the range toward the references: listen
        side by side, trace each gap, try changes that carry the
        principle over, keep what moves its reading with every guard
        kept, roll the rest back as sketches with their reasons."""
        p = self._project(project_id)
        ear = self._ear(mode, p)
        if sketches is None:
            sketches = [s["id"] for s in SK.chosen(p)]
        rep = G.improve_toward(p, range, list(references or []), bars=bars,
                               dims=dims, ear=ear, sketch_ids=sketches,
                               voice=voice, cache=self._cache(p),
                               max_gaps=int(gaps))
        return {"head": rep["head"], "reel": rep["reel"],
                "listening": rep["listening"],
                "statement": rep["statement"],
                "observations": [{k: o.get(k) for k in
                                  ("id", "basis", "dimension", "feature",
                                   "statement", "bars", "confidence")}
                                 for o in rep["observations"]],
                "worked_on": [{k: w.get(k) for k in
                               ("observation", "dimension", "statement",
                                "outcome", "why", "committed_rev",
                                "chosen", "goal")} | {
                    "hypotheses": [{k: h.get(k) for k in
                                    ("id", "principle", "parameter",
                                     "chain", "before", "after",
                                     "reference", "closed", "accepted",
                                     "why", "ab_reel", "sketch")}
                                   for h in w.get("hypotheses", [])]}
                    for w in rep["worked_on"]],
                "cost": rep["cost"]}

    def op_hear_continuation(self, project_id: str, section: str,
                             sketches=None, mode: str = "auto") -> dict:
        p = self._project(project_id)
        if sketches is None:
            sketches = [s["id"] for s in SK.chosen(p)]
        out = G.hear_continuation(p, section, ear=self._ear(mode, p),
                                  sketch_ids=sketches, cache=self._cache(p))
        p.save()
        return {k: out.get(k) for k in ("section", "range", "join", "reel",
                                         "index", "listening",
                                         "model_listening", "sketches")} | {
            "observations": [{k: o.get(k) for k in
                              ("id", "basis", "against", "feature",
                               "statement")}
                             for o in out["observations"]]}


# -- command line --------------------------------------------------------------
def _value(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return text


def main(argv=None) -> int:
    ops = sorted(n[3:] for n in dir(Agent) if n.startswith("op_"))
    parser = argparse.ArgumentParser(
        prog="agent.py", description="The agentic loop's facade. "
        "Operations: " + ", ".join(ops),
        epilog="Arguments as key=value (values read as JSON when they "
               "parse) or --args JSON. Example: python3 python/agent.py "
               "hear_range project_id=demo range=all")
    parser.add_argument("operation", choices=ops)
    parser.add_argument("pairs", nargs="*", help="key=value")
    parser.add_argument("--root", default="work/agentic",
                        help="where projects live (default work/agentic, "
                             "inside the folder your files go in)")
    parser.add_argument("--args", default=None, help="JSON object")
    parser.add_argument("--audio-endpoint", default=None,
                        help="an OpenAI-compatible endpoint that takes "
                             "audio (verified by probe before use)")
    parser.add_argument("--audio-model", default=None)
    a = parser.parse_args(argv)
    kwargs = json.loads(a.args) if a.args else {}
    for pair in a.pairs:
        if "=" not in pair:
            parser.error(f"{pair!r} is not key=value")
        k, v = pair.split("=", 1)
        kwargs[k] = _value(v)
    adapter = None
    if a.audio_endpoint:
        import llm
        endpoint = llm.Endpoint(url=a.audio_endpoint,
                                model=a.audio_model)
        adapter = EAR.OpenAIAudioAdapter(endpoint, declared=True)
    reply = Agent(a.root, adapter).call(a.operation, **kwargs)
    print(json.dumps(reply, ensure_ascii=False, indent=1))
    return 1 if "error" in reply else 0


if __name__ == "__main__":
    sys.exit(main())
