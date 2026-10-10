"""features.py — one scale for our renders and other people's recordings.

The loop compares a fragment of the piece with a fragment of a reference
and with the agent's own sketches. What it can say about any of them, it
says here, from the samples, with the same code for all three:

    loudness       BS.1770 K-weighting, 400 ms blocks, the absolute and
                   relative gates: LUFS, so fragments are matched as a
                   listener's ear would roughly match them, not by peak
    balance        energy in low (<250 Hz), mid (250-2000), high
                   (2000-8000) and air (>8000) bands, as shares of the
                   whole, and the spectral centroid
    onsets         a spectral-flux envelope (10 ms), its peaks, onsets per
                   beat when a beat is known
    attack         the median rise time (10% to 90% of the local peak) of
                   the onsets
    tails          how long the sound takes to fall 20 dB after an onset,
                   up to the next one: the room or the echo in a mix
    stereo         side over mid energy, and the channels' correlation
    beat grid      tempo by the onset envelope's autocorrelation, beat
                   phase by a comb, the bar by the low band's accents —
                   for a recording with no score; a confidence goes with it

and, for one voice heard alone (our stems, or a reference's stems when its
score is at hand), what a note does: its attack, its brightness at the
attack and in the body (synthesis/phases.py), and its vibrato from a pitch
track (depth, rate, the delay before it starts).

These are measurements. They say how two fragments differ; they do not
say which is better, and nothing here claims to have listened.
"""

import math
from typing import List, Optional, Tuple

import analysis
import audio

RATE = 44100
_BANDS = (("low", 20.0, 250.0), ("mid", 250.0, 2000.0),
          ("high", 2000.0, 8000.0), ("air", 8000.0, 20000.0))


# -- plumbing -----------------------------------------------------------------
def split(stereo) -> Tuple[list, list]:
    """Interleaved stereo (array or list) -> (left, right) lists."""
    return list(stereo[0::2]), list(stereo[1::2])


def mono_of(stereo) -> list:
    return [0.5 * (stereo[i] + stereo[i + 1])
            for i in range(0, len(stereo) - 1, 2)]


def _db(x: float) -> float:
    return 10.0 * math.log10(x) if x > 1e-20 else -200.0


