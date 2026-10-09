"""memory.py — what worked, where, and how sure: LearningRecords.

Unpacking a corpus or piling up patterns is not experience. What the loop
keeps is a causal decision, measured: in this context, this change to
these voices moved this measurement by this much, without these side
effects — or did not. A LearningRecord is kept for every decision, kept or
rolled back:

    task        the observation's kind, the target voice's role, the goal
    context     the voices involved by role (patch, register, notes' span),
                the target's band margin and who filled its band, the key,
                the tempo, the backend
    source      where it came from: this loop, measured, with the project,
                decision and revisions
    initial     the measurement before
    change      the patch chain in role terms (the voice whose role is
                "pad", not "fm2"), with its scope
    expected    what the hypotheses said before rendering
    result      the measurement after, the gain, the guards
    auditory    what an ear that listens said, or that nothing listened
    decision    committed or rolled_back, and why
    applies_to  its limits: the backend, the patches, the roles, the margin
                it started from. One case, not a rule for every FM scene
    cost        renders, render seconds, runtime, candidates, model tokens
    versions    content hashes of the modules that measured it, the seed

Records are JSON lines appended to a store file, never rewritten.

`recall` finds records of the same kind and target role, scores how alike
their context is (roles of the voices that filled the band, patches,
starting margin), and turns each committed chain into hypotheses for this
piece's voices — by role — expecting the gain it measured, with the
record's id as the basis. Nothing is applied blind: the loop renders and
guards a remembered hypothesis like any other. Records of what did not
help come back too, so the agent can see what not to try first.
"""

import hashlib
import json
import os
from typing import List, Optional, Tuple

from . import diagnose as D
from .state import AgenticError, _now

FORMAT = "chipgen-learning-record/1"
#: How alike a record's context must be to be recalled at all.
MIN_SIMILARITY = 0.5
_MODULES = ("ear.py", "diagnose.py", "loop.py", "patch.py", "render.py")


def versions() -> dict:
    here = os.path.dirname(os.path.abspath(__file__))
    h = hashlib.sha256()
    for name in _MODULES:
        with open(os.path.join(here, name), "rb") as handle:
            h.update(handle.read())
    return {"agentic_measure": h.hexdigest()[:12], "format": FORMAT}


def _role(content: dict, voice: str) -> str:
    return content["instruments"].get(voice, {}).get("role") or voice


def _in_roles(spec: dict, content: dict) -> dict:
    out = {k: v for k, v in spec.items() if k not in ("voice", "range")}
    out["role"] = _role(content, spec["voice"])
    return out


def _from_roles(spec: dict, content: dict, rng_spec: str) -> Optional[dict]:
    voice = next((v for v, ins in content["instruments"].items()
                  if (ins.get("role") or v) == spec["role"]), None)
    if voice is None:
        return None
    out = {k: v for k, v in spec.items() if k != "role"}
    out["voice"] = voice
    if out["op"] in ("level", "transpose", "vol") or "key" in out:
        out["range"] = rng_spec
    return out


