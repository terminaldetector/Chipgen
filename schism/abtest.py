"""abtest.py — A and B of a real module, played where a card points and
measured.

A is a copy of the module; B is the same copy with one mechanism taken out
(ablate.py). Both are played from the start of the song, so every effect
memory, tempo change and voice left sounding from earlier is what the song
has there, and cut at the address the card gives (an order and a row: the
trace says when the song first gets there). Two renders of each:

    isolated    only the card's channels: in libopenmpt through its
                interactive interface, the file untouched (an S3M's other
                channels at volume 0, since libopenmpt, like Scream Tracker
                3, drops a muted S3M channel's speed and tempo commands and
                the song moves; other formats' muted); in Schism Tracker,
                which still plays a disabled channel's commands (measured),
                by the channels' disabled bits in a copy; this is where the
                mechanism is measured
    in context  every channel; this says how much of the mix the
                mechanism is: the level of A - B against A ("null", dB)

and each in libopenmpt and, when it is installed, in Schism Tracker, whose
render is aligned to libopenmpt's (the lag found is reported; two players'
clocks drift apart by a few ms a minute at tempos that are not a whole
number of samples a tick).

What is measured on a render (`features`): the level; the strikes (onsets
that rise 6 dB or more over the 60 ms before them), each with its level and
right-minus-left balance; the modulation of the level (the spread of a
10 ms envelope in dB, where it is above the floor); the spectral centroid
over time (Hz, a 46 ms window every 23 ms); the balance over time; and,
when asked for, the pitch over time in cents from a reference (the same
reference for A and B, guessed from A). These are measurements; whether B
is worse is not among them.
"""

import hashlib
import math
from typing import Dict, Iterable, List, Optional

from . import ablate, corpus, openmpt, schismtracker
from .forge import dsp, phases

RATE = 44100
#: a strike rises at least this much over the 60 ms before it
STRIKE_RISE_DB = 6.0
#: and stands within this much of the loudest point of the render
STRIKE_FLOOR_DB = 45.0
BLOCK_MS = 10
CENTROID_WINDOW = 2048
SCHISM_MARGIN_S = 0.6


class ABError(ValueError):
    pass


def _db(x: float) -> float:
    return 20.0 * math.log10(x) if x > 1e-9 else -180.0


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def locate(data: bytes, order: int, row: int) -> dict:
    """When the song first plays (order, row), with the speed and tempo
    there and the order the song is in at that point."""
    with corpus.Module(data) as module:
        # at the render's own rate: libopenmpt's clock depends on it
        played = corpus.trace(module, rate=RATE)
    i = corpus._first_visit(played["rows"], order, row)
    if i is None:
        raise ABError(f"the song never plays order {order} row {row}")
    o, pattern, r, speed, tempo, at = played["rows"][i]
    return {"order": o, "pattern": pattern, "row": r, "speed": speed,
            "tempo": tempo, "at": at, "tick_ms": round(2500.0 / tempo, 3),
            "row_ms": round(2500.0 / tempo * speed, 3),
            "song_seconds": played["seconds"], "rows": played["rows"]}


# -- rendering ---------------------------------------------------------------
def _cut(frames, start: float, seconds: float):
    a = max(0, int(round(start * RATE))) * 2
    b = a + int(round(seconds * RATE)) * 2
    chunk = frames[a:b]
    return list(chunk[0::2]), list(chunk[1::2])


def render_openmpt(data: bytes, start: float, seconds: float, keep=None):
    """libopenmpt's render of [start, start + seconds]; with `keep`, only
    those channels are heard (the others play at volume 0: see
    openmpt.render_channels for why not muted)."""
    if keep:
        frames = openmpt.render_channels(data, keep, RATE, start + seconds)
    else:
        with openmpt.Song(data) as song:
            frames = song.render(RATE, seconds=start + seconds)
    return _cut(frames, start, seconds)


def _envelope_ms(left, right):
    mono = [(a + b) * 0.5 for a, b in zip(left, right)]
    return phases.envelope(mono, RATE)


def _correlation(a, b) -> float:
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a)
                    * sum((y - mb) ** 2 for y in b)) or 1e-12
    return num / den


