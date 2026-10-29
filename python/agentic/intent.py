"""intent.py — what a voice's notes should do, said phase by phase: "a
short sting, then a dark body, vibrato only on the long notes".

A heard complaint about an instrument is rarely one number. It names the
phases of a note — the attack (its first ~60 ms), the body (to ~350 ms),
the held part of the long notes, the release — and says of each what is
wrong. This turns such a statement into

    clauses   one per phase named: {"phase", "quality", "direction"},
              plus "only_long" (vibrato on the held notes only) and
              "late" (a vibrato that waits)
    goal      the voice's own readings (features.note_profile) moved by a
              step in the direction asked, for instrument.design — the
              same steps a heard category takes (guided.HEARD_INSTRUMENT),
              measured back, never asserted
    hold      the readings of the phases NOT named, with how far each may
              move: a change asked of the body may not move the attack

and reads back, per candidate, what each phase did (`report`): the asked
phase toward its goal, the held ones where they were. The programs that
do it are instrument.py's (synthesis/program.py): the carrier's rates for
the attack and the release, a modulator-level track over the note for the
brightness of the attack and the body, a delayed vibrato for the held
notes. On the YM2612 only; the other machines refuse instrument programs
by name.

A model's observation gives the clauses itself (the "phases" field of the
listening prompts). `parse` reads a statement written by a person or an
agent in English or Russian; what it cannot place it returns as
`unparsed`, it does not guess.
"""

import re
from typing import Dict, List, Optional

#: the phases of a note, the window each is read in, and its readings by
#: quality
PHASES = {
    "attack": {"window_ms": [0, 60],
               "readings": {"sharpness": "attack_rise_ms",
                            "brightness": "brightness_attack"}},
    "body": {"window_ms": [60, 350],
             "readings": {"brightness": "brightness_body"}},
    "held": {"window_ms": [350, None],
             "readings": {"vibrato": "vibrato_depth_cents"}},
    "release": {"window_ms": ["note off", "-20 dB"],
                "readings": {"length": "release_ms"}},
}
#: the step a direction moves a reading by (a factor of its own value);
#: sharpness is the attack's rise read the other way (sharper = shorter)
STEPS = {("sharpness", "more"): ("attack_rise_ms", 0.4),
         ("sharpness", "less"): ("attack_rise_ms", 3.0),
         ("brightness", "more"): (None, 1.45),
         ("brightness", "less"): (None, 0.65),
         ("vibrato", "more"): ("vibrato_depth_cents", 1.5),
         ("vibrato", "less"): ("vibrato_depth_cents", 0.4),
         ("length", "more"): ("release_ms", 2.0),
         ("length", "less"): ("release_ms", 0.5)}
#: how far a phase not named may move: absolute (ms, cents) or a share of
#: its own value
HOLD = {"attack_rise_ms": ("abs", 12.0),
        "brightness_attack": ("rel", 0.25),
        "brightness_body": ("rel", 0.25),
        "vibrato_depth_cents": ("abs", 8.0),
        "release_ms": ("abs", 120.0)}
#: a held note is one this long or longer (s), for "only on the long notes"
LONG_NOTE_S = 0.45

# -- reading a statement ------------------------------------------------------------
_PHASE_WORDS = [
    ("attack", r"attack|onset|transient|start of|pluck|sting|"
               r"атак|начал|укол|щипок|удар"),
    ("held", r"long note|held|sustained|долг|длинн|удерж|протяж"),
    ("body", r"body|корпус|тел[оау]|середин"),
    ("release", r"release|tail|decay|end of|хвост|отпуск|затух|окончан"),
]
_QUALITY_WORDS = [
    ("vibrato", r"vibrato|вибрато"),
    ("brightness", r"bright|dark|dull|ring|harsh|mellow|"
                   r"ярк|т[её]мн|глух|звон|резк.*тембр|мягк.*тембр"),
    ("sharpness", r"sharp|soft|slow|fast|punch|snap|sting|blunt|"
                  r"резк|мягк|медлен|быстр|укол|щелч|вял"),
    ("length", r"long|short|ring out|cut|длинн|коротк|обрыв"),
]
_MORE = r"more|bright|sharp|fast|punch|snap|sting|ring|harsh|long|deep|" \
        r"больше|сильн|ярк|резк|быстр|укол|звон|длинн|глубж|шире"
_LESS = r"less|dark|dull|mellow|soft|slow|blunt|short|cut|no |without|" \
        r"меньше|слаб|т[её]мн|глух|мягк|медлен|вял|коротк|без"