class Memory:
    """A store of LearningRecords (JSON lines)."""

    def __init__(self, path: str):
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)

    def records(self) -> List[dict]:
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def get(self, rid: str) -> dict:
        for r in self.records():
            if r["id"] == rid:
                return r
        raise AgenticError("no_record", f"no learning record {rid!r}",
                           id=rid)

    def _append(self, record: dict):
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False,
                                    sort_keys=True) + "\n")

    # -- writing --------------------------------------------------------------
    def learn(self, project, did: str) -> dict:
        """The LearningRecord of a decision (committed or not)."""
        d = project.decision(did)
        rev = d["base_rev"]
        c = project.checkout(rev)
        st = project.state
        target = d["voices"][0] if d.get("voices") else None
        card = d.get("card") or {}
        measured = card.get("measured") or {}
        shares = measured.get("band_share") or {}
        involved = [target] + [v for v in shares if v != target]
        located = card.get("located") or {}
        voices = {}
        for v in involved:
            if v is None or v not in c["instruments"]:
                continue
            ins = c["instruments"][v]
            voices[_role(c, v)] = {"patch": ins.get("patch"),
                                   "channel": ins.get("channel"),
                                   "register": ins.get("register"),
                                   "band_share": shares.get(v)}
        candidates = [x for r in d.get("rounds", []) for x in
                      r["candidates"]]
        tried = []
        for x in candidates:
            chain = x.get("chain") or []
            tried.append({"change": [_in_roles(s, c) for s in chain],
                          "gain_db": (x.get("metric") or {}).get("gain_db"),
                          "accepted": x.get("accepted"),
                          "why": x.get("why", "")[:160]})
        committed = d.get("outcome") == "committed"
        record = {
            "format": FORMAT,
            "id": "lr-" + hashlib.sha256(
                f"{st['identity']['id']}:{did}:{_now()}".encode()
            ).hexdigest()[:10],
            "at": _now(),
            "task": {"kind": d["kind"], "target_role": _role(c, target)
                     if target else None,
                     "goal": f"band margin >= {measured.get('goal_db')} dB"
                     if measured else None},
            "context": {"backend": "megadrive (YM2612 + SN76489), "
                                   "Nuked-OPN2 render",
                        "key": c["structure"].get("key"),
                        "bpm": c["structure"].get("bpm"),
                        "voices": voices,
                        "target_span": located.get("span"),
                        "range_bars": d["range"].get("bars")},
            "source": {"kind": "loop", "measured": True,
                       "project": st["identity"]["id"], "decision": did,
                       "base_rev": rev,
                       "committed_rev": d.get("committed_rev")},
            "initial": {"band_margin_db": measured.get("band_margin_db")},
            "change": [_in_roles(s, c) for s in d.get("chain", [])],
            "scope": "the observation's range, every voice's notes in it",
            "expected": [{"change": [_in_roles(s, c) for s in
                                     (x["chain"] or [])],
                          "expect_db": x.get("expected_db"),
                          "basis": x.get("basis")} for x in candidates],
            "result": {"band_margin_db": d.get("after"),
                       "gain_db": d.get("gain_db"),
                       "goal_met": d.get("goal_met"),
                       "guards": [{"name": g["name"], "ok": g["ok"]}
                                  for g in d.get("guards", [])]},
            "tried": tried,
            "auditory": {"mode": d.get("ear", {}).get("mode"),
                         "statement": d.get("ear", {}).get("statement"),
                         "judgement": None,
                         "uncertainty": "nothing listened; the result is "
                                        "a measurement of the render"},
            "decision": {"outcome": d["outcome"], "why": d.get("why")
                         or (f"gain {d.get('gain_db')} dB, every guard "
                             f"passed" if committed else "")},
            "applies_to": {
                "backend": "megadrive",
                "roles": sorted(voices),
                "patches": {r: v["patch"] for r, v in voices.items()},
                "initial_margin_db": measured.get("band_margin_db"),
                "limits": "measured once, in this arrangement; recall "
                          "re-renders and re-checks it before use"},
            "cost": d.get("cost"),
            "versions": dict(versions(),
                             seed=(st.get("composition") or {}).get("seed"))}
        self._append(record)
        return record

    # -- reading ----------------------------------------------------------------
    def similarity(self, record: dict, content: dict, obs: dict,
                   hearing: dict) -> float:
        """0..1: same kind and target role are required; then the roles of
        the voices filling the band, the patches, the starting margin."""
        target = obs["voices"][0] if obs.get("voices") else None
        if record["task"]["kind"] != obs["kind"]:
            return 0.0
        if record["task"]["target_role"] != (_role(content, target)
                                             if target else None):
            return 0.0
        m = hearing["measurements"]["voices"].get(target, {})
        shares = m.get("band_share") or {}
        here = {_role(content, v) for v in shares} | {_role(content, target)}
        there = set(record["context"]["voices"])
        roles = len(here & there) / max(1, len(here | there))
        patches_here = {_role(content, v): content["instruments"][v].get(
            "patch") for v in shares if v in content["instruments"]}
        patches_here[_role(content, target)] = content["instruments"].get(
            target, {}).get("patch")
        same = sum(1 for r, p in record["applies_to"]["patches"].items()
                   if patches_here.get(r) == p)
        patches = same / max(1, len(record["applies_to"]["patches"]))
        a = record["initial"].get("band_margin_db")
        b = m.get("band_margin_db")
        margin = 1.0 if a is None or b is None else \
            max(0.0, 1.0 - abs(a - b) / 10.0)
        return round(0.5 * roles + 0.2 * patches + 0.3 * margin, 3)

    def recall(self, timeline, obs: dict, hearing: dict,
               limit: int = 2) -> Tuple[List[dict], dict]:
        """Hypotheses from similar committed records (by role), and what
        was looked at. -> (hypotheses, retrieval report)."""
        content = timeline.content
        rng_spec = D._spec(obs)
        scored = []
        for r in self.records():
            s = self.similarity(r, content, obs, hearing)
            if s >= MIN_SIMILARITY:
                scored.append((s, r))
        scored.sort(key=lambda x: -x[0])
        hyps, used, weak = [], [], []
        for s, r in scored:
            if r["decision"]["outcome"] == "committed" and len(hyps) < limit:
                chain = [_from_roles(x, content, rng_spec)
                         for x in r["change"]]
                if any(x is None for x in chain) or not chain:
                    continue
                hyps.append({
                    "patch": chain, "kind": "remembered",
                    "hypothesis": f"what fixed a {r['task']['kind']} "
                                  f"{r['task']['target_role']} before: "
                                  + "; ".join(_describe(x) for x in
                                              r["change"]),
                    "expect_db": r["result"]["gain_db"],
                    "basis": f"remembered: record {r['id']} (similarity "
                             f"{s}), measured {r['result']['gain_db']} dB "
                             f"there",
                    "risks": ["a different arrangement may answer "
                              "differently: rendered and guarded here"],
                    "cells": 0, "record": r["id"]})
                used.append({"record": r["id"], "similarity": s})
            for t in r.get("tried", []):
                if not t["accepted"] or (t["gain_db"] or 0) < 1.0:
                    weak.append({"change": t["change"],
                                 "gain_db": t["gain_db"],
                                 "record": r["id"]})
        return hyps, {"records_seen": len(self.records()),
                      "matched": [{"record": r["id"], "similarity": s}
                                  for s, r in scored],
                      "used": used, "weak_before": weak[:6]}


def _describe(spec: dict) -> str:
    if spec["op"] == "level":
        return f"{spec['role']} {spec['db']:+g} dB"
    if spec["op"] == "transpose":
        return f"{spec['role']} {spec['semitones']:+d} semitones"
    if spec["op"] == "param":
        return f"{spec['role']} {spec['key']} {spec.get('delta', '')}"
    return f"{spec['role']} {spec['op']}"
