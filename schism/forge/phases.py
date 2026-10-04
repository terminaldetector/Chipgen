"""phases.py — one note read phase by phase, at the resolution each needs.

The eleven dials (descriptors.py) summarise a note: one brightness, one
attack, one motion for its whole life. (This is the IT forge's copy of
Chipgen's phase readings, on this project's own FFT.) That is what a search over patches
needs and it is blind exactly where a program works. An attack under about
15 ms disappears into the dials' 22 ms smoothing; the body and the release
are folded into one `decay`; a timbre that darkens inside the note has a
single brightness. These readings split the note instead:

    attack   key-on to just past the loudest point of its first 300 ms
    body     the next 250 ms (or less, if the key comes up first)
    held     the rest of the time the key is down
    release  from the key-up, up to 500 ms

and read each phase: its level (dBFS), its brightness (the centroid of the
power spectrum over the fundamental: 1 is a sine, a sawtooth is about 3,
and a phase that loses its upper partials reads lower even under a faint
noise floor; see `_brightness`) and its pitch against the written note. The attack time is read on a 1 ms envelope. Anything that
does not apply — a phase too short to have a spectrum, a noise that has no
pitch, a note already silent at its key-up, a sample with no loop — is
None with the reason next to it, never a zero that a search would then
happily optimise.

`in_context` reads a part inside its phrase, against the parts around it:
whether each attack stands above the background and how far each body sits
under it. `spread` compares the same reading across registers.

These are measurements of a signal. They say whether a change did what it
was meant to do (the body is darker, the attack is quicker, the release is
shorter); they do not say whether it sounds good, and nothing here claims
to.
"""

import math
from typing import Dict, List, Optional

from . import dsp

ENVELOPE_HOP_MS = 1.0
ENVELOPE_WINDOW_MS = 2.0
ATTACK_SEARCH_MS = 300.0
BODY_MS = 250.0
RELEASE_MS = 500.0
#: below this a phase is silence, and its brightness and pitch are not read
SILENT_DB = -60.0
#: a phase shorter than this many samples has no spectrum worth the name
MIN_WINDOW = 256
MAX_WINDOW = 4096
TOP_HZ = 12000.0


def _db(x: float) -> float:
    return 20.0 * math.log10(x) if x > 1e-12 else -240.0


def envelope(mono, rate: float):
    """RMS envelope on a 1 ms hop with a 2 ms window. -> list (index = ms)."""
    hop = max(1, int(round(rate * ENVELOPE_HOP_MS / 1000.0)))
    width = max(hop, int(round(rate * ENVELOPE_WINDOW_MS / 1000.0)))
    prefix = [0.0]
    total = 0.0
    for v in mono:
        total += v * v
        prefix.append(total)
    out = []
    n = len(mono)
    for start in range(0, max(1, n - width + 1), hop):
        end = min(n, start + width)
        out.append(math.sqrt(max(0.0, prefix[end] - prefix[start])
                             / max(1, end - start)))
    return out


def centred(mono, rate: float, ms: float = 50.0):
    """`mono` less its 50 ms moving average. A render's DC is removed as one
    mean over the whole song, so a silence after a note sits at a constant
    offset (measured: -37 dBFS) that an envelope would read as a note that
    never ends; this takes it out and leaves anything above ~20 Hz."""
    half = max(1, int(rate * ms / 2000.0))
    n = len(mono)
    prefix = [0.0]
    total = 0.0
    for v in mono:
        total += v
        prefix.append(total)
    out = []
    for i in range(n):
        a, b = max(0, i - half), min(n, i + half + 1)
        out.append(mono[i] - (prefix[b] - prefix[a]) / (b - a))
    return out


def _rms(x) -> float:
    return math.sqrt(sum(v * v for v in x) / len(x)) if len(x) else 0.0


def _pow2_at_most(n: int) -> int:
    p = 1
    while p * 2 <= n:
        p *= 2
    return p


