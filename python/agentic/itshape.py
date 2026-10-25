"""itshape.py — a voice's sound on an Impulse Tracker module said phase by
phase (intent.py) and made with what IT itself has: the instrument's
volume envelope, its filter, the sample's auto-vibrato.

    attack     sharper or softer: the volume envelope's first nodes (a
               ramp from 0 over k ticks, or the ramp taken away)
    body       darker: a filter envelope (the pitch envelope with its filter
               bit) open through the attack's window and closing after it,
               or the instrument's initial cutoff (IFC) for the whole note
               — the resonant low-pass Schism and libopenmpt both play;
               brighter only by opening a filter that is already there —
               the sample is as bright as it was rendered, and the reply
               says so
    held       a vibrato on the long notes: the sample's auto-vibrato with
               a slow sweep (its depth grows over the note, so a short note
               ends before it is heard)
    release    longer or shorter: the volume envelope's last node after
               its sustain point (the note-off)

Each candidate is a new module (the original is never written), played
from its first row by libopenmpt, the voice's channel alone and in the
mix; its notes read by features.note_profile — the same readings the
Mega Drive path uses — so the phases named move and the ones not named
are held (intent.to_goal / report). The ear chooses among those that kept
every guard when it passed the music probes.

An instrument plays wherever the patterns use it: the reply names every
channel that plays it, and those channels are measured too.

A module in sample mode (no instruments) is given one instrument per
sample first (`with_instruments`), which plays the same (checked: a null
of -150 dB on the export of fixtures.masked_lead).
"""

import os
import struct
import time
from typing import Dict, List, Optional

from . import ear as EAR
from . import features as F
from . import intent as INT
from . import itloop as IT
from . import loop as L
from .instrument import READINGS
from .state import AgenticError

INSTRUMENT_BYTES = 554
VOL_ENV, PITCH_ENV = 0x130, 0x1D4
IFC, IFR = 0x3A, 0x3B
#: the sample header's auto-vibrato: speed, depth, sweep (rate), waveform
VIB_SPEED, VIB_DEPTH, VIB_RATE, VIB_TYPE = 0x4C, 0x4D, 0x4E, 0x4F


def _u16(d, o):
    return struct.unpack_from("<H", d, o)[0]


def _u32(d, o):
    return struct.unpack_from("<I", d, o)[0]


def layout(data: bytes) -> dict:
    o, i, s, p = (_u16(data, x) for x in (0x20, 0x22, 0x24, 0x26))
    t = 0xC0 + o
    return {"orders": o, "instruments": i, "samples": s, "patterns": p,
            "ins_ptrs": t, "smp_ptrs": t + 4 * i,
            "pat_ptrs": t + 4 * i + 4 * s, "end": t + 4 * (i + s + p),
            "instrument_mode": bool(_u16(data, 0x2C) & 4)}


def instrument_at(data: bytes, k: int) -> int:
    """The file offset of instrument k (0-based)."""
    L_ = layout(data)
    return _u32(data, L_["ins_ptrs"] + 4 * k)


def sample_at(data: bytes, k: int) -> int:
    L_ = layout(data)
    return _u32(data, L_["smp_ptrs"] + 4 * k)


def _blank_instrument(sample_number: int, name: str) -> bytes:
    b = bytearray(INSTRUMENT_BYTES)
    b[0:4] = b"IMPI"
    b[0x17] = 60
    b[0x18] = 128
    b[0x19] = 32 | 0x80
    struct.pack_into("<H", b, 0x1C, 0x0214)
    b[0x1E] = 1
    text = name.encode("ascii", "replace")[:25]
    b[0x20:0x20 + len(text)] = text
    b[0x3D] = 0xFF
    struct.pack_into("<H", b, 0x3E, 0xFFFF)
    for n in range(120):
        b[0x40 + 2 * n] = n
        b[0x41 + 2 * n] = sample_number
    for k, env in enumerate((0x130, 0x182, 0x1D4)):
        b[env + 1] = 2
        level = 64 if k == 0 else 0
        b[env + 6] = level
        b[env + 9] = level
        struct.pack_into("<H", b, env + 10, 100)
    return bytes(b)


