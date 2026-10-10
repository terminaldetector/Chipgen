"""listen.py — parallel listening: the same musical moment in a reference,
in the piece and in the agent's own sketches, side by side.

A fragment is cut on the music, not on the clock: a number of bars from a
downbeat of each source's own grid (the project's bars; a reference's
settled grid, references.py; a sketch's bars). A reference at 82 BPM and
the piece at 140 BPM give fragments of different lengths, and the lengths
are kept: nothing is stretched, and each fragment carries its tempo, its
bars and its duration.

The fragments are matched in loudness (BS.1770, features.loudness) to one
target and scaled down together if a peak would pass -1 dBFS; the gains are
reported next to the original loudness. They are written one by one and as
one reel (with silence between and an index of where each starts), and the
piece's fragment comes with its voices, each rendered alone and written at
the mix's gain — the mix and the channels can be heard apart.

`compare` returns observations, each with the fragment and the time inside
it, the feature, the confidence, the voices (of the piece) when the link is
known — and its basis, never mixed up:

    heard by model   a listening model that passed the probes (ear.verify)
                     heard it in the reel
    measured         features.py read it from the samples: the readings of
                     the dimensions the reference is tagged for, ours
                     against theirs, where the gap is past a stated
                     threshold
    assumed          an inference that links a measured gap to a cause
                     (the reference's echo voice, confirmed in its source
                     and its audio, for a gap in space); a hypothesis to
                     test, not a finding

and `listening`: whether a model listened, and if not, why — measurements
are never reported as listening.
"""

import json
import os
from typing import List, Optional

from . import ear as EAR
from . import features as F
from . import references as REF
from . import render as R
from . import timeline as T
from .state import AgenticError, content as state_content

RATE = R.RATE
#: The loudness every fragment is brought to before peaks are checked.
TARGET_LUFS = -18.0
#: Silence between fragments in a reel.
GAP_S = 0.8
#: How far apart two readings must be to be reported (absolute, or
#: relative where the name says so). Set for these readings' scales; a
#: smaller gap is not reported, not called equal.
THRESHOLDS = {
    "balance.centroid_hz": ("relative", 0.20),
    "balance.shares.low": ("absolute", 0.08),
    "balance.shares.mid": ("absolute", 0.08),
    "balance.shares.high": ("absolute", 0.08),
    "voice.brightness_attack": ("relative", 0.20),
    "voice.brightness_body": ("relative", 0.20),
    "voice.brightness_development": ("absolute", 0.15),
    "voice.vibrato_depth_cents": ("absolute", 10.0),
    "voice.vibrato_rate_hz": ("absolute", 1.0),
    "voice.vibrato_delay_ms": ("absolute", 80.0),
    "voice.vibrato_share": ("absolute", 0.2),
    "onsets_per_beat": ("relative", 0.30),
    "notes_per_beat": ("relative", 0.25),
    "offbeat_share": ("absolute", 0.10),
    "attack_rise_ms": ("absolute", 8.0),
    "loudness.lufs": ("absolute", 2.0),
    "stereo.side_over_mid_db": ("absolute", 6.0),
    "stereo.correlation": ("absolute", 0.15),
    "tail_ms": ("relative", 0.40),
    "development.lufs_change": ("absolute", 2.0),
    "development.density_change": ("absolute", 0.5),
}
_WORDS = {
    "balance.centroid_hz": "the spectral centroid of the mix",
    "balance.shares.low": "the share of energy under 250 Hz",
    "balance.shares.mid": "the share of energy at 250-2000 Hz",
    "balance.shares.high": "the share of energy at 2-8 kHz",
    "voice.brightness_attack": "the lead's brightness in the attack",
    "voice.brightness_body": "the lead's brightness in the body",
    "voice.brightness_development": "how the lead's brightness moves from "
                                    "attack to body (body over attack)",
    "voice.vibrato_depth_cents": "the lead's vibrato depth (cents)",
    "voice.vibrato_rate_hz": "the lead's vibrato rate (Hz)",
    "voice.vibrato_delay_ms": "how long the lead's vibrato waits (ms)",
    "voice.vibrato_share": "the share of the lead's notes with a vibrato",
    "onsets_per_beat": "onsets per beat",
    "notes_per_beat": "notes per beat, every voice counted (how busy "
                      "the texture is)",
    "offbeat_share": "the share of onsets between the eighth notes "
                     "(syncopation, swing)",
    "attack_rise_ms": "the onsets' rise time (ms)",
    "loudness.lufs": "loudness (LUFS)",
    "stereo.side_over_mid_db": "stereo width (side over mid, dB)",
    "stereo.correlation": "left-right correlation",
    "tail_ms": "how long a sound rings after a gap (ms)",
    "development.lufs_change": "the loudness change from the first half "
                               "to the second",
    "development.density_change": "the change in onsets per beat from the "
                                  "first half to the second",
}
_DEVELOPMENT = ["development.lufs_change", "development.density_change"]


