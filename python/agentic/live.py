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

A model that hears a file put in front of it — a multimodal chat, an
agent that runs this repository itself — needs no endpoint: `export`
writes a blind packet (the tone probes, the music probes and the four
questions, each a WAV and its question as the endpoint would be sent
them, under neutral numbers; each packet with its own order of items and
of candidates and its own level, a fraction of a dB down) and keeps the key outside
it; the model answers in `answers.json`, saying for each whether it
listened or analysed the file with code; `score` reads the answers
against the key. Only answers that were listened to count as hearing.
"""

import array
import copy
import hashlib
import json
import os
import random
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


#: the four questions: (name, versions, goal, the version that is right;
#: "not copy" — anything but the copy, "uncertain" included)
PLAN = [
    ("T1 change", ["original", "copy", "vibrato"], GOAL_VIBRATO, "vibrato"),
    ("T1 identity", ["original", "copy"], GOAL_VIBRATO, "not copy"),
    ("T3 choice", ["original", "vibrato", "soft"], GOAL_SOFT, "soft"),
    ("T3 swapped", ["original", "soft", "vibrato"], GOAL_SOFT, "soft"),
]


def _right(want: str, picked: Optional[str]) -> bool:
    if want == "not copy":
        return picked != "copy"          # "uncertain" is right there
    return picked == want


def _labels(keys):
    # neutral labels: the model hears which one does it, it is not told
    return ["the piece as it is" if i == 0 else f"candidate {chr(64 + i)}"
            for i in range(len(keys))]


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
    out = {"adapter": adapter.name, "verified": verification.get("verified"),
           "music_probes": (verification.get("music") or {}).get("probes"),
           "trusted": EAR.trusted_to_choose(ear) if ear else
           {"trusted": False, "why": verification.get("why")},
           "platform": plat["name"], "questions": []}
    if ear is None:
        out["why"] = ("the model did not pass the tone probes: it was not "
                      "asked about music")
        return out
    for name, keys, goal, want in PLAN:
        versions = [(label, v[k]) for label, k in zip(_labels(keys), keys)]
        verdict = EAR.ab_choose(ear, versions, goal, voices=["lead"],
                                stereo=plat["stereo"],
                                platform=plat["name"])
        choice = verdict.get("choice")
        picked = keys[choice] if choice is not None else None
        ok = _right(want, picked)
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


# -- the packet: a model that hears a file put in front of it ---------------------------------
PACKET_README = """# Listening packet

Each item is a WAV file and a question about it. Listen to each file and
answer from what you hear.

- Answer every item in answers.json (a copy of answers_template.json):
  "answer" is your reply, exactly as the question asks for it.
- "how" says how you answered: "listened" if you heard the audio,
  "analysed" if you opened it with code or measured it instead. Both are
  accepted; only "listened" counts as hearing. Say which it was.
- "uncertain" is an accepted answer where you cannot tell.
- The items are in no particular order, and nothing in a file's name or
  in this folder says what the answer is.
"""


def _plan(rnd: random.Random):
    """PLAN with the candidates in an order of this packet's own: each
    packet puts the right version somewhere else, so the answers to one
    are no use in another. T3 swapped stays T3 choice reversed."""
    out = []
    t3 = None
    for name, keys, goal, want in PLAN:
        rest = keys[1:]
        if name == "T3 swapped" and t3:
            rest = list(reversed(t3))
        else:
            rest = rnd.sample(rest, len(rest))
        if name == "T3 choice":
            t3 = rest
        out.append((name, [keys[0]] + rest, goal, want))
    return out


def _level(wav: bytes, db: float) -> bytes:
    """The WAV `db` quieter: no packet is byte for byte another's."""
    import io
    import wave
    with wave.open(io.BytesIO(wav)) as handle:
        params = handle.getparams()
        pcm = array.array("h", handle.readframes(params.nframes))
    f = 10 ** (-db / 20.0)
    pcm = array.array("h", (int(round(x * f)) for x in pcm))
    out = io.BytesIO()
    with wave.open(out, "wb") as handle:
        handle.setparams(params)
        handle.writeframes(pcm.tobytes())
    return out.getvalue()