def with_instruments(data: bytes) -> bytes:
    """A sample-mode module with one instrument per sample, envelopes off
    and no fadeout: it plays as it did. A module in instrument mode comes
    back as it is."""
    L_ = layout(data)
    if L_["instrument_mode"]:
        return data
    if L_["instruments"]:
        raise AgenticError("odd_module", "a sample-mode module that lists "
                           "instruments; not rewritten")
    n = L_["samples"]
    delta = 4 * n
    head = bytearray(data[:L_["ins_ptrs"]])
    struct.pack_into("<H", head, 0x22, n)
    struct.pack_into("<H", head, 0x2C, _u16(data, 0x2C) | 4)
    struct.pack_into("<H", head, 0x2A, max(0x200, _u16(data, 0x2A)))
    if _u32(data, 0x38):
        struct.pack_into("<I", head, 0x38, _u32(data, 0x38) + delta)
    smp = [_u32(data, L_["smp_ptrs"] + 4 * k) + delta for k in range(n)]
    pat = [_u32(data, L_["pat_ptrs"] + 4 * k) for k in range(L_["patterns"])]
    pat = [x + delta if x else 0 for x in pat]
    body = bytearray(data[L_["end"]:])
    for k in range(n):                # each sample header's data pointer
        at = _u32(data, L_["smp_ptrs"] + 4 * k) - L_["end"] + 0x48
        struct.pack_into("<I", body, at, _u32(body, at) + delta)
    total = len(head) + 4 * n + 4 * n + 4 * L_["patterns"] + len(body)
    out = bytearray(head)
    out += b"".join(struct.pack("<I", total + INSTRUMENT_BYTES * k)
                    for k in range(n))
    out += b"".join(struct.pack("<I", x) for x in smp)
    out += b"".join(struct.pack("<I", x) for x in pat)
    out += body
    for k in range(n):
        so = _u32(data, L_["smp_ptrs"] + 4 * k)
        name = data[so + 0x14:so + 0x2E].split(b"\0")[0].decode(
            "ascii", "replace")
        out += _blank_instrument(k + 1, name)
    return bytes(out)


# -- reading and writing an instrument's parts ------------------------------------------
def envelope(data: bytes, k: int) -> dict:
    at = instrument_at(data, k) + VOL_ENV
    n = data[at + 1]
    return {"on": bool(data[at] & 1), "sustain": bool(data[at] & 4),
            "sustain_node": data[at + 4],
            "nodes": [(data[at + 6 + 3 * j], _u16(data, at + 7 + 3 * j))
                      for j in range(n)]}


def set_envelope(data: bytes, k: int, nodes, sustain_node=None) -> bytes:
    if not 2 <= len(nodes) <= 25:
        raise AgenticError("bad_envelope", "an IT envelope has 2-25 nodes")
    ticks = [t for _y, t in nodes]
    if ticks != sorted(ticks) or len(set(ticks)) != len(ticks) or \
            ticks[0] != 0 or ticks[-1] > 9999:
        raise AgenticError("bad_envelope", f"node ticks {ticks}: they start "
                           f"at 0 and rise")
    b = bytearray(data)
    at = instrument_at(data, k) + VOL_ENV
    b[at] = 1 | (4 if sustain_node is not None else 0)
    b[at + 1] = len(nodes)
    b[at + 4] = b[at + 5] = sustain_node or 0
    for j in range(25):
        y, t = nodes[j] if j < len(nodes) else (0, 0)
        if not 0 <= y <= 64:
            raise AgenticError("bad_envelope", f"level {y} outside 0-64")
        b[at + 6 + 3 * j] = y
        struct.pack_into("<H", b, at + 7 + 3 * j, t)
    return bytes(b)


def _filter_envelope(data: bytes, k: int, nodes) -> bytes:
    """The instrument's pitch envelope used as its filter envelope (flag bit
    7): node values -32..32, +32 the cutoff as set (IFC), lower closes it."""
    b = bytearray(data)
    at = instrument_at(data, k) + PITCH_ENV
    b[at] = 1 | 0x80
    b[at + 1] = len(nodes)
    for j in range(25):
        y, t = nodes[j] if j < len(nodes) else (0, 0)
        b[at + 6 + 3 * j] = y & 0xFF
        struct.pack_into("<H", b, at + 7 + 3 * j, t)
    return bytes(b)