# -- fragments ------------------------------------------------------------------------
def _stereo_list(samples) -> list:
    return samples.tolist() if hasattr(samples, "tolist") else list(samples)


def of_timeline(tl, spec: str, label: str, kind: str, source: str,
                stems: bool = False, cache: Optional[R.Cache] = None,
                voices=None) -> dict:
    """A range of a timeline (the project at a revision, or a sketch's
    content) as a fragment; with `stems`, each sounding voice (or the
    given `voices`) rendered alone over the same range."""
    rng = tl.range(spec)
    r = R.render_range(tl, rng, cache=cache)
    s = tl.content["structure"]
    beat = 60.0 / float(s["bpm"])
    out = {"label": label, "kind": kind, "source": source, "spec": spec,
           "t0": rng["t0"], "t1": rng["t1"],
           "seconds": round(rng["t1"] - rng["t0"], 3),
           "bars": rng["bars"], "bpm": float(s["bpm"]),
           "beat_s": round(beat, 5),
           "bar_starts": [round(tl.rows[k]["t0"] - rng["t0"], 4)
                          for k in range(rng["row0"], rng["row1"],
                                         tl.rows_per_bar)],
           "samples": _stereo_list(r.segment(rng["t0"], rng["t1"],
                                             mono=False)),
           "_timeline": tl, "_range": rng}
    if stems:
        out["stems"] = {}
        wanted = voices or [v for v in tl.voices()
                            if tl.sounding(rng["t0"], rng["t1"], v)]
        for v in wanted:
            sr = R.render_range(tl, rng, voices=[v], cache=cache)
            out["stems"][v] = _stereo_list(sr.segment(rng["t0"], rng["t1"],
                                                      mono=False))
    return out


def of_project(project, spec: str, rev: Optional[int] = None,
               stems: bool = False, cache: Optional[R.Cache] = None,
               label: str = "", voices=None) -> dict:
    c = project.checkout(rev) if rev is not None else \
        state_content(project.state)
    rev = project.head if rev is None else rev
    return of_timeline(T.build(c), spec, label or f"ours r{rev} {spec}",
                       "ours", f"r{rev}", stems, cache
                       or R.Cache(project.path("renders")), voices)


def of_sketch(project, sid: str, spec: str, stems: bool = False,
              cache: Optional[R.Cache] = None) -> dict:
    from . import sketches as SK
    s = SK.get(project, sid)
    tl = T.build(SK.content_of(project, sid))
    return of_timeline(tl, spec, f"sketch {sid} ({s['label']}) {spec}",
                       "sketch", sid, stems,
                       cache or R.Cache(project.path("renders")))


def of_reference(project, rid: str, first_bar: Optional[int] = None,
                 bars: Optional[int] = None,
                 region: Optional[str] = None) -> dict:
    """`bars` bars of a reference from bar `first_bar` (1-based, its own
    grid), or a named region (the first one when nothing is given). An
    estimated grid is snapped to the beat the audio has there."""
    ref = REF.get(project, rid)
    if first_bar is None:
        reg = next((r for r in ref["regions"]
                    if region is None or r["name"] == region), None)
        if reg is None:
            raise AgenticError("no_region", f"{rid} has no region "
                               f"{region!r}; it has "
                               f"{[r['name'] for r in ref['regions']]}")
        first_bar = reg["bars"][0]
        bars = bars or (reg["bars"][1] - reg["bars"][0] + 1)
    bars = bars or 4
    span = REF.bar_range(project, rid, first_bar, bars)
    start = REF.snap(project, rid, span["t0"])
    t0 = start["t"]
    t1 = span["t1"] + (t0 - span["t0"])
    starts = ref["grid"]["bars"]
    bar_starts = [round(starts[k] - span["t0"], 4)
                  for k in range(first_bar - 1,
                                 min(len(starts), span["bars"][1]))]
    out = {"label": f"reference {rid} ({ref['title']}) bars "
                    f"{span['bars'][0]}-{span['bars'][1]}",
           "kind": "reference", "source": rid,
           "spec": f"bars {span['bars'][0]}-{span['bars'][1]}",
           "t0": round(t0, 4), "t1": round(t1, 4),
           "seconds": round(t1 - t0, 3), "bars": span["bars"],
           "bpm": ref["grid"].get("bpm"), "beat_s": ref["grid"].get("beat_s"),
           "grid": ref["grid"]["source"], "snapped_ms": start["moved_ms"],
           "bar_starts": bar_starts, "tags": ref["tags"],
           "samples": REF.read(project, rid, t0, t1)}
    return out


