"""essentia_ear.py — what Essentia measures in a rendered file: rhythm,
tonality, timbre and the sections. Runs in the hearing interpreter, with
Essentia built from the fork (terminaldetector/essentia) and its Python
bindings on the path.

Every function takes mono float32 samples at RATE and returns plain data
split by what it is:

    measured     read off the signal by an Essentia algorithm or simple
                 arithmetic on its output (onset times, centroid in Hz,
                 RMS in dB, the inter-onset intervals)
    estimated    a model's guess with an error rate (tempo, beats, key,
                 predominant pitch): reported with the algorithm's own
                 strength or confidence where it has one, and the
                 assumptions it rests on
    candidates   section roles (intro, breakdown, climax, reprise,
                 transition) inferred from the measurements, each with the
                 evidence it rests on and no confidence: nothing here is
                 calibrated against a listener

Nothing is called a groove, a chord or a key without the word "estimate".
"""

import math

import numpy as np

import essentia
import essentia.standard as es

RATE = 44100
FRAME = 2048
HOP = 1024
SAME = 0.9          # the thresholds of agentic/form.py
VARIED = 0.7
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#",
              "B"]

essentia.log.infoActive = False
essentia.log.warningActive = False


def version() -> str:
    return f"essentia {essentia.__version__}"


def _r(x, n=3):
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) or math.isinf(x) else round(x, n)


def _db(x: float) -> float:
    return 20.0 * math.log10(max(float(x), 1e-9))


def _stats(values, n=3) -> dict:
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    if not len(v):
        return {"n": 0}
    return {"mean": _r(v.mean(), n), "std": _r(v.std(), n),
            "p10": _r(np.percentile(v, 10), n),
            "median": _r(np.median(v), n),
            "p90": _r(np.percentile(v, 90), n), "n": int(len(v))}


def _cos(a, b) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na and nb else 0.0


# -- rhythm ------------------------------------------------------------------------------
def _onsets(x) -> np.ndarray:
    onsets, _rate = es.OnsetRate()(x)
    return np.asarray(onsets, dtype=float)


def _strength(x, t: float) -> float:
    a = int(t * RATE)
    seg = x[a:a + int(0.03 * RATE)]
    return _db(np.sqrt(np.mean(seg ** 2))) if len(seg) else -120.0