def samples_of(data: bytes, k: int) -> List[int]:
    """The samples (0-based) instrument k plays."""
    at = instrument_at(data, k) + 0x40
    return sorted({data[at + 2 * n + 1] - 1 for n in range(120)
                   if data[at + 2 * n + 1]})


def _poke(data: bytes, offset: int, value: int) -> bytes:
    b = bytearray(data)
    b[offset] = value
    return bytes(b)


# -- candidates from an intent ----------------------------------------------------------------
def _candidates(data: bytes, k: int, intent: dict, tick_s: float
                ) -> List[dict]:
    """Changes that could move the phases the intent names, each with what
    it changes and what should happen."""
    env = envelope(data, k)
    nodes = env["nodes"] if env["on"] else [(64, 0), (64, 1)]
    sus = env["sustain_node"] if env["on"] and env["sustain"] else \
        len(nodes) - 1
    out = []
    for c in intent["clauses"]:
        key = (c["phase"], c["quality"], c["direction"])
        if key == ("attack", "sharpness", "less"):
            for ticks in (1, 3):
                shifted = [(0, 0)] + [(y, t + ticks) for y, t in nodes]
                if shifted[1][1] == 0:
                    continue
                out.append({"id": f"ramp{ticks}", "phase": "attack",
                            "data": set_envelope(data, k, shifted, sus + 1),
                            "change": {"volume envelope": shifted},
                            "expected": f"the notes rise from silence over "
                                        f"{ticks} tick(s) "
                                        f"({1000 * ticks * tick_s:.0f} ms)"})
        elif key == ("attack", "sharpness", "more"):
            if nodes[0][0] >= 64:
                out.append({"id": "attack", "refused": "the envelope starts "
                            "at full level: the attack is the sample's own"})
            else:
                ramp = [(64, 0)] + [(y, t) for y, t in nodes[1:]]
                out.append({"id": "noramp", "phase": "attack",
                            "data": set_envelope(data, k, ramp, sus),
                            "change": {"volume envelope": ramp},
                            "expected": "the envelope's ramp taken away"})
        elif key == ("body", "brightness", "less"):
            at = instrument_at(data, k)
            now = data[at + IFC] & 0x7F if data[at + IFC] & 0x80 else 127
            # the body only: a filter envelope open through the attack's
            # window (intent.PHASES), closing after it
            a = max(1, int(-(-INT.PHASES["attack"]["window_ms"][1] / 1000.0
                             // tick_s)))
            for low in (24, 16):
                d = _filter_envelope(_poke(data, at + IFC, 0x80 | now), k,
                                     [(32, 0), (32, a), (low, a + 5)])
                out.append({"id": f"fenv{low}", "phase": "body", "data": d,
                            "change": {"filter envelope": [[32, 0], [32, a],
                                                           [low, a + 5]]},
                            "expected": f"the filter open for the first "
                                        f"{a} ticks (the attack), closing "
                                        f"to {low}/32 over five more: a "
                                        f"darker body after the same "
                                        f"attack"})
            for cutoff in (int(now * 0.8), int(now * 0.6)):
                d = _poke(_poke(data, at + IFC, 0x80 | cutoff), at + IFR,
                          0x80)
                out.append({"id": f"ifc{cutoff}", "phase": "body",
                            "data": d, "change": {"filter cutoff":
                                                  [now, cutoff]},
                            "expected": f"the low-pass at {cutoff}/127: a "
                                        f"darker note, attack and body"})
        elif key == ("body", "brightness", "more"):
            at = instrument_at(data, k)
            if not data[at + IFC] & 0x80:
                out.append({"id": "brighter", "refused": "no filter to "
                            "open: the sample is as bright as it was "
                            "rendered (a brighter sample is the Schism "
                            "forge's)"})
            else:
                now = data[at + IFC] & 0x7F
                up = min(127, int(now * 1.3) + 1)
                out.append({"id": f"ifc{up}", "phase": "body",
                            "data": _poke(data, at + IFC, 0x80 | up),
                            "change": {"filter cutoff": [now, up]},
                            "expected": "the filter opened"})
        elif key == ("held", "vibrato", "more"):
            # the sweep sets how long the depth takes to grow: 48-64 let
            # notes of about half a second swing and shorter ones end
            # first (measured on fixtures.held_lead); 255 is at once
            plans = ((20, 64), (28, 48)) if c.get("only_long") else \
                ((20, 255), (12, 255))
            for depth, rate in plans:
                d = data
                for s in samples_of(data, k):
                    so = sample_at(data, s)
                    for off, v in ((VIB_SPEED, 20), (VIB_DEPTH, depth),
                                   (VIB_RATE, rate), (VIB_TYPE, 0)):
                        d = _poke(d, so + off, v)
                out.append({"id": f"autovib{depth}_{rate}", "phase": "held",
                            "data": d, "change": {"auto-vibrato": {
                                "speed": 20, "depth": depth,
                                "sweep": rate}},
                            "expected": f"a sine auto-vibrato (depth "
                                        f"{depth}) whose sweep {rate} makes "
                                        f"it grow over the note: the long "
                                        f"notes swing, the short ones end "
                                        f"first"})
        elif c["phase"] == "release":
            if len(nodes) <= sus + 1:
                out.append({"id": "release", "refused": "the envelope has "
                            "no release after its sustain point"})
                continue
            last_y, last_t = nodes[-1]
            hold_t = nodes[sus][1]
            span = last_t - hold_t
            for f in ((2.0, 3.0) if c["direction"] == "more" else
                      (0.5, 0.25)):
                new = max(1, int(round(span * f)))
                rel = nodes[:-1] + [(last_y, hold_t + new)]
                out.append({"id": f"release{new}", "phase": "release",
                            "data": set_envelope(data, k, rel, sus),
                            "change": {"release ticks": [span, new]},
                            "expected": f"the note-off fades over {new} "
                                        f"ticks ({1000 * new * tick_s:.0f} "
                                        f"ms), was {span}"})
        else:
            out.append({"id": f"{c['phase']}_{c['quality']}",
                        "refused": f"{c['phase']} {c['quality']} "
                                   f"{c['direction']}: nothing on an IT "
                                   f"instrument here makes it"})
    return out


# -- measuring a voice ---------------------------------------------------------------------
def _channel_notes(notes: List[dict], ch: int) -> List[dict]:
    return [{"start": n["start"], "end": n["end"],
             "f0": EAR.note_hz(n["pitch"])}
            for n in notes if n["channel"] == ch and
            n.get("pitch") is not None]


def listen_channel(data: bytes, ch: int, notes: List[dict],
                   seconds: Optional[float] = None) -> dict:
    """The channel alone, played from the top: what its notes do."""
    piece = IT.Piece(data)
    solo = IT._mono(IT.play(piece, keep=[ch], seconds=seconds)["samples"])
    prof = F.note_profile(solo, IT.RATE, _channel_notes(notes, ch))
    import math
    r = math.sqrt(sum(v * v for v in solo) / len(solo)) if solo else 0.0
    prof["rms_db"] = round(IT._db(r), 2)
    return prof


def shape(path: str, channel: int, statement, folder: str, ear=None,
          seconds: Optional[float] = None) -> dict:
    """The sound of the instrument on `channel` (0-based) moved as the
    statement says, phase by phase, the rest held. -> the record; a kept
    change is written to `folder` as shaped.it with A/B reels."""
    from . import listen as LI
    started = time.time()
    os.makedirs(folder, exist_ok=True)
    with open(path, "rb") as handle:
        original = handle.read()
    intent = INT.parse(statement)
    if not intent["clauses"]:
        raise AgenticError("no_intent", "no phase, quality and direction "
                           "could be read", unparsed=intent["unparsed"])
    data = with_instruments(original)
    converted = data is not original
    base_play = IT.play(IT.Piece(data), seconds=seconds)
    notes = base_play["notes"]
    used = sorted({n["instrument"] for n in notes
                   if n["channel"] == channel and n.get("instrument")})
    if not used:
        raise AgenticError("silent_channel", f"channel {channel + 1} plays "
                           f"no note")
    if len(used) > 1:
        raise AgenticError("several_instruments", f"channel {channel + 1} "
                           f"plays instruments {used}; shape one at a time")
    k = used[0] - 1
    sharing = sorted({n["channel"] for n in notes
                      if n.get("instrument") == used[0]})
    tempo = data[0x33] or 125
    tick_s = 2.5 / tempo
    base = listen_channel(data, channel, notes, seconds)
    lengths = [n["end"] - n["start"] for n in _channel_notes(notes, channel)]
    g = INT.to_goal(intent, base, lengths)
    goal, hold = g["goal"], g["hold"]
    busy = sorted({n["channel"] for n in notes})
    before = IT.measure(IT.Piece(data), busy, notes, seconds, base_play)
    record = {"module": os.path.basename(path), "channel": f"ch{channel + 1}",
              "instrument": used[0],
              "also_plays_on": [f"ch{c + 1}" for c in sharing
                                if c != channel],
              "converted_to_instruments": converted,
              "intent": intent["clauses"], "unparsed": intent["unparsed"],
              "goal": goal, "hold": hold, "notes": g["notes"],
              "before": {k_: base.get(k_) for k_ in
                         ("attack_rise_ms", "brightness_attack",
                          "brightness_body", "release_ms",
                          "vibrato_depth_cents", "vibrato_share", "rms_db")},
              "playback": base_play["playback"], "candidates": [],
              "listening": EAR.listening(ear)}
    renders = 1 + before["renders"] + 1
    tried = []
    for c in _candidates(data, k, intent, tick_s):
        cand = {"id": c["id"]}
        if "refused" in c:
            cand.update(accepted=False, why="refused: " + c["refused"])
            record["candidates"].append(cand)
            continue
        cand.update(change=c["change"], expected=c["expected"],
                    phase=c["phase"])
        prof = listen_channel(c["data"], channel, notes, seconds)
        mix = IT.play(IT.Piece(c["data"]), seconds=seconds)
        m = IT.measure(IT.Piece(c["data"]), busy, notes, seconds, mix)
        renders += 2 + m["renders"]
        rep = INT.report(intent, base, prof, goal, hold)
        moved = [r for r in rep["asked"] if "direction" in r]
        guards = [{"name": "no_clipping", "ok": m["mix"]["peak"] < 0.98,
                   "detail": f"peak {m['mix']['peak_db']:+.1f} dBFS"},
                  {"name": "mix_level", "ok": abs(
                      m["mix"]["rms_db"] - before["mix"]["rms_db"])
                   <= L.MIX_LEVEL_LIMIT_DB,
                   "detail": f"mix {before['mix']['rms_db']:+.1f} -> "
                             f"{m['mix']['rms_db']:+.1f} dB"},
                  {"name": "rest_kept", "ok": rep["all_held"],
                   "detail": "; ".join(
                       f"{h['reading']} {h['before']:g} -> {h['after']:g}"
                       for h in rep["held"] if not h["held"]) or
                   "the phases not named stay"}]
        newly = [f"ch{x + 1}" for x in busy if x != channel and
                 (before["channels"][x].get("band_margin_db") or -99)
                 >= EAR.BURIED_BODY_DB > (m["channels"][x].get(
                     "band_margin_db") or -99)]
        guards.append({"name": "no_new_buried", "ok": not newly,
                       "detail": ", ".join(newly) or "none"})
        mine = [r for r in moved if r["phase"] == c["phase"]]
        cand.update(phases=rep, guards=guards)
        failed = [x for x in guards if not x["ok"]]
        if failed:
            cand.update(accepted=False, why="guard failed: " + "; ".join(
                f"{x['name']} ({x['detail']})" for x in failed))
        elif not mine or not all(r.get("moved_the_asked_way") and
                                 abs(r["after"] - r["before"]) >=
                                 READINGS.get(r["reading"], (0.0,))[0]
                                 for r in mine):
            cand.update(accepted=False, why=f"the {c['phase']} did not "
                        f"move the way asked: " + "; ".join(
                            f"{r['reading']} {r['before']} -> {r['after']}"
                            for r in mine))
        else:
            cand.update(accepted=True, why="; ".join(
                f"{r['reading']} {r['before']} -> {r['after']}"
                for r in mine) + "; the rest held")
        cand["_data"], cand["_mix"] = c["data"], mix
        record["candidates"].append(cand)
        tried.append(cand)
    ok = [c for c in tried if c.get("accepted")]
    # one change per phase: the best of each, then all of them together
    def off_goal(c):
        rows = [r for r in c["phases"]["asked"] if r["phase"] == c["phase"]
                and "direction" in r]
        return sum(abs(r["after"] - r["goal"]) /
                   READINGS.get(r["reading"], (0.0, 1.0))[1] for r in rows)
    best_of = {}
    for c in sorted(ok, key=off_goal):
        if c["phase"] not in best_of:
            best_of[c["phase"]] = c
    ok = sorted(ok, key=off_goal)
    chosen, verdict, judged = None, None, "measured"
    if len(best_of) > 1:
        joint = data
        for c in best_of.values():
            joint = _merge(joint, c["_data"], data)
        prof = listen_channel(joint, channel, notes, seconds)
        mix = IT.play(IT.Piece(joint), seconds=seconds)
        rep = INT.report(intent, base, prof, goal, hold)
        renders += 2
        both = {"id": "+".join(c["id"] for c in best_of.values()),
                "phase": "together", "phases": rep,
                "change": {c["id"]: c["change"] for c in best_of.values()},
                "accepted": rep["all_held"] and rep["all_asked_moved"],
                "why": "each phase's kept change at once" +
                ("" if rep["all_held"] and rep["all_asked_moved"] else
                 ": a phase moved the wrong way or a held one moved"),
                "_data": joint, "_mix": mix}
        record["candidates"].append(both)
        if both["accepted"]:
            ok = [both] + ok
    chosen = ok[0] if ok else None
    if ok and EAR.listening(ear)["available"]:
        top = ok[:3]
        versions = [("as it is", base_play["samples"])] + [
            (c["id"], c["_mix"]["samples"]) for c in top]
        verdict = EAR.ab_choose(ear, versions,
                                goal=f"ch{channel + 1}'s notes: "
                                     f"{intent.get('statement') or statement}",
                                voices=[f"ch{channel + 1}"], rate=IT.RATE,
                                stereo=True, platform=IT.PLATFORM)
        trust = EAR.trusted_to_choose(ear)
        verdict["trusted"] = trust
        if not trust["trusted"]:
            verdict = dict(verdict, obeyed=False,
                           recorded_choice=verdict.get("choice"),
                           choice=None)
        elif verdict.get("choice") == 0:
            chosen, judged = None, "heard by model"
        elif verdict.get("choice") is not None:
            chosen, judged = top[verdict["choice"] - 1], "heard by model"
        else:
            judged = "measured; the model uncertain"
    record.update(listening_verdict=verdict, judged_by=judged)
    if chosen is None:
        record.update(outcome="rolled_back", why=(
            "the ear chose the module as it is" if judged ==
            "heard by model" else "no change moved the phases asked with "
            "the rest held"))
    else:
        out = os.path.join(folder, "shaped.it")
        with open(out, "wb") as handle:
            handle.write(chosen["_data"])
        solo_b = IT.play(IT.Piece(data), keep=[channel],
                         seconds=seconds)["samples"]
        solo_a = IT.play(IT.Piece(chosen["_data"]), keep=[channel],
                         seconds=seconds)["samples"]
        renders += 2
        frags = []
        for label, smp in ((f"ch{channel + 1} before, alone", solo_b),
                           (f"ch{channel + 1} after, alone", solo_a),
                           ("before, in the mix", base_play["samples"]),
                           ("after, in the mix", chosen["_mix"]["samples"])):
            secs = round(len(smp) / 2 / IT.RATE, 3)
            frags.append({"label": label, "kind": "ours",
                          "source": record["module"], "bars": None,
                          "seconds": secs, "t0": 0.0, "t1": secs,
                          "samples": list(smp)})
        reel = LI.write(frags, folder, "shape_ab", mp3=False, raw=True)
        record.update(outcome="committed", chosen=chosen["id"],
                      phases=chosen["phases"], file=out,
                      ab_reel=reel["reel"]["wav"],
                      ab_reel_raw=reel["reel_raw"]["wav"],
                      why=chosen["why"] + ("; chosen by the ear" if judged ==
                                           "heard by model" else
                                           "; chosen by the measurement"))
    for c in record["candidates"]:
        c.pop("_data", None)
        c.pop("_mix", None)
    record["cost"] = {"renders": renders,
                      "runtime_s": round(time.time() - started, 2)}
    return record


def _merge(a: bytes, b: bytes, base: bytes) -> bytes:
    """The bytes b changed from base, laid over a (changes to different
    parts of one instrument or its samples)."""
    out = bytearray(a)
    for i, (x, y) in enumerate(zip(base, b)):
        if x != y:
            out[i] = y
    return bytes(out)
