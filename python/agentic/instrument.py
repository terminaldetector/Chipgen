"""instrument.py — work on a voice's instrument toward a sound goal: say
what it should do, make it do that, hear it alone and in the arrangement,
keep it or put it back.

    goal         what the voice's notes should do, in the readings of
                 features.note_profile: vibrato depth, rate and delay;
                 brightness at the attack and in the body (their ratio is
                 how the timbre develops); attack rise; release. A goal is
                 written from a reference (`goal_from`: the matched voice's
                 readings, the source's claims where the audio confirmed
                 them) or by hand
    hypotheses   changes that could reach it, each saying what it changes
                 and what should happen:
                   program  on the instrument (synthesis/program.py): a
                            vibrato with its delay, a modulator-level track
                            over the note (the timbre moving in time), the
                            carrier's attack or release rate, legato or a
                            gate — the instrument, wherever it plays
                   effect   the score's own effect commands in one section
                            (`vib`, `trem` directives): the passage only
    evaluation   each candidate is rendered over the voice's own music in
                 the piece: the voice alone (what its notes now do, read
                 by note_profile) and the whole arrangement (the voice's
                 in-band margin, the loop's guards: nothing clips, no
                 other voice is newly buried, the mix level holds). A
                 candidate counts when it closes the distance to the goal
                 by a stated share, keeps what the goal did not ask to
                 change (pitch centre, level, the readings not targeted)
                 and passes the guards
    commit       the best one becomes the voice's instrument: a forge bank
                 file of its own in the project's banks/ (never rewritten,
                 so every revision can be rebuilt), recorded in the
                 structure, the voice's patch named — one revision.
                 Otherwise nothing changes, and the candidates are kept as
                 rejected sketches with the reason

The voice keeps its patch's registers: a program changes what the patch
does in time (and its envelope rates), it does not swap the sound for a
ready-made one.
"""

import copy
import hashlib
import os
import time
from typing import List, Optional

from . import ear as EAR
from . import features as F
from . import loop as L
from . import render as R
from . import timeline as T
from .state import AgenticError, content as state_content

#: The share of the distance to the goal a candidate must close.
MIN_CLOSED = 0.25
#: How far each reading may sit from its goal and still count as there,
#: and the scale its distance is divided by.
READINGS = {
    "vibrato_depth_cents": (6.0, 20.0),
    "vibrato_rate_hz": (0.8, 2.0),
    "vibrato_delay_ms": (60.0, 150.0),
    "vibrato_share": (0.15, 0.5),
    "brightness_development": (0.08, 0.25),
    "brightness_attack": (0.3, 1.0),
    "brightness_body": (0.3, 1.0),
    "attack_rise_ms": (5.0, 15.0),
    "release_ms": (40.0, 120.0),
}
#: What may not move when the goal does not name it.
KEEP = {"attack_rise_ms": 12.0, "release_ms": 120.0}
#: A voice's own level (RMS alone) may move this much (dB).
LEVEL_LIMIT_DB = 1.5


# -- goals ---------------------------------------------------------------------------
def goal_from(profile: dict, dims=("modulation", "timbre"),
              source_claims=()) -> dict:
    """A sound goal from a reference voice's readings (note_profile), on
    the readings the dimensions name. A claim the audio confirmed (a
    vibrato's depth and rate) fills a reading the profile lacks."""
    goal = {}
    want = set()
    if "modulation" in dims:
        want |= {"vibrato_depth_cents", "vibrato_rate_hz",
                 "vibrato_delay_ms", "vibrato_share"}
    if "timbre" in dims:
        want |= {"brightness_development", "brightness_body"}
    if "rhythm" in dims or "arrangement" in dims:
        want |= {"attack_rise_ms"}
    if "space" in dims:
        want |= {"release_ms"}
    for k in sorted(want):
        v = profile.get(k)
        if isinstance(v, (int, float)):
            goal[k] = float(v)
    for c in source_claims:
        check = c.get("audio_check") or {}
        if c.get("kind") == "vibrato" and check.get("verdict") == \
                "confirmed":
            r = check.get("reading") or {}
            goal.setdefault("vibrato_depth_cents", r.get("depth_cents"))
            if r.get("rate_hz"):
                goal.setdefault("vibrato_rate_hz", r["rate_hz"])
    return {k: v for k, v in goal.items() if v is not None}


