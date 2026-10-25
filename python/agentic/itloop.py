"""itloop.py — the hearing loop on an Impulse Tracker module: played from
the top, a buried channel heard, a level or a pan changed in the module's
own header, the candidates heard side by side and one kept.

What carries over from the Mega Drive loop, and what does not:

    played      every render is the whole module from its first row, by
                libopenmpt (openmpt.py): an IT row depends on effect
                memory, envelopes mid-note and NNA tails, so there is no
                chased window here (audit H09, section 4); a module longer
                than `seconds` is cut after it is played, never started
                late
    voices      a channel is a voice; it is heard alone by muting the
                others in libopenmpt (the file is not touched), and the
                rest without it the same way
    measured    the channel's in-band margin over its notes' bodies
                (ear.in_band, the Tool ear's own measure), the mix's level
                and peak
    changed     the channel volume (0-64) and the channel pan (0-64) of the
                IT header, bytes 0x80 and 0x40 on: what Schism shows as the
                channel's volume and pan. Nothing else is written: the
                samples, the instruments' envelopes, NNA, the filters and
                the patterns' own commands are the module's, and a change
                to them is refused by name (`refuse`) — the Schism forge
                (branch claude/schism-chipgen) has those, this loop does
                not
    chosen      after the guards, the original and up to three candidates
                go to the ear in one stereo clip (ear.ab_choose); its
                choice is obeyed when it passed the music probes, recorded
                otherwise. Without a model the measurement decides, and
                says so

Each kept change is a new file beside the original (rev1.it, ...), with
its sha256; the original is never written.
"""

import hashlib
import math
import os
import time
from typing import Dict, List, Optional

from . import ear as EAR
from . import loop as L
from . import openmpt as O
from .state import AgenticError

RATE = 44100
CHANNEL_PAN, CHANNEL_VOLUME = 0x40, 0x80
#: what this loop does not change on a module, and why
REFUSED = {
    "instrument": "the instrument programs are the YM2612's; an IT "
                  "instrument is its samples and envelopes",
    "envelope": "an IT envelope is the instrument's (the Schism forge "
                "writes them); not changed here",
    "nna": "new-note actions decide which notes ring on; not changed here",
    "filter": "the resonant filter and its envelope are the instrument's",
    "sample": "the samples are the module's sound; the Schism forge "
              "resynthesises them, this loop does not",
    "notes": "the patterns are the composer's",
    "echo": "an echo on an IT is a delayed copy on a free channel (SDx); "
            "not written here",
}
PLATFORM = "Impulse Tracker module, played by libopenmpt (stereo)"


def refuse(op: str):
    why = REFUSED.get(op, "not a change this loop makes on a module")
    raise AgenticError("not_on_it", f"{op}: {why}", op=op)


# -- the module ----------------------------------------------------------------------
class Piece:
    """An IT module's bytes, read and edited in its header only."""

    def __init__(self, data: bytes, label: str = "original"):
        if data[:4] != b"IMPM":
            raise AgenticError("not_an_it", "this loop edits Impulse "
                               "Tracker modules (IMPM); another format is a "
                               "reference only")
        self.data = bytes(data)
        self.label = label
        with O.Module(self.data) as m:
            self.channels = m.channels

    @classmethod
    def open(cls, path: str) -> "Piece":
        with open(path, "rb") as handle:
            return cls(handle.read(), os.path.basename(path))

    @property
    def sha(self) -> str:
        return hashlib.sha256(self.data).hexdigest()[:16]

    def volume(self, ch: int) -> int:
        return self.data[CHANNEL_VOLUME + ch]

    def pan(self, ch: int) -> int:
        return self.data[CHANNEL_PAN + ch]

    def _with(self, offset: int, value: int, label: str) -> "Piece":
        b = bytearray(self.data)
        b[offset] = value
        p = Piece.__new__(Piece)
        p.data, p.label, p.channels = bytes(b), label, self.channels
        return p