def _rfft_power(frame) -> list:
    """|FFT|^2 of a real frame (numpy when there is numpy)."""
    if audio.HAVE_NUMPY:
        import numpy as np
        spec = np.fft.rfft(np.asarray(frame, dtype=np.float64))
        return (spec.real ** 2 + spec.imag ** 2).tolist()
    spec = analysis._fft_pure(list(frame))
    return [abs(spec[k]) ** 2 for k in range(len(frame) // 2 + 1)]


_HANN = {}


def _hann(n: int) -> list:
    if n not in _HANN:
        _HANN[n] = analysis._hann(n)
    return _HANN[n]


# -- loudness (ITU-R BS.1770) ------------------------------------------------------
def _biquad(x, b, a):
    b0, b1, b2 = b
    _a0, a1, a2 = a
    y = [0.0] * len(x)
    x1 = x2 = y1 = y2 = 0.0
    for i, v in enumerate(x):
        out = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1 = x1, v
        y2, y1 = y1, out
        y[i] = out
    return y


def _k_coefficients(rate: float):
    """The two stages of the K-weighting, at any sample rate (the RBJ forms
    BS.1770's 48 kHz table is derived from): a +4 dB shelf above 1.5 kHz
    and a high-pass at 38 Hz."""
    out = []
    # shelf
    g, q, fc = 4.0, 1.0 / math.sqrt(2.0), 1500.0
    big_a = 10 ** (g / 40.0)
    w0 = 2 * math.pi * fc / rate
    alpha = math.sin(w0) / (2 * q)
    cw = math.cos(w0)
    sa = 2 * math.sqrt(big_a) * alpha
    b = (big_a * ((big_a + 1) + (big_a - 1) * cw + sa),
         -2 * big_a * ((big_a - 1) + (big_a + 1) * cw),
         big_a * ((big_a + 1) + (big_a - 1) * cw - sa))
    a = ((big_a + 1) - (big_a - 1) * cw + sa,
         2 * ((big_a - 1) - (big_a + 1) * cw),
         (big_a + 1) - (big_a - 1) * cw - sa)
    out.append((tuple(v / a[0] for v in b), tuple(v / a[0] for v in a)))
    # high-pass
    q, fc = 0.5, 38.0
    w0 = 2 * math.pi * fc / rate
    alpha = math.sin(w0) / (2 * q)
    cw = math.cos(w0)
    b = ((1 + cw) / 2, -(1 + cw), (1 + cw) / 2)
    a = (1 + alpha, -2 * cw, 1 - alpha)
    out.append((tuple(v / a[0] for v in b), tuple(v / a[0] for v in a)))
    return out


def loudness(stereo, rate: float = RATE, channels: int = 2) -> dict:
    """Integrated loudness of interleaved stereo (or of one channel, with
    `channels=1`), in LUFS: K-weighted, 400 ms blocks with 75% overlap,
    gated at -70 LUFS and then 10 LU under the ungated mean. A fragment
    shorter than one block is measured whole. Checked: a 997 Hz sine at
    -20 dBFS in both channels reads -20.04 LUFS (BS.1770 gives -20.0; the
    shelf here is the RBJ form at 44.1 kHz, not the 48 kHz table)."""
    chans = split(stereo) if channels == 2 else (list(stereo),)
    filtered = []
    for ch in chans:
        y = ch
        for b, a in _k_coefficients(rate):
            y = _biquad(y, b, a)
        filtered.append(y)
    n = len(filtered[0])
    block = int(0.4 * rate)
    step = max(1, block // 4)
    if n < block:
        z = sum(sum(v * v for v in ch) / max(1, n) for ch in filtered)
        return {"lufs": round(-0.691 + _db(z), 2), "blocks": 1,
                "method": "BS.1770 K-weighting, one block (fragment under "
                          "400 ms)"}
    prefix = []
    for ch in filtered:
        p = [0.0]
        total = 0.0
        for v in ch:
            total += v * v
            p.append(total)
        prefix.append(p)
    zs = []
    for start in range(0, n - block + 1, step):
        z = sum((p[start + block] - p[start]) / block for p in prefix)
        zs.append(z)
    gated = [z for z in zs if -0.691 + _db(z) > -70.0]
    if not gated:
        return {"lufs": -70.0, "blocks": len(zs),
                "method": "BS.1770: every block under the -70 LUFS gate"}
    rel = -0.691 + _db(sum(gated) / len(gated)) - 10.0
    final = [z for z in gated if -0.691 + _db(z) > rel]
    value = -0.691 + _db(sum(final) / len(final))
    return {"lufs": round(value, 2), "blocks": len(final),
            "method": "BS.1770 K-weighting, 400 ms blocks, absolute and "
                      "relative gates"}


# -- spectrum and balance -------------------------------------------------------------
def balance(mono, rate: float = RATE, size: int = 2048,
            frames: int = 24) -> dict:
    """Shares of energy per band and the centroid, averaged over up to
    `frames` Hann frames spread through the fragment."""
    n = len(mono)
    if n < size:
        size = 256
        if n < size:
            return {"why": "too short"}
    starts = sorted({int(i * (n - size) / max(1, frames - 1))
                     for i in range(frames)}) if n > size else [0]
    window = _hann(size)
    power = None
    for s in starts:
        p = _rfft_power([mono[s + i] * window[i] for i in range(size)])
        power = p if power is None else [x + y for x, y in zip(power, p)]
    binw = rate / size
    total = sum(power[1:]) or 1e-20
    shares = {}
    for name, lo, hi in _BANDS:
        a, b = max(1, int(lo / binw)), min(len(power), int(hi / binw) + 1)
        shares[name] = round(sum(power[a:b]) / total, 4)
    centroid = sum(p * k * binw for k, p in enumerate(power)) / total
    return {"shares": shares, "centroid_hz": round(centroid, 1),
            "method": f"{len(starts)} Hann frames of {size}, power"}


# -- onsets, attack, tails --------------------------------------------------------------
def onset_envelope(mono, rate: float = RATE, hop_s: float = 0.01,
                   size: int = 1024) -> Tuple[list, float, list]:
    """Spectral flux (half-wave rectified, log-compressed) every `hop_s`,
    and the low band's flux (below ~150 Hz) for the bar finder.
    -> (flux, hop in seconds, low-band flux)."""
    hop = max(1, int(round(hop_s * rate)))
    window = _hann(size)
    prev = None
    flux, low = [], []
    lo_bin = max(1, int(150.0 * size / rate))
    # frame k is centred on k * hop: the signal is padded by half a frame
    # in front, so a value's time is its index times the hop
    mono = [0.0] * (size // 2) + list(mono)
    for start in range(0, max(1, len(mono) - size), hop):
        frame = [mono[start + i] * window[i] for i in range(size)]
        mag = [math.log1p(1000.0 * math.sqrt(p)) for p in _rfft_power(frame)]
        if prev is None:
            flux.append(0.0)
            low.append(0.0)
        else:
            d = [m - q for m, q in zip(mag, prev)]
            flux.append(sum(v for v in d if v > 0))
            low.append(sum(v for v in d[:lo_bin] if v > 0))
        prev = mag
    return flux, hop / rate, low


def peaks(env, hop_s: float, min_gap_s: float = 0.05, k: float = 2.0,
          floor: float = 0.2) -> List[float]:
    """Onset times: local maxima above `k` times the moving median (300
    ms) and above `floor` times the fragment's strong flux (its 95th
    percentile) — the second keeps a quiet tail's flicker in a sparse
    passage from counting as a note. Of two maxima closer than
    `min_gap_s`, the higher stays. The defaults were set on a rendered
    Mega Drive mix whose note starts the score gives (74 of them, five
    voices with PSG noise hats and DAC drums): precision 1.00, recall
    0.99, timing within 4 ms (p10-p90); a lower `k` (1.4) found every
    note but also each noise burst's own fluctuation (precision 0.62)."""
    n = len(env)
    if n < 3:
        return []
    half = max(1, int(0.15 / hop_s))
    gap = max(1, int(min_gap_s / hop_s))
    ranked = sorted(env)
    strong = ranked[int(0.95 * (n - 1))]
    absolute = floor * strong
    found = []
    for i in range(1, n - 1):
        if not (env[i] >= env[i - 1] and env[i] >= env[i + 1]):
            continue
        lo, hi = max(0, i - half), min(n, i + half + 1)
        local = sorted(env[lo:hi])
        threshold = max(k * local[len(local) // 2], absolute) + 1e-6
        if env[i] > threshold:
            found.append(i)
    kept = []
    for i in found:
        if kept and i - kept[-1] < gap:
            if env[i] > env[kept[-1]]:
                kept[-1] = i
            continue
        kept.append(i)
    return [i * hop_s for i in kept]


def _env_1ms(mono, rate: float):
    hop = max(1, int(rate / 1000.0))
    return [math.sqrt(sum(v * v for v in mono[i:i + 2 * hop]) / (2 * hop))
            for i in range(0, max(1, len(mono) - 2 * hop), hop)]


def attack_and_tails(mono, rate: float, onset_times: List[float],
                     min_gap_s: float = 0.2) -> dict:
    """Median rise (10% -> 90% of the peak within 60 ms) of the onsets, and
    the median time to fall 20 dB under the peak, read only after onsets
    followed by at least `min_gap_s` without another (in a dense passage
    every tail runs into the next note, and its length would be the
    rhythm's, not the sound's). A tail that does not fall 20 dB before the
    next onset is counted apart, not in the median."""
    env = _env_1ms(mono, rate)
    rises, tails, cut, gaps = [], [], 0, 0
    for i, t in enumerate(onset_times):
        a = max(0, int(t * 1000.0) - 10)
        nxt = int(onset_times[i + 1] * 1000.0) if i + 1 < len(onset_times) \
            else len(env)
        seg = env[a:min(len(env), a + 70)]
        if len(seg) < 5 or max(seg) <= 1e-6:
            continue
        pk_i = max(range(len(seg)), key=lambda j: seg[j])
        pk = seg[pk_i]
        t10 = next((j for j in range(pk_i + 1) if seg[j] >= 0.1 * pk), 0)
        t90 = next((j for j in range(t10, pk_i + 1) if seg[j] >= 0.9 * pk),
                   pk_i)
        rises.append(t90 - t10)
        start = a + pk_i
        if nxt - start < int(min_gap_s * 1000.0):
            continue
        gaps += 1
        hit = next((j for j in range(start, min(nxt, len(env)))
                    if env[j] <= 0.1 * pk), None)
        if hit is None:
            cut += 1
        else:
            tails.append(hit - start)
    med = (lambda xs: sorted(xs)[len(xs) // 2] if xs else None)
    out = {"attack_rise_ms": med(rises), "tail_ms": med(tails),
           "tails_read": len(tails), "tails_cut_by_next_onset": cut,
           "onsets_read": len(rises)}
    if not tails:
        out["tail_why"] = (f"no onset is followed by {min_gap_s * 1000:.0f} "
                           f"ms without another" if not gaps else
                           "every tail ran into the next onset before "
                           "falling 20 dB")
    return out


def stereo(stereo_samples) -> dict:
    left, right = split(stereo_samples)
    mid = side = ll = rr = lr = 0.0
    for a, b in zip(left, right):
        m, s = 0.5 * (a + b), 0.5 * (a - b)
        mid += m * m
        side += s * s
        ll += a * a
        rr += b * b
        lr += a * b
    corr = lr / math.sqrt(ll * rr) if ll > 0 and rr > 0 else 1.0
    if mid <= 0:
        return {"side_over_mid_db": None, "correlation": round(corr, 3),
                "why": "silent"}
    ratio = _db(side) - _db(mid)
    out = {"side_over_mid_db": round(max(-60.0, ratio), 2),
           "correlation": round(corr, 3)}
    if ratio < -60.0:
        out["mono"] = True       # the floor: no side worth a number
    return out


# -- a beat grid for a recording with no score ------------------------------------------
def beat_grid(mono, rate: float = RATE, beats_per_bar: int = 4,
              lo_bpm: float = 70.0, hi_bpm: float = 180.0) -> dict:
    """Tempo, beat phase and the first downbeat of a recording, from its
    onset envelope. The tempo is the autocorrelation's best lag (with a mild
    preference for 120 BPM against octave errors); the phase is the comb
    with the most onset energy; the downbeat is the beat of the bar where
    the low band (kicks, bass) accents most. -> grid with a confidence
    (0..1, the autocorrelation peak over its neighbourhood)."""
    env, hop, low = onset_envelope(mono, rate)
    n = len(env)
    if n < 200:
        return {"why": "too short to find a beat (under 2 s)"}
    mean = sum(env) / n
    e = [v - mean for v in env]
    lag_lo, lag_hi = int(60.0 / hi_bpm / hop), int(60.0 / lo_bpm / hop) + 1
    acf = {}
    for lag in range(max(1, lag_lo - 2), lag_hi + 3):
        acf[lag] = sum(e[i] * e[i + lag] for i in range(n - lag)) / (n - lag)
    best, best_lag = None, None
    for lag in range(lag_lo, lag_hi + 1):
        bpm = 60.0 / (lag * hop)
        prior = math.exp(-0.5 * (math.log2(bpm / 120.0) / 0.9) ** 2)
        score = acf[lag] * (0.6 + 0.4 * prior)
        if best is None or score > best:
            best, best_lag = score, lag
    a, b, c = acf[best_lag - 1], acf[best_lag], acf[best_lag + 1]
    denom = a - 2 * b + c
    shift = 0.5 * (a - c) / denom if denom else 0.0
    period = (best_lag + max(-0.5, min(0.5, shift))) * hop
    neighbours = [abs(acf[lag]) for lag in range(lag_lo, lag_hi + 1)]
    conf = max(0.0, min(1.0, b / (max(neighbours) or 1e-9))) if b > 0 \
        else 0.0
    spread = sorted(neighbours)
    contrast = (b - spread[len(spread) // 2]) / (abs(b) + 1e-9)
    # beat phase: the comb with the most onset energy
    steps = max(1, int(round(period / hop)))
    phase_scores = []
    for ph in range(steps):
        total = 0.0
        k = ph
        while k < n:
            total += env[int(k)]
            k += period / hop
        phase_scores.append(total)
    phase = max(range(steps), key=lambda p: phase_scores[p]) * hop
    # downbeat: which beat of the bar the low band accents
    bar_scores = []
    for j in range(beats_per_bar):
        total = 0.0
        t = phase + j * period
        while t / hop < n:
            total += low[int(t / hop)]
            t += period * beats_per_bar
        bar_scores.append(total)
    first = phase + max(range(beats_per_bar), key=lambda j: bar_scores[j]) \
        * period
    # the earliest downbeat: a phase a hair under a whole bar is a downbeat
    # at (about) zero
    bar = period * beats_per_bar
    first = first % bar
    if bar - first < 0.06:
        first -= bar
    first = max(0.0, first)
    return {"bpm": round(60.0 / period, 2), "beat_s": round(period, 4),
            "beats_per_bar": beats_per_bar,
            "first_downbeat_s": round(first, 3),
            "confidence": round(max(0.0, min(1.0, 0.5 * conf
                                               + 0.5 * contrast)), 3),
            "method": "onset-envelope autocorrelation (tempo), comb (phase), "
                      "low-band accents (bar); estimated from the audio"}


# -- a voice alone: what its notes do -----------------------------------------------------
def pitch_track(mono, rate: float, f0: float, start_s: float, stop_s: float,
                hop_ms: float = 10.0, win: int = 2048,
                origin: Optional[float] = None) -> List[tuple]:
    """(seconds from `origin` — the note's start; `start_s` if not given —,
    cents from f0) every `hop_ms` over [start_s, stop_s). A window is
    centred on its time, so `win` sets how much smoothing the track has
    (2048 samples: 46 ms, enough for vibrato up to about 10 Hz)."""
    from synthesis import phases
    origin = start_s if origin is None else origin
    out = []
    t = start_s
    half = win // 2
    while t < stop_s:
        c = int(t * rate)
        seg = mono[max(0, c - half):c + half]
        if len(seg) >= win // 2:
            cents, _q = phases._pitch(seg, rate, f0)
            if isinstance(cents, (int, float)):
                out.append((round(t - origin, 4), cents))
        t += hop_ms / 1000.0
    return out


def vibrato(track: List[tuple], min_depth: float = 6.0) -> dict:
    """Depth (half the peak-to-peak, cents), rate (Hz, from the crossings
    of the detrended curve) and delay (ms before the swing first exceeds
    `min_depth`). None values come with the reason."""
    if len(track) < 12:
        return {"depth_cents": None, "why": "under 120 ms of pitch to read"}
    cents = [c for _t, c in track]
    mean = sum(cents) / len(cents)
    dev = [c - mean for c in cents]
    swing = sorted(abs(d) for d in dev)
    p90 = swing[int(0.9 * (len(swing) - 1))]
    if p90 < min_depth:
        return {"depth_cents": round(p90, 1), "rate_hz": None,
                "delay_ms": None, "why": "no vibrato: the pitch stays "
                                         f"within {min_depth:g} cents"}
    onset_i = next((i for i, d in enumerate(dev) if abs(d) >= min_depth),
                   None)
    onset = track[onset_i][0] if onset_i is not None else None
    # the rate from the swing's own crossings, between the first and the
    # last (a delay before the vibrato does not dilute it)
    crossings = [track[i - 1][0] + (track[i][0] - track[i - 1][0])
                 * (-dev[i - 1]) / (dev[i] - dev[i - 1])
                 for i in range(max(1, (onset_i or 1) - 2), len(dev))
                 if (dev[i - 1] < 0) != (dev[i] < 0)
                 and dev[i] != dev[i - 1]]
    rate_hz = None
    if len(crossings) >= 3 and crossings[-1] > crossings[0]:
        rate_hz = (len(crossings) - 1) / 2.0 / (crossings[-1]
                                                 - crossings[0])
    return {"depth_cents": round(p90, 1),
            "rate_hz": round(rate_hz, 2) if rate_hz else None,
            "delay_ms": round(1000.0 * onset, 0) if onset is not None
            else None}


def note_profile(mono, rate: float, notes: List[dict],
                 min_len_s: float = 0.25, limit: int = 8) -> dict:
    """What a voice's notes do, read on the voice alone: for up to `limit`
    of its notes at least `min_len_s` long — the attack (rise), the
    brightness at the attack and in the body (their ratio is the timbre's
    development), the release, and the vibrato. Medians over the notes.
    `notes` are {"start", "end", "f0"} in seconds and Hz, `mono` starting
    at 0."""
    from synthesis import phases
    reads = []
    for n in notes:
        if n["end"] - n["start"] < min_len_s or not n.get("f0"):
            continue
        a = int(n["start"] * rate)
        b = min(len(mono), int((n["end"] + 0.3) * rate))
        seg = mono[a:b]
        if len(seg) < int(0.1 * rate):
            continue
        r = phases.note(seg, rate, n["f0"], n["end"] - n["start"])
        # the window is 46 ms wide: start and stop half a window inside
        # the note, so the neighbours' pitches do not leak into the track
        track = pitch_track(mono, rate, n["f0"], n["start"] + 0.03,
                            n["end"] - 0.025, origin=n["start"])
        reads.append({"reading": r, "vibrato": vibrato(track)})
        if len(reads) >= limit:
            break
    if not reads:
        return {"notes": 0, "why": f"no note of {min_len_s * 1000:.0f} ms "
                                   f"or longer with a pitch"}

    def med(values):
        values = sorted(v for v in values if isinstance(v, (int, float)))
        return values[len(values) // 2] if values else None

    def phase_value(r, phase, key):
        return ((r.get("phases") or {}).get(phase) or {}).get(key)

    attack_b = med(phase_value(x["reading"], "attack", "brightness")
                   for x in reads)
    body_b = med(phase_value(x["reading"], "body", "brightness")
                 for x in reads)
    return {
        "notes": len(reads),
        "attack_rise_ms": med((x["reading"].get("attack") or {}).get(
            "rise_ms") for x in reads),
        "brightness_attack": round(attack_b, 3) if attack_b else None,
        "brightness_body": round(body_b, 3) if body_b else None,
        "brightness_development": round(body_b / attack_b, 3)
        if attack_b and body_b else None,
        "release_ms": med((x["reading"].get("release") or {}).get(
            "ms_to_minus20") for x in reads),
        "vibrato_depth_cents": med(x["vibrato"].get("depth_cents")
                                   for x in reads),
        "vibrato_rate_hz": med(x["vibrato"].get("rate_hz") for x in reads),
        "vibrato_delay_ms": med(x["vibrato"].get("delay_ms")
                                for x in reads),
        "method": "synthesis/phases.py per note (attack, body brightness = "
                  "power centroid over f0); vibrato from a 10 ms pitch track "
                  "(autocorrelation)"}


# -- a whole fragment ----------------------------------------------------------------------
def fragment(stereo_samples, rate: float = RATE,
             beat_s: Optional[float] = None) -> dict:
    """Everything above for one fragment of a mix."""
    mono = mono_of(stereo_samples)
    env, hop, _low = onset_envelope(mono, rate)
    times = peaks(env, hop)
    dur = len(mono) / rate
    out = {"seconds": round(dur, 3), "loudness": loudness(stereo_samples,
                                                          rate),
           "balance": balance(mono, rate), "stereo": stereo(stereo_samples),
           "onsets": len(times),
           "onsets_per_second": round(len(times) / dur, 2) if dur else None}
    if beat_s:
        out["onsets_per_beat"] = round(len(times) / (dur / beat_s), 2)
    out.update(attack_and_tails(mono, rate, times))
    return out


# -- two fragments ---------------------------------------------------------------------------
#: which readings answer which of the user's tags
DIMENSIONS = {
    "timbre": ["balance.centroid_hz", "balance.shares.high",
               "balance.shares.low", "voice.brightness_attack",
               "voice.brightness_body", "voice.brightness_development"],
    "modulation": ["voice.vibrato_depth_cents", "voice.vibrato_rate_hz",
                   "voice.vibrato_delay_ms", "voice.brightness_development"],
    "rhythm": ["onsets_per_beat", "attack_rise_ms"],
    "arrangement": ["onsets_per_beat", "balance.shares.low",
                    "balance.shares.mid", "balance.shares.high"],
    "development": ["loudness.lufs", "onsets_per_beat",
                    "balance.centroid_hz"],
    "space": ["stereo.side_over_mid_db", "stereo.correlation", "tail_ms"],
}


def get(profile: dict, path: str):
    node = profile
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, (int, float)) else None


def differences(ours: dict, theirs: dict, dimensions: List[str]) -> List[dict]:
    """The readings of the given dimensions, ours against theirs, with the
    gap. A reading either side lacks is left out and said so."""
    out = []
    for dim in dimensions:
        for path in DIMENSIONS.get(dim, []):
            a, b = get(ours, path), get(theirs, path)
            if a is None or b is None:
                continue
            gap = b - a
            rel = gap / abs(a) if a else None
            out.append({"dimension": dim, "reading": path, "ours": a,
                        "reference": b, "gap": round(gap, 4),
                        "relative": round(rel, 3) if rel is not None
                        else None})
    return out
