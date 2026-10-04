"""descriptors.py — read the DNA dials back off a rendered note.

The DNA says "brightness 0.7"; this module is the definition of what that
means. A note is rendered through the real player (probe.py: a one-note
module played by libopenmpt), and the functions here turn the samples into
the same eleven numbers, each on the same 0..1 scale and by the rule
written next to its axis in dna.AXIS_DOC. Nothing here knows what made the
sound or asks a model anything, which is the point: it is the local, cheap
judge that lets a search run a few hundred renders without a language model
in the loop.

The constants and the rules are the ones the Chipgen chip synthesis layer
uses (they were tuned on three FM and pulse chips); what is new here is
only that the note may be stereo, in which case the movement readings look
at each side as well as the sum, because a pan sweep moves nothing in the
mix and everything in the ear.

Everything is pure Python on lists of floats, and one note costs a few
tens of milliseconds to read.

## Scales

Time axes are logarithmic, because 5 ms against 10 ms is as audible a
difference as 200 ms against 400 ms and a linear axis would spend all its
resolution on the long end. Level axes are in dB for the same reason.
The constants are at the top so a disagreement is a one-line change and a
re-run of the bank survey (`python3 python/forge.py survey`).
"""

import math
import operator

from . import dsp

# -- scale constants ---------------------------------------------------------
ENVELOPE_HOP = 0.0025          # seconds between envelope points
ENVELOPE_WINDOW = 0.010
BRIGHT_LOW, BRIGHT_HIGH = 1.0, 16.0          # centroid / fundamental
ATTACK_REFERENCE, ATTACK_LONG = 0.002, 0.4   # seconds
DECAY_SHORT, DECAY_LONG = 0.03, 3.0
BODY_FLOOR_DB = -40.0
#: The sustain is read at a fixed moment, not at "the end of whatever was
#: held", so a quick probe and a long one agree: 0.6 s after the note
#: starts (or 0.25 s after its peak, for a slow swell), averaged over 150 ms.
BODY_AT = 0.6
BODY_AFTER_PEAK = 0.25
BASS_RATIO_LOW, BASS_RATIO_HIGH = 0.2, 100.0
RELEASE_SHORT, RELEASE_LONG = 0.02, 1.0
FLATNESS_LOW, FLATNESS_HIGH = 0.005, 0.5
METAL_INHARM_FULL = 0.35
METAL_ROUGH_FULL = 0.8
DETUNE_FULL = 0.4              # amplitude-beating depth (over the floor) that means "1.0"
MOTION_AM_FULL = 0.35
MOTION_PM_FULL = 35.0          # cents (std, over the floor)
#: What the estimators read on a steady note: the noise they have no
#: business calling movement. Measured on the sine/saw/organ patches.
MOVEMENT_FLOOR = 0.04
PM_FLOOR = 1.0
#: Movement is read while the note is above this share of its peak and for
#: at least this long; a note that dies sooner has no movement to read.
MOVEMENT_LEVEL = 0.025
MOVEMENT_SECONDS = 0.3
#: The slowest beating that counts as detune; below it a one-second note
#: has not gone round once and the envelope's own curve looks the same.
DETUNE_MIN_HZ = 0.7
DETUNE_MIN_SECONDS = 1.0
#: ...and the fastest: from here up it is vibrato or tremolo, motion.
MOTION_MIN_HZ = 3.5
PUNCH_FULL_OCTAVES = 3.0
#: The first milliseconds of any note are a step from silence, which is
#: broadband whatever the patch; the timbre starts after it.
ATTACK_WINDOW_SKIP = 0.008
#: Partials read for the harmonic profile.
HARMONICS = 24
#: Above this the spectrum is mostly the resampler and the noise floor.
TOP_HZ = 12000.0