# -- loudness ------------------------------------------------------------------------------
def match(fragments: List[dict], target: float = TARGET_LUFS) -> dict:
    """Bring every fragment to `target` LUFS; if a peak would then pass
    -1 dBFS, everything down by the same amount. Sets each fragment's
    `lufs` (as rendered) and `gain_db`. -> the common reduction."""
    import math
    peaks = []
    for f in fragments:
        f["lufs"] = F.loudness(f["samples"], RATE)["lufs"]
        f["gain_db"] = round(target - f["lufs"], 2) if f["lufs"] > -70 \
            else 0.0
        peak = max((abs(v) for v in f["samples"]), default=0.0)
        peaks.append(20 * math.log10(peak) + f["gain_db"] if peak > 0
                     else -200.0)
    common = min(0.0, -1.0 - max(peaks + [-200.0]))
    for f in fragments:
        f["gain_db"] = round(f["gain_db"] + common, 2)
    return {"target_lufs": target, "common_reduction_db": round(common, 2),
            "method": "BS.1770 integrated loudness, then one reduction for "
                      "all if a peak passed -1 dBFS"}


def _scaled(samples, gain_db: float) -> list:
    g = 10 ** (gain_db / 20.0)
    return [v * g for v in samples]


# -- writing ---------------------------------------------------------------------------------
def _write(path: str, samples, mp3: bool) -> dict:
    REF._write_wav(path, samples)
    out = {"wav": path}
    if mp3:
        import audio
        import mp3 as mp3mod
        buf = audio.from_floats(samples, channels=2)
        target = os.path.splitext(path)[0] + ".mp3"
        info = mp3mod.encode(buf, RATE, target, encoder="auto")
        out["mp3"] = target
        out["encoder"] = info["encoder"]
    return out


def _safe(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_"
                   for ch in text)[:48]


def write(fragments: List[dict], folder: str, name: str,
          mp3: bool = True) -> dict:
    """Each fragment (and the piece's stems) loudness-matched, then the
    reel: the fragments in order with GAP_S of silence between. -> the
    index: files, where each fragment sits in the reel, its bars."""
    os.makedirs(folder, exist_ok=True)
    loud = match(fragments)
    index = {"name": name, "loudness": loud, "gap_s": GAP_S,
             "fragments": [], "note": "fragments are cut on bars of each "
             "source's own grid; tempos and lengths differ and were not "
             "changed"}
    reel = []
    for i, f in enumerate(fragments, 1):
        tag = f"{name}_{i}_{_safe(f['kind'] + '_' + f['source'])}"
        scaled = _scaled(f["samples"], f["gain_db"])
        files = _write(os.path.join(folder, tag + ".wav"), scaled, mp3)
        start = len(reel) / 2 / RATE
        reel.extend(scaled)
        reel.extend([0.0] * int(GAP_S * RATE) * 2)
        entry = {"n": i, "label": f["label"], "kind": f["kind"],
                 "source": f["source"], "bars": f["bars"],
                 "bpm": f.get("bpm"), "seconds": f["seconds"],
                 "own_time": [f["t0"], f["t1"]],
                 "in_reel": [round(start, 3), round(start + f["seconds"], 3)],
                 "bar_starts_in_reel": [round(start + b, 3)
                                        for b in f.get("bar_starts", [])],
                 "lufs_as_rendered": f["lufs"], "gain_db": f["gain_db"],
                 "files": files}
        if f.get("stems"):
            entry["stems"] = {}
            for v, s in f["stems"].items():
                entry["stems"][v] = _write(
                    os.path.join(folder, f"{tag}_{_safe(v)}.wav"),
                    _scaled(s, f["gain_db"]), mp3)
            entry["stems_note"] = ("each voice rendered alone, at the mix's "
                                   "gain; the mix is not their sum "
                                   "(register-write timing)")
        index["fragments"].append(entry)
    index["reel"] = _write(os.path.join(folder, f"{name}_reel.wav"), reel,
                           mp3)
    index["reel_seconds"] = round(len(reel) / 2 / RATE, 3)
    with open(os.path.join(folder, f"{name}_index.json"), "w",
              encoding="utf-8") as handle:
        json.dump(index, handle, ensure_ascii=False, indent=1)
    index["path"] = os.path.join(folder, f"{name}_index.json")
    return index