def distance(profile: dict, goal: dict) -> dict:
    """Per reading: how far from the goal, past its tolerance, in units of
    its scale; and the total. A reading the voice does not show (no
    vibrato to read a rate from) counts as a whole scale away."""
    parts = {}
    for k, target in goal.items():
        tol, scale = READINGS.get(k, (0.0, abs(target) or 1.0))
        v = profile.get(k)
        if k in ("vibrato_depth_cents", "vibrato_share") and v is None:
            v = 0.0
        if v is None:
            parts[k] = 1.0
            continue
        parts[k] = round(max(0.0, abs(v - target) - tol) / scale, 3)
    return {"parts": parts, "total": round(sum(parts.values()), 3)}


# -- the voice's instrument as a forge entry -----------------------------------------------
def _base_compiled(patch_name: str):
    """The voice's instrument as a forge Compiled (its program, if the
    forge made it; a bank patch otherwise, with none)."""
    import instruments
    from synthesis import registry
    from synthesis.backends.base import Compiled
    forged = registry.lookup("ym2612", patch_name)
    if forged is not None:
        return copy.deepcopy(forged)
    try:
        patch = instruments.get(patch_name)
    except Exception:
        raise AgenticError("unknown_patch", f"{patch_name!r} is not an FM "
                           f"patch this engine knows") from None
    return Compiled("ym2612", patch_name, patch.copy())


def _entry(name: str, compiled, program, role: str, why: str):
    from synthesis import bank as B
    from synthesis import program as P
    from synthesis.dna import DNA
    from synthesis.genome import Genome
    c = P.attach(copy.copy(compiled), program)
    c.name = name
    c.patch = compiled.patch.copy()
    c.patch.name = name
    c.notes = list(getattr(compiled, "notes", []) or []) + [why]
    return B.Entry(name=name, backend="ym2612",
                   genome=Genome(backend="ym2612", dna=DNA.from_dict({}),
                                 name=name, register=("A", 4)),
                   compiled=c, measured={}, role=role,
                   character=f"{compiled.name} with a program: {why}")


def _install(entry):
    from synthesis import program as P
    from synthesis.backends import get_backend
    get_backend("ym2612").install(P.prepare(entry.compiled), entry.character)


def _program_of(compiled):
    from synthesis import program as P
    return P.of(compiled) or P.Program(engine="ym2612")


# -- hypotheses ------------------------------------------------------------------------------
def _attack_ms(profile: dict) -> float:
    rise = profile.get("attack_rise_ms") or 10.0
    return max(20.0, min(120.0, rise + 15.0))


def _delay_for_share(lengths: List[float], share: float) -> Optional[float]:
    """The vibrato delay (ms) that lets about `share` of these notes swing:
    the reference's habit — vibrato on the held notes, none on the short
    ones — carried over to this part's own note lengths. A note needs
    about 150 ms past the delay for a swing to be heard."""
    if not lengths or share is None or share <= 0.0:
        return None
    ordered = sorted(lengths, reverse=True)
    k = max(0, min(len(ordered) - 1, int(round(share * len(ordered))) - 1))
    return max(0.0, round((ordered[k] - 0.15) * 1000.0 / 10.0) * 10.0)


