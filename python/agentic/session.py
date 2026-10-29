"""session.py — what was actually sent to a listening model, and what came
back: the audible session of a project (audit section 5).

Every clip an audio adapter is sent goes through `RecordingAdapter`, which
keeps

    the clip    by its content: audible/clips/<sha256[:16]>.wav (one copy
                however often it is sent), with its channels, rate,
                seconds and format read from the WAV itself — what the
                model received, not what a JSON says it should have
    the call    when, why (the probe, a hearing, a choice among versions,
                a reel), the prompt's length, the reply, how long it took,
                and the tokens if the endpoint reported them (else the
                characters, said to be characters)

one line each in audible/session.jsonl. `summary` adds them up (the cost
for T10: calls, audio seconds sent, characters); `contract` lays the
project's decisions out in the observation/action shape of the audit's
section 6 — clip, observation, action plan, experiment — each clip named
by the hash of the file in audible/clips, or "none" when no model heard
it. Nothing here says a model heard anything the log does not show it was
sent.
"""

import hashlib
import io
import json
import os
import time
import wave
from typing import List, Optional

FOLDER = "audible"


def _purpose(prompt: str) -> str:
    p = prompt.lower()
    if "versions of the same passage" in p:
        return "choose among versions"
    if "fragments of chip or tracker" in p:
        return "compare a reel"
    if "listening to" in p and "chiptune piece" in p:
        return "hear a range"
    return "probe"


def wav_facts(wav: bytes) -> dict:
    with wave.open(io.BytesIO(wav)) as w:
        n, rate = w.getnframes(), w.getframerate()
        return {"format": "wav", "channels": w.getnchannels(),
                "rate": rate, "bits": 8 * w.getsampwidth(),
                "seconds": round(n / rate, 3) if rate else 0.0}


class RecordingAdapter:
    """An audio adapter that keeps what passes through it. Everything else
    (name, declares_audio) is the wrapped adapter's."""

    def __init__(self, adapter, root: str, revision=None):
        self.adapter = adapter
        self.root = root
        self.revision = revision
        os.makedirs(os.path.join(root, FOLDER, "clips"), exist_ok=True)

    @property
    def name(self) -> str:
        return self.adapter.name

    def declares_audio(self) -> bool:
        return self.adapter.declares_audio()

    def listen(self, wav: bytes, prompt: str) -> str:
        sha = hashlib.sha256(wav).hexdigest()[:16]
        path = os.path.join(self.root, FOLDER, "clips", f"{sha}.wav")
        if not os.path.exists(path):
            with open(path, "wb") as handle:
                handle.write(wav)
        entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "adapter": self.adapter.name, "clip": sha,
                 **wav_facts(wav), "purpose": _purpose(prompt),
                 "revision": self.revision, "prompt_chars": len(prompt)}
        started = time.time()
        try:
            reply = self.adapter.listen(wav, prompt)
        except Exception as error:
            entry.update(error=str(error)[:200],
                         seconds_waited=round(time.time() - started, 3))
            self._log(entry)
            raise
        entry.update(reply=str(reply)[:400], reply_chars=len(str(reply)),
                     seconds_waited=round(time.time() - started, 3))
        usage = getattr(self.adapter, "last_usage", None)
        if usage:
            entry["tokens"] = usage
        self._log(entry)
        return reply

    def _log(self, entry: dict):
        with open(os.path.join(self.root, FOLDER, "session.jsonl"), "a",
                  encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def entries(root: str) -> List[dict]:
    path = os.path.join(root, FOLDER, "session.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def summary(root: str) -> dict:
    """The cost of listening so far: calls, audio seconds and clips sent,
    by purpose; characters (tokens when the endpoint gave them)."""
    es = entries(root)
    by = {}
    for e in es:
        b = by.setdefault(e["purpose"], {"calls": 0, "audio_seconds": 0.0,
                                         "channels": set()})
        b["calls"] += 1
        b["audio_seconds"] = round(b["audio_seconds"] + e["seconds"], 3)
        b["channels"].add(e["channels"])
    for b in by.values():
        b["channels"] = sorted(b["channels"])
    return {"calls": len(es),
            "audio_seconds": round(sum(e["seconds"] for e in es), 3),
            "clips": len({e["clip"] for e in es}),
            "errors": sum(1 for e in es if "error" in e),
            "prompt_chars": sum(e["prompt_chars"] for e in es),
            "reply_chars": sum(e.get("reply_chars", 0) for e in es),
            "tokens": [e["tokens"] for e in es if e.get("tokens")] or
            "not reported by the endpoint",
            "by_purpose": by,
            "heard": bool(es) and any(e["purpose"] != "probe" for e in es)}


def contract(project, last: int = 10) -> List[dict]:
    """The project's last decisions in the audit's observation/action
    shape (section 6). A decision a model took part in names its clip by
    the hash kept in audible/clips; one it did not says so."""
    sent = [e for e in entries(project.root) if e["purpose"] != "probe"]
    out = []
    for d in project.state.get("decisions", [])[-last:]:
        v = d.get("listening_verdict") or {}
        clip_sha = v.get("clip_sha")
        clip = next((e for e in sent if e["clip"] == clip_sha), None)
        obs = d.get("observation") or {}
        if isinstance(obs, str):
            obs = {"statement": obs}
        ins = d.get("instrument") or {}
        out.append({
            "decision": d.get("id"),
            "clip": {"hash": clip["clip"], "channels": clip["channels"],
                     "seconds": clip["seconds"], "format": clip["format"],
                     "revision": clip.get("revision"),
                     "purpose": clip["purpose"]} if clip else
            {"hash": None, "why": "no model heard this decision" if not v
             else "the clip was not sent through this session's recorder"},
            "observation": {"basis": obs.get("basis") or
                            d.get("judged_by") or "measured",
                            "category": obs.get("category"),
                            "voices": d.get("voices"),
                            "statement": obs.get("statement"),
                            "confidence": obs.get("confidence"),
                            "cause_is_hypothesis": True},
            "action_plan": {"kind": d.get("kind"),
                            "voices": d.get("voices"),
                            "range": (d.get("range") or {}).get("spec"),
                            "preserve": ["notes", "rhythm", "harmony"]
                            if d.get("kind") in ("instrument", "correction",
                                                 "guided") else None,
                            "intent": (ins.get("intent") or {}).get(
                                "clauses")},
            "experiment": {"candidates": len(ins.get("candidates", []))
                           or len(d.get("rounds", []) or []),
                           "approval": d.get("judged_by") or "measured",
                           "choice": v.get("choice"),
                           "obeyed": v.get("obeyed", v.get("choice")
                                           is not None),
                           "outcome": d.get("outcome")}})
    return out
