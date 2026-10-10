"""One run of the agent improving a piece by ear and by reference, on the
Mega Drive — every step through python/agent.py, the way an agent calls it.

    python3 examples/agentic/references/make_reference_run.py \\
        --module PATH/TO/SATELL.S3M [--out DIR]

1.  a piece: four sections planned (theme, variation, development,
    cadence), the first three composed by the built-in composer
2.  two references of different character, kept with what they are for:
    a Mega Drive action track from corpus/core (Gunstar Heroes, "Military
    on the Max-Power": FM timbre, a lead's vibrato, a busy pulse; its .trk
    and FM bank) and a module given with --module (the run below used
    Purple Motion's "Satellite One", an S3M: a wide stereo field, voices
    placed apart — space and arrangement). Their sources' claims are
    checked against their audio
3.  two of the agent's own sketches: another draft of B (energy 0.95)
    and one of D (energy 0.9), kept as the direction for D. The built-in
    composer's energy sets the dynamics and, above 0.7, the bass's octave
    leaps (and an arpeggio's rate): the same notes, played stronger
4.  the piece before: MP3
5.  parallel listening on B: B (mix and voices), each reference's
    matching bars, the sketch of B — aligned on bars, loudness-matched,
    one reel with an index; observations with their basis
6.  one guided round on B: the gaps traced, hypotheses tested with A/B
    reels (reference, original, candidate), the kept change committed,
    the rest kept as rejected sketches with the reason
7.  the continuation: D composed, heard with C, the join and the chosen
    sketch of D
8.  the piece after: MP3; and B before, B after and the references as one
    aligned reel
9.  the audio-model check: whether an audio model that passed the probes
    is configured here (CHIPGEN_AUDIO_ENDPOINT and CHIPGEN_AUDIO_MODEL);
    if not, the boundary is written down and nothing is called heard
10. report.json (every reply) and report.md

The references and their excerpts are study material under their own
licences: they stay in the output folder, never in the branch.
"""

import argparse
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "python"))

from agent import Agent                     # noqa: E402
from agentic import ear as EAR              # noqa: E402
from agentic import listen as LI            # noqa: E402
from agentic import references as REF       # noqa: E402

GUNSTAR = "gunstar_heroes_mega_drive_genesis_04_military_on_the_max_power"
PLAN = [
    {"id": "A", "function": "statement", "bars": 4, "energy": 0.6,
     "harmony": ["Am", "F", "C", "G"]},
    {"id": "B", "function": "variation", "bars": 4, "energy": 0.7,
     "harmony": ["Am", "F", "C", "G"]},
    {"id": "C", "function": "development", "bars": 4, "energy": 0.8,
     "harmony": ["Dm", "Em", "F", "G"]},
    {"id": "D", "function": "cadence", "bars": 4, "energy": 0.6,
     "harmony": ["F", "G", "E", "Am"]}]
WORK = None


def _dump(path, obj):
    text = json.dumps(obj, indent=1, ensure_ascii=False) + "\n"
    if WORK:
        text = text.replace(json.dumps(WORK)[1:-1], "<out>")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _ok(reply):
    if "error" in reply:
        raise SystemExit(json.dumps(reply, indent=1))
    return reply


def _adapter():
    url = os.environ.get("CHIPGEN_AUDIO_ENDPOINT")
    if not url:
        return None
    import llm
    endpoint = llm.Endpoint(url=url,
                            model=os.environ.get("CHIPGEN_AUDIO_MODEL"))
    return EAR.OpenAIAudioAdapter(endpoint, declared=True)


def _render(agent, pid, out, name):
    p = agent._project(pid)
    r = _ok(agent.call("export", project_id=pid, format="mp3"))
    target = os.path.join(out, f"{name}.mp3")
    shutil.copy(r["path"], target)
    t = _ok(agent.call("export", project_id=pid, format="trk"))
    shutil.copy(t["path"], os.path.join(out, f"{name}.trk"))
    return {"mp3": target, "rev": p.head}


