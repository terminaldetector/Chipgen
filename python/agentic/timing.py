"""timing.py — where each voice's notes are heard against the grid: ahead
or behind, how evenly, and how the off-beats swing (the audit of 27
October, P1: "the time of the attack against the grid, micro-delays,
groove — not RMS or a spectrum in their place").

For each voice the voice alone is played from the top of the piece
(method "full"), its onsets found in the audio (features.onset_envelope /
peaks, then each one refined to the millisecond on its own envelope: its
steepest rise), and each written note matched to
the audio onset nearest it within MATCH_S. A note's lateness is split in
two:

    written     where the score puts the note against its row: a cell's
                note delay (Cxx), a swing the rhythm op wrote
    sounding    where the audio starts against where the note was written
                to start: the patch's attack, the engine's latency —
                what the ear hears that the score does not say

and per voice: the mean and median of the total (negative is ahead), its
spread (the standard deviation: how evenly the voice sits), the share of
notes more than LATE_MS behind or ahead, and the swing — where the
off-beat eighths fall between two beats (0.50 straight, 0.67 a triplet
swing), read from the audio. Notes whose onset is not found in the audio
are counted and named, not guessed.
"""

import math
from typing import Dict, List, Optional

from . import features as F
from . import render as R

#: how far an audio onset may be from a written note to be its onset (s)
MATCH_S = 0.06
#: a note this far off its row is reported as ahead or behind (ms)
LATE_MS = 15.0


def _refine(mono, rate: float, t: float, window: float = 0.03) -> float:
    """The onset `t` (from 10 ms frames) to the millisecond: the steepest
    rise of the 1 ms envelope between t - 12 ms and t + `window`. (Half of
    the peak was tried first: where a note follows another of the same
    voice the envelope is already high, half the peak is reached before
    the new attack, and a voice read 9 ms ahead of its own key-on.)"""
    a = max(0, int((t - 0.012) * rate))
    b = min(len(mono), int((t + window) * rate))
    hop = max(1, int(rate / 1000.0))
    if b - a < 4 * hop:
        return t
    env = [math.sqrt(sum(v * v for v in mono[i:i + hop]) / hop)
           for i in range(a, b - hop, hop)]
    smooth = [sum(env[max(0, i - 1):i + 2]) / len(env[max(0, i - 1):i + 2])
              for i in range(len(env))]
    rises = [smooth[i + 1] - smooth[i] for i in range(len(smooth) - 1)]
    if not rises or max(rises) <= 0:
        return t
    k = max(range(len(rises)), key=lambda i: rises[i])
    return a / rate + k * hop / rate


def onsets(mono, rate: float = R.RATE) -> List[float]:
    env, hop, _low = F.onset_envelope(mono, rate)
    return [_refine(mono, rate, t) for t in F.peaks(env, hop)]


def voice(tl, v: str, cache=None) -> dict:
    """One voice: its notes against its rows, as heard alone."""
    rng = tl.range("all")
    r = R.render_range(tl, rng, voices=[v], cache=cache, method="full")
    mono = list(r.segment(rng["t0"], rng["t1"], mono=True))
    heard = onsets(mono)
    rows = tl.rows
    lpb = int(tl.content["structure"]["lpb"])
    notes = [n for n in tl.notes if n["voice"] == v and
             n.get("pitch") is not None]
    matched, missing = [], []
    for n in notes:
        row_t = rows[n["row"]]["t0"] - rng["t0"]
        start = n["start"] - rng["t0"]
        near = min(heard, key=lambda t: abs(t - start), default=None)
        if near is None or abs(near - start) > MATCH_S:
            missing.append(round(n["start"], 3))
            continue
        matched.append({"row": n["row"], "row_t": row_t,
                        "written_ms": 1000.0 * (start - row_t),
                        "sounding_ms": 1000.0 * (near - start),
                        "total_ms": 1000.0 * (near - row_t),
                        "offbeat": n["row"] % lpb == lpb // 2,
                        "onset": near})
    out = {"voice": v, "notes": len(notes), "matched": len(matched),
           "not_found": missing[:12], "not_found_count": len(missing)}
    if not matched:
        out["why"] = "no written note was found in the voice's audio"
        return out
    tot = sorted(m["total_ms"] for m in matched)
    mean = sum(tot) / len(tot)
    sd = math.sqrt(sum((x - mean) ** 2 for x in tot) / len(tot))
    out.update(
        mean_ms=round(mean, 1), median_ms=round(tot[len(tot) // 2], 1),
        spread_ms=round(sd, 1),
        written_ms=round(sum(m["written_ms"] for m in matched)
                         / len(matched), 1),
        sounding_ms=round(sum(m["sounding_ms"] for m in matched)
                          / len(matched), 1),
        behind=sum(1 for x in tot if x > LATE_MS),
        ahead=sum(1 for x in tot if x < -LATE_MS))
    beat = rows[min(lpb, len(rows) - 1)]["t0"] - rows[0]["t0"] \
        if len(rows) > lpb else None
    off = [m for m in matched if m["offbeat"]]
    if beat and off:
        pos = []
        for m in off:
            beat_start = m["row_t"] - (lpb // 2) * (beat / lpb)
            pos.append((m["onset"] - beat_start) / beat)
        out["swing"] = round(sum(pos) / len(pos), 3)
        out["offbeats"] = len(off)
    return out


def measure(tl, voices: Optional[List[str]] = None, cache=None) -> dict:
    """Every voice that plays (or `voices`), and a line on each: ahead,
    behind, uneven, swung."""
    vs = voices or [v for v in tl.voices()
                    if any(n["voice"] == v and n.get("pitch") is not None
                           for n in tl.notes)]
    per = {v: voice(tl, v, cache) for v in vs}
    notes = []
    for v, m in per.items():
        if "mean_ms" not in m:
            continue
        if m["mean_ms"] > LATE_MS:
            notes.append(f"{v} sits {m['mean_ms']:.0f} ms behind its rows "
                         f"({m['sounding_ms']:.0f} ms of it in the sound, "
                         f"{m['written_ms']:.0f} ms in the score)")
        elif m["mean_ms"] < -LATE_MS:
            notes.append(f"{v} sits {-m['mean_ms']:.0f} ms ahead of its rows")
        if m["spread_ms"] > LATE_MS:
            notes.append(f"{v} is uneven: its notes spread "
                         f"{m['spread_ms']:.0f} ms around their rows")
        if m.get("swing") is not None and m["swing"] > 0.56:
            notes.append(f"{v}'s off-beats fall at {m['swing']:.2f} of the "
                         f"beat (0.50 straight, 0.67 a triplet swing)")
    return {"voices": per, "notes": notes,
            "method": "each voice alone, played from the top; onsets from "
                      "spectral flux refined to the millisecond on the "
                      "envelope; matched to written notes within "
                      f"{MATCH_S * 1000:.0f} ms"}