# -- measuring -------------------------------------------------------------------------------
def _halves(samples, beat_s: Optional[float]) -> dict:
    n = len(samples) // 4 * 2          # half the frames, interleaved
    if n < 4 * RATE:
        return {"why": "under two seconds a half"}
    a = F.fragment(samples[:n], RATE, beat_s)
    b = F.fragment(samples[n:], RATE, beat_s)
    out = {"lufs_change": round(b["loudness"]["lufs"]
                                - a["loudness"]["lufs"], 2)}
    if beat_s:
        out["density_change"] = round(b["onsets_per_beat"]
                                      - a["onsets_per_beat"], 2)
    return out


def _our_notes(f: dict, voice: str) -> List[dict]:
    tl, rng = f["_timeline"], f["_range"]
    return [{"start": n["start"] - rng["t0"],
             "end": min(n["end"], rng["t1"]) - rng["t0"],
             "f0": EAR.note_hz(n["pitch"])}
            for n in tl.sounding(rng["t0"], rng["t1"], voice)
            if n["start"] >= rng["t0"] - 1e-6 and n.get("pitch") is not None]


def _reference_notes(project, f: dict, voice: str, mono) -> List[dict]:
    import analysis
    out = []
    for n in REF.notes(project, f["source"]):
        if n["voice"] != voice or n.get("pitch") is None:
            continue
        if not (f["t0"] <= n["start"] < f["t1"]):
            continue
        a = int((n["start"] - f["t0"]) * RATE)
        b = int((min(n["end"], f["t1"]) - f["t0"]) * RATE)
        seg = mono[a + int(0.04 * RATE):b]
        # a module's samples set its true pitch: read it from the sound
        f0 = analysis.fundamental(seg, RATE) if len(seg) >= 2048 else None
        if f0:
            out.append({"start": n["start"] - f["t0"],
                        "end": min(n["end"], f["t1"]) - f["t0"], "f0": f0})
    return out


def measure(project, f: dict, voice: Optional[str] = None,
            reference_voice: Optional[str] = None) -> dict:
    """The fragment's readings (features.fragment), its development (first
    half against second), and, for one voice, what its notes do
    (features.note_profile) on that voice rendered alone."""
    out = F.fragment(f["samples"], RATE, f.get("beat_s"))
    out["development"] = _halves(f["samples"], f.get("beat_s"))
    # onsets per beat: from the score when there is one (the piece's notes,
    # a reference's source), the audio's count kept next to it. The audio
    # detector is set for precision: on 16th-note textures it found a
    # third of the onsets (recall 0.31 and 0.38, measured), so a dense
    # passage reads sparser than it is from the audio alone
    out["onsets_per_beat_audio"] = out.get("onsets_per_beat")
    beat = f.get("count_beat_s") or f.get("beat_s")
    if beat and f.get("count_beat_s"):
        # the per-beat counts on the beat the comparison chose (below)
        out["onsets_per_beat_audio"] = round(
            out["onsets"] / (f["seconds"] / beat), 2)
    starts = _score_starts(project, f)
    if starts is not None and beat:
        merged = []
        for t in sorted(starts):
            if not merged or t - merged[-1] > 0.03:
                merged.append(t)
        beats = f["seconds"] / beat
        out["onsets_per_beat"] = round(len(merged) / beats, 2)
        # every voice's notes, not merged: how busy the texture is (the
        # merged count saturates at one a row once any voice plays every
        # row)
        out["notes_per_beat"] = round(len(starts) / beats, 2)
        out["onsets_from"] = "score"
    else:
        merged = None
        if beat:
            out["onsets_per_beat"] = out["onsets_per_beat_audio"]
        out["onsets_from"] = "audio"
    if beat:
        # how much of the rhythm falls between the eighth notes: the share
        # of onsets more than 6% of a beat from any eighth (a syncopated or
        # swung part has more); from the score when there is one, from the
        # audio's onsets as well
        misfit = _rows_misfit(REF.get(project, f["source"]), beat) \
            if f["kind"] == "reference" else None
        if misfit:
            # the audio is those rows rendered: no reading from it either
            out["offbeat_share_not_measurable"] = misfit
        else:
            env, hop, _low = F.onset_envelope(F.mono_of(f["samples"]), RATE)
            heard = _offbeat(F.peaks(env, hop), beat)
            if heard is not None:
                out["offbeat_share_audio"] = heard
            score = _offbeat(merged, beat) if merged is not None else None
            if score is not None:
                out["offbeat_share"] = score
            elif heard is not None:
                out["offbeat_share"] = heard
    if voice and f["kind"] in ("ours", "sketch"):
        stem = (f.get("stems") or {}).get(voice)
        if stem is None:
            tl, rng = f["_timeline"], f["_range"]
            stem = _stereo_list(R.render_range(
                tl, rng, voices=[voice]).segment(rng["t0"], rng["t1"],
                                                 mono=False))
        out["voice"] = F.note_profile(F.mono_of(stem), RATE,
                                      _our_notes(f, voice))
        out["voice"]["name"] = voice
    elif reference_voice and f["kind"] == "reference":
        mono = F.mono_of(REF.stem(project, f["source"], reference_voice,
                                  f["t1"]))[int(f["t0"] * RATE):]
        out["voice"] = F.note_profile(mono, RATE, _reference_notes(
            project, f, reference_voice, mono))
        out["voice"]["name"] = reference_voice
    return out


