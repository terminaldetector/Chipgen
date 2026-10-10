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
                                mode: str = "auto") -> dict:
        p = self._project(project_id)
        ear = self._ear(mode, p)
        fixes = []

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

        r = COMP.continue_composition(p, goal=goal, stop_after=sections,
                                      seed=seed, hear=hear,
                                      cache=self._cache(p),
                                      after=correct_section if correct
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
                "corrections": fixes,
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
