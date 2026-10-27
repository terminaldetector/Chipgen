"""form.py — the piece's form, read twice: from the score and from the
audio (the audit of 27 October, P1: repetition and difference of the
sections, the harmonic cadence, the switches of density — "not RMS or a
spectrum in their place").

Each time the order plays a section (a slot) is described

    from the score   the pitch classes it sounds (weighted by duration),
                     the rows it strikes, notes per beat, the lead's
                     register
    from the audio   its chroma (the energy of each pitch class, 55 Hz -
                     2 kHz, from the rendered mix), onsets per beat, its
                     loudness and brightness

and every two slots are compared: 1.0 the same, 0.0 nothing shared. The
slots are lettered in order: one as close as SAME to an earlier one takes
its letter, one as close as VARIED takes it with a prime, the rest a new
letter — "A A' B C", once from the score and once from the audio, and
whether the two agree.

The plan's functions are checked against it: a variation should sit near
its statement (VARIED to SAME), a development further from it (under
VARIED), a reprise near it again, and a cadence should end on the key's
tonic. Each section's cadence is read from its harmony (the chord before
the last and the last, against the key: full V-I, plagal IV-I, half
ending on V, deceptive V-vi) and from its audio (the tonic's share of the
chroma in its last beat against the section's).
"""

import math
from typing import Dict, List, Optional

from . import features as F
from . import render as R

SAME = 0.9
VARIED = 0.7
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#",
              "B"]
_FLAT = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}


def _pc(name: str) -> Optional[int]:
    if not name:
        return None
    root = name[:2] if len(name) > 1 and name[1] in "#b" else name[:1]
    root = _FLAT.get(root, root)
    return NOTE_NAMES.index(root.upper()) if root.upper() in NOTE_NAMES \
        else None


def _cos(a, b) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def _near(a: float, b: float) -> float:
    return 1.0 - abs(a - b) / max(abs(a), abs(b), 1e-9)