def rhythm(x, beats_per_bar: int = 4, window_bars: int = 4,
           bpm_hint: float = None) -> dict:
    """Onsets, tempo and beats, the inter-onset intervals, where the
    onsets fall in the beat and how strongly, bar against bar, and the
    density window by window. With `bpm_hint` (a score's own tempo, the
    audio starting on its first beat) the intervals and the grid are
    counted against it; the tempo is still estimated and reported."""
    dur = len(x) / RATE
    onsets = _onsets(x)
    bpm, beats, beats_conf, _est, _intervals = \
        es.RhythmExtractor2013(method="multifeature")(x)
    beats = np.asarray(beats, dtype=float)
    beat_s = 60.0 / bpm if bpm > 0 else None
    if bpm_hint:
        beat_s = 60.0 / float(bpm_hint)
    out = {"measured": {"seconds": _r(dur), "onsets": len(onsets),
                        "onset_times": [_r(t) for t in onsets[:256]]},
           "estimated": {}, "limitations": []}
    # Essentia's own scale for the multifeature beat confidence
    conf_label = ("very low" if beats_conf < 1.0 else "low"
                  if beats_conf <= 1.5 else "average"
                  if beats_conf <= 3.5 else "high")
    out["estimated"]["tempo"] = {
        "bpm": _r(bpm, 2), "beats": len(beats),
        "beat_confidence": _r(beats_conf, 2),
        "beat_confidence_scale": "Essentia RhythmExtractor2013 "
                                 "multifeature: 0-5.32; <1 very low, "
                                 "1-1.5 low, 1.5-3.5 average, >3.5 high",
        "beat_confidence_label": conf_label}
    if len(onsets) < 2 or not beat_s:
        out["limitations"].append("fewer than two onsets or no tempo: "
                                  "no intervals or grid")
        return out
    ioi = np.diff(onsets)
    ioi_beats = ioi / beat_s
    q = np.round(ioi_beats * 4) / 4                   # to a sixteenth
    values, counts = np.unique(q, return_counts=True)
    p = counts / counts.sum()
    entropy = max(0.0, float(-(p * np.log2(p)).sum()))
    order = np.argsort(-counts)
    out["measured"]["ioi_seconds"] = _stats(ioi)
    out["measured"]["ioi_in_beats"] = {
        "histogram": [{"beats": _r(values[i], 2), "share": _r(p[i])}
                      for i in order[:8]],
        "distinct": int(len(values)),
        "dominant_share": _r(p[order[0]]),
        "entropy_bits": _r(entropy)}
    out["measured"]["onsets_per_beat"] = _r(len(onsets) / (dur / beat_s))
    # where in the beat: the sixteenth each onset is nearest, against the
    # estimated beats (or a straight grid from the tempo if there are few)
    if bpm_hint:
        grid = np.arange(0.0, dur + 2 * beat_s, beat_s)
        out["measured"]["grid"] = f"the given tempo {float(bpm_hint):g} " \
                                  f"BPM from 0 s"
    else:
        grid = beats if len(beats) >= 4 else np.arange(
            onsets[0], dur + beat_s, beat_s)
        out["measured"]["grid"] = "the estimated beats"
    steps, devs, strengths, bars = [], [], [], {}
    for t in onsets:
        k = int(np.searchsorted(grid, t, side="right")) - 1
        if k < 0 or k + 1 >= len(grid):
            continue
        span = grid[k + 1] - grid[k]
        pos = (t - grid[k]) / span * 4
        step = int(round(pos)) % 4
        steps.append(step)
        devs.append((pos - round(pos)) * span / 4 * 1000.0)
        s = _strength(x, t)
        strengths.append((step, s))
        bar = k // beats_per_bar
        bars.setdefault(bar, set()).add((k % beats_per_bar) * 4 + step)
    if steps:
        share = [steps.count(i) / len(steps) for i in range(4)]
        acc = {}
        for i in range(4):
            vals = [s for st, s in strengths if st == i]
            acc[i] = _r(np.mean(vals), 1) if vals else None
        out["measured"]["position_in_beat"] = {
            "steps": ["on the beat", "second 16th", "off-beat 8th",
                      "fourth 16th"],
            "share": [_r(v) for v in share],
            "mean_level_db": [acc[i] for i in range(4)]}
        out["measured"]["off_grid_ms"] = _stats(devs, 1)
        out["estimated"]["syncopation_share"] = _r(share[1] + share[3])
    if len(bars) >= 2:
        keys = sorted(bars)
        same = [len(bars[a] & bars[b]) / max(1, len(bars[a] | bars[b]))
                for a, b in zip(keys, keys[1:])]
        out["measured"]["bars"] = {
            "count": len(keys), "assumed_beats_per_bar": beats_per_bar,
            "distinct_patterns": len({tuple(sorted(bars[k]))
                                      for k in keys}),
            "consecutive_bar_overlap": _r(np.mean(same))}
        out["limitations"].append(
            f"bars are counted as {beats_per_bar} estimated beats from the "
            f"first; the downbeat is not estimated")
    # density window by window, and where it switches
    win = window_bars * beats_per_bar * beat_s
    dens = []
    t = 0.0
    while t < dur - win / 2:
        n = int(((onsets >= t) & (onsets < t + win)).sum())
        span = min(win, dur - t)
        dens.append({"t0": _r(t, 2), "t1": _r(t + span, 2),
                     "onsets_per_beat": _r(n / (span / beat_s), 2)})
        t += win
    out["measured"]["density_windows"] = dens[:64]
    switches = []
    for a, b in zip(dens, dens[1:]):
        lo, hi = sorted((a["onsets_per_beat"] or 0, b["onsets_per_beat"]
                         or 0))
        if lo > 0 and hi / lo >= 1.5:
            switches.append({"at": b["t0"], "from": a["onsets_per_beat"],
                             "to": b["onsets_per_beat"]})
    out["measured"]["density_switches"] = switches
    out["limitations"].append("groove is not judged: the timing figures "
                              "are deviations from the estimated grid, "
                              "not a feel")
    return out