def _clause(text: str) -> Optional[dict]:
    t = text.lower()
    phase = next((p for p, w in _PHASE_WORDS if re.search(w, t)), None)
    quality = next((q for q, w in _QUALITY_WORDS if re.search(w, t)), None)
    if quality == "vibrato":
        phase = "held"
    if quality is None and phase is not None:
        quality = {"attack": "sharpness", "body": "brightness",
                   "held": "vibrato", "release": "length"}[phase]
    if quality == "length" and phase in (None, "release"):
        phase = "release"
    if phase == "attack" and quality == "length":
        # "a short attack" is a sharp one
        quality = "sharpness"
    if phase is None:
        phase = {"sharpness": "attack", "brightness": "body",
                 "vibrato": "held", "length": "release"}.get(quality)
    if phase is None or quality not in PHASES[phase]["readings"]:
        return None
    only_long = bool(re.search(r"only|только", t)) and bool(
        re.search(r"long|held|долг|длинн|протяж", t))
    late = bool(re.search(r"late|delay|after|wait|позже|запаздыв|"
                          r"задерж|после", t))
    if quality == "length" and phase == "release":
        more = re.search(r"long|ring out|длинн", t)
        direction = "more" if more else "less"
    elif quality == "sharpness":
        # "short", "sting", "sharp" -> a sharper attack
        less = re.search(r"soft|slow|blunt|мягк|медлен|вял", t)
        direction = "less" if less else "more"
    else:
        less = re.search(_LESS, t)
        more = re.search(_MORE, t)
        if quality == "vibrato" and (only_long or late) and not less:
            direction = "more"
        elif less and not (more and more.start() < less.start()):
            direction = "less"
        elif more or quality == "vibrato":
            direction = "more"
        else:
            return None
    out = {"phase": phase, "quality": quality, "direction": direction,
           "text": text.strip()}
    if quality == "vibrato" and only_long:
        out["only_long"] = True
    if quality == "vibrato" and late:
        out["late"] = True
    return out


def parse(statement) -> dict:
    """A statement (text) or clauses (a list of dicts with phase, quality,
    direction) -> {"clauses", "unparsed"}. One clause per phase: a later
    clause on the same phase and quality replaces an earlier one."""
    if isinstance(statement, (list, tuple)):
        clauses, unparsed = [], []
        for c in statement:
            ph, q, d = c.get("phase"), c.get("quality"), c.get("direction")
            if ph in PHASES and q in PHASES[ph]["readings"] and \
                    d in ("more", "less"):
                clauses.append({k: c[k] for k in c if k in
                                ("phase", "quality", "direction",
                                 "only_long", "late", "text")})
            else:
                unparsed.append(c)
        return {"clauses": _dedupe(clauses), "unparsed": unparsed}
    parts = [p for p in re.split(r"[,;.]| then | and | but |, затем |"
                                 r" затем | потом | а | и | но ",
                                 " " + str(statement) + " ")
             if p.strip()]
    clauses, unparsed = [], []
    for p in parts:
        c = _clause(p)
        (clauses if c else unparsed).append(c or p.strip())
    return {"clauses": _dedupe(clauses), "unparsed": unparsed,
            "statement": str(statement)}


def _dedupe(clauses: List[dict]) -> List[dict]:
    seen: Dict[tuple, dict] = {}
    for c in clauses:
        seen[(c["phase"], c["quality"])] = c
    return list(seen.values())


