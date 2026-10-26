"""live.py — the audit's T1 and T3 with a live audio model: real renders of
the Mega Drive sent as one WAV, the model asked to choose, and the choice
checked against what was changed. Run by examples/agentic/acceptance.py
when CHIPGEN_AUDIO_ENDPOINT names a model, and by `agent.py live_check`.

The versions are fixtures.held_lead (a lead of short and held notes over
bass and pad), played from the top, the lead's range:

    original    as it is
    copy        the original again, byte for byte (a control)
    vibrato     the lead with a vibrato (the score's `vib`, 40 cents,
                6 Hz, after 150 ms)
    soft        the lead's carriers attacking slowly (the patch op
                `param`, the carriers' attack rate 10)

and four questions:

    T1 change     original, copy, vibrato — "the lead's long notes should
                  swing": right if it picks the vibrato
    T1 identity   original, copy — the same goal: right if it does not
                  pick the copy (a copy cannot do it better: "uncertain"
                  or the original)
    T3 choice     original, vibrato, soft — "the lead's notes should start
                  softly": right if it picks the soft one
    T3 swapped    the two candidates in the other order: right if it picks
                  the soft one again (an ear that picks by position fails
                  one)

The model is never told which version is which: the original is "the
piece as it is", the others "candidate A", "candidate B".

Every clip goes through the project's audible session (session.py), so
the WAVs the model received are kept by hash beside the answers. A model
that failed the probes is still asked and recorded, and the record says
it was not trusted. Nothing here is a stand-in: without an endpoint it
does not run.
"""

import copy
import os
import time
from typing import Optional

from . import ear as EAR
from . import fixtures
from . import loop as L
from . import patch as PATCH
from . import render as R
from . import session as SESSION
from . import timeline as T
from .state import Project, content as state_content

GOAL_VIBRATO = ("the lead's long notes should swing gently in pitch (a "
                "vibrato); nothing else should change")
GOAL_SOFT = ("the lead's notes should start softly, rising in over a short "
             "moment instead of striking; nothing else should change")


def _versions(project, cache):
    c = state_content(project.state)
    base = T.build(c)
    vib = copy.deepcopy(c)
    sec = vib["sections"][0]
    ch = vib["instruments"]["lead"]["channel"]
    sec.setdefault("directives", []).append(
        {"before_row": 0, "text": f"vib {ch} 40 6.0 0.15"})
    sec["directives"].append({"before_row": len(sec["rows"]),
                              "text": f"vib {ch} off"})
    soft = c
    for op in PATCH.carriers(c, "lead"):
        soft, _d = PATCH.param(soft, "lead", f"op{op}.ar", value=10)
    spec = "all"
    plat = EAR.platform_of(base)

    def clip(content):
        return L._clip(T.build(content), spec, cache, plat["stereo"])
    original = clip(c)
    return {"original": original, "copy": list(original),
            "vibrato": clip(vib), "soft": clip(soft)}, plat, base


def run(adapter, folder: str, verification: Optional[dict] = None) -> dict:
    """Ask the model behind `adapter` the four questions. -> the record:
    each question's versions, its choice, right or not, the clip's hash."""
    started = time.time()
    os.makedirs(folder, exist_ok=True)
    project = Project.create(os.path.join(folder, "project"),
                             fixtures.held_lead())
    cache = R.Cache(project.path("renders"))
    recorder = SESSION.RecordingAdapter(adapter, project.root,
                                        revision=project.head)
    verification = verification or EAR.verify(recorder)
    ear = EAR.choose("native", recorder, verification, cache) \
        if verification.get("verified") else None
    v, plat, base = _versions(project, cache)
    plan = [
        ("T1 change", ["original", "copy", "vibrato"], GOAL_VIBRATO,
         lambda k: k == "vibrato"),
        ("T1 identity", ["original", "copy"], GOAL_VIBRATO,
         lambda k: k != "copy"),
        ("T3 choice", ["original", "vibrato", "soft"], GOAL_SOFT,
         lambda k: k == "soft"),
        ("T3 swapped", ["original", "soft", "vibrato"], GOAL_SOFT,
         lambda k: k == "soft"),
    ]
    out = {"adapter": adapter.name, "verified": verification.get("verified"),
           "music_probes": (verification.get("music") or {}).get("probes"),
           "trusted": EAR.trusted_to_choose(ear) if ear else
           {"trusted": False, "why": verification.get("why")},
           "platform": plat["name"], "questions": []}
    if ear is None:
        out["why"] = ("the model did not pass the tone probes: it was not "
                      "asked about music")
        return out
    for name, keys, goal, right in plan:
        # neutral labels: the model hears which one does it, it is not
        # told ("version 1" is the piece as it is, the prompt says)
        versions = [("the piece as it is" if i == 0 else
                     f"candidate {chr(64 + i)}", v[k])
                    for i, k in enumerate(keys)]
        verdict = EAR.ab_choose(ear, versions, goal, voices=["lead"],
                                stereo=plat["stereo"],
                                platform=plat["name"])
        choice = verdict.get("choice")
        picked = keys[choice] if choice is not None else None
        ok = right(picked) if picked is not None else (
            name == "T1 identity")             # "uncertain" is right there
        out["questions"].append({
            "question": name, "versions": keys, "goal": goal,
            "picked": picked if picked is not None else "uncertain",
            "right": ok, "confidence": verdict.get("confidence"),
            "statement": verdict.get("statement"),
            "clip": verdict.get("clip_sha"),
            "audio_seconds": verdict.get("audio_seconds"),
            "problem": verdict.get("problem")})
    out["passed"] = {
        "T1": all(q["right"] for q in out["questions"]
                  if q["question"].startswith("T1")),
        "T3": all(q["right"] for q in out["questions"]
                  if q["question"].startswith("T3"))}
    out["session"] = SESSION.summary(project.root)
    out["seconds"] = round(time.time() - started, 1)
    return out