# -- tonality -----------------------------------------------------------------------------
def _hpcp_frames(x) -> np.ndarray:
    win = es.Windowing(type="blackmanharris62", size=FRAME)
    spec = es.Spectrum(size=FRAME)
    peaks = es.SpectralPeaks(orderBy="magnitude", magnitudeThreshold=1e-5,
                             minFrequency=40, maxFrequency=5000,
                             maxPeaks=60, sampleRate=RATE)
    hpcp = es.HPCP(size=12, referenceFrequency=440, harmonics=8,
                   nonLinear=False, normalized="unitMax", sampleRate=RATE)
    out = []
    for frame in es.FrameGenerator(x, frameSize=FRAME, hopSize=HOP,
                                   startFromZero=True):
        f, m = peaks(spec(win(frame)))
        out.append(hpcp(f, m))
    return np.asarray(out) if out else np.zeros((0, 12))


def _rotate_to_c(hpcp12) -> list:
    """Essentia's HPCP bin 0 is A (referenceFrequency 440): C first."""
    h = list(hpcp12)
    return h[3:] + h[:3]


def tonal_centroid(chroma_c) -> list:
    """Harte, Sandler & Gasser (2006): the 6-D tonal centroid of a 12-bin
    chroma (C first): fifths, minor and major thirds as circles."""
    c = np.asarray(chroma_c, float)
    c = c / c.sum() if c.sum() else c
    l = np.arange(12)
    phi = np.array([np.sin(l * 7 * np.pi / 6), np.cos(l * 7 * np.pi / 6),
                    np.sin(l * 3 * np.pi / 2), np.cos(l * 3 * np.pi / 2),
                    0.5 * np.sin(l * 2 * np.pi / 3),
                    0.5 * np.cos(l * 2 * np.pi / 3)])
    return [_r(v) for v in phi @ c]


def tonality(x, window_s: float = 8.0) -> dict:
    """Pitch-class profile, key estimates under three profiles, the tonal
    centroid, harmonic change window by window, and the predominant
    pitch's range."""
    out = {"measured": {}, "estimated": {}, "limitations": []}
    frames = _hpcp_frames(x)
    if not len(frames):
        out["limitations"].append("too short for a pitch-class profile")
        return out
    mean = frames.mean(axis=0)
    chroma = _rotate_to_c(mean / (mean.max() or 1.0))
    out["measured"]["pitch_class_profile"] = {
        n: _r(v) for n, v in zip(NOTE_NAMES, chroma)}
    out["measured"]["tonal_centroid"] = tonal_centroid(chroma)
    keys = {}
    for profile in ("bgate", "temperley", "edma"):
        k, scale, strength = es.KeyExtractor(profileType=profile,
                                             sampleRate=RATE)(x)
        keys[profile] = {"key": f"{k} {scale}", "strength": _r(strength)}
    names = [v["key"] for v in keys.values()]
    top = max(set(names), key=names.count)
    out["estimated"]["key"] = {
        "profiles": keys, "most_named": top,
        "profiles_agree": names.count(top) == len(names),
        "note": "a key estimate, not ground truth: strengths are the "
                "profile correlation, not a probability"}
    # window by window: the chroma and how far it moves
    per = max(1, int(window_s * RATE / HOP))
    wins = []
    key_alg = es.Key(profileType="bgate")
    for i in range(0, len(frames), per):
        block = frames[i:i + per]
        if len(block) < per // 2 and wins:
            break
        m = block.mean(axis=0)
        k, scale, strength, _f = key_alg(m.astype(np.float32))
        wins.append({"t0": _r(i * HOP / RATE, 2),
                     "t1": _r(min(len(x), (i + per) * HOP) / RATE, 2),
                     "chroma": _rotate_to_c(m / (m.max() or 1.0)),
                     "key_estimate": f"{k} {scale}",
                     "key_strength": _r(strength)})
    for a, b in zip(wins, wins[1:]):
        b["chroma_change"] = _r(1.0 - _cos(a["chroma"], b["chroma"]))
    for w in wins:
        w["chroma"] = [_r(v, 2) for v in w["chroma"]]
    out["measured"]["windows"] = wins[:48]
    # the predominant pitch: what range the leading line covers
    eq = es.EqualLoudness(sampleRate=RATE)(x)
    pitch, conf = es.PredominantPitchMelodia(frameSize=2048, hopSize=128,
                                             sampleRate=RATE)(eq)
    pitch = np.asarray(pitch)
    voiced = pitch[pitch > 0]
    if len(voiced) > 10:
        midi = 69 + 12 * np.log2(voiced / 440.0)
        lo, hi = np.percentile(midi, 5), np.percentile(midi, 95)
        out["estimated"]["predominant_pitch"] = {
            "voiced_share": _r(len(voiced) / len(pitch)),
            "median_midi": _r(np.median(midi), 1),
            "range_semitones_p5_p95": _r(hi - lo, 1),
            "low_midi_p5": _r(lo, 1), "high_midi_p95": _r(hi, 1),
            "algorithm": "PredominantPitchMelodia (Salamon & Gomez)",
            "note": "the most salient line, which in a dense mix may jump "
                    "between voices"}
    else:
        out["limitations"].append("no predominant pitch found")
    return out