def _brightness(x, rate, f0):
    """Centroid of the power spectrum over f0/2..12 kHz, divided by f0 (by
    1 Hz when there is no f0). -> ((power, magnitude) centroids, window)
    or (None, why).

    Power, not magnitude, is what the phases call brightness. Measured on an
    FM bass with its modulators turned down step by step: the 2nd and 3rd
    harmonics fell 22 and 17 dB, and the magnitude centroid *rose* 4.7 ->
    5.4, because a -40 dB residue above the 6th harmonic (the DAC's grit)
    weighs as much in magnitudes as the partials that were going away. The
    power centroid follows the partials. The magnitude centroid is kept as
    `brightness_mag`, the dials' definition, for comparison."""
    size = min(MAX_WINDOW, _pow2_at_most(len(x)))
    if size < MIN_WINDOW:
        return None, f"shorter than {1000.0 * MIN_WINDOW / rate:.0f} ms"
    start = (len(x) - size) // 2
    mags, binw = dsp.spectrum(x, start, size, rate)
    lo = max(1, int((f0 * 0.5 if f0 else 20.0) / binw))
    hi = min(len(mags) - 1, int(min(TOP_HZ, rate / 2.0) / binw))
    band = mags[lo:hi + 1]
    total = sum(band)
    power = sum(m * m for m in band)
    if total <= 0 or power <= 0:
        return None, "no energy in the band"
    by_mag = sum(m * i * binw for i, m in enumerate(band, lo)) / total
    by_power = sum(m * m * i * binw for i, m in enumerate(band, lo)) / power
    return (by_power / (f0 or 1.0), by_mag / (f0 or 1.0)), size


_WACF = {}


def _autocorrelation(seg):
    size = len(seg)
    spec = dsp.fft(list(seg) + [0.0] * size)
    power = [abs(c) ** 2 for c in spec]
    return [c.real for c in dsp.fft(power)[:size]]


def _window_acf(size):
    if size not in _WACF:
        _WACF[size] = _autocorrelation(dsp.hann(size))
    return _WACF[size]