def main(argv=None) -> int:
    global WORK
    ap = argparse.ArgumentParser()
    ap.add_argument("--module", required=True,
                    help="a module to use as the second reference")
    ap.add_argument("--out", default=os.path.join(
        ROOT, "examples", "agentic", "_work", "reference_run"))
    ap.add_argument("--seed", type=int, default=3)
    a = ap.parse_args(argv)
    out = os.path.abspath(a.out)
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    WORK = out
    started = time.time()
    agent = Agent(os.path.join(out, "projects"), adapter=_adapter())
    report = {"steps": []}

    def step(name, reply):
        report["steps"].append({"step": name, "reply": reply})
        _dump(os.path.join(out, "report.json"), report)
        return reply

    # 1. the piece
    step("create", _ok(agent.call(
        "create_musical_state", preset="etude", plan=PLAN,
        project_id="piece",
        intent="an étude in A minor in four sections; it should move like "
               "a Mega Drive action track and its lead should sing")))
    step("compose A-C", _ok(agent.call(
        "continue_composition", project_id="piece", sections=3,
        seed=a.seed, hear=False)))
    # 2. the references
    core = os.path.join(ROOT, "corpus", "core")
    step("reference 1", _ok(agent.call(
        "add_reference", project_id="piece",
        path=os.path.join(core, "scores", GUNSTAR + ".trk"),
        bank=os.path.join(core, "banks", GUNSTAR + ".json"),
        tags=["timbre", "modulation", "rhythm"],
        note="Mega Drive action: an FM lead that sings with a vibrato on "
             "its held notes, over a busy pulse",
        licence="a transcription in this repository's corpus (study)",
        seconds=40)))
    step("reference 2", _ok(agent.call(
        "add_reference", project_id="piece", path=a.module,
        tags=["space", "arrangement"],
        note="a wide stereo field: voices placed apart, room around them",
        licence="Mod Archive distribution licence; personal study",
        seconds=40)))
    # 3. the sketches
    s1 = step("sketch of B", _ok(agent.call(
        "section_sketches", project_id="piece", section="B",
        energies=[0.95])))["sketches"][0]["id"]
    s2 = step("sketch of D", _ok(agent.call(
        "section_sketches", project_id="piece", section="D",
        energies=[0.9])))["sketches"][0]["id"]
    step("choose", _ok(agent.call(
        "choose_sketch", project_id="piece", sketch_id=s2,
        why="a stronger cadence (louder, the bass leaping octaves): the "
            "direction for D")))
    # 4. before
    report["before"] = _render(agent, "piece", out, "before")
    p = agent._project("piece")
    rev_before = p.head
    # 5. parallel listening
    step("listen B", _ok(agent.call(
        "listen_compare", project_id="piece", range="B",
        references=["ref1", "ref2"], sketches=[s1])))
    # 6. a guided round
    guided = step("improve B toward the references", _ok(agent.call(
        "improve_toward_reference", project_id="piece", range="B",
        references=["ref1", "ref2"], sketches=[s1])))
    step("reject the sketch of B", _ok(agent.call(
        "reject_sketch", project_id="piece", sketch_id=s1,
        why="compared side by side with B and the references; B was "
            "worked on instead (see the guided round)")))
    # 7. the continuation
    step("compose D, hear the continuation", _ok(agent.call(
        "continue_composition", project_id="piece", sections=1,
        hear=True, listen=True, sketches=[s2])))
    # 8. after, and B before/after with the references in one reel
    report["after"] = _render(agent, "piece", out, "after")
    p = agent._project("piece")
    frags = []
    for rid in ("ref1", "ref2"):
        reg = REF.get(p, rid)["regions"][0]
        frags.append(LI.of_reference(p, rid, reg["bars"][0], 4))
    frags.append(LI.of_project(p, "B", rev=rev_before,
                               label="B before"))
    frags.append(LI.of_project(p, "B", label="B after"))
    final = LI.write(frags, os.path.join(out, "ab"), "B_before_after")
    report["ab_final"] = {k: final[k] for k in ("reel", "fragments",
                                                "loudness", "path")}
    step("sketches at the end", _ok(agent.call(
        "list_sketches", project_id="piece")))
    # 9. the audio model
    report["audio_model_check"] = step("check listening",
                                       agent.call("check_listening"))
    _dump(os.path.join(out, "audio_model_check.json"),
          report["audio_model_check"])
    report["seconds"] = round(time.time() - started, 1)
    report["head"] = agent._project("piece").head
    report["revisions"] = agent._project("piece").state["revisions"]
    _dump(os.path.join(out, "report.json"), report)
    _write_md(out, report, guided)
    print(json.dumps({"out": out, "seconds": report["seconds"],
                      "head": report["head"]}, indent=1))
    return 0