# -- the score -----------------------------------------------------------------------------
def _score_slot(tl, s: dict, lead: Optional[str]) -> dict:
    notes = [n for n in tl.notes if s["t0"] <= n["start"] < s["t1"] and
             n.get("pitch") is not None]
    pcs = [0.0] * 12
    for n in notes:
        pcs[int(n["pitch"]) % 12] += max(0.0, min(n["end"], s["t1"])
                                         - n["start"])
    rows = s["row1"] - s["row0"]
    struck = [0] * rows
    for n in notes:
        k = n["row"] - s["row0"]
        if 0 <= k < rows:
            struck[k] = 1
    lpb = int(tl.content["structure"]["lpb"])
    leads = sorted(n["pitch"] for n in notes if n["voice"] == lead)
    return {"pcs": pcs, "struck": struck,
            "per_beat": len(notes) / max(1e-9, rows / lpb),
            "lead": leads[len(leads) // 2] if leads else None}


def _score_sim(a: dict, b: dict) -> float:
    parts = [_cos(a["pcs"], b["pcs"])]
    n = min(len(a["struck"]), len(b["struck"]))
    both = sum(1 for i in range(n) if a["struck"][i] and b["struck"][i])
    either = sum(1 for i in range(n) if a["struck"][i] or b["struck"][i])
    parts.append(both / either if either else 1.0)
    parts.append(_near(a["per_beat"], b["per_beat"]))
    if a["lead"] is not None and b["lead"] is not None:
        parts.append(max(0.0, 1.0 - abs(a["lead"] - b["lead"]) / 12.0))
    return round(sum(parts) / len(parts), 3)


# -- the audio -----------------------------------------------------------------------------
def chroma(mono, rate: float = R.RATE, size: int = 4096) -> List[float]:
    """Energy per pitch class, 55 Hz - 2 kHz, summed over frames."""
    out = [0.0] * 12
    window = F._hann(size)
    for start in range(0, max(1, len(mono) - size), size // 2):
        frame = [mono[start + i] * window[i] for i in range(size)]
        power = F._rfft_power(frame)
        for k in range(1, len(power)):
            f = k * rate / size
            if f < 55.0 or f > 2000.0:
                continue
            midi = 69 + 12 * math.log2(f / 440.0)
            out[int(round(midi)) % 12] += power[k]
    total = sum(out) or 1.0
    return [v / total for v in out]


def _audio_slot(mono, rate: float, beat_s: float) -> dict:
    env, hop, _low = F.onset_envelope(mono, rate)
    onsets = F.peaks(env, hop)
    secs = len(mono) / rate
    bal = F.balance(mono, rate)
    rms = math.sqrt(sum(v * v for v in mono) / len(mono)) if mono else 0.0
    return {"chroma": chroma(mono, rate),
            "per_beat": len(onsets) / max(1e-9, secs / beat_s),
            "centroid": bal.get("centroid_hz") or 0.0,
            "level_db": 20 * math.log10(rms) if rms > 1e-9 else -120.0}


def _audio_sim(a: dict, b: dict) -> float:
    parts = [_cos(a["chroma"], b["chroma"]),
             _near(a["per_beat"], b["per_beat"]),
             _near(a["centroid"], b["centroid"]),
             max(0.0, 1.0 - abs(a["level_db"] - b["level_db"]) / 12.0)]
    return round(sum(parts) / len(parts), 3)


# -- letters, cadences, the plan --------------------------------------------------------------
def letters(sim: List[List[float]]) -> List[str]:
    out: List[str] = []
    next_letter = 0
    for i in range(len(sim)):
        best = max(range(i), key=lambda j: sim[i][j], default=None)
        if best is not None and sim[i][best] >= SAME:
            out.append(out[best].rstrip("'"))
        elif best is not None and sim[i][best] >= VARIED:
            out.append(out[best].rstrip("'") + "'")
        else:
            out.append(chr(65 + next_letter))
            next_letter += 1
    return out


def cadence(harmony: List[str], key: str) -> dict:
    """The section's last two chords against the key."""
    tonic = _pc((key or "C").split()[0])
    minor = "minor" in (key or "").lower()
    chords = [c for c in (harmony or []) if c]
    if tonic is None or not chords:
        return {"kind": "unknown", "why": "no harmony or key to read"}
    last = _pc(chords[-1])
    before = _pc(chords[-2]) if len(chords) > 1 else None
    rel = lambda pc: None if pc is None else (pc - tonic) % 12
    l, b = rel(last), rel(before)
    if l == 0 and b == 7:
        kind = "full (V-I)"
    elif l == 0 and b == 5:
        kind = "plagal (IV-I)"
    elif l == 0:
        kind = "ends on the tonic"
    elif l == 7:
        kind = "half (ends on V)"
    elif b == 7 and l == (8 if minor else 9):
        kind = "deceptive (V-vi)"
    else:
        kind = "open (no cadence)"
    return {"kind": kind, "chords": chords[-2:],
            "closes": kind.startswith(("full", "plagal", "ends on"))}


def measure(tl, cache=None) -> dict:
    """The form from the score and from the audio, the plan checked, each
    section's cadence."""
    slots = tl.slots()
    content = tl.content
    lead = next((v for v, i in content["instruments"].items()
                 if i.get("role") == "lead"), None)
    key = content["structure"].get("key") or "C major"
    lpb = int(content["structure"]["lpb"])
    beat_s = tl.duration / max(1, len(tl.rows)) * lpb
    score = [_score_slot(tl, s, lead) for s in slots]
    rng = tl.range("all")
    mix = R.render_range(tl, rng, cache=cache, method="full")
    tonic = _pc(key.split()[0])
    audio = []
    tonic_end = []
    for s in slots:
        mono = list(mix.segment(s["t0"], s["t1"], mono=True))
        audio.append(_audio_slot(mono, R.RATE, beat_s))
        last = list(mix.segment(max(s["t0"], s["t1"] - beat_s), s["t1"],
                                mono=True))
        if tonic is not None and last:
            share = chroma(last)[tonic]
            tonic_end.append(round(share / max(1e-9, audio[-1]["chroma"][
                tonic]), 2))
        else:
            tonic_end.append(None)
    n = len(slots)
    s_sim = [[_score_sim(score[i], score[j]) for j in range(n)]
             for i in range(n)]
    a_sim = [[_audio_sim(audio[i], audio[j]) for j in range(n)]
             for i in range(n)]
    plan = {p["id"]: p for p in content["structure"].get("plan") or []}
    sections = {x["id"]: x for x in content.get("sections", [])}
    rows = []
    first_of: Dict[str, int] = {}
    statement = None
    checks = []
    for i, s in enumerate(slots):
        sid = s["section"]
        fn = (plan.get(sid) or {}).get("function") or \
            ((sections.get(sid) or {}).get("intent") or {}).get("function")
        if fn == "statement" and statement is None:
            statement = i
        cad = cadence((sections.get(sid) or {}).get("harmony"), key)
        rows.append({"slot": s["slot"], "section": sid, "function": fn,
                     "bar": s["row0"] // tl.rows_per_bar + 1,
                     "notes_per_beat": round(score[i]["per_beat"], 2),
                     "onsets_per_beat": round(audio[i]["per_beat"], 2),
                     "level_db": round(audio[i]["level_db"], 1),
                     "centroid_hz": round(audio[i]["centroid"], 0),
                     "cadence": cad, "tonic_in_last_beat": tonic_end[i]})
        first_of.setdefault(sid, i)
        if statement is not None and i != statement and fn:
            near = a_sim[i][statement]
            sn = s_sim[i][statement]
            want = {"variation": (VARIED, SAME), "reprise": (SAME, 1.01),
                    "recapitulation": (SAME, 1.01),
                    "development": (0.0, VARIED),
                    "contrast": (0.0, VARIED)}.get(fn)
            if want:
                ok = want[0] <= sn < want[1]
                checks.append({"slot": s["slot"], "section": sid,
                               "function": fn,
                               "to_statement": {"score": sn, "audio": near},
                               "expected": list(want), "met": ok})
        if fn in ("cadence", "ending", "close", "outro", "coda"):
            checks.append({"slot": s["slot"], "section": sid,
                           "function": fn, "cadence": cad["kind"],
                           "tonic_in_last_beat": tonic_end[i],
                           "met": bool(cad.get("closes"))})
    form_score = letters(s_sim)
    form_audio = letters(a_sim)
    return {"form": {"score": " ".join(form_score),
                     "audio": " ".join(form_audio),
                     "agree": form_score == form_audio},
            "sections": rows, "plan_checks": checks,
            "similarity": {"score": s_sim, "audio": a_sim},
            "thresholds": {"same": SAME, "varied": VARIED},
            "method": "per slot: pitch classes, struck rows, density and "
                      "the lead's register from the score; chroma (55 Hz-"
                      "2 kHz), onsets per beat, brightness and level from "
                      "the rendered mix, played from the top"}