def level(piece: Piece, ch: int, db: float) -> (Piece, dict):
    """The channel's volume moved by `db` (realised in the header's 0-64
    steps). Past 64 or to 0 is refused, not clamped."""
    v = piece.volume(ch)
    if v == 0:
        raise AgenticError("silent_channel", f"channel {ch + 1} has volume "
                           f"0: a level change cannot be heard", channel=ch)
    new = int(round(v * 10 ** (db / 20.0)))
    if not 1 <= new <= 64:
        raise AgenticError("past_range", f"channel {ch + 1}: volume {v} "
                           f"{db:+.1f} dB is {new}, outside 1-64 (the "
                           f"volume column and global volume are not "
                           f"changed here)", channel=ch)
    realised = 20 * math.log10(new / v)
    return piece._with(CHANNEL_VOLUME + ch, new,
                       f"ch{ch + 1} {realised:+.1f} dB"), {
        "op": "level", "channel": ch, "from": v, "to": new,
        "asked_db": db, "realised_db": round(realised, 2)}


def pan(piece: Piece, ch: int, position: float) -> (Piece, dict):
    """The channel's pan to `position` (-1 left .. 1 right); a channel
    switched off or in surround is refused."""
    v = piece.pan(ch)
    if v & 0x80 or v == 100:
        raise AgenticError("pan_locked", f"channel {ch + 1} is off or in "
                           f"surround (pan byte {v})", channel=ch)
    new = int(round(32 + 32 * max(-1.0, min(1.0, position))))
    return piece._with(CHANNEL_PAN + ch, new,
                       f"ch{ch + 1} pan {new}"), {
        "op": "pan", "channel": ch, "from": v, "to": new}


# -- playing and measuring --------------------------------------------------------------
def play(piece: Piece, keep: Optional[List[int]] = None,
         seconds: Optional[float] = None) -> dict:
    """The module from its first row (only `keep` heard, if given). ->
    {"samples" (interleaved stereo), "rows", "notes", "seconds"}."""
    with O.Module(piece.data, keep=keep) as m:
        samples, rows = m.render(RATE, seconds)
        dur = len(samples) / 2 / RATE
        notes = m.notes(rows, dur) if keep is None else None
    return {"samples": list(samples), "rows": rows, "notes": notes,
            "seconds": round(dur, 3),
            "playback": "full: from the module's first row (libopenmpt)"}


def _mono(stereo) -> list:
    return [0.5 * (stereo[i] + stereo[i + 1])
            for i in range(0, len(stereo) - 1, 2)]


def _db(x: float) -> float:
    return 20 * math.log10(x) if x > 1e-9 else -120.0


def measure(piece: Piece, channels: List[int], notes: List[dict],
            seconds: Optional[float] = None, mix: Optional[dict] = None
            ) -> dict:
    """Per channel in `channels`: alone, and the rest without it: the
    in-band margin over its notes, its level. And the mix's level and
    peak. `notes` are the module's (play(piece)['notes'])."""
    mix = mix or play(piece, seconds=seconds)
    m = _mono(mix["samples"])
    peak = max((abs(v) for v in mix["samples"]), default=0.0)
    rms = math.sqrt(sum(v * v for v in m) / len(m)) if m else 0.0
    out = {"mix": {"rms_db": round(_db(rms), 2), "peak_db":
                   round(_db(peak), 2), "peak": round(peak, 4)},
           "channels": {}, "renders": 1}
    stems = {}
    for ch in sorted(set(channels)):
        stems[ch] = _mono(play(piece, keep=[ch], seconds=seconds)["samples"])
        out["renders"] += 1
    for ch in channels:
        rest = [c for c in range(piece.channels) if c != ch]
        bg = _mono(play(piece, keep=rest, seconds=seconds)["samples"])
        out["renders"] += 1
        mine = [(n["start"], n["end"], EAR.note_hz(n["pitch"]))
                for n in notes if n["channel"] == ch and
                n.get("pitch") is not None]
        solo = stems[ch]
        r = math.sqrt(sum(v * v for v in solo) / len(solo)) if solo else 0.0
        entry = {"voice": f"ch{ch + 1}", "notes": len(mine),
                 "rms_db": round(_db(r), 2),
                 "volume": piece.volume(ch), "pan": piece.pan(ch)}
        if mine:
            band = EAR.in_band(solo, bg, mine, RATE,
                               others={c: s for c, s in stems.items()
                                       if c != ch})
            entry["band_margin_db"] = band["band_margin_db"]
        out["channels"][ch] = entry
    return out