def hypotheses(compiled, profile: dict, goal: dict, voice: str,
               channel: str, section: Optional[str] = None,
               lengths: Optional[List[float]] = None) -> List[dict]:
    """Changes that could bring the voice's notes to `goal`, each with the
    parameter, what should happen and its scope. `lengths` are the voice's
    note lengths in the range (seconds)."""
    from synthesis import program as P
    base = _program_of(compiled)
    out = []
    depth = goal.get("vibrato_depth_cents")
    if depth and depth >= 8.0:
        rate = goal.get("vibrato_rate_hz") or 5.5
        delay = goal.get("vibrato_delay_ms")
        delay = 200.0 if delay is None else delay
        fitted = _delay_for_share(lengths or [], goal.get("vibrato_share"))
        tries = []
        for d, r, dl in ([(depth, rate, fitted)] if fitted is not None
                         else []) + [(depth, rate, delay),
                                     (0.7 * depth, rate, delay + 120.0),
                                     (depth, rate, 0.0)]:
            key = (round(d, 1), round(r, 2), round(max(0.0, dl)))
            if key not in tries:
                tries.append(key)
        for k, (d, r, dl) in enumerate(tries):
            p = base.copy()
            p.vibrato = {"depth_cents": round(d, 1),
                         "speed_hz": round(r, 2),
                         "delay_ms": round(max(0.0, dl))}
            out.append({"id": f"vib{k + 1}", "scope": "program",
                        "family": "vibrato", "program": p,
                        "parameter": "vibrato depth, rate, delay",
                        "change": dict(p.vibrato),
                        "expected": f"a {d:.0f}-cent swing at {r:.1f} Hz "
                                    f"after {max(0.0, dl):.0f} ms on held "
                                    f"notes; the attack and the pitch "
                                    f"centre unchanged"})
        if section:
            out.append({"id": "vib_fx", "scope": "effect",
                        "family": "effect",
                        "directive": f"vib {channel} {depth:.0f} {rate:.1f} "
                                     f"{delay / 1000.0:.2f}",
                        "section": section,
                        "parameter": "the score's vib command in section "
                                     f"{section}",
                        "change": {"depth_cents": round(depth, 1),
                                   "speed_hz": round(rate, 2),
                                   "delay_ms": round(delay)},
                        "expected": "the same swing, in this section only "
                                    "(an effect command, not the "
                                    "instrument)"})
    target = goal.get("brightness_development")
    now = profile.get("brightness_development")
    if target is not None and now is not None and \
            abs(target - now) > READINGS["brightness_development"][0]:
        a = _attack_ms(profile)
        for k, steps in enumerate((2, 4)):
            out.append(_tl_hypothesis(base, a, steps, now, target,
                                      f"tl{k + 1}"))
    level = goal.get("brightness_body")
    now_level = profile.get("brightness_body")
    if level is not None and now_level is not None and \
            abs(level - now_level) > READINGS["brightness_body"][0]:
        sign = 1 if level < now_level else -1     # + is darker
        for k, steps in enumerate((3, 6)):
            out.append(_level_hypothesis(base, sign * steps, now_level,
                                         level, f"br{k + 1}"))
    rise = goal.get("attack_rise_ms")
    now_rise = profile.get("attack_rise_ms")
    if rise is not None and now_rise is not None and \
            abs(rise - now_rise) > READINGS["attack_rise_ms"][0]:
        roles = P.fm_roles(compiled.patch)
        cur = min(P._fm_operator(compiled.patch, op).attack_rate
                  for op in (1, 2, 3, 4) if roles[op] == "carrier")
        for k, step in enumerate((3, 6)):
            new = max(1, min(31, cur - step if rise > now_rise
                             else cur + step))
            out.append(_setting_hypothesis(
                base, "car.ar", cur, new, f"ar{k + 1}",
                "car.ar (carrier attack rate)",
                f"attack rise from {now_rise:.0f} ms toward {rise:.0f} ms"))
    rel = goal.get("release_ms")
    now_rel = profile.get("release_ms")
    if rel is not None and now_rel is not None and \
            abs(rel - now_rel) > READINGS["release_ms"][0]:
        roles = P.fm_roles(compiled.patch)
        cur = max(P._fm_operator(compiled.patch, op).release_rate
                  for op in (1, 2, 3, 4) if roles[op] == "carrier")
        for k, step in enumerate((2, 4)):
            new = max(0, min(15, cur - step if rel > now_rel
                             else cur + step))
            out.append(_setting_hypothesis(
                base, "car.rr", cur, new, f"rr{k + 1}",
                "car.rr (carrier release rate)",
                f"release from {now_rel:.0f} ms toward {rel:.0f} ms"))
    return out


def _interleave(hyps: List[dict]) -> List[dict]:
    """One of each family before the second of any: a budget cut keeps
    every kind of change in play."""
    families: List[List[dict]] = []
    seen = {}
    for h in hyps:
        f = h.get("family", h["id"])
        if f not in seen:
            seen[f] = len(families)
            families.append([])
        families[seen[f]].append(h)
    out = []
    for k in range(max((len(f) for f in families), default=0)):
        out.extend(f[k] for f in families if k < len(f))
    return out