def _align(reference, candidate, offset: int, low: int, high: int) -> int:
    """The lag (ms, from `low` to `high`) at which candidate[offset + lag:]
    best matches the reference, on dB envelopes floored 60 dB under their
    peaks: every 2 ms first, then to the ms."""
    def floored(env):
        db = [_db(v) for v in env]
        top = max(db) if db else 0.0
        return [max(v, top - 60.0) for v in db]
    ref, cand = floored(reference), floored(candidate)
    n = len(ref)

    def score(lag, step):
        pairs = [(ref[i], cand[offset + lag + i]) for i in range(0, n, step)
                 if 0 <= offset + lag + i < len(cand)]
        if len(pairs) < n // (2 * step):
            return -2.0
        return _correlation([p[0] for p in pairs], [p[1] for p in pairs])

    coarse = max(range(low, high + 1, 2), key=lambda lag: score(lag, 2))
    return max(range(max(low, coarse - 2), min(high, coarse + 2) + 1),
               key=lambda lag: score(lag, 1))


def render_schism(data: bytes, start: float, seconds: float, last_order: int,
                  reference=None, suffix: str = ".it"):
    """Schism Tracker plays whole songs, so the copy it gets ends after
    `last_order` (ablate.end_after); the part read is before that. With a
    `reference` render (libopenmpt's), the cut is aligned to it."""
    short = ablate.end_after(data, last_order).bytes()
    frames = schismtracker.render(short, RATE, suffix=suffix)
    if reference is None:
        left, right = _cut(frames, start, seconds)
        return left, right, None
    margin = SCHISM_MARGIN_S
    lead = min(margin, start)
    left, right = _cut(frames, start - lead, seconds + lead + margin)
    offset = int(lead * 1000)
    lag = _align(_envelope_ms(*reference), _envelope_ms(left, right), offset,
                 -offset, int(margin * 1000) - 10)
    a = int(round((lead + lag / 1000.0) * RATE))
    n = len(reference[0])
    return left[a:a + n], right[a:a + n], lag


# -- measuring ---------------------------------------------------------------
def _blocks(x, size):
    return [x[i:i + size] for i in range(0, len(x) - size + 1, size)]


def _rms(x) -> float:
    return math.sqrt(sum(v * v for v in x) / len(x)) if x else 0.0


def _strikes(db: List[float], balance_at, floor: float) -> List[dict]:
    out = []
    n = len(db)
    for i in range(1, n - 1):
        if db[i] < floor:
            continue
        lo, hi = max(0, i - 15), min(n, i + 16)
        if db[i] < max(db[lo:hi]):
            continue
        before = db[max(0, i - 60):max(1, i - 2)]
        rise = db[i] - min(before) if before else 0.0
        if rise < STRIKE_RISE_DB:
            continue
        if out and i - out[-1]["ms"] < 30:
            if db[i] > out[-1]["db"]:
                out.pop()
            else:
                continue
        out.append({"ms": i, "db": round(db[i], 2), "rise_db": round(rise, 2),
                    "balance_db": balance_at(i)})
    return out


def _decimated(mono, factor=4):
    low = dsp.filtered(dsp.filtered(mono, "lp", RATE / (2.6 * factor), RATE),
                       "lp", RATE / (2.6 * factor), RATE)
    return low[::factor], RATE / factor


def guess_f0(mono) -> Optional[float]:
    """The fundamental of the loudest 93 ms of a render, by trying a fifth at
    a time from 40 Hz up and keeping the highest that is nearly as periodic
    as the best (a period's multiples are periodic too)."""
    x, rate = _decimated(mono)
    size = 1024
    if len(x) < size:
        return None
    step = size // 4
    best_start = max(range(0, len(x) - size + 1, step),
                     key=lambda s: _rms(x[s:s + size]))
    seg = x[best_start:best_start + size]
    found = []
    f = 40.0
    while f < rate / 4:
        cents, periodicity = phases._pitch(seg, rate, f)
        if cents is not None:
            found.append((f * 2 ** (cents / 1200.0), periodicity))
        f *= 1.5
    if not found:
        return None
    top = max(p for _, p in found)
    return max(hz for hz, p in found if p >= 0.9 * top)


def pitch_track(mono, f0: float, hop_ms: int = 10) -> List[Optional[float]]:
    """Cents from `f0` every `hop_ms`, on a 46 ms window, searched within a
    fifth of f0 and of its octave: where the octave above is as periodic,
    the note is there (+1200)."""
    x, rate = _decimated(mono)
    size, hop = 512, max(1, int(rate * hop_ms / 1000.0))
    out = []
    for start in range(0, len(x) - size + 1, hop):
        seg = x[start:start + size]
        if _rms(seg) < 1e-4:
            out.append(None)
            continue
        cents, p = phases._pitch(seg, rate, f0)
        up, pu = phases._pitch(seg, rate, f0 * 2)
        if up is not None and (cents is None or pu >= 0.97 * p):
            out.append(round(1200.0 + up, 1))
        else:
            out.append(None if cents is None else round(cents, 1))
    return out