# -- the loop ----------------------------------------------------------------------------
def _candidates(piece: Piece, lead: int, others: List[int]) -> List[dict]:
    """Level and pan changes that could bring the lead out: the lead up,
    the channels that fill its band down, both; a pan apart."""
    out = []
    plans = [("lead +3 dB", [(lead, 3.0)]), ("lead +6 dB", [(lead, 6.0)])]
    # one channel at a time first: the one that fills the lead's band can
    # come down without burying the others
    for c in others:
        plans += [(f"ch{c + 1} -3 dB", [(c, -3.0)]),
                  (f"ch{c + 1} -6 dB", [(c, -6.0)])]
    plans += [("the rest -3 dB", [(c, -3.0) for c in others]),
              ("lead +3, the rest -3 dB",
               [(lead, 3.0)] + [(c, -3.0) for c in others])]
    for name, steps in plans:
        p, deltas = piece, []
        try:
            for ch, db in steps:
                p, d = level(p, ch, db)
                deltas.append(d)
        except AgenticError as error:
            out.append({"id": name, "refused": error.to_dict()})
            continue
        out.append({"id": name, "piece": p, "changes": deltas})
    if others:
        try:
            p, d = pan(piece, others[0], -0.6 if piece.pan(lead) >= 32
                       else 0.6)
            out.append({"id": f"ch{others[0] + 1} panned away",
                        "piece": p, "changes": [d]})
        except AgenticError as error:
            out.append({"id": "pan", "refused": error.to_dict()})
    return out