def _tl_hypothesis(base, attack_ms: float, steps: int, now: float,
                   target: float, hid: str) -> dict:
    from synthesis import program as P
    p = base.copy()
    p.tracks = [t for t in p.tracks if t.target != "mod.tl"]
    if target > now:
        # the body brighter than the attack: start the modulators down
        # and open them after the attack
        points = [(0, steps), (attack_ms, steps), (attack_ms + 120, 0)]
        words = "darker attack opening into a brighter body"
    else:
        points = [(0, 0), (attack_ms, 0), (attack_ms + 120, steps)]
        words = "bright attack closing into a darker body"
    p.tracks.append(P.Track("mod.tl", points, "ms", "linear", None, None,
                            "program"))
    return {"id": hid, "family": "mod.tl", "amount": steps,
            "scope": "program", "program": p,
            "parameter": "mod.tl (modulator level) over the note",
            "change": {"mod.tl": points},
            "expected": f"{words} ({steps} steps, {0.75 * steps:.1f} dB): "
                        f"body over attack brightness from {now:.2f} "
                        f"toward {target:.2f}",
            "_attack_ms": attack_ms}


def _level_hypothesis(base, steps: int, now: float, target: float,
                      hid: str) -> dict:
    """The modulators held `steps` quieter (darker; negative: brighter)
    for the whole note: the timbre's overall brightness."""
    from synthesis import program as P
    p = base.copy()
    p.tracks = [t for t in p.tracks if t.target != "mod.tl"]
    p.tracks.append(P.Track("mod.tl", [(0, steps)], "ms", "step", None,
                            None, "program"))
    return {"id": hid, "family": "brightness", "amount": steps,
            "scope": "program", "program": p,
            "parameter": "mod.tl (modulator level) for the whole note",
            "change": {"mod.tl": [[0, steps]]},
            "expected": f"{'darker' if steps > 0 else 'brighter'} by "
                        f"{abs(steps)} steps ({0.75 * abs(steps):.1f} dB "
                        f"on the modulators): body brightness from "
                        f"{now:.2f} toward {target:.2f}"}


def _setting_hypothesis(base, key: str, cur: int, new: int, hid: str,
                        parameter: str, expected: str) -> dict:
    p = base.copy()
    p.settings[key] = new
    return {"id": hid, "family": key, "amount": new, "scope": "program",
            "program": p, "parameter": parameter,
            "change": {key: [cur, new]}, "expected": expected,
            "_from": cur}


#: the reading each family of amounts moves
_FAMILY_READING = {"mod.tl": "brightness_development",
                   "brightness": "brightness_body",
                   "car.ar": "attack_rise_ms", "car.rr": "release_ms"}
#: the readings each family is meant to move
_FAMILY_READINGS = {"vibrato": ("vibrato_depth_cents", "vibrato_rate_hz",
                                "vibrato_delay_ms", "vibrato_share"),
                    "mod.tl": ("brightness_development",),
                    "brightness": ("brightness_body", "brightness_attack"),
                    "car.ar": ("attack_rise_ms",),
                    "car.rr": ("release_ms",)}


def combine(compiled, goal: dict, hyps: List[dict],
            tried: List[dict], avoid=()) -> Optional[dict]:
    """The best program change of each family that moved its own readings
    toward the goal (and broke no guard but the share of the whole
    distance), put together in one program. `avoid`: changes (ids) not to
    use, for another try after a combination with them broke a guard."""
    best = {}
    for h, c in zip(hyps, tried):
        fam = h.get("family")
        if fam not in _FAMILY_READINGS or h["scope"] != "program" or \
                not c.get("distance") or h["id"] in avoid:
            continue
        if any(not g["ok"] for g in c.get("guards", [])):
            continue
        part = sum(c["distance"]["parts"].get(k, 0.0)
                   for k in _FAMILY_READINGS[fam] if k in goal)
        if fam not in best or part < best[fam][0]:
            best[fam] = (part, h, c)
    useful = {f: v for f, v in best.items()
              if v[2].get("closed", 0) > 0.0}
    if len(useful) < 2:
        return None
    from synthesis import program as P
    p = _program_of(compiled)
    parts = []
    shape, offset = None, 0
    for fam, (_part, h, _c) in sorted(useful.items()):
        q = h["program"]
        if fam == "vibrato":
            p.vibrato = dict(q.vibrato)
        elif fam == "mod.tl":
            shape = q.track("mod.tl")
        elif fam == "brightness":
            offset = int(q.track("mod.tl").points[0][1])
        else:
            p.settings[fam] = q.settings[fam]
        parts.append(h["id"])
    if shape is not None or offset:
        # one modulator track: the development's shape, moved by the
        # overall level's offset
        points = [(t, v + offset) for t, v in shape.points] \
            if shape is not None else [(0, offset)]
        p.tracks = [t for t in p.tracks if t.target != "mod.tl"] + [
            P.Track("mod.tl", points, "ms",
                    "linear" if shape is not None else "step", None, None,
                    "program")]
    return {"id": "+".join(parts), "family": "combined", "round": 3,
            "parts": parts, "scope": "program", "program": p,
            "parameter": "together: " + ", ".join(
                useful[f][1]["parameter"] for f in sorted(useful)),
            "change": {h: useful[f][1]["change"]
                       for f, h in zip(sorted(useful), parts)},
            "expected": "each family's best change at once: "
                        + "; ".join(useful[f][1]["expected"]
                                    for f in sorted(useful))}