# -- timbre and spectrum ---------------------------------------------------------------------
BANDS = [(20, 250), (250, 2000), (2000, 6000), (6000, 16000)]


def timbre(x, stereo=None) -> dict:
    """Spectral shape frame by frame (centroid, roll-off, flux, flatness,
    HFC, MFCC), the energy in four bands, level and dynamics, loudness
    (EBU R128 when stereo is given) and the attacks."""
    win = es.Windowing(type="hann", size=FRAME)
    spec = es.Spectrum(size=FRAME)
    centroid = es.Centroid(range=RATE / 2)
    rolloff = es.RollOff(sampleRate=RATE)
    flux = es.Flux()
    flat = es.Flatness()
    hfc = es.HFC(sampleRate=RATE)
    mfcc = es.MFCC(inputSize=FRAME // 2 + 1, sampleRate=RATE)
    rms = es.RMS()
    bands = [es.EnergyBand(sampleRate=RATE, startCutoffFrequency=a,
                           stopCutoffFrequency=b) for a, b in BANDS]
    rows = {"centroid_hz": [], "rolloff_hz": [], "flux": [],
            "flatness": [], "hfc": [], "rms_db": []}
    mf, be = [], []
    for frame in es.FrameGenerator(x, frameSize=FRAME, hopSize=HOP,
                                   startFromZero=True):
        level = rms(frame)
        s = spec(win(frame))
        rows["rms_db"].append(_db(level))
        if level < 1e-4:                        # silence: no shape
            continue
        rows["centroid_hz"].append(centroid(s))
        rows["rolloff_hz"].append(rolloff(s))
        rows["flux"].append(flux(s))
        rows["flatness"].append(flat(s))
        rows["hfc"].append(hfc(s))
        mf.append(mfcc(s)[1])
        be.append([b(s) for b in bands])
    out = {"measured": {}, "estimated": {}, "limitations": []}
    for k, v in rows.items():
        out["measured"][k] = _stats(v, 2)
    loud = [v for v in rows["rms_db"] if v > -70]
    if loud:
        out["measured"]["dynamics"] = {
            "rms_db_p95_minus_p5": _r(np.percentile(loud, 95)
                                      - np.percentile(loud, 5), 1),
            "frames_above_-70_db": len(loud)}
    if mf:
        out["measured"]["mfcc_mean"] = [_r(v, 2) for v in
                                        np.mean(mf, axis=0)]
    if be:
        e = np.asarray(be).sum(axis=0)
        e = e / e.sum() if e.sum() else e
        out["measured"]["band_energy_share"] = {
            f"{a}-{b} Hz": _r(v) for (a, b), v in zip(BANDS, e)}
    comp, _l = es.DynamicComplexity(sampleRate=RATE)(x)
    out["measured"]["dynamic_complexity_db"] = _r(comp, 2)
    if stereo is not None and len(stereo) > RATE:
        _m, _s, integrated, lra = es.LoudnessEBUR128(sampleRate=RATE)(
            stereo.astype(np.float32))
        out["measured"]["loudness_lufs"] = _r(integrated, 1)
        out["measured"]["loudness_range_lu"] = _r(lra, 1)
    # attacks: log attack time on the envelope after each onset
    env = es.Envelope(sampleRate=RATE, attackTime=1, releaseTime=30)(x)
    lat = es.LogAttackTime(sampleRate=RATE, startAttackThreshold=0.2,
                           stopAttackThreshold=0.9)
    attacks = []
    for t in _onsets(x)[:200]:
        a, b = int(t * RATE), int((t + 0.12) * RATE)
        seg = env[a:b]
        if len(seg) > 64:
            _log, start, stop = lat(seg)
            attacks.append((stop - start) * 1000.0)
    out["measured"]["attack_ms"] = _stats(attacks, 1)
    return out


# -- sections ---------------------------------------------------------------------------------
def _blocks(x, block_s: float = 1.0):
    """Per block of `block_s` seconds: MFCC mean (c1..c12), chroma, RMS dB,
    centroid, onsets."""
    win = es.Windowing(type="hann", size=FRAME)
    spec = es.Spectrum(size=FRAME)
    mfcc = es.MFCC(inputSize=FRAME // 2 + 1, sampleRate=RATE)
    centroid = es.Centroid(range=RATE / 2)
    frames_mfcc, frames_cent, frames_rms = [], [], []
    for frame in es.FrameGenerator(x, frameSize=FRAME, hopSize=HOP,
                                   startFromZero=True):
        s = spec(win(frame))
        frames_mfcc.append(mfcc(s)[1])
        frames_cent.append(centroid(s))
        frames_rms.append(float(np.sqrt(np.mean(frame ** 2))))
    hp = _hpcp_frames(x)
    onsets = _onsets(x)
    per = max(1, int(round(block_s * RATE / HOP)))
    n = len(frames_mfcc) // per
    blocks = []
    for i in range(n):
        sl = slice(i * per, (i + 1) * per)
        r = np.mean(frames_rms[sl])
        t0, t1 = i * per * HOP / RATE, (i + 1) * per * HOP / RATE
        blocks.append({
            "t0": t0, "t1": t1,
            "mfcc": np.mean(frames_mfcc[sl], axis=0)[1:13],
            "chroma": np.asarray(_rotate_to_c(hp[sl].mean(axis=0)))
            if len(hp[sl]) else np.zeros(12),
            "rms_db": _db(r),
            "centroid": float(np.mean(frames_cent[sl])),
            "onsets": int(((onsets >= t0) & (onsets < t1)).sum())})
    return blocks, np.asarray(frames_mfcc), onsets


def _novelty(blocks, kernel: int = 8) -> np.ndarray:
    """Foote's novelty over the blocks' self-similarity (MFCC and chroma,
    z-scored): how much the past and the future of each block differ."""
    if len(blocks) < 4:
        return np.zeros(len(blocks))
    f = np.array([np.concatenate([b["mfcc"], b["chroma"] * 4,
                                  [b["rms_db"] / 6.0]]) for b in blocks])
    f = (f - f.mean(axis=0)) / (f.std(axis=0) + 1e-9)
    f = f / (np.linalg.norm(f, axis=1, keepdims=True) + 1e-9)
    ssm = f @ f.T
    k = min(kernel, len(blocks) // 2)
    g = np.outer(np.hanning(2 * k), np.hanning(2 * k))
    sign = np.ones((2 * k, 2 * k))
    sign[:k, k:] = -1
    sign[k:, :k] = -1
    kern = g * sign
    pad = np.pad(ssm, k, mode="edge")
    nov = np.array([(pad[i:i + 2 * k, i:i + 2 * k] * kern).sum()
                    for i in range(len(blocks))])
    nov = np.maximum(nov, 0)
    return nov / nov.max() if nov.max() > 0 else nov


def _letters(sim) -> list:
    """As agentic/form.py letters sections: SAME takes the letter, VARIED
    takes it with a prime, the rest a new letter."""
    out, nxt = [], 0
    for i in range(len(sim)):
        best = max(range(i), key=lambda j: sim[i][j], default=None)
        if best is not None and sim[i][best] >= SAME:
            out.append(out[best].rstrip("'"))
        elif best is not None and sim[i][best] >= VARIED:
            out.append(out[best].rstrip("'") + "'")
        else:
            out.append(chr(65 + nxt % 26))
            nxt += 1
    return out


def sections(x, boundaries=None, block_s: float = 1.0,
             min_section_s: float = 6.0) -> dict:
    """The piece cut into sections — at `boundaries` (seconds) when the
    caller knows them (a Chipgen score does), else where the novelty of
    the timbre and the chroma peaks — each described, compared with every
    other, lettered, and given candidate roles with their evidence."""
    blocks, _mf, onsets = _blocks(x, block_s)
    dur = len(x) / RATE
    out = {"measured": {}, "estimated": {}, "candidates": [],
           "limitations": []}
    if len(blocks) < 2:
        out["limitations"].append("shorter than two blocks")
        return out
    nov = _novelty(blocks)
    if boundaries:
        cuts = sorted(float(b) for b in boundaries if 0 < float(b) < dur)
        out["measured"]["boundaries_from"] = "the caller (the score's own " \
                                             "section starts)"
    else:
        # peaks of the novelty, at least min_section_s apart, above half
        # its median-to-max range
        thr = np.median(nov) + 0.5 * (nov.max() - np.median(nov))
        gap = max(1, int(min_section_s / block_s))
        # not within a section's length of either end: the kernel there
        # sees the edge, not a change
        cuts, last = [], 0
        for i in range(gap, len(nov) - gap):
            if nov[i] >= thr and nov[i] >= nov[i - 1] and \
                    nov[i] >= nov[i + 1] and i - last >= gap:
                cuts.append(blocks[i]["t0"])
                last = i
        out["estimated"]["boundaries_from"] = (
            "novelty peaks (Foote kernel over MFCC and HPCP blocks of "
            f"{block_s:.1f} s), at least {min_section_s:.0f} s apart")
    edges = [0.0] + cuts + [dur]
    segs = []
    for a, b in zip(edges, edges[1:]):
        bl = [k for k in blocks if k["t0"] >= a - 1e-6 and
              k["t1"] <= b + 1e-6] or \
            [min(blocks, key=lambda k: abs(k["t0"] - a))]
        n_on = int(((onsets >= a) & (onsets < b)).sum())
        i = min(range(len(blocks)), key=lambda j: abs(blocks[j]["t0"] - a))
        segs.append({
            "t0": _r(a, 2), "t1": _r(b, 2),
            "onsets_per_s": _r(n_on / max(1e-9, b - a), 2),
            "rms_db": _r(np.mean([k["rms_db"] for k in bl]), 1),
            "centroid_hz": _r(np.mean([k["centroid"] for k in bl]), 0),
            "mfcc": np.mean([k["mfcc"] for k in bl], axis=0),
            "chroma": np.mean([k["chroma"] for k in bl], axis=0),
            "boundary_novelty": _r(nov[i], 2) if a > 0 else None})
    n = len(segs)
    mf = np.array([s["mfcc"] for s in segs])
    mfz = (mf - mf.mean(axis=0)) / (mf.std(axis=0) + 1e-9) if n > 2 \
        else mf
    sim, parts = [[1.0] * n for _ in range(n)], {}
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            timbre_sim = (_cos(mfz[i], mfz[j]) + 1) / 2 if n > 2 else \
                (_cos(mf[i], mf[j]) + 1) / 2
            chroma_sim = _cos(segs[i]["chroma"], segs[j]["chroma"])
            da, db_ = segs[i]["onsets_per_s"] or 0, segs[j][
                "onsets_per_s"] or 0
            dens = 1 - abs(da - db_) / max(da, db_, 1e-9)
            lev = max(0.0, 1 - abs(segs[i]["rms_db"] - segs[j]["rms_db"])
                      / 12.0)
            sim[i][j] = _r((timbre_sim + chroma_sim + dens + lev) / 4)
            parts[(i, j)] = {"timbre": _r(timbre_sim), "chroma":
                             _r(chroma_sim), "density": _r(dens),
                             "level": _r(lev)}
    letters = _letters(sim)
    for k, s in enumerate(segs):
        s["letter"] = letters[k]
        if k:
            s["to_previous"] = {"similarity": sim[k][k - 1],
                                **parts[(k, k - 1)]}
        del s["mfcc"]
        s["chroma"] = [_r(v, 2) for v in s["chroma"]]
    out["measured"]["sections"] = segs[:32]
    out["measured"]["similarity"] = [[_r(v, 2) for v in row]
                                     for row in sim][:32]
    out["estimated"]["form_letters"] = " ".join(letters)
    out["candidates"] = _roles(segs, sim)
    out["limitations"] += [
        "section roles are candidates from the measurements, with their "
        "evidence; none is calibrated against a listener",
        "the similarity mixes timbre (MFCC), chroma, density and level "
        "equally; components are given beside it"]
    return out


def _roles(segs, sim) -> list:
    out = []
    n = len(segs)
    if n < 2:
        return out
    dens = [s["onsets_per_s"] or 0 for s in segs]
    lev = [s["rms_db"] for s in segs]
    cen = [s["centroid_hz"] or 0 for s in segs]
    med_d, med_l, med_c = np.median(dens), np.median(lev), np.median(cen)
    if dens[0] < 0.67 * dens[1] or lev[0] < lev[1] - 3:
        out.append({"role": "intro", "section": 0, "t0": segs[0]["t0"],
                    "evidence": [f"density {dens[0]} against {dens[1]} "
                                 f"onsets/s next", f"level {lev[0]} against "
                                 f"{lev[1]} dB next"], "confidence": None})
    for i in range(1, n):
        ev = []
        if dens[i] <= 0.67 * med_d:
            ev.append(f"density {dens[i]} onsets/s, median {med_d:.2f}")
        if cen[i] <= 0.67 * med_c:
            ev.append(f"centroid {cen[i]:.0f} Hz, median {med_c:.0f}")
        if lev[i] <= med_l - 4:
            ev.append(f"level {lev[i]} dB, median {med_l:.1f}")
        if ev:
            out.append({"role": "breakdown", "section": i,
                        "t0": segs[i]["t0"], "evidence": ev,
                        "confidence": None})
    score = [lev[i] - med_l + 6 * (cen[i] / (med_c or 1) - 1)
             + 3 * (dens[i] / (med_d or 1) - 1) for i in range(n)]
    top = int(np.argmax(score))
    if lev[top] >= med_l + 1.5 or cen[top] >= 1.3 * med_c:
        out.append({"role": "climax", "section": top, "t0": segs[top]["t0"],
                    "evidence": [f"level {lev[top]} dB (median "
                                 f"{med_l:.1f})", f"centroid {cen[top]:.0f} "
                                 f"Hz (median {med_c:.0f})", f"density "
                                 f"{dens[top]} onsets/s"],
                    "confidence": None})
    for i in range(2, n):
        j = max(range(i - 1), key=lambda k: sim[i][k])
        if sim[i][j] >= SAME and sim[i][i - 1] < sim[i][j]:
            out.append({"role": "reprise", "section": i, "t0": segs[i]["t0"],
                        "of_section": j, "evidence": [
                            f"similarity {sim[i][j]} to section {j}, "
                            f"{sim[i][i - 1]} to the one before"],
                        "confidence": None})
    lengths = [segs[i]["t1"] - segs[i]["t0"] for i in range(n)]
    med_len = np.median(lengths)
    for i in range(1, n - 1):
        if lengths[i] <= 0.5 * med_len and sim[i - 1][i + 1] < SAME:
            out.append({"role": "transition", "section": i,
                        "t0": segs[i]["t0"], "evidence": [
                            f"{lengths[i]:.1f} s against a median "
                            f"{med_len:.1f} s, between two sections "
                            f"{sim[i - 1][i + 1]} alike"],
                        "confidence": None})
    return out


def compare_sections(x_cand, x_ref, cand_boundaries=None,
                     ref_boundaries=None) -> dict:
    """Two pieces' sections side by side: how far each one's sections
    spread in density, level and brightness, how many switch, which
    roles each has."""
    c = sections(x_cand, cand_boundaries)
    r = sections(x_ref, ref_boundaries)

    def spread(res):
        segs = res["measured"].get("sections") or []
        if not segs:
            return {}
        d = [s["onsets_per_s"] or 0 for s in segs]
        l = [s["rms_db"] for s in segs]
        ce = [s["centroid_hz"] or 0 for s in segs]
        return {"sections": len(segs),
                "density_ratio_max_min": _r(max(d) / max(min(d), 1e-9), 2),
                "level_range_db": _r(max(l) - min(l), 1),
                "centroid_ratio_max_min": _r(max(ce) / max(min(ce), 1e-9),
                                             2),
                "mean_similarity_to_previous": _r(np.mean(
                    [s["to_previous"]["similarity"] for s in segs[1:]]))
                if len(segs) > 1 else None,
                "roles": sorted({k["role"] for k in res["candidates"]})}
    sc, sr = spread(c), spread(r)
    gaps = []
    for k in ("density_ratio_max_min", "level_range_db",
              "centroid_ratio_max_min"):
        if sc.get(k) is not None and sr.get(k) is not None:
            gaps.append({"measure": k, "candidate": sc[k],
                         "reference": sr[k]})
    missing = sorted(set(sr.get("roles", [])) - set(sc.get("roles", [])))
    return {"measured": {"candidate": sc, "reference": sr,
                         "contrast": gaps},
            "candidates": [{"role_missing_in_candidate": m,
                            "confidence": None} for m in missing],
            "detail": {"candidate": c, "reference": r},
            "limitations": c["limitations"] + [
                "spreads compare the whole pieces' contrast, not section "
                "to section at the same time"]}