#: How far from an eighth an onset may sit and still be on it, as a share
#: of the eighth (6% of a beat).
OFFBEAT_TOLERANCE = 0.12


def _offbeat(times, beat: float) -> Optional[float]:
    if not times:
        return None
    off = 0
    for t in times:
        phase = (t / beat) % 0.5 / 0.5
        if min(phase, 1.0 - phase) > OFFBEAT_TOLERANCE:
            off += 1
    return round(off / len(times), 3)


def _rows_misfit(ref: dict, beat: float) -> Optional[str]:
    """Why a score reference's notes cannot be placed against the eighths
    of `beat`, or None when they can. A source whose own grid the audio
    confirmed has rows that divide its beat. One whose grid gave way to
    the audio's (a transcription's 52 ms rows against the music's beat)
    has each note on its nearest row: when an eighth is not a whole number
    of rows, every other straight eighth sits half a row away, past the
    tolerance, and the share of onsets between the eighths would measure
    the transcription's grid, not the music's rhythm."""
    if (ref.get("grid") or {}).get("source") != "audio":
        return None
    row = (ref.get("source_grid") or {}).get("row_s")
    if not row or not beat:
        return None
    eighth = beat / 2.0
    k = eighth / row
    misfit = abs(k - round(k)) * row
    if round(k) >= 1 and misfit <= OFFBEAT_TOLERANCE * eighth:
        return None
    return (f"its rows ({row * 1000:.0f} ms) do not divide an eighth of "
            f"the beat it is counted on ({eighth * 1000:.0f} ms): a straight "
            f"eighth can sit {misfit * 1000:.0f} ms from its row, past the "
            f"{OFFBEAT_TOLERANCE * eighth * 1000:.0f} ms the reading allows")


def _score_starts(project, f: dict) -> Optional[List[float]]:
    """Note starts inside the fragment, from its score, in seconds from
    the fragment's start; None for a recording."""
    if f.get("_timeline") is not None:
        tl, rng = f["_timeline"], f["_range"]
        return [n["start"] - rng["t0"] for n in tl.notes
                if rng["t0"] - 1e-6 <= n["start"] < rng["t1"] - 1e-6]
    if f["kind"] == "reference":
        notes = REF.notes(project, f["source"])
        if not notes:
            return None
        return [n["start"] - f["t0"] for n in notes
                if f["t0"] <= n["start"] < f["t1"]]
    return None


def match_voice(project, rid: str, role: str) -> dict:
    """The reference's voice that plays the role `role` of ours, read from
    its source: for a lead, a voice whose vibrato the audio confirmed,
    else the highest voice with long notes; for a bass, the lowest; for
    drums, the busiest unpitched one. -> {voice, why} or {voice: None}."""
    ref = REF.get(project, rid)
    voices = ref.get("voices") or {}
    if not voices:
        return {"voice": None, "why": "a recording: no voices to match"}
    pitched = {v: i for v, i in voices.items()
               if i.get("median_pitch") is not None and i["notes"] >= 4}
    if role in ("lead", "melody", "counter"):
        confirmed = [c["voices"][0] for c in ref.get("claims", [])
                     if c["kind"] == "vibrato"
                     and (c.get("audio_check") or {}).get("verdict")
                     == "confirmed"]
        if confirmed:
            return {"voice": confirmed[0], "why": "its vibrato is in the "
                    "source and confirmed in its audio"}
        long_ = [v for v, i in pitched.items()
                 if i["median_length_s"] >= 0.2]
        pool = long_ or list(pitched)
        if not pool:
            return {"voice": None, "why": "no pitched voice"}
        v = max(pool, key=lambda k: pitched[k]["median_pitch"])
        return {"voice": v, "why": "the highest voice with long notes"}
    if role == "bass":
        if not pitched:
            return {"voice": None, "why": "no pitched voice"}
        v = min(pitched, key=lambda k: pitched[k]["median_pitch"])
        return {"voice": v, "why": "the lowest voice"}
    unpitched = [v for v, i in voices.items() if i.get("median_pitch")
                 is None]
    pool = unpitched or list(voices)
    v = max(pool, key=lambda k: voices[k]["notes"])
    return {"voice": v, "why": "the busiest unpitched voice" if unpitched
            else "the busiest voice"}