PROMPT_HEAD = """# Listening test — answer by ear only

Attached are 14 audio files, q01.wav to q14.wav. For each, listen to the audio and answer its question below.

Rules:
- Do NOT run code, open the files in Python or any analyser, or measure them. Answer only from what you hear.
- If you cannot hear audio directly (only through code), say so and do not answer.
- "uncertain" is allowed where you cannot tell.

Reply with one JSON object: {"q01": {"answer": "...", "how": "listened"}, ...}. For the questions that ask for JSON, put that JSON as the answer.
"""


def export(folder: str, seed: Optional[int] = None) -> dict:
    """Write the blind packet to `folder`/packet and its key to
    `folder`/key.json. Give the model the packet only. Each packet has
    its own order of items, its own order of the candidates and its own
    level (0.1-1.0 dB down per file), so nothing carries over from one to
    the next. -> a summary."""
    packet = os.path.join(folder, "packet")
    os.makedirs(packet, exist_ok=True)
    rnd = random.Random(seed if seed is not None else
                        int.from_bytes(os.urandom(8), "big"))
    project = Project.create(os.path.join(folder, "project"),
                             fixtures.held_lead())
    cache = R.Cache(project.path("renders"))
    v, plat, _base = _versions(project, cache)
    items = []
    for k, (freqs, question, accepted) in enumerate(EAR.PROBES):
        items.append({"kind": "tone probe", "name": f"tone {k + 1}",
                      "wav": EAR._tone(freqs), "question": question,
                      "accepted": sorted(accepted)})
    for name, kind, question, accepted in EAR.MUSIC_PROBES:
        items.append({"kind": "music probe", "name": name,
                      "wav": EAR._music_clip(kind), "question": question,
                      "accepted": sorted(accepted)})
    for name, keys, goal, want in _plan(rnd):
        versions = [(label, v[k]) for label, k in zip(_labels(keys), keys)]
        wav, prompt, marks = EAR.choose_request(
            versions, goal, stereo=plat["stereo"], platform=plat["name"])
        items.append({"kind": "question", "name": name, "wav": wav,
                      "question": prompt, "versions": keys, "want": want,
                      "seconds": marks["seconds"]})
    rnd.shuffle(items)
    for it in items:
        it["level_db"] = round(rnd.uniform(0.1, 1.0), 2)
        it["wav"] = _level(it["wav"], it["level_db"])
    questions, key, template = [], {}, {}
    for n, it in enumerate(items, 1):
        qid = f"q{n:02d}"
        name = f"{qid}.wav"
        with open(os.path.join(packet, name), "wb") as handle:
            handle.write(it["wav"])
        questions.append({"id": qid, "file": name,
                          "question": it["question"]})
        template[qid] = {"answer": "", "how": "listened"}
        key[qid] = {k: x for k, x in it.items()
                    if k not in ("wav", "question")}
        key[qid]["sha256"] = hashlib.sha256(it["wav"]).hexdigest()
    with open(os.path.join(packet, "questions.json"), "w") as handle:
        json.dump(questions, handle, indent=1)
    with open(os.path.join(packet, "answers_template.json"), "w") as handle:
        json.dump(template, handle, indent=1)
    with open(os.path.join(packet, "README.md"), "w") as handle:
        handle.write(PACKET_README)
    # everything in one paste, for a chat the WAVs are attached to
    prompt = [PROMPT_HEAD] + [f"## {q['id']} — {q['file']}\n\n"
                              f"{q['question']}\n" for q in questions]
    with open(os.path.join(packet, "PROMPT.md"), "w") as handle:
        handle.write("\n".join(prompt))
    with open(os.path.join(folder, "key.json"), "w") as handle:
        json.dump({"platform": plat["name"], "items": key}, handle,
                  indent=1)
    return {"packet": packet, "key": os.path.join(folder, "key.json"),
            "items": len(items),
            "audio_seconds": round(sum(it.get("seconds", 0) or 0
                                       for it in items), 1),
            "give_the_model": "the packet folder only, never key.json"}