def _write_md(out, report, guided):
    replies = {st["step"]: st["reply"] for st in report["steps"]}
    lines = ["# Improving a piece by reference: one run", ""]
    check = report["audio_model_check"]
    lines.append("**Listening:** " + (
        f"a model listened ({check.get('adapter')})" if check.get("available")
        else f"no audio model listened — {check.get('why')}. "
             f"{check.get('consequence')}."))
    lines.append("")
    lines.append(f"Before: `before.mp3` (revision "
                 f"{report['before']['rev']}); after: `after.mp3` "
                 f"(revision {report['after']['rev']}). B before and after "
                 f"next to the references, aligned on bars: "
                 f"`ab/B_before_after_reel.mp3`.")
    lines.append("")
    lines.append("## The references")
    for name in ("reference 1", "reference 2"):
        ref = (replies.get(name) or {}).get("reference") or {}
        if not ref:
            continue
        lines.append(f"- **{ref.get('id')}** {ref.get('title')} "
                     f"({ref.get('kind')}; {ref.get('engine')}), for "
                     f"{', '.join(ref.get('tags') or [])}: {ref.get('note')}")
        lines.append(f"  - grid: {(ref.get('grid') or {}).get('how')}")
        for c in ref.get("claims") or []:
            lines.append(f"  - the source says {c['claim']}: "
                         f"{c.get('verdict')} by its audio")
    lines.append("")
    lines.append("## What was observed on B")
    for o in guided["observations"]:
        lines.append(f"- [{o['basis']}] {o['statement']}")
    for n in guided.get("not_compared") or []:
        lines.append(f"- [not compared] {n['reading']} against "
                     f"{n['against']}: {n['why']}")
    lines.append("")
    lines.append("## What was tried, kept and rolled back")
    for w in guided["worked_on"]:
        lines.append(f"### {w['dimension']}: {w['statement']}")
        lines.append(f"Outcome: **{w['outcome']}** — {w.get('why')}")
        if w.get("note"):
            lines.append(f"(Note: {w['note']}.)")
        if w.get("goal"):
            lines.append("Goal: " + ", ".join(
                f"{k} {v:g}" for k, v in w["goal"].items())
                + (f" ({w['goal_note']})" if w.get("goal_note") else ""))
        for h in w["hypotheses"]:
            kept = "kept" if w.get("outcome") == "committed" and \
                w.get("chosen") == h.get("id") else "rolled back"
            what = h.get("principle") or h.get("parameter")
            lines.append(f"- {h.get('id')} ({what}): {kept} — {h.get('why')}"
                         + (f" A/B: `{os.path.relpath(h['ab_reel'], out)}`"
                            if h.get("ab_reel") else ""))
        lines.append("")
    cont = replies.get("compose D, hear the continuation") or {}
    for c in cont.get("continuations") or []:
        lines.append(f"## The continuation: {c['section']} after the "
                     f"section before it")
        j = c.get("join") or {}
        if j:
            lines.append(f"- the join: {j.get('loudness_change_lufs'):+} LU, "
                         f"onsets per beat {j.get('onsets_per_beat')}, "
                         f"centroid {j.get('centroid_hz')} Hz; the score's "
                         f"transition {'ok' if (j.get('score') or {}).get('ok') else 'with issues'}")
        for text in c.get("observations") or []:
            lines.append(f"- [measured] {text}")
        for sk in c.get("sketches") or []:
            lines.append(f"- sketch {sk['id']}: " + (
                "compared, its D played with the piece's instruments now"
                + (f" ({', '.join(sk['silent_in_it'])} silent in it: the "
                   f"piece gained that voice after the sketch)"
                   if sk.get("silent_in_it") else "")
                if sk["compared"] else f"not compared — {sk['why']}"))
        for text in c.get("against_sketches") or []:
            lines.append(f"  - [measured] {text}")
        if c.get("reel"):
            lines.append(f"- reel: `{os.path.relpath(c['reel'].get('mp3') or c['reel']['wav'], out)}`")
        lines.append("")
    sketches = (replies.get("sketches at the end") or {}).get("sketches")
    if sketches:
        lines.append("## The sketches kept")
        for sk in sketches:
            lines.append(f"- {sk['id']} {sk.get('label')}: {sk['status']}"
                         + (f" — {sk.get('reason') or sk.get('chosen_because') or ''}"))
        lines.append("")
    with open(os.path.join(out, "report.md"), "w",
              encoding="utf-8") as handle:
        handle.write("\n".join(lines).replace(out, "<out>") + "\n")


if __name__ == "__main__":
    sys.exit(main())
