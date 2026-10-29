"""Make the agentic loop's examples, by running it — nothing here is
written by hand.

    python3 examples/agentic/make_examples.py [--out examples/agentic]
                                              [--work DIR]

1. masked_lead/  the spec's test A through the facade, the way an agent
   calls it: create, hear, propose, improve. The diagnostic card, the
   patch-delta, the decision with every candidate, the score before and
   after (.trk) and the A/B, loudness-matched (MP3; WAV with --wav).
2. memory.json   test E: a similar piece (masked_lead_b) corrected with
   and without what step 1 learned — candidates, renders, seconds.
3. etude/        a context étude: four sections composed one after the
   other from the plan (theme, variation, development, cadence), each
   heard with the bar before it, each fault the ear measured corrected
   by the loop with the memory before the next section is written,
   everything committed as revisions. The score, the MP3 (and the draft
   the same plan and seed give with no ear), the MusicalState, the motif
   graph read back from the rows, the transitions, and a LearningRecord.
4. measurements.json  every facade call: seconds, renders, reply size.

The ear here is the Tool ear: every verdict is a measurement of the
render, and the files say so. Nothing was listened to.
"""

import argparse
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "python"))

from agent import Agent                     # noqa: E402
from agentic import compose as C            # noqa: E402
from agentic import motif as M              # noqa: E402
from agentic import state as S              # noqa: E402


#: The scratch folder the run works in; paths under it are written as
#: `<work>/...` so the files do not carry this machine's directories.
WORK = None