def _word(reply) -> str:
    text = str(reply or "").strip().lower()
    return text.split()[0].strip(".,!\"'") if text else ""


def score(folder: str, answers: Optional[str] = None) -> dict:
    """The packet's answers against its key. -> the probes, the four
    questions as `run` reports them, how each was answered, and whether
    it counts as hearing: every probe right and every answer listened."""
    with open(os.path.join(folder, "key.json")) as handle:
        key = json.load(handle)["items"]
    path = answers or os.path.join(folder, "packet", "answers.json")
    with open(path) as handle:
        given = json.load(handle)
    out = {"probes": {}, "questions": [], "how": {}, "changed_files": [],
           # a packet is answered wherever the model is; how is its own
           # word. Only `run` (input_audio through the adapter, logged by
           # the audible session) is a channel this code has seen.
           "channel": "packet: how each item was answered is the model's "
                      "own word, not verified"}
    for qid, k in sorted(key.items()):
        a = given.get(qid) or {}
        if not isinstance(a, dict):
            a = {"answer": a}
        how = str(a.get("how", "")).strip().lower() or "not said"
        out["how"][how] = out["how"].get(how, 0) + 1
        wav = os.path.join(folder, "packet", f"{qid}.wav")
        if os.path.exists(wav):
            with open(wav, "rb") as handle:
                if hashlib.sha256(handle.read()).hexdigest() != k["sha256"]:
                    out["changed_files"].append(qid)
        if k["kind"] != "question":
            out["probes"][k["name"]] = {
                "kind": k["kind"], "item": qid, "how": how,
                "reply": str(a.get("answer", ""))[:60],
                "right": _word(a.get("answer")) in k["accepted"]}
            continue
        reply = a.get("answer")
        reply = json.dumps(reply) if isinstance(reply, dict) else \
            str(reply or "")
        rec = {"question": k["name"], "item": qid, "versions": k["versions"],
               "how": how}
        try:
            if _word(reply) == "uncertain":
                verdict = {"choice": None}
            else:
                verdict = EAR.read_choice(reply, len(k["versions"]))
            picked = k["versions"][verdict["choice"]] \
                if verdict["choice"] is not None else None
            rec.update(picked=picked or "uncertain",
                       right=_right(k["want"], picked),
                       confidence=verdict.get("confidence"),
                       statement=verdict.get("statement"))
        except Exception as error:
            rec.update(picked=None, right=False,
                       problem=f"no usable answer: {str(error)[:120]}")
        out["questions"].append(rec)
    order = [p[0] for p in PLAN]
    out["questions"].sort(key=lambda q: order.index(q["question"]))
    probes_ok = all(p["right"] for p in out["probes"].values())
    out["passed"] = {
        "probes": probes_ok,
        "T1": all(q["right"] for q in out["questions"]
                  if q["question"].startswith("T1")),
        "T3": all(q["right"] for q in out["questions"]
                  if q["question"].startswith("T3"))}
    listened = out["how"].get("listened", 0) == len(key)
    # hearing as the model declares it; run() is the verified channel
    out["counts_as_hearing"] = bool(
        probes_ok and listened and not out["changed_files"] and
        out["passed"]["T1"] and out["passed"]["T3"])
    if not listened:
        out["why"] = ("not every item was listened to: answers worked out "
                      "with code are measurement, not hearing")
    elif out["changed_files"]:
        out["why"] = "packet files changed after export"
    with open(os.path.join(folder, "score.json"), "w") as handle:
        json.dump(out, handle, indent=1)
    return out