# -- comparing -------------------------------------------------------------------------------------
def _gap_is_large(path: str, a: float, b: float) -> bool:
    kind, limit = THRESHOLDS.get(path, ("relative", 0.25))
    if kind == "absolute":
        return abs(b - a) >= limit
    base = max(abs(a), 1e-9)
    return abs(b - a) / base >= limit


def _differences(ours: dict, theirs: dict, dims: List[str]) -> List[dict]:
    out = F.differences(ours, theirs, dims)
    if "development" in dims:
        for path in _DEVELOPMENT:
            a, b = F.get(ours, path), F.get(theirs, path)
            if a is not None and b is not None:
                out.append({"dimension": "development", "reading": path,
                            "ours": a, "reference": b,
                            "gap": round(b - a, 4), "relative": None})
    return out


COMPARE_PROMPT = """You are listening to {n} fragments of chip or tracker \
music, one after another with {gap:.1f} s of silence between, matched in \
loudness. Each is the same musical span ({bars}) cut from its own grid: \
tempos differ and were not changed.
{index}
The piece being made is fragment {ours}. Its voices: {voices}.
Listen for: {dims}. Compare the piece with each reference on those, and \
with the agent's own sketches. Report only what you hear. Times are in \
seconds from the start of this clip.
Answer with JSON only: {{"observations": [{{"fragment": <n>, "t0": <s>, \
"t1": <s>, "feature": "<timbre|modulation|rhythm|arrangement|development|\
space|other>", "statement": "<short>", "voices": ["<a voice of the piece, \
if it is about one>"], "confidence": <0..1>}}], "closest_to_goal": \
<the n of the piece or of a sketch, or null>}}"""


def _listen_reel(ear, fragments: List[dict], index: dict, dims: List[str],
                 voices: List[str]) -> dict:
    status = EAR.listening(ear)
    if not status["available"]:
        return {"available": False, "why": status["why"]}
    native = ear.native if isinstance(ear, EAR.HybridEar) else ear
    lines = []
    ours_n = None
    for e in index["fragments"]:
        if e["kind"] == "ours" and ours_n is None:
            ours_n = e["n"]
        tempo = f", {e['bpm']:.0f} BPM" if e.get("bpm") else ""
        lines.append(f"{e['n']}. {e['in_reel'][0]:.2f}-{e['in_reel'][1]:.2f}"
                     f" s: {e['kind'].upper()} {e['label']}{tempo}")
    wav = EAR.wav_bytes(F.mono_of(REF.read_wav(index["reel"]["wav"])), RATE)
    prompt = COMPARE_PROMPT.format(
        n=len(index["fragments"]), gap=GAP_S,
        bars=", ".join(sorted({f"{e['bars'][1] - e['bars'][0] + 1} bars"
                               for e in index["fragments"]})),
        index="\n".join(lines), ours=ours_n, voices=", ".join(voices),
        dims=", ".join(dims) or "everything")
    out = {"available": True, "model": native.adapter.name,
           "audio_seconds": index["reel_seconds"]}
    try:
        reply = native.adapter.listen(wav, prompt)
        text = reply[reply.index("{"):reply.rindex("}") + 1]
        data = json.loads(text)
        obs = []
        for o in data.get("observations", [])[:24]:
            n = int(o.get("fragment", 0))
            entry = next((e for e in index["fragments"] if e["n"] == n),
                         None)
            if entry is None:
                continue
            a = float(o.get("t0", entry["in_reel"][0]))
            b = float(o.get("t1", entry["in_reel"][1]))
            obs.append({"fragment": entry["label"], "n": n,
                        "t0": round(max(0.0, a - entry["in_reel"][0]), 3),
                        "t1": round(max(0.0, b - entry["in_reel"][0]), 3),
                        "feature": str(o.get("feature", "other")),
                        "statement": str(o.get("statement", ""))[:240],
                        "voices": [v for v in o.get("voices", [])
                                   if isinstance(v, str)],
                        "confidence": max(0.0, min(1.0, float(
                            o.get("confidence", 0.5)))),
                        "basis": "heard by model",
                        "evidence": {"method": f"the reel, heard by "
                                               f"{native.adapter.name}"}})
        out.update(observations=obs,
                   closest_to_goal=data.get("closest_to_goal"))
    except Exception as error:          # recorded, never replaced
        out.update(observations=[], problem=f"no usable answer: "
                                            f"{str(error)[:160]}")
    return out