def features(left, right, f0: Optional[float] = None) -> dict:
    """What a render does: level, strikes, modulation, centroid, balance,
    and pitch when `f0` is given."""
    mono = [(a + b) * 0.5 for a, b in zip(left, right)]
    centred = phases.centred(mono, RATE)
    env = phases.envelope(centred, RATE)
    db = [_db(v) for v in env]
    peak = max(db) if db else -180.0
    floor = peak - STRIKE_FLOOR_DB
    size = int(RATE * BLOCK_MS / 1000)
    lb = [_rms(b) for b in _blocks(left, size)]
    rb = [_rms(b) for b in _blocks(right, size)]
    level = [_db(math.sqrt((a * a + b * b) / 2.0)) for a, b in zip(lb, rb)]
    balance = [round(_db(b) - _db(a), 2) if max(a, b) > 1e-6 else None
               for a, b in zip(lb, rb)]

    def balance_at(ms):
        k = min(len(balance) - 1, ms // BLOCK_MS)
        return balance[k] if k >= 0 else None

    loud = [v for v in level if v > floor]
    mean = sum(loud) / len(loud) if loud else None
    spread = (math.sqrt(sum((v - mean) ** 2 for v in loud) / len(loud))
              if loud else None)
    centroids = []
    for start in range(0, len(mono) - CENTROID_WINDOW + 1,
                       CENTROID_WINDOW // 2):
        seg = mono[start:start + CENTROID_WINDOW]
        if _db(_rms(seg)) < floor:
            centroids.append(None)
            continue
        value, _ = phases._brightness(seg, RATE, None)
        centroids.append(None if value is None else round(value[0], 1))
    present = [c for c in centroids if c is not None]
    out = {
        "seconds": round(len(mono) / RATE, 3),
        "level_db": round(_db(_rms(mono)), 2),
        "peak_db": round(peak, 2),
        "strikes": _strikes(db, balance_at, floor),
        "modulation_db": None if spread is None else round(spread, 2),
        "level_track_db": [round(v, 1) for v in level],
        "centroid_hz": {
            "mean": round(sum(present) / len(present), 1) if present else None,
            "first": present[0] if present else None,
            "last": present[-1] if present else None,
            "track": centroids,
            "hop_ms": round(1000.0 * CENTROID_WINDOW / 2 / RATE, 2)},
        "balance_db": {
            "mean": round(sum(b for b in balance if b is not None)
                          / max(1, sum(1 for b in balance if b is not None)),
                          2),
            "min": min((b for b in balance if b is not None), default=None),
            "max": max((b for b in balance if b is not None), default=None),
            "track": balance},
        "block_ms": BLOCK_MS}
    if f0:
        track = pitch_track(mono, f0)
        voiced = [c for c in track if c is not None]
        out["pitch"] = {"f0_reference_hz": round(f0, 2), "hop_ms": 10,
                        "track_cents": track,
                        "voiced": len(voiced), "of": len(track)}
        if voiced:
            m = sum(voiced) / len(voiced)
            out["pitch"].update(
                mean_cents=round(m, 1),
                spread_cents=round(math.sqrt(sum((c - m) ** 2
                                                 for c in voiced)
                                             / len(voiced)), 1),
                low_cents=min(voiced), high_cents=max(voiced))
    return out


def null_db(a, b) -> float:
    """The level of A - B against A, both stereo (left, right) lists."""
    diff = [x - y for x, y in zip(a[0] + a[1], b[0] + b[1])]
    return round(_db(_rms(diff)) - _db(_rms(a[0] + a[1])), 2)


def _summary(f: dict) -> dict:
    keep = {k: f[k] for k in ("level_db", "peak_db", "modulation_db")}
    keep["strikes"] = len(f["strikes"])
    keep["centroid_mean_hz"] = f["centroid_hz"]["mean"]
    keep["balance_min_db"] = f["balance_db"]["min"]
    keep["balance_max_db"] = f["balance_db"]["max"]
    if "pitch" in f:
        keep["pitch_spread_cents"] = f["pitch"].get("spread_cents")
        keep["pitch_low_cents"] = f["pitch"].get("low_cents")
        keep["pitch_high_cents"] = f["pitch"].get("high_cents")
    return keep


def run(data: bytes, order: int, row: int, seconds: float,
        changes: Iterable[str], channels: Iterable[int],
        engines: Iterable[str] = ("openmpt", "schism"), pitch: bool = False,
        context: bool = True, keep_audio: bool = False) -> dict:
    """One A/B: `changes` (ablate.GRAMMAR) taken out of a copy, read at
    (order, row) for `seconds` on `channels` (1-based) alone and in the
    mix. The bytes given are not changed (the report says so, by hash)."""
    before = sha256(data)
    suffix = "." + (ablate.kind(data) or "it")
    where = locate(data, order, row)
    rows = where.pop("rows")
    start = where["at"]
    # every order the song passes before the cut: an end marker after the
    # highest of them changes nothing that is read
    last_order = max((r[0] for r in rows if r[5] <= start + seconds + 1.0),
                     default=order)
    b_patch = ablate.apply(data, changes)
    channels = list(channels)
    a_solo = ablate.solo(data, channels)
    b_solo = ablate.solo(b_patch.bytes(), channels)
    report = {"address": dict(where, seconds=seconds, channels=channels),
              "change": {"asked": list(changes), "bytes": b_patch.record()},
              "isolation": {
                  "openmpt": "every other channel silenced through "
                             "libopenmpt's interactive interface (S3M: "
                             "volume 0; others: muted); nothing in the file "
                             "changes",
                  "schism": f"every other channel's disabled bit set in a "
                            f"copy ({len(a_solo.record())} bytes), in A and "
                            f"B alike"},
              "hashes": {"original": before, "A": before,
                         "B": sha256(b_patch.bytes()),
                         "A_schism_copy": sha256(a_solo.bytes()),
                         "B_schism_copy": sha256(b_solo.bytes())},
              "engines": {}, "audio": {}}
    engines = [e for e in ("openmpt", "schism") if e in engines
               and (e == "openmpt" or schismtracker.available())]
    reference = {}
    for engine in engines:
        renders = {}
        for label, blob, whole in (("A", a_solo.bytes(), data),
                                   ("B", b_solo.bytes(), b_patch.bytes())):
            if engine == "openmpt":
                renders[label] = render_openmpt(whole, start, seconds,
                                                keep=channels)
                reference[label] = renders[label]
            else:
                l, r, lag = render_schism(blob, start, seconds, last_order,
                                          reference.get(label), suffix)
                renders[label] = (l, r)
                renders[label + "_lag_ms"] = lag
        f0 = guess_f0([(a + b) * 0.5 for a, b in zip(*renders["A"])]) \
            if pitch else None
        fa = features(*renders["A"], f0=f0)
        fb = features(*renders["B"], f0=f0)
        entry = {"A": fa, "B": fb,
                 "summary": {"A": _summary(fa), "B": _summary(fb)},
                 "null_isolated_db": null_db(renders["A"], renders["B"])}
        if engine == "schism":
            entry["lag_ms"] = {"A": renders["A_lag_ms"],
                               "B": renders["B_lag_ms"]}
        if context and engine == "openmpt":
            a_mix = render_openmpt(data, start, seconds)
            b_mix = render_openmpt(b_patch.bytes(), start, seconds)
            entry["null_in_context_db"] = null_db(a_mix, b_mix)
            if keep_audio:
                report["audio"]["openmpt_mix"] = {"A": a_mix, "B": b_mix}
        if keep_audio:
            report["audio"][engine] = {"A": renders["A"], "B": renders["B"]}
        report["engines"][engine] = entry
    report["randomness"] = _randomness(data)
    report["hashes"]["original_after"] = sha256(data)
    report["original_untouched"] = report["hashes"]["original_after"] == before
    return report


def _randomness(data: bytes) -> dict:
    """IT instruments may vary each note's volume or pan at random; a
    render that hears them differs a little from one run to the next."""
    if ablate.kind(data) != "it":
        return {"instruments": []}
    from . import it_read
    try:
        module = it_read.read(data, load_data=False)
    except it_read.ITReadError:
        return {"instruments": []}
    numbers = [n for n, ins in enumerate(module.instruments, 1)
               if ins.random_volume or ins.random_pan]
    return {"instruments": numbers,
            "note": "these instruments vary each note's volume or pan at "
                    "random, so a render that hears them is not the same "
                    "twice: readings in the mix move a little between runs"
            if numbers else ""}