def correct_buried(path: str, lead: int, folder: str, ear=None,
                   seconds: Optional[float] = None,
                   min_gain_db: float = 1.0) -> dict:
    """The lead channel (0-based) brought out of the channels that fill
    its band, by the header's levels and pans: each candidate played from
    the top and measured, guarded, the survivors chosen among by the ear
    (or, without one, by the measurement). -> the record; a kept change is
    written to `folder` as rev1.it beside an A/B reel."""
    from . import listen as LI
    started = time.time()
    os.makedirs(folder, exist_ok=True)
    base = Piece.open(path)
    played = play(base, seconds=seconds)
    notes = played["notes"]
    busy = sorted({n["channel"] for n in notes})
    if lead not in busy:
        raise AgenticError("silent_channel", f"channel {lead + 1} plays no "
                           f"note", channel=lead)
    others = [c for c in busy if c != lead]
    before = measure(base, busy, notes, seconds, played)
    renders = before["renders"]
    margin0 = before["channels"][lead].get("band_margin_db")
    record = {"module": os.path.basename(path), "sha256": base.sha,
              "lead": f"ch{lead + 1}", "playback": played["playback"],
              "chase": "none: an IT row depends on effect memory, envelopes "
                       "and NNA tails, so every render starts at the top",
              "before": {"band_margin_db": margin0,
                         "mix": before["mix"]},
              "candidates": [], "listening": EAR.listening(ear)}
    if margin0 is None:
        raise AgenticError("no_notes_to_measure", f"ch{lead + 1}: no note "
                           f"long enough to read in its band")
    if margin0 >= EAR.BURIED_BODY_DB:
        record.update(outcome="nothing_to_do", why=f"ch{lead + 1} reads "
                      f"{margin0:+.1f} dB in its band, not buried")
        return record
    tried = []
    for c in _candidates(base, lead, others):
        cand = {"id": c["id"]}
        if "refused" in c:
            cand.update(accepted=False, why=f"refused: "
                        f"{c['refused']['message']}")
            record["candidates"].append(cand)
            continue
        mix = play(c["piece"], seconds=seconds)
        m = measure(c["piece"], busy, notes, seconds, mix)
        renders += m["renders"]
        margin = m["channels"][lead].get("band_margin_db")
        guards = [{"name": "no_clipping", "ok": m["mix"]["peak"] < 0.98,
                   "detail": f"peak {m['mix']['peak_db']:+.1f} dBFS"},
                  {"name": "mix_level", "ok": abs(m["mix"]["rms_db"] -
                                                   before["mix"]["rms_db"])
                   <= L.MIX_LEVEL_LIMIT_DB, "detail": f"mix {before['mix']['rms_db']:+.1f} "
                   f"-> {m['mix']['rms_db']:+.1f} dB"}]
        # a channel the candidate turned down was asked to be quieter: its
        # own margin is reported, not guarded (the Mega Drive loop guards
        # the melodic roles only; a module has no roles). Every channel it
        # left alone may not end up buried
        lowered = {d["channel"] for d in c["changes"]
                   if d["op"] == "level" and d["to"] < d["from"]}
        newly = [f"ch{ch + 1}" for ch in others if ch not in lowered and
                 (before["channels"][ch].get("band_margin_db") or -99)
                 >= EAR.BURIED_BODY_DB > (m["channels"][ch].get(
                     "band_margin_db") or -99)]
        guards.append({"name": "no_new_buried", "ok": not newly,
                       "detail": ", ".join(newly) or "none"})
        cand["lowered"] = {f"ch{ch + 1}": {
            "band_margin_db": [before["channels"][ch].get("band_margin_db"),
                               m["channels"][ch].get("band_margin_db")]}
            for ch in sorted(lowered)}
        gain = margin - margin0 if margin is not None else None
        cand.update(changes=c["changes"], band_margin_db=margin,
                    gain_db=None if gain is None else round(gain, 2),
                    guards=guards, sha256=c["piece"].sha)
        failed = [g for g in guards if not g["ok"]]
        if failed:
            cand.update(accepted=False, why="guard failed: " + "; ".join(
                f"{g['name']} ({g['detail']})" for g in failed))
        elif gain is None or gain < min_gain_db:
            cand.update(accepted=False, why=f"the lead moved "
                        f"{gain or 0:+.1f} dB in its band, under "
                        f"{min_gain_db:g}")
        else:
            cand.update(accepted=True, why=f"the lead {margin0:+.1f} -> "
                        f"{margin:+.1f} dB in its band, every guard kept")
        cand["_piece"], cand["_mix"] = c["piece"], mix
        record["candidates"].append(cand)
        tried.append(cand)
    ok = sorted((c for c in tried if c.get("accepted")),
                key=lambda c: -c["gain_db"])
    best, verdict, judged = (ok[0] if ok else None), None, "measured"
    if ok and EAR.listening(ear)["available"]:
        top = ok[:3]
        versions = [("the module as it is", played["samples"])] + [
            (f"{c['id']}", c["_mix"]["samples"]) for c in top]
        verdict = EAR.ab_choose(ear, versions, goal=f"ch{lead + 1}, the "
                                f"lead, heard clearly over the rest",
                                voices=[f"ch{lead + 1}"], rate=RATE,
                                stereo=True, platform=PLATFORM)
        trust = EAR.trusted_to_choose(ear)
        verdict["trusted"] = trust
        if not trust["trusted"]:
            verdict = dict(verdict, obeyed=False,
                           recorded_choice=verdict.get("choice"),
                           choice=None)
        elif verdict.get("choice") == 0:
            best, judged = None, "heard by model"
        elif verdict.get("choice") is not None:
            best, judged = top[verdict["choice"] - 1], "heard by model"
        else:
            judged = "measured; the model uncertain"
    record["listening_verdict"] = verdict
    record["judged_by"] = judged
    if best is None:
        record.update(outcome="rolled_back", why=(
            "the ear chose the module as it is" if verdict and
            verdict.get("choice") == 0 else
            "no candidate brought the lead out with every guard kept"))
    else:
        out = os.path.join(folder, "rev1.it")
        with open(out, "wb") as handle:
            handle.write(best["_piece"].data)
        frags = []
        for label, samples in (("before", played["samples"]),
                               (f"after: {best['id']}",
                                best["_mix"]["samples"])):
            frags.append({"label": label, "kind": "ours",
                          "source": record["module"], "bars": None,
                          "seconds": round(len(samples) / 2 / RATE, 3),
                          "t0": 0.0, "t1": round(len(samples) / 2 / RATE, 3),
                          "samples": samples, "playback": played["playback"]})
        reel = LI.write(frags, folder, "it_ab", mp3=False, raw=True)
        record.update(outcome="committed", chosen=best["id"],
                      changes=best["changes"], file=out,
                      sha256_after=best["sha256"],
                      after={"band_margin_db": best["band_margin_db"]},
                      ab_reel=reel["reel"]["wav"],
                      ab_reel_raw=reel["reel_raw"]["wav"],
                      why=best["why"] + (f"; chosen by the ear"
                                         if judged == "heard by model"
                                         else "; chosen by the measurement"))
    for c in record["candidates"]:
        c.pop("_piece", None)
        c.pop("_mix", None)
    record["cost"] = {"renders": renders,
                      "runtime_s": round(time.time() - started, 2)}
    return record