def compare(project, fragments: List[dict], folder: str, name: str,
            ear=None, voice: Optional[str] = None,
            dims: Optional[List[str]] = None, mp3: bool = True) -> dict:
    """Write the fragments and the reel, measure them, and say how the
    piece differs from each reference (on the dimensions it is tagged
    for, or `dims`) and from each sketch (on every dimension). `voice` is
    the piece's voice whose notes are read (the lead by default); each
    reference's voice for the same role is found by match_voice. -> the
    report with the observations and the listening status."""
    ours = next((f for f in fragments if f["kind"] == "ours"), None)
    if ours is None:
        raise AgenticError("no_piece", "compare needs a fragment of the "
                           "piece (listen.of_project)")
    tl = ours["_timeline"]
    if voice is None:
        voice = next((v for v, ins in tl.content["instruments"].items()
                      if ins.get("role") in ("lead", "melody")), None)
    role = tl.content["instruments"].get(voice, {}).get("role", "lead") \
        if voice else "lead"
    index = write(fragments, folder, name, mp3)
    readings = {}
    for f in fragments:
        f.pop("count_beat_s", None)
        if f["kind"] == "reference" and f.get("grid") == "audio" and \
                f.get("beat_s") and ours.get("beat_s"):
            # a beat estimated from audio can be the half or the double
            # of the one a musician would count: per-beat readings are
            # taken on the level nearest the piece's beat, and said so
            import math
            k = round(math.log2(ours["beat_s"] / f["beat_s"]))
            k = max(-1, min(1, k))
            if k:
                f["count_beat_s"] = f["beat_s"] * (2.0 ** k)
        rv = None
        if f["kind"] == "reference":
            rv = match_voice(project, f["source"], role)
            f["matched_voice"] = rv
        readings[f["label"]] = measure(
            project, f, voice=voice if f["kind"] != "reference" else None,
            reference_voice=(rv or {}).get("voice"))
    observations = []
    not_compared = []
    k = 0
    for f in fragments:
        if f is ours:
            continue
        if f["kind"] == "reference":
            use = dims or f.get("tags") or list(F.DIMENSIONS)
        else:
            use = dims or list(F.DIMENSIONS)
        mine = readings[ours["label"]]
        theirs = readings[f["label"]]
        if theirs.get("onsets_from") == "audio" and \
                mine.get("onsets_from") == "score":
            # a recording has no score: both are counted from the audio
            mine = dict(mine, onsets_per_beat=mine["onsets_per_beat_audio"],
                        offbeat_share=mine.get("offbeat_share_audio"),
                        onsets_from="audio")
            mine.pop("notes_per_beat", None)
        for key, path in (("offbeat_share_not_measurable",
                           "offbeat_share"),):
            if theirs.get(key) and path in [
                    p for dim in use for p in F.DIMENSIONS.get(dim, [])]:
                not_compared.append({"against": f["label"], "reading": path,
                                     "why": theirs[key]})
        said = set()
        for d in _differences(mine, theirs, use):
            if not _gap_is_large(d["reading"], d["ours"], d["reference"]):
                continue
            if d["reading"] in said:
                continue            # a reading two dimensions share: once
            said.add(d["reading"])
            k += 1
            words = _WORDS.get(d["reading"], d["reading"])
            per_voice = d["reading"].startswith("voice.")
            whose = f["kind"] if f["kind"] != "reference" else "reference"
            observations.append({
                "id": f"L{k}", "basis": "measured",
                "dimension": d["dimension"], "feature": d["reading"],
                "fragment": ours["label"], "against": f["label"],
                "against_kind": f["kind"],
                "t0": 0.0, "t1": ours["seconds"],
                "piece_time": [round(ours["t0"], 3), round(ours["t1"], 3)],
                "bars": ours.get("bars"),
                "voices": [voice] if per_voice and voice else [],
                "reference_voice": (f.get("matched_voice") or {}).get(
                    "voice") if per_voice else None,
                "ours": d["ours"], "theirs": d["reference"],
                "gap": d["gap"],
                "counted_from": theirs.get("onsets_from")
                if d["reading"] in ("onsets_per_beat", "notes_per_beat",
                                    "offbeat_share") else None,
                "statement": f"{words}: ours {d['ours']:g}, the {whose} "
                             f"{d['reference']:g}" + (
                                 f" (counted from the "
                                 f"{theirs.get('onsets_from')}, on beats of "
                                 f"{ours.get('bpm') or 0:.0f} and "
                                 f"{60.0 / (f.get('count_beat_s') or f.get('beat_s') or 0.5):.0f} BPM"
                                 + (", the reference's estimated beat "
                                    "taken at the level nearest ours"
                                    if f.get("count_beat_s") else "")
                                 + ")"
                                 if d["reading"] in ("onsets_per_beat",
                                                     "notes_per_beat",
                                                     "offbeat_share")
                                 else ""),
                "confidence": 0.7,
                "evidence": {"method": "features.py on both fragments "
                                       "(loudness-matched copies; the "
                                       "readings are level-independent "
                                       "ratios, onsets, pitch and time)",
                             "threshold": THRESHOLDS.get(d["reading"])}})
    # links the source makes plausible: assumptions to test, not findings
    for f in fragments:
        if f["kind"] != "reference":
            continue
        ref = REF.get(project, f["source"])
        for c in ref.get("claims", []):
            verdict = (c.get("audio_check") or {}).get("verdict", "")
            if c["kind"] == "echo" and verdict == "confirmed" and \
                    "space" in (dims or f.get("tags") or []):
                k += 1
                observations.append({
                    "id": f"L{k}", "basis": "assumed",
                    "dimension": "space", "feature": "echo voice",
                    "fragment": ours["label"], "against": f["label"],
                    "against_kind": "reference", "t0": 0.0,
                    "t1": ours["seconds"], "voices": [],
                    "statement": f"the reference's space is assumed to come "
                                 f"partly from {c['voices'][0]}, a quieter "
                                 f"copy of {c['voices'][1]} "
                                 f"{c['source_evidence']['delay_s']} s "
                                 f"later (in its source and confirmed in "
                                 f"its audio); the piece has no such voice",
                    "confidence": 0.5,
                    "evidence": {"claim": c["claim"],
                                 "audio_check": c["audio_check"]}})
            if c["kind"] == "vibrato" and verdict == "confirmed" and \
                    "modulation" in (dims or f.get("tags") or []):
                k += 1
                observations.append({
                    "id": f"L{k}", "basis": "assumed",
                    "dimension": "modulation", "feature": "vibrato voice",
                    "fragment": ours["label"], "against": f["label"],
                    "against_kind": "reference", "t0": 0.0,
                    "t1": ours["seconds"],
                    "voices": [voice] if voice else [],
                    "statement": f"the reference's movement on long notes "
                                 f"is assumed to be its {c['voices'][0]} "
                                 f"vibrato ({c['audio_check']['why']}); a "
                                 f"vibrato on ours is the transfer to try",
                    "confidence": 0.5,
                    "evidence": {"claim": c["claim"],
                                 "audio_check": c["audio_check"]}})
    voices = list(tl.content["instruments"])
    heard = _listen_reel(ear, fragments, index,
                         sorted({t for f in fragments
                                 for t in (f.get("tags") or [])}) or
                         list(F.DIMENSIONS), voices) if ear is not None \
        else {"available": False, "why": EAR.listening(None)["why"]}
    observations = (heard.get("observations") or []) + observations
    return {"index": index["path"], "reel": index["reel"],
            "fragments": [{k: v for k, v in e.items() if k != "files"}
                          for e in index["fragments"]],
            "loudness": index["loudness"],
            "readings": {lab: _compact(r) for lab, r in readings.items()},
            "matched_voices": {f["label"]: f.get("matched_voice")
                               for f in fragments
                               if f["kind"] == "reference"},
            "voice": voice,
            "observations": observations,
            "not_compared": not_compared,
            "listening": EAR.listening(ear) if ear is not None
            else EAR.listening(None),
            "model_listening": {k: v for k, v in heard.items()
                                if k != "observations"}}


def _compact(r: dict) -> dict:
    keep = {k: r.get(k) for k in ("seconds", "onsets_per_beat",
                                  "notes_per_beat", "offbeat_share",
                                  "onsets_from", "onsets_per_second",
                                  "attack_rise_ms", "tail_ms")}
    keep["lufs"] = r["loudness"]["lufs"]
    keep["balance"] = r["balance"].get("shares")
    keep["centroid_hz"] = r["balance"].get("centroid_hz")
    keep["stereo"] = r["stereo"]
    keep["development"] = r.get("development")
    if r.get("voice"):
        keep["voice"] = {k: v for k, v in r["voice"].items()
                         if k != "method"}
    return keep