def _dump(path, obj):
    text = json.dumps(obj, indent=1, ensure_ascii=False) + "\n"
    if WORK:
        text = text.replace(json.dumps(WORK)[1:-1], "<work>")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _ok(reply):
    if "error" in reply:
        raise SystemExit(json.dumps(reply, indent=1))
    return reply


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=HERE)
    parser.add_argument("--work", default=None)
    parser.add_argument("--wav", action="store_true",
                        help="also copy the A/B WAVs")
    args = parser.parse_args(argv)
    work = os.path.abspath(args.work or os.path.join(args.out, "_work"))
    if os.path.exists(work):
        shutil.rmtree(work)
    global WORK
    WORK = work
    a = Agent(work)
    out = args.out
    started = time.time()

    # 1. test A through the facade
    d1 = os.path.join(out, "masked_lead")
    os.makedirs(d1, exist_ok=True)
    _ok(a.call("create_musical_state", preset="masked_lead",
               project_id="masked_lead"))
    before = _ok(a.call("export", project_id="masked_lead", format="trk"))
    shutil.copy(before["path"], os.path.join(d1, "before.trk"))
    heard = _ok(a.call("hear_range", project_id="masked_lead",
                       range="all"))
    _dump(os.path.join(d1, "hearing.json"), heard)
    oid = next(o["id"] for o in heard["observations"]
               if o["kind"] == "buried")
    card = _ok(a.call("diagnose_range", project_id="masked_lead",
                      observation_id=oid))
    _dump(os.path.join(d1, "diagnostic_card.json"), card)
    fixed = _ok(a.call("improve", project_id="masked_lead",
                       observation_id=oid))
    _dump(os.path.join(d1, "improve_reply.json"), fixed)
    p = S.Project.open(os.path.join(work, "masked_lead"))
    decision = p.decision(fixed["decision"])
    _dump(os.path.join(d1, "decision.json"), decision)
    _dump(os.path.join(d1, "patch_delta.json"),
          {"chain": decision.get("chain"), "deltas": decision.get("deltas"),
           "rev": decision.get("committed_rev"),
           "guards": decision.get("guards")})
    after = _ok(a.call("export", project_id="masked_lead", format="trk"))
    shutil.copy(after["path"], os.path.join(d1, "after.trk"))
    for label, files in decision["ab"]["files"].items():
        shutil.copy(files["mp3"], os.path.join(d1, f"ab_{label}.mp3"))
        if args.wav:
            shutil.copy(files["wav"], os.path.join(d1, f"ab_{label}.wav"))
    _dump(os.path.join(d1, "ab_levels.json"),
          {k: decision["ab"][k] for k in ("original_levels",
                                           "gain_applied_db", "matched")})
    rb = _ok(a.call("rollback_patch", project_id="masked_lead",
                    revision_id=0, why="show that the fix can be undone"))
    _dump(os.path.join(d1, "rollback.json"), rb)
    # an agent's own patch that "clears" the lead by moving the melody up
    # an octave: measured, and refused by the guard that keeps the melody
    refused = a.call("commit_patch", project_id="masked_lead",
                     patch={"op": "transpose", "voice": "lead",
                            "range": "all", "semitones": 12},
                     why="the lead an octave up, out of the pad's way")
    _dump(os.path.join(d1, "refused_patch.json"), refused)

    # 2. test E: the similar piece, without and with memory
    rows = {}
    for name, use in (("without_memory", False), ("with_memory", True)):
        _ok(a.call("create_musical_state", preset="masked_lead_b",
                   project_id=name))
        h = _ok(a.call("hear_range", project_id=name, range="all"))
        oid_b = next(o["id"] for o in h["observations"]
                     if o["kind"] == "buried")
        r = _ok(a.call("improve", project_id=name, observation_id=oid_b,
                       memory=use, ab=False))
        rows[name] = {k: r.get(k) for k in ("outcome", "before", "after",
                                             "gain_db", "goal_met", "chain",
                                             "memory", "learning_record")}
        rows[name]["cost"] = r["cost"]
        rows[name]["candidates"] = r["candidates"]
    _dump(os.path.join(out, "memory.json"), rows)

    # 3. the context étude
    d3 = os.path.join(out, "etude")
    os.makedirs(d3, exist_ok=True)
    _ok(a.call("create_musical_state", preset="etude", project_id="etude"))
    comp = _ok(a.call("continue_composition", project_id="etude", seed=7,
                      hear=True, correct=True))
    _dump(os.path.join(d3, "composition.json"), comp)
    p = S.Project.open(os.path.join(work, "etude"))
    c = S.content(p.state)
    trk = _ok(a.call("export", project_id="etude", format="trk"))
    shutil.copy(trk["path"], os.path.join(d3, "etude.trk"))
    vgm = _ok(a.call("export", project_id="etude", format="vgm"))
    shutil.copy(vgm["path"], os.path.join(d3, "etude.vgm"))
    mp3 = _ok(a.call("render_range", project_id="etude", range="all"))
    shutil.copy(mp3["files"]["mix"]["path"], os.path.join(d3, "etude.mp3"))
    # the same plan and seed with no ear and no corrections: the draft
    _ok(a.call("create_musical_state", preset="etude",
               project_id="etude_draft"))
    _ok(a.call("continue_composition", project_id="etude_draft", seed=7,
               hear=False))
    draft = _ok(a.call("render_range", project_id="etude_draft",
                       range="all"))
    shutil.copy(draft["files"]["mix"]["path"],
                os.path.join(d3, "etude_draft_no_ear.mp3"))
    dtrk = _ok(a.call("export", project_id="etude_draft", format="trk"))
    shutil.copy(dtrk["path"], os.path.join(d3, "etude_draft_no_ear.trk"))
    g = p.state["motifs"]
    _dump(os.path.join(d3, "motif_graph.json"),
          {"motifs": g["motifs"], "instances_said": g["instances"],
           "instances_found": M.analyse(c, g["motifs"],
                                        c["structure"]["key"])})
    _dump(os.path.join(d3, "checks.json"),
          {"harmony": {s["id"]: C.harmony_check(c, s["id"])
                       for s in c["sections"]},
           "transitions": [x.get("transition") for x in
                           p.state["composition"]["log"]
                           if x.get("transition")],
           "hanging": C.hanging(c)})
    _dump(os.path.join(d3, "musical_state.json"), p.state)
    recs = a.memory.records()
    if recs:
        _dump(os.path.join(d3, "learning_record.json"), recs[-1])
        _dump(os.path.join(out, "learning_record_first.json"), recs[0])

    # 4. what every call cost
    calls = []
    for pid in sorted(os.listdir(work)):
        path = os.path.join(work, pid, "calls.jsonl")
        if os.path.exists(path):
            for line in open(path, encoding="utf-8"):
                x = json.loads(line)
                calls.append({"project": pid, "op": x["op"],
                              "cost": x["cost"], "error": x["error"]})
    _dump(os.path.join(out, "measurements.json"),
          {"calls": calls, "total_seconds": round(time.time() - started, 1),
           "model_tokens": 0,
           "note": "no model was in these loops; approx_tokens is the "
                   "size of each reply an agent would read (bytes / 4, "
                   "an estimate)"})
    shutil.rmtree(work)
    print(f"done in {time.time() - started:.0f} s -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