def _clip(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _log_map(value: float, low: float, high: float) -> float:
    if value is None or value <= low:
        return 0.0
    return _clip(math.log(value / low) / math.log(high / low))


class Measurement:
    """Everything read off one note. `axes` are the 0..1 dials; the rest
    are the raw numbers behind them, kept because a mismatch is debugged in
    seconds and milliseconds, not in dials."""

    def __init__(self):
        self.axes = {}           # the eleven DNA axes, 0..1 (None = unmeasurable)
        self.raw = {}            # seconds, ratios, dB, Hz
        self.harmonics = []      # amplitude of partial 1..24, relative to the loudest
        self.bands = {}          # octave-band energy shares
        self.semitones = []      # share of the energy in each semitone from A0
        self.pitch = "exact"     # exact | sub_octave | octave_up | off
        self.measured = True

    def to_dict(self):
        return {"axes": {k: (None if v is None else round(v, 4))
                         for k, v in self.axes.items()},
                "raw": {k: (None if v is None else round(v, 5))
                        for k, v in self.raw.items()},
                "harmonics": [round(h, 4) for h in self.harmonics],
                "pitch": self.pitch}


#: the semitone bands of the spectrum the arrangement model reads: A0
#: (27.5 Hz) upward, one a semitone
SEMITONE_BANDS = 120
SEMITONE_BASE_HZ = 27.5


def semitone_powers(profiles):
    """The share of the energy in each semitone band, averaged over the
    spectral windows given (the attack's and the body's). A sound moved up
    n semitones by the player moves n bands, which is what lets one probe
    stand for a note anywhere on the keyboard."""
    out = [0.0] * SEMITONE_BANDS
    count = 0
    for prof in profiles:
        mags, binw = prof["mags"], prof["bin"]
        part = [0.0] * SEMITONE_BANDS
        for i in range(1, len(mags)):
            f = i * binw
            if f < SEMITONE_BASE_HZ * 0.97:
                continue
            band = int(round(12.0 * math.log2(f / SEMITONE_BASE_HZ)))
            if band >= SEMITONE_BANDS:
                break
            part[band] += mags[i] * mags[i]
        total = sum(part)
        if total > 0:
            count += 1
            for k in range(SEMITONE_BANDS):
                out[k] += part[k] / total
    return [v / count for v in out] if count else out


# --------------------------------------------------------------------------
# Building blocks
# --------------------------------------------------------------------------
_HANN = {}
_PREFIX_CACHE = {}


_hann = dsp.hann
_next_pow2 = dsp.next_pow2
_spectrum = dsp.spectrum


def envelope(x, rate: float):
    """RMS envelope, one point per ENVELOPE_HOP. -> (list, hop in seconds)."""
    n = len(x)
    hop = max(1, int(round(ENVELOPE_HOP * rate)))
    width = max(hop, int(round(ENVELOPE_WINDOW * rate)))
    prefix = [0.0] * (n + 1)
    total = 0.0
    for i, v in enumerate(x):
        total += v * v
        prefix[i + 1] = total
    env = []
    for start in range(0, max(1, n - width + 1), hop):
        end = min(n, start + width)
        env.append(math.sqrt(max(0.0, prefix[end] - prefix[start]) / (end - start)))
    return env, hop / rate


def _first_crossing(env, level: float, start: int, step: int = 1):
    """First index from `start` where env crosses `level` (rising when the
    start is below it, falling when above), or None."""
    if start < 0 or start >= len(env):
        return None
    above = env[start] >= level
    i = start
    while 0 <= i < len(env):
        if (env[i] >= level) != above:
            return i
        i += step
    return None


def _quiet_floor(env) -> float:
    return max(env) * 1e-4 if env else 0.0


def _goertzel_track(x, rate: float, freq: float, start: int, stop: int,
                    window: float = 0.025, hop: float = 0.0125):
    """Amplitude and phase of one frequency in overlapping windows.
    -> [(time, amplitude, phase)]; the engine of the pitch- and
    amplitude-movement readings.

    The window holds a whole number of periods of `freq`. Anything else
    lets the mirror image of the tone (and its harmonics) leak into the
    reading, and the reading then ripples at the rate the window slides
    through the period: a Hann window over whole periods rejects those
    exactly, which is why this is not simply `window` seconds."""
    periods = max(3, int(round(window * freq)))
    n = int(round(periods * rate / freq))
    step = max(1, int(round(hop * rate)))
    w = 2.0 * math.pi * freq / rate
    out = []
    # The carrier times the window, once per window length; a window is
    # then two dot products.
    hann = _hann(n)
    cos_w = [math.cos(w * i) * hann[i] for i in range(n)]
    sin_w = [math.sin(w * i) * hann[i] for i in range(n)]
    mul = operator.mul
    pos = start
    while pos + n <= stop:
        seg = x[pos:pos + n]
        re = sum(map(mul, seg, cos_w))
        im = -sum(map(mul, seg, sin_w))
        out.append((pos / rate, 2.0 * math.hypot(re, im) / n,
                    math.atan2(im, re) - w * pos))
        pos += step
    return out


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


# --------------------------------------------------------------------------
# The measurement
# --------------------------------------------------------------------------
def _smooth(env, width: int):
    """Centred boxcar average, shortened at the ends."""
    n = len(env)
    if width <= 1 or n < 2:
        return list(env)
    prefix = [0.0]
    for v in env:
        prefix.append(prefix[-1] + v)
    half = width // 2
    out = []
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        out.append((prefix[hi] - prefix[lo]) / (hi - lo))
    return out


def _profile(x, rate, start, size, f_req):
    """Spectral reading of one window: centroid ratio, the harmonic
    profile, bass ratio, the metallic components and the flatness."""
    n = len(x)
    start = max(0, min(start, max(0, n - size)))
    mags, binw = _spectrum(x, start, size, rate)
    top_bin = min(len(mags) - 1, int(TOP_HZ / binw))
    peak_mag = max(mags[:top_bin + 1]) or 1e-12
    floor = peak_mag * 0.01
    out = {"bin": binw, "mags": mags, "top": top_bin, "start": start,
           "size": size}

    amps = []
    for k in range(1, HARMONICS + 1):
        centre = k * f_req
        if centre >= TOP_HZ or centre >= rate / 2 - binw:
            amps.append(0.0)
            continue
        lo = max(1, int((centre * 0.97) / binw))
        hi = min(top_bin, int(math.ceil((centre * 1.03) / binw)) + 1)
        amps.append(max(mags[lo:hi + 1]) if hi >= lo else 0.0)
    loudest = max(amps) or 1.0
    out["harmonics"] = [a / loudest for a in amps]
    sub_lo = max(1, int((f_req * 0.5 * 0.97) / binw))
    sub_hi = max(sub_lo, int(math.ceil(f_req * 0.5 * 1.03 / binw)) + 1)
    out["sub"] = (max(mags[sub_lo:sub_hi + 1]) / loudest
                  if sub_hi < len(mags) else 0.0)

    num = den = 0.0
    for i in range(1, top_bin + 1):
        a = mags[i]
        if a > floor:
            num += a * i * binw
            den += a
    out["centroid"] = num / den if den else f_req

    low_lo = int(f_req * 0.35 / binw)
    low_hi = int(f_req * 1.5 / binw)
    mid_hi = int(f_req * 4.5 / binw)
    e_low = sum(a * a for a in mags[max(1, low_lo):low_hi + 1])
    e_mid = sum(a * a for a in mags[low_hi + 1:mid_hi + 1])
    out["bass_ratio"] = e_low / (e_mid + 1e-18)

    peaks = []
    limit = min(top_bin, int(8000.0 / binw))
    peak_floor = max(mags[:limit + 1]) * 0.08
    for i in range(2, limit):
        a = mags[i]
        if a >= peak_floor and a >= mags[i - 1] and a > mags[i + 1]:
            denom = mags[i - 1] - 2 * a + mags[i + 1]
            shift = 0.5 * (mags[i - 1] - mags[i + 1]) / denom if denom else 0.0
            peaks.append(((i + shift) * binw, a))
    inharm = total = 0.0
    for freq, a in peaks:
        if freq < f_req * 0.6:
            continue
        ratio_f = freq / f_req
        inharm += a * abs(ratio_f - round(ratio_f)) * 2.0
        total += a
    out["inharmonicity"] = inharm / total if total else 0.0

    h = out["harmonics"]
    energy = sum(v * v for v in h) or 1.0
    upper_share = sum(v * v for v in h[4:]) / energy
    rough = 0.0
    if upper_share > 0.01:
        irregular = weight = 0.0
        for k in range(3, 22):
            nbr = (h[k - 1] + h[k] + h[k + 1]) / 3.0
            irregular += abs(h[k] - nbr)
            weight += h[k]
        # normalised by the whole profile, so a sound that is all low
        # partials cannot read as ragged
        rough = irregular / (sum(h) or 1.0)
        rough = min(1.0, rough) * min(1.0, upper_share / 0.25)
    out["roughness"] = rough
    out["upper_share"] = upper_share

    p_lo = max(1, int(f_req * 0.5 / binw))
    power = [a * a + 1e-18 for a in mags[p_lo:top_bin + 1]]
    if len(power) > 8:
        out["flatness"] = (math.exp(sum(math.log(p) for p in power)
                                    / len(power))
                           / (sum(power) / len(power)))
    else:
        out["flatness"] = 0.0
    return out


def _metal_of(p) -> float:
    return _clip(0.5 * _clip(p["inharmonicity"] / METAL_INHARM_FULL)
                 + 0.5 * _clip(p["roughness"] / METAL_ROUGH_FULL))


def measure(mono, rate: float, requested_hz: float, hold: float,
            tail: float, movement: bool = True,
            solo: "Measurement" = None, sides=None) -> Measurement:
    """Read a note that was held for `hold` seconds then released and
    listened to for `tail` more.

    `movement` turns on the detune and motion readings, which need a hold
    of about a second to be meaningful and cost most of the analysis time;
    a fast probe leaves them out (None) and the scorer ignores them.

    `solo` is the reading of the same instrument's plain voice, for when
    `mono` is that voice with its unison and vibrato added: the voices beat,
    so the envelope read off the sum is not the instrument's, and the
    movement reading is placed by the solo's attack, fall and partials.

    `sides` is (left, right) when the note is stereo. A pan sweep moves
    nothing in the sum and everything in the ear, so the amplitude
    movement is read off each side as well and the largest counts.
    """
    m = Measurement()
    x = list(mono)
    n = len(x)
    if n < int(0.1 * rate):
        m.measured = False
        return m
    env, hop = envelope(x, rate)
    m.raw["peak"] = max(abs(v) for v in x)
    m.raw["rms"] = math.sqrt(sum(v * v for v in x) / n)
    if max(env) <= 1e-6:
        m.measured = False
        return m

    hold_end_idx = min(len(env) - 1, int(hold / hop))
    smooth = _smooth(env, 9)                      # ~22 ms
    window_end = min(hold_end_idx, int(0.8 / hop))
    peak_idx = max(range(0, max(1, window_end)), key=lambda i: smooth[i])
    peak_level = smooth[peak_idx]
    peak_time = peak_idx * hop
    m.raw["peak_time"] = peak_time

    # -- attack: 10% -> 80% of the peak, scaled to 10% -> 90% --------------
    i10 = _first_crossing(smooth, 0.1 * peak_level, 0) \
        if smooth[0] < 0.1 * peak_level else 0
    i80 = _first_crossing(smooth, 0.8 * peak_level, 0) \
        if smooth[0] < 0.8 * peak_level else 0
    onset_idx = i10 or 0
    rise = max(0.0, ((i80 if i80 is not None else peak_idx) - onset_idx)
               * hop - hop) * (8.0 / 7.0)
    attack_s = rise
    m.raw["attack_s"] = attack_s
    m.axes["attack"] = _clip(math.log(1.0 + attack_s / ATTACK_REFERENCE)
                             / math.log(1.0 + ATTACK_LONG / ATTACK_REFERENCE))

    # -- sustain and decay --------------------------------------------------
    t_ref = max(BODY_AT, peak_time + BODY_AFTER_PEAK)
    t_ref = min(t_ref, hold - 0.08)
    r_lo = max(peak_idx + 1, int((t_ref - 0.075) / hop))
    r_hi = max(r_lo + 1, min(hold_end_idx, int((t_ref + 0.075) / hop)))
    held = smooth[r_lo:r_hi] or [smooth[min(len(smooth) - 1, r_lo)]]
    sustain_level = sum(held) / len(held)
    sustain_ratio = min(1.0, sustain_level / peak_level)
    m.raw["sustain_ratio"] = sustain_ratio
    m.raw["body_at"] = t_ref
    sustain_db = 20.0 * math.log10(max(sustain_ratio, 1e-6))
    m.raw["sustain_db"] = sustain_db
    m.axes["body"] = _clip((sustain_db - BODY_FLOOR_DB) / -BODY_FLOOR_DB)
    if sustain_ratio >= 0.85:
        decay_s = None
        m.axes["decay"] = 1.0
    else:
        level = sustain_level + (peak_level - sustain_level) * math.exp(-1.0)
        idx = _first_crossing(smooth, level, peak_idx)
        decay_s = ((idx if idx is not None else hold_end_idx) - peak_idx) * hop
        m.axes["decay"] = _log_map(max(decay_s, 1e-6), DECAY_SHORT, DECAY_LONG)
    m.raw["decay_s"] = decay_s

    # -- release: 20 dB down after the note-off ----------------------------
    # A note that has already died away has no release to measure; reading
    # it as "instant" would call every pluck staccato.
    release_s = 0.0
    m.axes["articulation"] = None
    if hold_end_idx < len(env) - 3:
        at_off = max(env[max(0, hold_end_idx - 2):hold_end_idx + 1])
        if at_off >= 0.03 * peak_level:
            release_s = tail
            idx = _first_crossing(env, at_off * 0.1, hold_end_idx)
            if idx is not None:
                release_s = (idx - hold_end_idx) * hop
            m.axes["articulation"] = _log_map(max(release_s, 1e-6),
                                              RELEASE_SHORT, RELEASE_LONG)
    m.raw["release_s"] = release_s

    # -- two spectral windows: the attack and the sustained body -----------
    period = rate / requested_hz
    size_a = _next_pow2(max(2048, int(10 * period)))
    size_s = min(16384, _next_pow2(max(4096, int(24 * period))))
    start_a = int((onset_idx * hop + ATTACK_WINDOW_SKIP) * rate)
    pa = _profile(x, rate, start_a, min(size_a, 8192), requested_hz)
    sustain_at = onset_idx * hop + 0.25
    idx_s = min(len(env) - 1, int(sustain_at / hop))
    have_sustain = (sustain_at + size_s / rate < hold + 0.05
                    and smooth[idx_s] > peak_level * 0.03)
    ps = _profile(x, rate, int(sustain_at * rate), size_s, requested_hz) \
        if have_sustain else None
    body = ps or pa
    m.raw["sustain_window"] = 1.0 if ps else 0.0

    m.harmonics = body["harmonics"]
    m.raw["sub_amplitude"] = body["sub"]
    h = m.harmonics
    if body["sub"] > 0.5 and h[0] < body["sub"] * 1.5:
        m.pitch = "sub_octave"
    elif h[0] < 0.15 and h[1] > 0.5:
        m.pitch = "octave_up"
    elif max(h[:4]) < 0.2:
        m.pitch = "off"
    else:
        m.pitch = "exact"
    if h[0] < 0.15 and h[1] < 0.15 and max(h[:6]) > 0.5:
        m.pitch = "off"        # the fundamental and its octave are missing

    ratio_a = max(1.0, pa["centroid"] / requested_hz)
    ratio_s = max(1.0, (ps or pa)["centroid"] / requested_hz)
    m.raw["centroid_ratio_attack"] = ratio_a
    m.raw["centroid_ratio_body"] = ratio_s
    ratio = ratio_a if not ps else math.exp(0.6 * math.log(ratio_a)
                                            + 0.4 * math.log(ratio_s))
    m.raw["centroid_ratio"] = ratio
    m.raw["centroid_hz"] = ratio * requested_hz
    m.axes["brightness"] = _log_map(ratio, BRIGHT_LOW, BRIGHT_HIGH)

    m.raw["bass_ratio"] = body["bass_ratio"]
    m.axes["bass_weight"] = _log_map(body["bass_ratio"], BASS_RATIO_LOW,
                                     BASS_RATIO_HIGH)

    metal_a, metal_s = _metal_of(pa), _metal_of(ps) if ps else 0.0
    metal = (0.6 * max(metal_a, metal_s) + 0.4 * min(metal_a, metal_s)
             if ps else metal_a)
    m.raw["inharmonicity"] = max(pa["inharmonicity"],
                                 (ps or pa)["inharmonicity"])
    m.raw["roughness"] = max(pa["roughness"], (ps or pa)["roughness"])
    m.axes["metallicity"] = _clip(metal)

    m.raw["flatness"] = body["flatness"]
    m.axes["noise"] = _log_map(body["flatness"], FLATNESS_LOW, FLATNESS_HIGH)

    # punch: how much brighter the attack is than the body
    m.raw["punch_octaves"] = 0.0
    m.axes["punch"] = 0.0
    if ps:
        octaves = math.log2(max(pa["centroid"], 1.0) / max(ps["centroid"], 1.0))
        m.raw["punch_octaves"] = octaves
        m.axes["punch"] = _clip(octaves / PUNCH_FULL_OCTAVES)
    else:
        later = min(n - 2048, int((onset_idx * hop + 0.12) * rate))
        if later > 0 and smooth[min(len(env) - 1, int((onset_idx * hop + 0.12) / hop))] \
                > peak_level * 0.018:
            pb = _profile(x, rate, later, 2048, requested_hz)
            octaves = math.log2(max(pa["centroid"], 1.0)
                                / max(pb["centroid"], 1.0))
            m.raw["punch_octaves"] = octaves
            m.axes["punch"] = _clip(octaves / PUNCH_FULL_OCTAVES)

    # octave bands for the arrangement model
    m.semitones = semitone_powers([pa] + ([ps] if ps else []))
    a0 = body["start"]
    shares = dsp.band_energy(x[a0:a0 + body["size"]], rate, body["size"])
    total_e = sum(shares.values()) or 1.0
    m.bands = {k: v / total_e for k, v in shares.items()}

    # -- movement: detune and motion ---------------------------------------
    m.axes["detune"] = None
    m.axes["motion"] = None
    if movement:
        if solo is not None:
            m.raw["attack_s"] = solo.raw.get("attack_s", 0.0)
            decay_s = solo.raw.get("decay_s")
            m.harmonics = solo.harmonics or m.harmonics
        _movement(m, x, rate, requested_hz, env, hop, onset_idx, peak_level,
                  hold_end_idx, decay_s, sides)
    return m


def _polyfit2(xs, ys):
    """Least-squares quadratic through (xs, ys) -> (a, b, c) for a+bx+cx^2."""
    n = len(xs)
    sx = sum(xs)
    sx2 = sum(x * x for x in xs)
    sx3 = sum(x ** 3 for x in xs)
    sx4 = sum(x ** 4 for x in xs)
    sy = sum(ys)
    sxy = sum(x * y for x, y in zip(xs, ys))
    sx2y = sum(x * x * y for x, y in zip(xs, ys))
    # solve the 3x3 normal equations by Cramer's rule
    def det(a):
        return (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
                - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
                + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    m = [[n, sx, sx2], [sx, sx2, sx3], [sx2, sx3, sx4]]
    d = det(m)
    if abs(d) < 1e-18:
        return (sy / n, 0.0, 0.0)
    out = []
    rhs = [sy, sxy, sx2y]
    for col in range(3):
        mc = [row[:] for row in m]
        for r in range(3):
            mc[r][col] = rhs[r]
        out.append(det(mc) / d)
    return tuple(out)


def _knee(track, jump_db: float = 1.0, lookback: int = 8,
          lookahead: int = 26) -> int:
    """How many points of an amplitude track come before its envelope
    changes character.

    A percussive envelope falls in a straight line (in dB) and then, at its
    sustain level, drops off a cliff; no polynomial follows that, and what
    it leaves behind looks like a slow tremolo. The window for the movement
    reading therefore stops where the slope changes by more than `jump_db`
    per hop against the points just before, *and the level stays down*. A
    tremolo falls just as fast, but it comes back within a period, so
    "stays down" is judged over `lookahead` hops (325 ms, longer than the
    slowest movement counted, 3.5 Hz); near the end of the track, where
    that cannot be seen, only a very large drop counts."""
    db = [20.0 * math.log10(max(a, 1e-9)) for _, a, _ in track]
    slopes = [db[i + 1] - db[i] for i in range(len(db) - 1)]
    for i in range(lookback, len(slopes)):
        recent = sorted(slopes[i - lookback:i])
        ref = recent[len(recent) // 2]
        if slopes[i] - ref < -jump_db:           # a sudden fall...
            ahead = db[i + 2:i + 2 + lookahead]
            if len(ahead) >= 6:
                if max(ahead) < db[i] - 1.0:     # ...that stays down
                    return max(0, i - 1)         # (a beat's dip comes back)
            elif slopes[i] - ref < -3.0 * jump_db:
                return max(0, i - 1)
    return len(track)


def _beat_depths(track):
    """Depth of the slow (detune) and fast (motion) amplitude movement of
    one partial's amplitude track. -> (slow, fast, top_f, top_a, duration,
    count) with the quadratic fall of the envelope taken out."""
    times = [t for t, _, _ in track]
    amps = [a for _, a, _ in track]
    step = times[1] - times[0]
    t0 = times[0]
    ts = [t - t0 for t in times]
    logs = [math.log(max(a, 1e-9)) for a in amps]
    # The fall of a real envelope is not a straight line in dB, and what
    # is left of its curve reads as a slow beat; a quadratic takes it out.
    # It also takes out beats slower than about 0.7 Hz (less than a cycle
    # and a half of a one-second note), which is why DETUNE_MIN_HZ is where
    # it is and the compilers aim above it.
    count = len(ts)
    a0, a1, a2 = _polyfit2(ts, logs)
    resid = [math.exp(l - (a0 + a1 * t + a2 * t * t)) - 1.0
             for t, l in zip(ts, logs)]
    duration = ts[-1] - ts[0]
    depths = []
    f = DETUNE_MIN_HZ
    while f <= 20.0 and f < 0.5 / step:
        if f * duration >= 0.45:             # at least half a cycle
            w = 2 * math.pi * f
            re = sum(r * math.cos(w * t) for r, t in zip(resid, ts))
            im = sum(r * math.sin(w * t) for r, t in zip(resid, ts))
            depths.append((f, 2.0 * math.hypot(re, im) / count))
        f += 0.2
    slow = fast = 0.0
    top_f = top_a = 0.0
    if depths:
        top_f, top_a = max(depths, key=lambda p: p[1])
        if top_f < MOTION_MIN_HZ:
            slow = top_a
        else:
            fast = top_a
        # the other category's best, away from the leakage of the top peak
        rest = [a for f, a in depths if abs(f - top_f) > 1.5
                and (f < MOTION_MIN_HZ) != (top_f < MOTION_MIN_HZ)]
        if rest:
            if top_f < MOTION_MIN_HZ:
                fast = max(rest)
            else:
                slow = max(rest)
    return slow, fast, top_f, top_a, duration, count


def _movement(m, x, rate, f_req, env, hop, onset_idx, peak_level, hold_end_idx,
              decay_s, sides=None):
    """Amplitude and pitch movement of the strongest low partial through
    the held note: beating from DETUNE_MIN_HZ up to MOTION_MIN_HZ is detune,
    MOTION_MIN_HZ and up is motion.

    Measured once the fall to the sustain is over, and only while the note
    is still well above the floor: a decay is not a tremolo, however much
    its shape differs from a straight line.
    """
    # The lowest of the strong partials: a detune beats every partial, but
    # the k-th at k times the rate, and the rate is what tells detune
    # (under 3.5 Hz) from motion.
    top = max(m.harmonics[:6])
    k = next(i for i in range(6) if m.harmonics[i] >= 0.4 * top) + 1
    freq = k * f_req
    if freq > TOP_HZ or freq > rate / 2.5:
        return
    # wait out the attack and the fall to the body: three time constants
    # of a fall are 95% of it, and what is left bends the log-amplitude
    wait = (m.raw.get("attack_s", 0.0) * 1.2 + 0.04
            + (0.0 if decay_s is None else min(1.0, 3.0 * decay_s)))
    first = onset_idx + int(wait / hop)
    last = hold_end_idx
    while last > first and env[last] < peak_level * MOVEMENT_LEVEL:
        last -= 1
    if (last - first) * hop < MOVEMENT_SECONDS:
        m.raw["movement_measured"] = 0.0
        return
    track = _goertzel_track(x, rate, freq, int(first * hop * rate),
                            int(last * hop * rate))
    track = track[:_knee(track)]
    if len(track) < 24:
        m.raw["movement_measured"] = 0.0
        return
    times = [t for t, _, _ in track]
    amps = [a for _, a, _ in track]
    if sum(amps) / len(amps) <= 1e-9:
        return
    step = times[1] - times[0]
    slow, fast, top_f, top_a, duration, count = _beat_depths(track)
    if top_a:
        m.raw["beat_hz"] = top_f
        m.raw["beat_depth"] = top_a
    # A pan sweep or a one-sided tremolo: the sum can be steady while each
    # ear is not. Read each side over the same stretch and keep the largest
    # fast movement.
    if sides is not None:
        left, right = sides
        total = sum(abs(a - b) for a, b in zip(left[::37], right[::37]))
        scale = sum(abs(a) + abs(b) for a, b in zip(left[::37], right[::37]))
        if scale > 0 and total / scale > 0.03:
            for side in (left, right):
                sub = _goertzel_track(side, rate, freq, int(first * hop * rate),
                                      int(last * hop * rate))
                sub = sub[:_knee(sub)]
                if len(sub) >= 24 and sum(a for _, a, _ in sub) > 1e-9:
                    s_slow, s_fast = _beat_depths(sub)[:2]
                    fast = max(fast, s_fast)
                    m.raw["side_am"] = max(m.raw.get("side_am", 0.0), s_fast)
    # pitch movement from the phase of the same partial, with any steady
    # drift (a portamento, an off-centre fnum) taken out
    ts = [t - times[0] for t in times]
    phases = [p for _, _, p in track]
    devs = []
    for i in range(1, len(phases)):
        devs.append(_wrap(phases[i] - phases[i - 1]) / (2 * math.pi * step))
    pm_cents = 0.0
    if len(devs) > 4:
        dt_ = ts[1:len(devs) + 1]
        mean_t = sum(dt_) / len(dt_)
        mean_d = sum(devs) / len(devs)
        denom = sum((t - mean_t) ** 2 for t in dt_) or 1.0
        slope = sum((t - mean_t) * (d - mean_d)
                    for t, d in zip(dt_, devs)) / denom
        flat = [d - mean_d - slope * (t - mean_t) for t, d in zip(dt_, devs)]
        var = sum(v * v for v in flat) / len(flat)
        pm_cents = 1731.0 * math.sqrt(var) / freq
    m.raw["am_slow"] = slow
    m.raw["am_fast"] = fast
    m.raw["pm_cents"] = pm_cents
    m.raw["movement_measured"] = 1.0
    # A beat of a hertz and a bit needs a second of note to be told from
    # the envelope's own curve; a shorter note has no detune reading
    # rather than a false zero.
    m.axes["detune"] = _clip((slow - MOVEMENT_FLOOR) / DETUNE_FULL) \
        if duration >= DETUNE_MIN_SECONDS else None
    m.axes["motion"] = _clip((fast - MOVEMENT_FLOOR) / MOTION_AM_FULL
                             + max(0.0, pm_cents - PM_FLOOR) / MOTION_PM_FULL)


def format_measurement(m: Measurement) -> str:
    a = m.axes
    parts = [f"{k} {v:.2f}" if v is not None else f"{k} -"
             for k, v in a.items()]
    raw = m.raw
    lines = ["  ".join(parts[:6]), "  ".join(parts[6:])]
    lines.append(f"attack {1000 * raw.get('attack_s', 0):.0f} ms  "
                 f"decay {raw['decay_s'] * 1000:.0f} ms  "
                 if raw.get("decay_s") is not None else
                 f"attack {1000 * raw.get('attack_s', 0):.0f} ms  decay none  ")
    lines[-1] += (f"sustain {raw.get('sustain_db', 0):.1f} dB  "
                  f"release {1000 * raw.get('release_s', 0):.0f} ms  "
                  f"centroid {raw.get('centroid_ratio', 0):.1f}x  "
                  f"pitch {m.pitch}")
    return "\n".join(lines)