def refine(compiled, base_prof: dict, goal: dict, hyps: List[dict],
           tried: List[dict]) -> List[dict]:
    """One more amount per family, read off the first round: the reading
    moved from the base value to the candidate's as the amount went from
    nothing to its value; the amount that would put it on the goal is
    estimated on that line (bounded), if it is not one already tried."""
    out = []
    for family, reading in _FAMILY_READING.items():
        goal_v = goal.get(reading)
        base_v = base_prof.get(reading)
        if goal_v is None or base_v is None:
            continue
        pairs = []
        for h, c in zip(hyps, tried):
            if h.get("family") != family or not c.get("profile"):
                continue
            v = c["profile"].get(reading)
            if v is not None:
                pairs.append((h, v))
        if not pairs:
            continue
        h, v = max(pairs, key=lambda hv: abs(hv[1] - base_v))
        moved = v - base_v
        if abs(moved) < 1e-6:
            continue
        if family == "mod.tl":
            amount = int(round(h["amount"] * (goal_v - base_v) / moved))
            amount = max(1, min(24, amount))
            if amount in {x["amount"] for x, _ in pairs}:
                continue
            g = _tl_hypothesis(_program_of(compiled), h["_attack_ms"],
                               amount, base_v, goal_v, f"{h['id']}r")
        elif family == "brightness":
            amount = int(round(h["amount"] * (goal_v - base_v) / moved))
            amount = max(-24, min(24, amount))
            if amount == 0 or amount in {x["amount"] for x, _ in pairs}:
                continue
            g = _level_hypothesis(_program_of(compiled), amount, base_v,
                                  goal_v, f"{h['id']}r")
        else:
            span = h["amount"] - h["_from"]
            amount = int(round(h["_from"] + span * (goal_v - base_v)
                               / moved))
            high = 31 if family == "car.ar" else 15
            amount = max(0, min(high, amount))
            if amount in {x["amount"] for x, _ in pairs} or \
                    amount == h["_from"]:
                continue
            g = _setting_hypothesis(_program_of(compiled), family,
                                    h["_from"], amount, f"{h['id']}r",
                                    h["parameter"], h["expected"])
        g["round"] = 2
        g["expected"] += f" (round 2: estimated from {h['id']}, which moved " \
                         f"{reading} {base_v:g} -> {v:g})"
        out.append(g)
    return out


# -- measuring a version of the voice --------------------------------------------------------------
def voice_notes(tl, rng: dict, voice: str) -> List[dict]:
    return [{"start": n["start"] - rng["t0"],
             "end": min(n["end"], rng["t1"]) - rng["t0"],
             "f0": EAR.note_hz(n["pitch"])}
            for n in tl.sounding(rng["t0"], rng["t1"], voice)
            if n["start"] >= rng["t0"] - 1e-6 and n.get("pitch") is not None]