# -- a goal and what must hold ---------------------------------------------------------
def to_goal(intent: dict, base: dict, lengths: List[float] = ()) -> dict:
    """The clauses as a sound goal on the voice's own readings `base`
    (instrument.listen_voice), and the readings of the phases not named
    with how far each may move. -> {"goal", "hold", "notes"}; "notes"
    says what could not be asked (no long note to put a vibrato on)."""
    goal: Dict[str, float] = {}
    notes: List[str] = []
    named = set()
    for c in intent["clauses"]:
        reading = PHASES[c["phase"]]["readings"][c["quality"]]
        named.add(reading)
        key, factor = STEPS[(c["quality"], c["direction"])]
        key = key or reading
        now = base.get(key)
        if key == "vibrato_depth_cents":
            now = now or 0.0
            if c["direction"] == "more":
                goal[key] = round(max(25.0, now * factor), 1)
                goal.setdefault("vibrato_rate_hz",
                                base.get("vibrato_rate_hz") or 5.5)
                long_ = [x for x in lengths if x >= 0.25]
                if c.get("only_long"):
                    held = [x for x in long_ if x >= LONG_NOTE_S]
                    if not held:
                        notes.append(f"no note of {LONG_NOTE_S:g} s or "
                                     f"longer here to hold a vibrato")
                        goal.pop(key)
                        goal.pop("vibrato_rate_hz", None)
                        continue
                    goal["vibrato_share"] = round(len(held) / len(long_), 3)
                    named.add("vibrato_share")
                else:
                    goal["vibrato_share"] = 0.6
                if c.get("late"):
                    goal["vibrato_delay_ms"] = max(
                        200.0, (base.get("vibrato_delay_ms") or 0.0) * 1.5)
            else:
                goal[key] = round(now * factor, 1)
            continue
        if not isinstance(now, (int, float)):
            notes.append(f"{key}: the voice shows no reading to move")
            named.discard(reading)
            continue
        target = round(max(0.5, now * factor), 3)
        if key == "attack_rise_ms" and c["direction"] == "less":
            # softer from an attack that rises in a millisecond or two:
            # a factor of its own value moves nothing; a step does
            target = round(max(now * factor, now + 25.0), 3)
        from .instrument import READINGS
        if abs(target - now) <= READINGS.get(key, (0.0,))[0]:
            # already as far that way as a step can take it (an attack
            # that rises in 0 ms is as sharp as it reads): held instead
            notes.append(f"{c['phase']} {c['quality']}: already "
                         f"{now:g} ({key}); no step moves it "
                         f"{c['direction']}, so it is held")
            named.discard(reading)
            continue
        goal[key] = target
    # one brightness named and the other held: the change is how the
    # timbre develops over the note, so the modulator track that shapes
    # it is in play (instrument._tl_hypothesis)
    ba, bb = base.get("brightness_attack"), base.get("brightness_body")
    if ba and bb and ("brightness_attack" in goal or
                      "brightness_body" in goal):
        goal["brightness_development"] = round(
            goal.get("brightness_body", bb) /
            goal.get("brightness_attack", ba), 3)
    hold = {}
    for key, (kind, v) in HOLD.items():
        if key in named or key in goal:
            continue
        now = base.get(key)
        if not isinstance(now, (int, float)):
            continue
        hold[key] = round(v if kind == "abs" else max(0.05, abs(now) * v), 3)
    return {"goal": goal, "hold": hold, "notes": notes}


# -- what each phase did --------------------------------------------------------------
def report(intent: dict, base: dict, after: dict, goal: dict,
           hold: dict) -> dict:
    """Per phase, before and after: the asked phases (did the reading move
    the asked way, did it reach the goal) and the held ones (did they
    stay). -> {"asked": [...], "held": [...], "all_asked_moved",
    "all_held"}."""
    from .instrument import READINGS
    asked = []
    for c in intent["clauses"]:
        reading = PHASES[c["phase"]]["readings"][c["quality"]]
        key = STEPS[(c["quality"], c["direction"])][0] or reading
        if key not in goal:
            continue
        a, b, g = base.get(key), after.get(key), goal[key]
        if key == "vibrato_depth_cents":
            a, b = a or 0.0, b or 0.0
        row = {"phase": c["phase"], "quality": c["quality"],
               "direction": c["direction"], "reading": key,
               "before": a, "after": b, "goal": g}
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            want = 1 if STEPS[(c["quality"], c["direction"])][1] > 1 else -1
            row["moved_the_asked_way"] = (b - a) * want > 0
            row["reached"] = abs(b - g) <= READINGS.get(key, (0.0,))[0]
        asked.append(row)
        if c.get("only_long") and "vibrato_share" in goal:
            asked.append({"phase": "held", "quality": "only on the long "
                          "notes", "reading": "vibrato_share",
                          "before": base.get("vibrato_share"),
                          "after": after.get("vibrato_share"),
                          "goal": goal["vibrato_share"],
                          "reached": after.get("vibrato_share") is not None
                          and abs(after["vibrato_share"] -
                                  goal["vibrato_share"]) <=
                          READINGS["vibrato_share"][0]})
    held = []
    for key, limit in hold.items():
        a, b = base.get(key), after.get(key)
        if not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
            continue
        phase = next(p for p, d in PHASES.items()
                     if key in d["readings"].values())
        held.append({"phase": phase, "reading": key, "before": a,
                     "after": b, "limit": limit,
                     "held": abs(b - a) <= limit})
    return {"asked": asked, "held": held,
            "all_asked_moved": all(r.get("moved_the_asked_way", True)
                                   for r in asked if "direction" in r),
            "all_held": all(r["held"] for r in held)}