def _pitch(x, rate, f0):
    """Fundamental by autocorrelation (FFT), searched within a fifth of the
    written note. -> (cents from f0, periodicity) or (None, why)."""
    if not f0:
        return None, "no written pitch"
    size = min(MAX_WINDOW, _pow2_at_most(len(x)))
    lag_hi = int(rate / (f0 / 1.5)) + 2
    if size < max(MIN_WINDOW, 3 * lag_hi // 1.5):
        return None, "too short for three periods"
    start = (len(x) - size) // 2
    seg = x[start:start + size]
    mean = sum(seg) / size
    window = dsp.hann(size)
    seg = [(v - mean) * window[i] for i, v in enumerate(seg)]
    acf = _autocorrelation(seg)
    if acf[0] <= 0:
        return None, "silent"
    # divided by the window's own autocorrelation (Boersma's correction):
    # without it a Hann window pulls a low note's peak to a shorter lag
    wacf = _window_acf(size)
    lag_lo = max(2, int(rate / (f0 * 1.5)))
    lag_hi = min(size // 2, lag_hi)
    norm = [acf[lag] / wacf[lag] if wacf[lag] > 1e-9 else 0.0
            for lag in range(lag_hi + 2)]
    best, best_lag = -1e30, 0
    for lag in range(lag_lo, lag_hi + 1):
        if norm[lag] > best:
            best, best_lag = norm[lag], lag
    if best_lag == 0:
        return None, "no period in range"
    periodicity = best / norm[0]
    if periodicity < 0.45:
        return None, f"no clear period (periodicity {periodicity:.2f})"
    a, b, c = norm[best_lag - 1], best, norm[best_lag + 1]
    denom = a - 2 * b + c
    shift = 0.5 * (a - c) / denom if denom else 0.0
    hz = rate / (best_lag + max(-0.5, min(0.5, shift)))
    return 1200.0 * math.log2(hz / f0), periodicity


def _phase(mono, rate, start_s, stop_s, f0, pitched):
    a, b = int(start_s * rate), int(stop_s * rate)
    x = mono[a:b]
    out = {"from_ms": round(start_s * 1000.0, 1),
           "to_ms": round(stop_s * 1000.0, 1)}
    why = {}
    if len(x) < 8:
        out.update(level_db=None, brightness=None, pitch_cents=None)
        why = {"level_db": "empty", "brightness": "empty",
               "pitch_cents": "empty"}
        out["why"] = why
        return out
    level = _db(_rms(x))
    out["level_db"] = round(level, 2)
    if level < SILENT_DB:
        out["brightness"] = out["pitch_cents"] = None
        why["brightness"] = why["pitch_cents"] = \
            f"silent (below {SILENT_DB:g} dBFS)"
    else:
        value, info = _brightness(x, rate, f0 if pitched else None)
        if value is None:
            why["brightness"] = info
            out["brightness"] = out["brightness_mag"] = None
        else:
            out["brightness"] = round(value[0], 3)
            out["brightness_mag"] = round(value[1], 3)
            out["window"] = info
        if pitched:
            cents, info = _pitch(x, rate, f0)
            out["pitch_cents"] = None if cents is None else round(cents, 1)
            if cents is None:
                why["pitch_cents"] = info
        else:
            out["pitch_cents"] = None
            why["pitch_cents"] = "unpitched"
    if why:
        out["why"] = why
    return out


def note(mono, rate: float, f0: Optional[float], key_off: float,
         pitched: bool = True, loop: Optional[tuple] = None) -> dict:
    """One note read phase by phase. `mono` starts at the key-on; the key
    comes up `key_off` seconds in. `loop` is (begin, end) frames of a sample
    loop when the engine has one, for the seam reading."""
    mono = centred(mono, rate)
    env = envelope(mono, rate)
    hop = ENVELOPE_HOP_MS / 1000.0
    total = len(mono) / rate
    out = {"resolution_ms": ENVELOPE_HOP_MS, "key_off_ms":
           round(key_off * 1000.0, 1), "length_ms": round(total * 1000.0, 1)}
    if not env or max(env) <= 0:
        out["silent"] = True
        return out
    search = min(len(env), int(min(ATTACK_SEARCH_MS / 1000.0, key_off) / hop)
                 + 1)
    search = max(1, search)
    peak_i = max(range(search), key=lambda i: env[i])
    peak = env[peak_i]
    onset = next((i for i in range(peak_i + 1) if env[i] >= 0.1 * peak), 0)
    t90 = next((i for i in range(onset, peak_i + 1) if env[i] >= 0.9 * peak),
               peak_i)
    out["attack"] = {"onset_ms": round(onset * ENVELOPE_HOP_MS, 1),
                     "rise_ms": round((t90 - onset) * ENVELOPE_HOP_MS, 1),
                     "peak_ms": round(peak_i * ENVELOPE_HOP_MS, 1),
                     "peak_db": round(_db(peak), 2)}
    attack_end = min(key_off, (peak_i + 10) * hop)
    body_end = min(key_off, attack_end + BODY_MS / 1000.0)
    release_end = min(total, key_off + RELEASE_MS / 1000.0)
    phases = {"attack": _phase(mono, rate, 0.0, max(attack_end, 0.005), f0,
                               pitched)}
    if body_end - attack_end >= 0.02:
        phases["body"] = _phase(mono, rate, attack_end, body_end, f0, pitched)
    else:
        phases["body"] = {"level_db": None, "brightness": None,
                          "pitch_cents": None,
                          "why": {"all": "the key comes up during the attack"}}
    if key_off - body_end >= 0.04:
        phases["held"] = _phase(mono, rate, body_end, key_off, f0, pitched)
    else:
        phases["held"] = {"level_db": None, "brightness": None,
                          "pitch_cents": None,
                          "why": {"all": "released before it is held"}}
    if release_end - key_off >= 0.02:
        phases["release"] = _phase(mono, rate, key_off, release_end, f0,
                                   pitched)
    else:
        phases["release"] = {"level_db": None, "brightness": None,
                             "pitch_cents": None,
                             "why": {"all": "nothing rendered after the "
                                            "key-up"}}
    out["phases"] = phases
    # the release: how long after the key-up the level falls 20 dB
    k = int(key_off / hop)
    before = env[max(0, k - 5):k] or env[max(0, k - 1):k + 1]
    at_off = sum(before) / len(before) if before else 0.0
    rel = {"ms_to_minus20": None}
    if _db(at_off) < SILENT_DB:
        rel["why"] = "already silent at the key-up"
    else:
        hit = next((i for i in range(k, len(env)) if env[i] <= 0.1 * at_off),
                   None)
        if hit is None:
            rel["why"] = (f"still within 20 dB of its key-up level "
                          f"{(len(env) - k) * ENVELOPE_HOP_MS:.0f} ms later")
        else:
            rel["ms_to_minus20"] = round((hit - k) * ENVELOPE_HOP_MS, 1)
    out["release"] = rel
    if loop is None:
        out["loop_seam"] = None
        out["loop_seam_why"] = "the sample has no loop"
    else:
        out["loop_seam"] = None
        out["loop_seam_why"] = "read on the sample itself: loop_seam()"
    return out


def loop_seam(data, begin: int, end: int) -> dict:
    """How well a sample loop joins: the jump and the change of slope where
    the end runs back into the beginning, each as a share of the loop's RMS
    step (1.0: the join is a step as large as a typical one in the loop).
    A clean loop reads near or below 1 on both; a click reads several."""
    if data is None or end - begin < 4:
        return {"step": None, "slope": None, "why": "no loop to read"}
    x = [float(v) for v in data[begin:end]]
    steps = [abs(b - a) for a, b in zip(x, x[1:])]
    typical = math.sqrt(sum(s * s for s in steps) / len(steps)) or 1e-9
    jump = abs(x[0] - x[-1])
    slope_end = x[-1] - x[-2]
    slope_start = x[1] - x[0]
    return {"step": round(jump / typical, 3),
            "slope": round(abs(slope_start - slope_end) / typical, 3)}


def value(reading: dict, path: str):
    """`reading` at a dotted path ("phases.body.brightness",
    "attack.rise_ms"), or None if any step is missing or None."""
    node = reading
    for part in path.split("."):
        if not isinstance(node, dict) or node.get(part) is None:
            return None
        node = node[part]
    return node


def why(reading: dict, path: str) -> Optional[str]:
    """Why a reading is None, if it says."""
    parts = path.split(".")
    node = reading
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return "not read"
        node = node[part]
    if isinstance(node, dict):
        reasons = node.get("why") or {}
        return reasons.get(parts[-1]) or reasons.get("all")
    return None


def spread(readings: List[dict], paths: List[str]) -> Dict[str, dict]:
    """The same readings at several registers: {path: {"values", "range"}};
    the range is None when fewer than two registers have a value."""
    out = {}
    for path in paths:
        values = [value(r, path) for r in readings]
        known = [v for v in values if v is not None]
        out[path] = {"values": values,
                     "range": (round(max(known) - min(known), 3)
                               if len(known) >= 2 else None)}
    return out


def in_context(solo, background, rate: float, notes, attack_ms: float = 40.0,
               body_ms: float = 200.0) -> dict:
    """A part inside its phrase. `solo` is the part alone, `background`
    everything else, same length and rate; `notes` [(key-on s, key-off s)].
    -> per-note attack and body margins (the part over the background, dB)
    and the mix's peak."""
    margins = []
    n = min(len(solo), len(background))
    for on, off in notes:
        row = {"at_ms": round(on * 1000.0, 1)}
        for name, start, stop in (("attack", on, on + attack_ms / 1000.0),
                                  ("body", on + attack_ms / 1000.0,
                                   min(off, on + (attack_ms + body_ms)
                                       / 1000.0))):
            a, b = int(start * rate), min(n, int(stop * rate))
            if b - a < 16:
                row[f"{name}_margin_db"] = None
                continue
            part = _rms(solo[a:b])
            rest = _rms(background[a:b])
            row[f"{name}_margin_db"] = round(_db(part) - _db(max(rest, 1e-9)),
                                             2)
        margins.append(row)
    mix = [solo[i] + background[i] for i in range(n)]
    peak = max((abs(v) for v in mix), default=0.0)
    known = [m["attack_margin_db"] for m in margins
             if m.get("attack_margin_db") is not None]
    bodies = [m["body_margin_db"] for m in margins
              if m.get("body_margin_db") is not None]
    return {"notes": margins,
            "attack_margin_db": round(sorted(known)[len(known) // 2], 2)
            if known else None,
            "body_margin_db": round(sorted(bodies)[len(bodies) // 2], 2)
            if bodies else None,
            "mix_peak": round(peak, 4),
            "clips": peak >= 0.999,
            "part_rms_db": round(_db(_rms(solo[:n])), 2),
            "background_rms_db": round(_db(_rms(background[:n])), 2)}