def _pitch_centre(mono, notes) -> Optional[float]:
    from synthesis import phases
    cents = []
    for n in notes[:6]:
        if n["end"] - n["start"] < 0.15:
            continue
        a = int((n["start"] + 0.04) * R.RATE)
        b = int(min(n["start"] + 0.12, n["end"]) * R.RATE)
        seg = mono[a:b]
        if len(seg) < 1024:
            continue
        c, _q = phases._pitch(seg, R.RATE, n["f0"])
        if isinstance(c, (int, float)):
            cents.append(c)
    if not cents:
        return None
    cents.sort()
    return round(cents[len(cents) // 2], 1)


def listen_voice(tl, spec: str, voice: str, cache) -> dict:
    """The voice alone over the range: what its notes do, its level, and
    the pitch centre of its early body (a vibrato swings around it; a
    change that moved it would detune the voice)."""
    rng = tl.range(spec)
    r = R.render_range(tl, rng, voices=[voice], cache=cache)
    mono = list(r.in_range())
    notes = voice_notes(tl, rng, voice)
    prof = F.note_profile(mono, R.RATE, notes)
    prof["rms_db"] = round(R.rms_db(mono), 2)
    prof["pitch_centre_cents"] = _pitch_centre(mono, notes)
    return prof


# -- the work, start to finish -----------------------------------------------------------------------
def _content_with(project, voice: str, patch: Optional[str] = None,
                  directive: Optional[dict] = None) -> dict:
    c = state_content(project.state)
    if patch:
        c["instruments"][voice]["patch"] = patch
    if directive:
        for s in c["sections"]:
            if s["id"] == directive["section"]:
                s.setdefault("directives", []).append(
                    {"before_row": 0, "text": directive["directive"]})
                s["directives"].append({"before_row": len(s["rows"]),
                                        "text": directive["off"]})
    return c


def _bank_file(project, entry) -> dict:
    from synthesis import bank as B
    folder = project.path("banks")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{entry.name}.bank.json")
    if os.path.exists(path):
        raise AgenticError("bank_exists", f"{entry.name} is already a bank "
                           f"in this project; designs are never rewritten")
    B.Bank(f"{project.state['identity']['id']}-{entry.name}",
           [entry]).save(path)
    with open(path, "rb") as handle:
        sha = hashlib.sha256(handle.read()).hexdigest()[:16]
    return {"kind": "forge", "path": os.path.relpath(path, project.root),
            "sha256": sha}


def design(project, voice: str, goal: dict, spec: str,
           cache: Optional[R.Cache] = None, ear=None,
           section: Optional[str] = None, max_candidates: int = 6,
           why: str = "") -> dict:
    """Work on `voice`'s instrument toward `goal` over the range `spec`
    (where it plays in the piece): hypotheses, each rendered alone and in
    the arrangement, the best that closes MIN_CLOSED of the distance with
    every guard kept is committed. -> the record (also the decision)."""
    from . import sketches as SK
    started = time.time()
    st = project.state
    ins = st["instruments"].get(voice)
    if not ins or not ins.get("channel", "").startswith("fm"):
        raise AgenticError("not_fm", f"{voice} is not an FM voice: the "
                           f"programs are the YM2612's (a PSG voice has its "
                           f"own volume and pitch effects)", voice=voice)
    if not goal:
        raise AgenticError("no_goal", "a sound goal names at least one "
                           "reading")
    cache = cache or R.Cache(project.path("renders"))
    ear = ear or EAR.ToolEar(cache)
    tool = EAR.measurer(ear, cache)
    renders0 = cache.misses
    base_tl = T.build(state_content(st))
    rng = base_tl.range(spec)
    base_prof = listen_voice(base_tl, spec, voice, cache)
    base_h = tool.hear(base_tl, rng)
    base_dist = distance(base_prof, goal)
    compiled = _base_compiled(ins["patch"])
    lengths = [n["end"] - n["start"] for n in voice_notes(base_tl, rng,
                                                           voice)]
    hyps = _interleave(hypotheses(compiled, base_prof, goal, voice,
                                  ins["channel"], section,
                                  lengths))[:max_candidates]
    record = {"voice": voice, "patch_before": ins["patch"], "goal": goal,
              "range": spec, "why": why,
              "before": {"profile": _compact(base_prof),
                         "distance": base_dist,
                         "band_margin_db": (base_h["measurements"]["voices"]
                                            .get(voice) or {}).get(
                             "band_margin_db")},
              "candidates": [], "base_rev": project.head,
              "listening": EAR.listening(ear)}
    n = sum(1 for s in st.get("sketches", []) if s.get("section")
            == "instrument") + len(st["revisions"])
    counter = [0]

    def evaluate(h: dict) -> dict:
        counter[0] += 1
        name = f"{voice}_d{n}{counter[0]}"
        cand = {"id": h["id"], "scope": h["scope"],
                "parameter": h["parameter"], "change": h["change"],
                "expected": h["expected"], "round": h.get("round", 1)}
        try:
            if h["scope"] == "program":
                entry = _entry(name, compiled, h["program"],
                               ins.get("role", ""), h["expected"])
                _install(entry)
                c = _content_with(project, voice, patch=name)
                cand["instrument"] = name
            else:
                off = f"vib {ins['channel']} off"
                c = _content_with(project, voice, directive={
                    "section": h["section"], "directive": h["directive"],
                    "off": off})
                entry = None
            tl = T.build(c)
        except Exception as error:          # a refusal is a result too
            cand.update(accepted=False, why=f"could not be built: "
                                            f"{str(error)[:160]}")
            return cand
        prof = listen_voice(tl, spec, voice, cache)
        moved = prof["rms_db"] - base_prof["rms_db"]
        if h["scope"] == "program" and abs(moved) > LEVEL_LIMIT_DB:
            # an FM timbre change moves the level too (less modulation is
            # more fundamental): bring the voice back by velocity, so the
            # candidate is heard for its timbre, not for being louder
            try:
                from . import patch as PATCH
                c, delta = PATCH.apply(c, {"op": "level", "voice": voice,
                                           "range": "all", "db": -moved,
                                           "scope": "section"}, tl)
                tl = T.build(c)
                prof = listen_voice(tl, spec, voice, cache)
                cand["level_matched"] = {
                    "moved_db": round(moved, 2),
                    "velocity_db": delta["realised_db"]["mean"],
                    "now_db": round(prof["rms_db"] - base_prof["rms_db"],
                                    2)}
            except AgenticError as error:
                cand["level_matched"] = {"moved_db": round(moved, 2),
                                         "refused": str(error)[:120]}
        h_c = tool.hear(tl, tl.range(spec))
        dist = distance(prof, goal)
        guards = [g for g in L.guards(base_tl, tl, base_h, h_c,
                                      rng, {"voices": [voice]})
                  if g["name"] in ("no_clipping", "notes_kept",
                                   "no_new_faults", "voices_present",
                                   "mix_level")]
        kept = []
        for key, limit in KEEP.items():
            if key in goal:
                continue
            a, b = base_prof.get(key), prof.get(key)
            if a is not None and b is not None and abs(b - a) > limit:
                kept.append(f"{key} {a:g} -> {b:g}")
        lvl = prof["rms_db"] - base_prof["rms_db"]
        if abs(lvl) > LEVEL_LIMIT_DB:
            kept.append(f"level {lvl:+.1f} dB")
        pc0, pc1 = base_prof.get("pitch_centre_cents"), \
            prof.get("pitch_centre_cents")
        if pc0 is not None and pc1 is not None and abs(pc1 - pc0) > 15.0:
            kept.append(f"pitch centre {pc0:+.0f} -> {pc1:+.0f} cents")
        guards.append({"name": "rest_kept", "ok": not kept,
                       "detail": "; ".join(kept) or "the readings the goal "
                                                    "did not name stay"})
        closed = (base_dist["total"] - dist["total"]) / base_dist["total"] \
            if base_dist["total"] > 0 else 0.0
        cand.update(profile=_compact(prof), distance=dist,
                    closed=round(closed, 3), guards=guards,
                    band_margin_db=(h_c["measurements"]["voices"].get(voice)
                                    or {}).get("band_margin_db"))
        failed = [g for g in guards if not g["ok"]]
        if failed:
            cand.update(accepted=False, why="guard failed: " + "; ".join(
                f"{g['name']} ({g['detail']})" for g in failed))
        elif closed < MIN_CLOSED:
            cand.update(accepted=False, why=f"closed {closed:.0%} of the "
                                            f"distance to the goal, under "
                                            f"{MIN_CLOSED:.0%}")
        else:
            cand.update(accepted=True, why=f"closed {closed:.0%} of the "
                                           f"distance, every guard kept")
        cand["_content"], cand["_timeline"], cand["_entry"] = c, tl, entry
        cand["_amount"] = h.get("amount")
        return cand

    for h in hyps:
        record["candidates"].append(evaluate(h))
    # round 2: where an amount (modulator steps, a rate) undershot or
    # overshot, one more candidate at the amount the two readings point to
    more = refine(compiled, base_prof, goal, hyps, record["candidates"])
    for h in more:
        record["candidates"].append(evaluate(h))
    # round 3: the best change of each family together, when more than
    # one family moved its own readings toward the goal. Two changes that
    # each kept every guard can break one together (a vibrato from the
    # attack and a darker body moved the pitch centre 15 cents): then the
    # same once more with each family's next best change in turn
    joint = combine(compiled, goal, hyps + more, record["candidates"])
    if joint is not None:
        cand = evaluate(joint)
        record["candidates"].append(cand)
        if any(not g["ok"] for g in cand.get("guards", [])):
            tried_ids = {joint["id"]}
            for part in joint["parts"]:
                alt = combine(compiled, goal, hyps + more,
                              record["candidates"], avoid={part})
                if alt is None or alt["id"] in tried_ids:
                    continue
                tried_ids.add(alt["id"])
                record["candidates"].append(evaluate(alt))
    best = None
    for cand in record["candidates"]:
        if cand.get("accepted") and (best is None or cand["distance"]["total"]
                                     < best["distance"]["total"]):
            best = cand
    verdict = None
    if best is not None:
        verdict = L._listen_ab(ear, base_tl, best["_timeline"], spec, cache,
                               {"voices": [voice], "kind": "instrument",
                                "statement": f"{voice}'s notes should do "
                                             f"this: {goal}"})
        if EAR.vetoes(verdict):
            best["accepted"] = False
            best["why"] += f"; but {verdict['model']} preferred the " \
                           f"original (confidence {verdict['confidence']:.2f})"
            best = None
    record["listening_verdict"] = verdict
    for cand in record["candidates"]:
        if cand is best or not cand.get("_content"):
            continue
        cand["sketch"] = SK.save(
            project, f"{voice}: {cand['id']} ({cand['parameter']})",
            content=cand["_content"], status="rejected",
            reason=cand["why"], base_rev=project.head,
            measured={"distance": cand.get("distance"),
                      "closed": cand.get("closed")},
            section="instrument",
            why=f"a candidate for {voice}'s sound goal")
    if best is None:
        record.update(outcome="rolled_back", committed_rev=None,
                      why="no candidate closed enough of the distance with "
                          "every guard kept; the instrument stays "
                          f"{ins['patch']}")
    else:
        c = best["_content"]
        if best["scope"] == "program":
            entry = best["_entry"]
            bank = _bank_file(project, entry)
            c["structure"]["banks"] = [
                b for b in c["structure"].get("banks", []) or []
                if b["path"] != bank["path"]] + [bank]
            best["bank"] = bank["path"]
        rev = project.commit(c, {"kind": "instrument", "voice": voice,
                                 "candidate": best["id"],
                                 "parameter": best["parameter"],
                                 "change": best["change"]})
        record.update(outcome="committed", committed_rev=rev,
                      chosen=best["id"],
                      patch_after=c["instruments"][voice]["patch"],
                      why=best["why"])
    record["cost"] = {"renders": cache.misses - renders0,
                      "runtime_s": round(time.time() - started, 2),
                      "candidates": len(record["candidates"])}
    for cand in record["candidates"]:
        for key in ("_content", "_timeline", "_entry", "_amount"):
            cand.pop(key, None)
    did = project.add_decision({"kind": "instrument", "observation": None,
                                "voices": [voice], "outcome":
                                record["outcome"], "range": {"spec": spec},
                                "rounds": [], "stopped": "candidates tried",
                                "instrument": record})
    project.save()
    record["decision"] = did
    return record


def _compact(profile: dict) -> dict:
    return {k: profile.get(k) for k in
            ("notes", "attack_rise_ms", "brightness_attack",
             "brightness_body", "brightness_development", "release_ms",
             "vibrato_depth_cents", "vibrato_rate_hz", "vibrato_delay_ms",
             "vibrato_share", "rms_db", "pitch_centre_cents")}
