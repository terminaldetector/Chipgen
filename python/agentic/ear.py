"""ear.py — three ways of hearing a range, and never confusing them.

    Tool    the engine's own measurements of the render, read against the
            score it knows: levels, each voice against everything else
            (attack and body margins, and the margin in the voice's own
            band, note by note), who fills that band, notes that were
            written and are not there, notes that hang on, clipping. (Not
            clicks: a sample-step detector also fires on every edge of the
            PSG's square and noise, measured, so it is left out rather than
            reporting edges as faults.) Nothing here listens. Its reports
            say "technically analysed the render", never "heard" or
            "listened".
    Native  a model that takes audio, sent the range as WAV with the
            intent and time markers, asked for structured observations.
            Only for an adapter that has shown it hears: a model's name
            proves nothing, so `verify` plays it two short probes whose
            answers cannot be guessed from text (how many tones, which is
            higher) and the adapter is Native only if it answers them.
    Hybrid  both. Each auditory observation is checked against the
            measurements of the same voices in the same time: confirmed,
            not confirmed, or not measurable — and neither side wins by
            default.

`choose(mode, adapter)` picks honestly: "auto" is Hybrid only with a
verified adapter, Tool otherwise, and asking for Native or Hybrid without
one is refused with the reason.

An observation is a dict: kind, voices, where (seconds, rows, bars,
sections), a short statement, the evidence with its method, its basis
("measured", "score" or "auditory model"), a confidence and a severity.
What caused it is diagnose.py's question.
"""

import array
import base64
import io
import json
import math
import wave
from typing import Dict, List, Optional

import analysis
from synthesis import phases

from . import render as R
from .state import AgenticError

TOOL_STATEMENT = ("technically analysed the render with the engine's own "
                  "measurements; nothing was listened to")
#: A voice whose body sits this far under everything else is "buried".
BURIED_BODY_DB = -3.0
#: A note this far under the background at its onset is "not heard".
UNHEARD_ONSET_DB = -20.0
#: A sustained sound held this many bars or more is reported.
LONG_HOLD_BARS = 2
_OCTAVES = (62.5, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0,
            16000.0)


# -- measurements -----------------------------------------------------------
def band_profile(samples, rate: int = R.RATE, size: int = 4096,
                 windows: int = 8) -> Dict[str, float]:
    """Energy per octave band (dB), averaged over up to `windows` Hann
    windows spread through the samples (a Welch average, pure Python)."""
    n = len(samples)
    if n < size:
        size = max(256, 1 << (max(1, n).bit_length() - 1))
    if n < size:
        return {f"{int(c)}": -180.0 for c in _OCTAVES}
    starts = [int(i * (n - size) / max(1, windows - 1))
              for i in range(windows)] if n > size else [0]
    window = analysis._hann(size)
    power = [0.0] * (size // 2 + 1)
    for s in sorted(set(starts)):
        frame = [samples[s + i] * window[i] for i in range(size)]
        spec = analysis._fft_pure(frame)
        for k in range(size // 2 + 1):
            power[k] += abs(spec[k]) ** 2
    out = {}
    binw = rate / size
    for centre in _OCTAVES:
        lo, hi = centre / math.sqrt(2), centre * math.sqrt(2)
        total = sum(power[k] for k in range(int(lo / binw) + 1,
                                            min(len(power), int(hi / binw) + 1)))
        out[f"{int(centre)}"] = round(10 * math.log10(total + 1e-20), 2)
    return out


_HANN = {}


def band_energy(samples, a: int, b: int, f_lo: float, f_hi: float,
                rate: int = R.RATE, size: int = 2048) -> float:
    """Mean power between f_lo and f_hi over [a, b), from Hann-windowed
    FFT frames (hop size/2; one zero-padded frame if the span is short)."""
    if size not in _HANN:
        _HANN[size] = analysis._hann(size)
    window = _HANN[size]
    starts = list(range(a, max(a + 1, b - size + 1), size // 2)) or [a]
    lo = max(1, int(f_lo * size / rate))
    hi = min(size // 2, int(f_hi * size / rate) + 1)
    total = 0.0
    for s0 in starts:
        frame = [0.0] * size
        for i in range(size):
            j = s0 + i
            if j >= b or j >= len(samples):
                break
            frame[i] = samples[j] * window[i]
        spec = analysis._fft_pure(frame)
        total += sum(abs(spec[k]) ** 2 for k in range(lo, hi))
    return total / len(starts)


def in_band(solo, background, notes, rate: int = R.RATE, others=None,
            body_from_ms: float = 40.0, body_to_ms: float = 240.0) -> dict:
    """Each note against the background in its own band: from 0.8 x its
    fundamental to 6 x (where its main partials are), over the note's body.
    That is the masking a listener hears: a bass an octave below the lead
    takes the same share of the mix's level and almost none of the lead's
    band (measured on fixtures.masked_lead: broadband margin -5.8 dB either
    way, in-band margin moves with the register). `notes` are
    (start s, end s, f0 Hz); `others` {voice: samples} are split by their
    share of the background's energy in those bands."""
    rows, shares = [], {}
    for on, off, f0 in notes:
        if not f0:
            continue
        a = int((on + body_from_ms / 1000.0) * rate)
        b = int(min(off, on + body_to_ms / 1000.0) * rate)
        if b - a < 256:
            continue
        lo, hi = 0.8 * f0, min(rate / 2.0, 6.0 * f0)
        part = band_energy(solo, a, b, lo, hi, rate)
        rest = band_energy(background, a, b, lo, hi, rate)
        margin = 10 * math.log10((part + 1e-20) / (rest + 1e-20))
        rows.append({"at_ms": round(on * 1000.0, 1), "f0_hz": round(f0, 1),
                     "band_hz": [round(lo), round(hi)],
                     "band_margin_db": round(margin, 2)})
        for u, x in (others or {}).items():
            shares[u] = shares.get(u, 0.0) + band_energy(x, a, b, lo, hi,
                                                         rate)
    margins = sorted(r["band_margin_db"] for r in rows)
    total = sum(shares.values()) or 1e-20
    return {"notes": rows,
            "band_margin_db": margins[len(margins) // 2] if margins
            else None,
            "band_share": {u: round(e / total, 3) for u, e in
                           sorted(shares.items(), key=lambda kv: -kv[1])
                           if e / total > 0.01}}


def note_hz(pitch: int) -> float:
    """The engine's pitch numbering (octave * 12 + semitone, A-4 = 57)."""
    return 440.0 * 2 ** ((pitch - 57) / 12.0)


def _db(x: float) -> float:
    return 20.0 * math.log10(x) if x > 1e-9 else -180.0


def _rms(x) -> float:
    total = 0.0
    for v in x:
        total += v * v
    return math.sqrt(total / len(x)) if len(x) else 0.0


def _peak(x) -> float:
    return max((abs(v) for v in x), default=0.0)


def _voice_role(timeline, voice: str) -> str:
    ins = timeline.content["instruments"].get(voice, {})
    return ins.get("role", "")


def _where(timeline, t0: float, t1: float) -> dict:
    first = timeline.row_at(t0) or timeline.rows[0]
    last = timeline.row_at(max(t0, t1 - 1e-6)) or timeline.rows[-1]
    return {"t0": round(t0, 4), "t1": round(t1, 4),
            "rows": [first["index"], last["index"]],
            "bars": [first["bar"] + 1, last["bar"] + 1],
            "sections": sorted({timeline.rows[i]["section"]
                                for i in range(first["index"],
                                               last["index"] + 1)})}


class ToolEar:
    """Measurements of the render, read against the score."""

    mode = "tool"

    def __init__(self, cache: Optional[R.Cache] = None):
        self.cache = cache or R.Cache()

    def hear(self, timeline, rng: dict, focus: Optional[List[str]] = None,
             bands: bool = False, intent: str = "") -> dict:
        """`intent` is accepted so every ear is called the same way; the
        measurements do not depend on it."""
        renders_before = self.cache.misses
        seconds_before = self.cache.render_seconds
        t0, t1 = rng["t0"], rng["t1"]
        mix = R.render_range(timeline, rng, cache=self.cache)
        m = mix.in_range()
        mix_peak = _peak(m)
        measurements = {"mix": {"rms_db": round(_db(_rms(m)), 2),
                                "peak_db": round(_db(mix_peak), 2),
                                "limiter": abs(mix_peak - 0.98) < 0.002},
                        "voices": {}}
        observations = []
        if mix_peak >= 0.98:
            observations.append({
                "kind": "clipping", "voices": [],
                "where": _where(timeline, t0, t1),
                "statement": "the mix reached the limiter (peak 0.98): "
                             "something was louder than full scale",
                "evidence": {"peak": round(mix_peak, 4),
                             "method": "sample peak of the mix render"},
                "basis": "measured", "confidence": 0.95,
                "severity": "high"})
        sounding = [v for v in timeline.voices()
                    if timeline.sounding(t0, t1, v)]
        voices = [v for v in sounding if focus is None or v in focus]
        stems = {}
        for v in sounding:
            stems[v] = R.render_range(timeline, rng, voices=[v],
                                      cache=self.cache).in_range()
        for v in voices:
            solo = stems[v]
            background = R.render_range(timeline, rng, exclude=[v],
                                        cache=self.cache).in_range()
            notes = [n for n in timeline.sounding(t0, t1, v)
                     if n["start"] >= t0 - 1e-6]
            spans = [(n["start"] - t0, min(n["end"], t1) - t0) for n in notes]
            context = phases.in_context(solo, background, R.RATE, spans) \
                if spans else None
            entry = {"role": _voice_role(timeline, v),
                     "notes": len(notes),
                     "rms_db": round(_db(_rms(solo)), 2),
                     "peak_db": round(_db(_peak(solo)), 2)}
            if context:
                entry["attack_margin_db"] = context["attack_margin_db"]
                entry["body_margin_db"] = context["body_margin_db"]
                entry["per_note"] = context["notes"]
            pitched = [(n["start"] - t0, min(n["end"], t1) - t0,
                        note_hz(n["pitch"])) for n in notes
                       if n.get("pitch") is not None]
            if pitched:
                band = in_band(solo, background, pitched,
                               others={u: x for u, x in stems.items()
                                       if u != v})
                entry["band_margin_db"] = band["band_margin_db"]
                entry["band_share"] = band["band_share"]
                entry["per_note_band"] = band["notes"]
            if bands and len(solo) > 512:
                entry["bands_db"] = band_profile(solo)
            measurements["voices"][v] = entry
            # notes written and not heard
            unheard = [nm for nm, n in zip(entry.get("per_note", []), notes)
                       if nm.get("attack_margin_db") is not None
                       and max(nm["attack_margin_db"],
                               nm.get("body_margin_db") or -180.0)
                       < UNHEARD_ONSET_DB]
            if unheard:
                observations.append({
                    "kind": "unheard_notes", "voices": [v],
                    "where": _where(timeline, t0, t1),
                    "statement": f"{len(unheard)} of {len(notes)} {v} "
                                 f"note(s) start more than "
                                 f"{-UNHEARD_ONSET_DB:g} dB under "
                                 f"everything else",
                    "evidence": {"at_ms": [u["at_ms"] for u in unheard[:8]],
                                 "method": "the louder of a note's attack "
                                           "(first 40 ms) and body (to 240 "
                                           "ms), the voice alone against all "
                                           "other voices together"},
                    "basis": "measured", "confidence": 0.8,
                    "severity": "high"})
            body = entry.get("band_margin_db")
            tonal = timeline.content["instruments"].get(v, {}).get(
                "channel", v).startswith(("fm", "psg", "opl", "nes0",
                                          "nes1", "nes2"))
            if body is not None and body < BURIED_BODY_DB and tonal and \
                    entry["role"] in ("lead", "melody", "counter", ""):
                observations.append({
                    "kind": "buried", "voices": [v],
                    "where": _where(timeline, t0, t1),
                    "statement": f"{v} sits {abs(body):.1f} dB under the "
                                 f"rest of the mix in its own band",
                    "evidence": {"band_margin_db": body,
                                 "band_share": entry.get("band_share"),
                                 "broadband_body_margin_db":
                                     entry.get("body_margin_db"),
                                 "attack_margin_db":
                                     entry.get("attack_margin_db"),
                                 "method": "each note's body (40-240 ms) "
                                           "alone against all other voices "
                                           "together, energy between 0.8x "
                                           "and 6x its fundamental, median "
                                           "over notes; band_share splits "
                                           "the background by voice"},
                    "basis": "measured", "confidence": 0.8,
                    "severity": "high" if entry["role"] == "lead"
                    else "medium"})
        # sounds that hang on
        rows_per_bar = timeline.rows_per_bar
        for n in timeline.sounding(t0, t1):
            if n.get("end_row") is None or n.get("row") is None:
                continue
            length_rows = n["end_row"] - n["row"]
            noise_long = (n["column"] in ("noise", "nes3")
                          and length_rows >= LONG_HOLD_BARS * rows_per_bar)
            tone_long = (n["column"].startswith(("fm", "psg", "opl", "nes"))
                         and n["column"] not in ("nes3", "nes4")
                         and length_rows >= 4 * rows_per_bar)
            if noise_long or tone_long:
                crosses = n["section"] != timeline.rows[
                    min(len(timeline.rows) - 1, n["end_row"] - 1)]["section"]
                observations.append({
                    "kind": "long_hold", "voices": [n["voice"]],
                    "where": _where(timeline, n["start"], n["end"]),
                    "statement": f"{n['voice']} holds {n['name']} for "
                                 f"{length_rows / rows_per_bar:.1f} bars"
                                 + (" across a section boundary" if crosses
                                    else "") + "; intended?",
                    "evidence": {"start_s": round(n["start"], 3),
                                 "end_s": round(n["end"], 3),
                                 "method": "key-on to key-off in the score"},
                    "basis": "score", "confidence": 0.6,
                    "severity": "medium" if n["column"] == "noise"
                    else "low"})
        return {"mode": "tool", "statement": TOOL_STATEMENT,
                "range": {k: rng[k] for k in ("spec", "t0", "t1", "bars",
                                               "sections")},
                "observations": observations, "measurements": measurements,
                "cost": {"renders": self.cache.misses - renders_before,
                         "render_seconds": round(self.cache.render_seconds
                                                 - seconds_before, 2)}}


# -- a model that takes audio -------------------------------------------------
def wav_bytes(samples, rate: int = R.RATE, stereo: bool = False) -> bytes:
    pcm = array.array("h", (max(-32768, min(32767, int(v * 32767)))
                            for v in samples))
    out = io.BytesIO()
    with wave.open(out, "wb") as handle:
        handle.setnchannels(2 if stereo else 1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())
    return out.getvalue()


def _tone(freqs, seconds_each: float = 0.35, gap: float = 0.25,
          rate: int = 16000) -> bytes:
    data = []
    for f in freqs:
        n = int(seconds_each * rate)
        for i in range(n):
            env = min(1.0, i / 200.0, (n - i) / 200.0)
            data.append(0.4 * env * math.sin(2 * math.pi * f * i / rate))
        data.extend([0.0] * int(gap * rate))
    return wav_bytes(data, rate)


#: (clip, question, accepted answers). The answers are in the audio only.
PROBES = [
    ([440.0, 440.0, 440.0], "How many separate tones do you hear? Answer "
                            "with a single digit.", {"3", "three"}),
    ([330.0, 660.0], "Two tones play one after the other. Is the second "
                     "higher or lower than the first? Answer with one word: "
                     "higher or lower.", {"higher"}),
    ([880.0, 440.0], "Two tones play one after the other. Is the second "
                     "higher or lower than the first? Answer with one word: "
                     "higher or lower.", {"lower"}),
]


def _square(freq: float, seconds: float, rate: int, attack_s: float = 0.003,
            level: float = 0.35) -> list:
    """A chip-like square note with a linear attack and a short release."""
    n = int(seconds * rate)
    a = max(1, int(attack_s * rate))
    period = rate / freq
    return [level * (1.0 if (i % period) < period / 2 else -1.0)
            * min(1.0, i / a, (n - i) / 200.0) for i in range(n)]


def _music_clip(kind: str, rate: int = 16000) -> bytes:
    gap = [0.0] * int(0.4 * rate)
    if kind in ("same", "different"):
        a = _square(440, 0.25, rate) + _square(554.4, 0.25, rate) + \
            _square(659.3, 0.25, rate)
        b = list(a) if kind == "same" else \
            _square(440, 0.25, rate) + _square(523.3, 0.25, rate) + \
            _square(659.3, 0.25, rate)
        return wav_bytes(a + gap + b, rate)
    if kind in ("sharp_first", "sharp_second"):
        sharp = _square(330, 0.6, rate, attack_s=0.002)
        soft = _square(330, 0.6, rate, attack_s=0.18)
        first, second = (sharp, soft) if kind == "sharp_first" else \
            (soft, sharp)
        return wav_bytes(first + gap + second, rate)
    if kind == "left_first":
        tone = _square(392, 0.5, rate)
        stereo = []
        for v in tone:
            stereo += [v, 0.0]
        stereo += [0.0] * (2 * len(gap))
        for v in tone:
            stereo += [0.0, v]
        return wav_bytes(stereo, rate, stereo=True)
    raise ValueError(kind)


#: (name, clip, question, accepted). What a model must hear to be trusted
#: to choose among versions: two identical versions are the same, two that
#: differ by one note are not, which of two notes starts sharply (both
#: orders), and where a stereo tone sits. A model that answers from the
#: text alone gets about 1 in 32.
MUSIC_PROBES = [
    ("identity", "same", "Two versions play one after the other. Are they "
     "the same or different? Answer with one word: same or different.",
     {"same"}),
    ("difference", "different", "Two versions play one after the other. "
     "Are they the same or different? Answer with one word: same or "
     "different.", {"different"}),
    ("attack", "sharp_first", "Two notes play one after the other. Which "
     "starts more sharply? Answer with one word: first or second.",
     {"first"}),
    ("attack_swapped", "sharp_second", "Two notes play one after the "
     "other. Which starts more sharply? Answer with one word: first or "
     "second.", {"second"}),
    ("pan", "left_first", "Two tones play one after the other. Is the first "
     "one on the left or on the right? Answer with one word: left or "
     "right.", {"left"}),
]


def answer_by_dsp(wav: bytes, prompt: str) -> Optional[str]:
    """The probes answered from the samples by signal processing — not a
    model: the positive control the probes are checked against (tests,
    check_listening.py). Tones by their energy, pitch by zero crossings,
    same/different by the samples, attack by the rise of the envelope,
    left/right by the channels' energy. None for a question that is not a
    probe."""
    with wave.open(io.BytesIO(wav)) as w:
        rate, ch = w.getframerate(), w.getnchannels()
        pcm = array.array("h", w.readframes(w.getnframes()))
    mono = [sum(pcm[i:i + ch]) / ch for i in range(0, len(pcm), ch)]
    hop = rate // 100
    loud = [max(abs(v) for v in mono[i:i + hop]) > 2000
            for i in range(0, len(mono) - hop, hop)]
    spans, start = [], None
    for i, on in enumerate(loud + [False]):
        if on and start is None:
            start = i
        if not on and start is not None:
            spans.append((start * hop, i * hop))
            start = None
    if "How many" in prompt:
        return str(len(spans))
    if len(spans) < 2:
        return None

    def zc(a, b):
        return sum(1 for j in range(a + 1, b)
                   if (mono[j - 1] < 0) != (mono[j] < 0)) / (b - a)
    if "higher or lower" in prompt:
        return "higher" if zc(*spans[1]) > zc(*spans[0]) else "lower"
    if "same or different" in prompt:
        (a0, a1), (b0, b1) = spans[0], spans[1]
        n = min(a1 - a0, b1 - b0)
        diff = sum(abs(mono[a0 + k] - mono[b0 + k]) for k in range(n))
        size = sum(abs(mono[a0 + k]) for k in range(n)) or 1.0
        return "same" if diff / size < 0.05 else "different"
    if "starts more sharply" in prompt:
        def rise(a, b):
            peak = max(abs(v) for v in mono[a:b]) or 1.0
            return next((k for k in range(a, b)
                         if abs(mono[k]) >= 0.8 * peak), b) - a
        return "first" if rise(*spans[0]) < rise(*spans[1]) else "second"
    if "left or on the right" in prompt and ch == 2:
        a, b = spans[0]
        left = sum(abs(pcm[2 * k]) for k in range(a, b))
        right = sum(abs(pcm[2 * k + 1]) for k in range(a, b))
        return "left" if left > right else "right"
    return None


def verify_music(adapter) -> dict:
    """The music probes: whether a model that hears tones also hears what
    a choice among versions rests on. -> {"passed", "probes": {name:
    right}, "answers"}."""
    probes, answers = {}, []
    for name, kind, question, accepted in MUSIC_PROBES:
        try:
            reply = adapter.listen(_music_clip(kind), question)
        except Exception as error:
            answers.append({"probe": name, "error": str(error)[:160]})
            probes[name] = False
            continue
        word = reply.strip().lower().split()[0].strip(".,!") if \
            reply.strip() else ""
        probes[name] = word in accepted
        answers.append({"probe": name, "reply": reply[:60],
                        "right": probes[name]})
    return {"passed": all(probes.values()), "probes": probes,
            "answers": answers}


class AudioAdapter:
    """Something that can be sent audio and a question. `listen` returns
    the reply text. An OpenAI-compatible endpoint is below; a test can pass
    any object with the same two methods."""

    name = "adapter"

    def declares_audio(self) -> bool:
        return False

    def listen(self, wav: bytes, prompt: str) -> str:
        raise AgenticError("no_audio_input", f"{self.name} takes no audio")


class OpenAIAudioAdapter(AudioAdapter):
    """Chat completions with an `input_audio` content part (the OpenAI
    format). Whether the model behind the endpoint hears is for `verify`
    to find out; setting `declared=True` only says the operator claims
    it."""

    def __init__(self, endpoint, declared: bool = False):
        self.endpoint = endpoint
        self.name = f"openai:{endpoint.model}"
        self.declared = declared

    def declares_audio(self) -> bool:
        return self.declared

    def listen(self, wav: bytes, prompt: str) -> str:
        import llm
        message = {"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "input_audio",
             "input_audio": {"data": base64.b64encode(wav).decode("ascii"),
                             "format": "wav"}}]}
        return llm.chat(self.endpoint, [message], temperature=0.0,
                        max_tokens=600)


def verify(adapter: AudioAdapter) -> dict:
    """Play the probes; Native only if every answer is right. A text-only
    model guessing has about a 1 in 40 chance (a digit, then two coin
    flips); the record says what was asked and answered."""
    answers = []
    ok = adapter.declares_audio()
    if not ok:
        return {"verified": False, "adapter": adapter.name,
                "why": "the adapter does not declare audio input",
                "answers": []}
    for freqs, question, accepted in PROBES:
        try:
            reply = adapter.listen(_tone(freqs), question)
        except Exception as error:      # a refusal is an answer too
            answers.append({"question": question, "error": str(error)[:200]})
            ok = False
            continue
        word = reply.strip().lower().split()[0].strip(".,!") if \
            reply.strip() else ""
        right = word in accepted
        answers.append({"question": question, "reply": reply[:80],
                        "right": right})
        ok = ok and right
    out = {"verified": ok, "adapter": adapter.name, "answers": answers,
           "why": "answered every probe" if ok else
           "did not answer every probe correctly"}
    if ok:
        # it hears tones; whether it can be trusted to choose among
        # versions is the music probes' question
        out["music"] = verify_music(adapter)
    return out


def trusted_to_choose(ear) -> dict:
    """Whether the listening model behind `ear` passed the music probes:
    its choice among versions decides only then; otherwise it is recorded
    and not obeyed."""
    native = ear.native if isinstance(ear, HybridEar) else ear
    v = getattr(native, "verification", None) or {}
    music = v.get("music")
    if music is None:
        return {"trusted": False, "why": "the music probes were not run"}
    if music["passed"]:
        return {"trusted": True, "why": "passed the music probes"}
    failed = [k for k, ok in music["probes"].items() if not ok]
    return {"trusted": False, "why": f"failed the music probes: "
                                     f"{', '.join(failed)}"}


#: the chips a piece plays, by the first letters of its channels, and
#: whether that machine has a stereo output a listener should get
PLATFORMS = (("fm", "Sega Mega Drive: YM2612 FM + SN76489", True),
             ("psg", "Sega Mega Drive: YM2612 FM + SN76489", True),
             ("dac", "Sega Mega Drive: YM2612 FM + SN76489", True),
             ("nes", "Nintendo NES: the 2A03's pulses, triangle, noise and "
                     "DMC (mono)", False),
             ("opl", "AdLib: the YM3812 (OPL2), 2-operator FM (mono)",
              False))


def platform_of(content_or_timeline) -> dict:
    """{"name", "stereo"} of the machine a piece plays on, read from its
    channels: the prompts name it, and a mono machine is sent mono."""
    content = getattr(content_or_timeline, "content", content_or_timeline)
    cols = [c for c in (content or {}).get("columns", []) if c]
    for prefix, name, stereo in PLATFORMS:
        if prefix in ("nes", "opl") and any(c.startswith(prefix)
                                            for c in cols):
            return {"name": name, "stereo": stereo}
    return {"name": PLATFORMS[0][1], "stereo": True}


#: what a heard observation is about, in words the loop can act on: the
#: router (guided.route) turns a category and a direction into changes
CATEGORIES = ("attack", "body_brightness", "vibrato", "articulation",
              "space", "density", "groove", "balance", "other")


NATIVE_PROMPT = """You are listening to {seconds:.1f} s of a chiptune piece \
({platform}). Its intent: {intent}.
Time 0 of the clip is {t0:.2f} s into the piece. Voices: {voices}. \
The clip is {channels}.
Listen for melody, rhythm, timbre, harmony, phrasing, clarity of each \
voice, transitions, form and dynamics. Report only what you hear.
For each thing you would change, say what it is about and which way: \
category is one of attack (how the notes start), body_brightness (how \
bright the held part of the notes is), vibrato, articulation (how long or \
detached the notes are), space (stereo width), density (how busy), groove \
(placement against the beat), balance (a voice too loud or soft), other; \
direction is "more" or "less" of it. When it is about how one voice's \
notes sound, you may also say it phase by phase ("phases"): the attack \
(first 60 ms; quality sharpness or brightness), the body (to 350 ms; \
brightness), the held part of long notes (vibrato; only_long when it \
belongs on the long notes only), the release (length).
Answer with JSON only: {{"observations": [{{"kind": "<buried|harsh|\
muddy|too_loud|too_quiet|timing|transition|form|other>", "category": \
"<category>", "direction": "<more|less|null>", "voices": ["<voice>"], \
"phases": [{{"phase": "<attack|body|held|release>", "quality": \
"<sharpness|brightness|vibrato|length>", "direction": "<more|less>", \
"only_long": <true|false>}}], \
"t0": <s from clip start>, "t1": <s>, "statement": "<short>", \
"confidence": <0..1>, "cause": "<your guess or null>"}}]}}"""


def phases_of(o: dict) -> list:
    """The phase clauses of a model's observation that name a phase, a
    quality and a direction this layer has (intent.PHASES); the rest are
    dropped, not guessed at."""
    from .intent import parse
    raw = o.get("phases")
    if not isinstance(raw, list):
        return []
    return parse([c for c in raw if isinstance(c, dict)])["clauses"]


class NativeEar:
    """A verified audio model, asked for structured observations."""

    mode = "native"

    def __init__(self, adapter: AudioAdapter, verification: dict,
                 cache: Optional[R.Cache] = None):
        if not verification.get("verified"):
            raise AgenticError(
                "no_audio_input", f"{adapter.name} has not shown it hears "
                f"audio ({verification.get('why')}); use the Tool ear",
                adapter=adapter.name)
        self.adapter = adapter
        self.verification = verification
        self.cache = cache or R.Cache()

    def hear(self, timeline, rng: dict, focus: Optional[List[str]] = None,
             intent: str = "") -> dict:
        # played from the top: what is heard is the piece, not a chased
        # window's approximation of it
        mix = R.render_range(timeline, rng, cache=self.cache, method="full")
        plat = platform_of(timeline)
        # the machine's own output: stereo for the Mega Drive (its pans are
        # part of what is heard), mono for a mono machine
        samples = mix.in_range(mono=not plat["stereo"])
        prompt = NATIVE_PROMPT.format(
            seconds=rng["t1"] - rng["t0"], intent=intent or "not stated",
            t0=rng["t0"], voices=", ".join(timeline.voices()),
            platform=plat["name"],
            channels="stereo" if plat["stereo"] else "mono")
        reply = self.adapter.listen(wav_bytes(samples,
                                              stereo=plat["stereo"]), prompt)
        observations, problem = [], None
        try:
            text = reply[reply.index("{"):reply.rindex("}") + 1]
            data = json.loads(text)
            for o in data.get("observations", []):
                a = rng["t0"] + float(o.get("t0", 0.0))
                b = rng["t0"] + float(o.get("t1", rng["t1"] - rng["t0"]))
                cat = str(o.get("category") or "other")
                way = o.get("direction")
                observations.append({
                    "kind": str(o.get("kind", "other")),
                    "category": cat if cat in CATEGORIES else "other",
                    "direction": way if way in ("more", "less") else None,
                    "voices": [v for v in o.get("voices", [])
                               if isinstance(v, str)],
                    "phases": phases_of(o),
                    "where": _where(timeline, max(rng["t0"], a),
                                    min(rng["t1"], max(a, b))),
                    "statement": str(o.get("statement", ""))[:200],
                    "evidence": {"model_cause": o.get("cause"),
                                 "method": f"auditory judgement of "
                                           f"{self.adapter.name}"},
                    "basis": "auditory model",
                    "confidence": max(0.0, min(1.0, float(
                        o.get("confidence", 0.5)))),
                    "severity": "medium"})
        except (ValueError, TypeError) as error:
            problem = f"the model's reply was not the JSON asked for: " \
                      f"{str(error)[:120]}"
        return {"mode": "native",
                "statement": f"the range was sent as audio to "
                             f"{self.adapter.name}, verified by probe",
                "range": {k: rng[k] for k in ("spec", "t0", "t1", "bars",
                                               "sections")},
                "observations": observations, "reply_problem": problem,
                "measurements": {}, "audio": {
                    "channels": 2 if plat["stereo"] else 1, "rate": R.RATE,
                    "format": "wav", "playback": "full"},
                "cost": {"audio_seconds": round(rng["t1"] - rng["t0"], 2)}}


#: what a model's kind claims, and the measured reading that can check it
_CHECKS = {"buried": ("band_margin_db", lambda v: v < BURIED_BODY_DB),
           "too_quiet": ("band_margin_db", lambda v: v < BURIED_BODY_DB),
           "too_loud": ("band_margin_db", lambda v: v > 6.0),
           "muddy": ("band_margin_db", lambda v: v < 0.0)}


class HybridEar:
    """Tool and Native over the same range, cross-checked."""

    mode = "hybrid"

    def __init__(self, tool: ToolEar, native: NativeEar):
        self.tool = tool
        self.native = native

    def hear(self, timeline, rng: dict, focus: Optional[List[str]] = None,
             intent: str = "") -> dict:
        measured = self.tool.hear(timeline, rng, focus=focus)
        heard = self.native.hear(timeline, rng, intent=intent)
        voices = measured["measurements"]["voices"]
        for obs in heard["observations"]:
            check = _CHECKS.get(obs["kind"])
            verdicts = []
            for v in obs["voices"]:
                reading = voices.get(v, {}).get(check[0]) if check else None
                if check is None or reading is None:
                    verdicts.append("not measurable")
                else:
                    verdicts.append("confirmed" if check[1](reading)
                                    else "not confirmed")
            obs["cross_check"] = {
                "verdict": (verdicts[0] if len(set(verdicts)) == 1
                            else "mixed") if verdicts else "not measurable",
                "reading": {v: voices.get(v, {}).get(check[0])
                            for v in obs["voices"]} if check else None}
            if obs["cross_check"]["verdict"] == "confirmed":
                obs["basis"] = "auditory model + measured"
                obs["confidence"] = round(min(1.0, obs["confidence"] + 0.2),
                                          2)
        return {"mode": "hybrid",
                "statement": f"{TOOL_STATEMENT}; and {heard['statement']}",
                "range": measured["range"],
                "observations": measured["observations"]
                + heard["observations"],
                "measurements": measured["measurements"],
                "reply_problem": heard.get("reply_problem"),
                "cost": {**measured["cost"], **heard["cost"]}}


def choose(mode: str = "auto", adapter: Optional[AudioAdapter] = None,
           verification: Optional[dict] = None,
           cache: Optional[R.Cache] = None):
    """The ear the situation allows, said plainly."""
    if mode not in ("auto", "tool", "native", "hybrid"):
        raise AgenticError("bad_ear", f"ear is auto, tool, native or "
                           f"hybrid, not {mode!r}")
    tool = ToolEar(cache)
    if mode == "tool":
        return tool
    verified = bool(adapter and verification and verification.get("verified"))
    if mode == "auto":
        if not verified:
            return tool
        return HybridEar(tool, NativeEar(adapter, verification, tool.cache))
    if not verified:
        raise AgenticError(
            "no_audio_input",
            f"the {mode} ear needs an adapter that has shown it hears audio; "
            + ("none is configured" if adapter is None else
               f"{adapter.name} has not ({(verification or {}).get('why', 'not verified')})"),
            requested=mode)
    native = NativeEar(adapter, verification, tool.cache)
    return native if mode == "native" else HybridEar(tool, native)


# -- what the loop uses each ear for -------------------------------------------------
def measurer(ear, cache: Optional[R.Cache] = None) -> ToolEar:
    """The measuring ear behind any ear: the loop's metric and its guards
    are measurements, whichever ear observed. Shares the ear's cache."""
    if isinstance(ear, ToolEar):
        return ear
    if isinstance(ear, HybridEar):
        return ear.tool
    return ToolEar(getattr(ear, "cache", None) or cache)


def listening(ear) -> dict:
    """Whether anything listens behind `ear`, said plainly — every reply
    that could be read as hearing carries this."""
    if isinstance(ear, HybridEar):
        ear = ear.native
    if isinstance(ear, NativeEar):
        music = ear.verification.get("music") or {}
        return {"mode": "native", "available": True,
                "model": ear.adapter.name, "verified": True,
                "why": ear.verification.get("why", "answered the probes"),
                "music_probes": music.get("probes"),
                "trusted_to_choose": bool(music.get("passed"))}
    return {"mode": "tool", "available": False, "model": None,
            "verified": False,
            "why": "no audio model that passed the probes is configured: "
                   "the observations are measurements, nothing listened"}


def statement(ear) -> str:
    if isinstance(ear, HybridEar):
        return (f"{TOOL_STATEMENT}; and the range was sent as audio to "
                f"{ear.native.adapter.name}, verified by probe")
    if isinstance(ear, NativeEar):
        return (f"the range was sent as audio to {ear.adapter.name}, "
                f"verified by probe")
    return TOOL_STATEMENT


#: A listening model's preference for the original vetoes a commit only
#: this sure of itself (a weaker "A" is recorded, not obeyed).
VETO_CONFIDENCE = 0.6
#: Silence between the two versions in an A/B clip (seconds).
AB_GAP_S = 0.75

AB_PROMPT = """You are listening to two versions of the same {seconds:.1f} s passage of a chiptune piece ({platform}), one after the other, matched in loudness ({channels}). Version A plays from {a0:.2f} s to {a1:.2f} s of this clip, version B from {b0:.2f} s to {b1:.2f} s; silence between them. Voices: {voices}.
What the change is meant to improve: {goal}.
Which version does that better? Report only what you hear.
Answer with JSON only: {{"preference": "<A|B|same>", "confidence": <0..1>, "statement": "<short: what you heard>", "observations": [{{"version": "<A|B>", "t0": <s from that version's start>, "t1": <s>, "feature": "<what you heard>", "voices": ["<voice>"], "confidence": <0..1>}}]}}"""


def _mono(samples, stereo: bool):
    if not stereo:
        return list(samples)
    return [0.5 * (samples[i] + samples[i + 1])
            for i in range(0, len(samples) - 1, 2)]


def _sequence(versions, rate: int, stereo: bool, matched: bool = True):
    """The versions one after another with AB_GAP_S of silence between,
    each matched to the first's loudness (BS.1770, on as many channels as
    they have) and all scaled down together if a peak would pass -1 dBFS.
    Stereo stays stereo: a pan is part of what is compared. -> (samples,
    marks: each version's start and end in the clip, its gain)."""
    from . import features as F
    ch = 2 if stereo else 1
    versions = [list(v) for v in versions]
    louds = [F.loudness(v, rate, channels=ch)["lufs"] for v in versions]
    # matched: each at the first's loudness (timbre, form); not matched:
    # as rendered (a change of balance or dynamics is the point)
    gains = [louds[0] - lv if matched and louds[0] > -70.0 and lv > -70.0
             else 0.0 for lv in louds]
    peak = max([abs(x) * 10 ** (g / 20.0) for v, g in zip(versions, gains)
                for x in v] + [1e-9])
    common = min(1.0, 10 ** (-1.0 / 20.0) / peak)
    gap = [0.0] * int(AB_GAP_S * rate) * ch
    clip, spans, t = [], [], 0.0
    for k, (v, g) in enumerate(zip(versions, gains)):
        if k:
            clip.extend(gap)
            t += AB_GAP_S
        f = 10 ** (g / 20.0) * common
        clip.extend(x * f for x in v)
        dur = len(v) / ch / rate
        spans.append({"t0": round(t, 3), "t1": round(t + dur, 3),
                      "gain_db": round(g, 2)})
        t += dur
    return clip, {"versions": spans, "channels": ch, "matched": matched,
                  "common_gain_db": round(20 * math.log10(common), 2)}


def ab_clip(a, b, rate: int = R.RATE, stereo: bool = True):
    """A, silence, B as one clip — stereo when they are — B matched to A's
    loudness and both scaled down together if a peak would pass -1 dBFS.
    -> (samples, {a0, a1, b0, b1, gain_b_db, channels})."""
    clip, m = _sequence([a, b], rate, stereo)
    va, vb = m["versions"]
    return clip, {"a0": va["t0"], "a1": va["t1"], "b0": vb["t0"],
                  "b1": vb["t1"], "gain_b_db": vb["gain_db"],
                  "common_gain_db": m["common_gain_db"],
                  "channels": m["channels"]}


def ab_verdict(ear, a, b, goal: str, voices=(), rate: int = R.RATE,
               stereo: bool = True,
               platform: str = PLATFORMS[0][1]) -> dict:
    """Ask the listening model behind `ear` which of two versions does
    `goal` better. Without a verified model: {"available": False, why} —
    and nothing is put in its place. The reply is the model's, recorded
    as "heard by model"; it is never merged with the measurements."""
    status = listening(ear)
    if not status["available"]:
        return {"available": False, "why": status["why"], "basis": None}
    native = ear.native if isinstance(ear, HybridEar) else ear
    clip, marks = ab_clip(a, b, rate, stereo)
    prompt = AB_PROMPT.format(seconds=marks["a1"], a0=marks["a0"],
                              a1=marks["a1"], b0=marks["b0"],
                              b1=marks["b1"],
                              voices=", ".join(voices) or "not listed",
                              goal=goal, platform=platform,
                              channels="stereo" if marks["channels"] == 2
                              else "mono")
    out = {"available": True, "model": native.adapter.name,
           "basis": "heard by model", "clip": marks,
           "audio_seconds": round(len(clip) / marks["channels"] / rate, 2)}
    try:
        reply = native.adapter.listen(
            wav_bytes(clip, rate, stereo=marks["channels"] == 2), prompt)
    except Exception as error:          # a refusal is recorded, not hidden
        out.update(preference=None, problem=f"the model did not answer: "
                                            f"{str(error)[:160]}")
        return out
    try:
        text = reply[reply.index("{"):reply.rindex("}") + 1]
        data = json.loads(text)
        pref = str(data.get("preference", "")).strip().upper()
        pref = {"A": "A", "B": "B", "SAME": "same"}.get(pref)
        if pref is None:
            raise ValueError(f"preference {data.get('preference')!r}")
        out.update(preference=pref, confidence=max(0.0, min(1.0, float(
            data.get("confidence", 0.5)))),
            statement=str(data.get("statement", ""))[:240],
            observations=[{
                "version": str(o.get("version", "")).upper()[:1],
                "t0": float(o.get("t0", 0.0)),
                "t1": float(o.get("t1", 0.0)),
                "feature": str(o.get("feature", ""))[:160],
                "voices": [v for v in o.get("voices", [])
                           if isinstance(v, str)],
                "confidence": max(0.0, min(1.0, float(
                    o.get("confidence", 0.5)))),
                "basis": "heard by model"}
                for o in data.get("observations", [])[:12]])
    except (ValueError, TypeError) as error:
        out.update(preference=None, problem=f"the reply was not the JSON "
                                            f"asked for: {str(error)[:120]}",
                   reply=reply[:200])
    return out


def vetoes(verdict: Optional[dict]) -> bool:
    """True when a verified listening model preferred the original (A)
    with at least VETO_CONFIDENCE."""
    return bool(verdict and verdict.get("available")
                and verdict.get("preference") == "A"
                and verdict.get("confidence", 0.0) >= VETO_CONFIDENCE)


CHOOSE_PROMPT = """You are listening to {n} versions of the same passage of a chiptune piece ({platform}), one after another with {gap:.2f} s of silence between, {levels} ({channels}).
{index}
Version 1 is the piece as it is; the others are candidate changes. What the change is meant to do: {goal}.
Which version does that best, keeping the music as good as it was? Report only what you hear; if you cannot tell them apart, say "uncertain".
Answer with JSON only: {{"choice": <the version's number, or "uncertain">, "confidence": <0..1>, "statement": "<short: what you heard>", "ranking": [<version numbers, best first>]}}"""


def ab_choose(ear, versions, goal: str, voices=(), rate: int = R.RATE,
              stereo: bool = True, platform: str = PLATFORMS[0][1],
              matched: bool = True) -> dict:
    """Let the listening model behind `ear` choose among the original and
    several candidates (versions: [(label, samples)], the original first)
    — the ear choosing, not vetoing a winner the numbers chose. -> {
    available, choice (0 = the original, None = uncertain or no answer),
    confidence, statement, ranking, clip}. Without a verified model:
    {"available": False} and nothing in its place."""
    status = listening(ear)
    if not status["available"]:
        return {"available": False, "why": status["why"], "basis": None}
    native = ear.native if isinstance(ear, HybridEar) else ear
    clip, marks = _sequence([v for _l, v in versions], rate, stereo,
                            matched)
    lines = [f"{k + 1}. {m['t0']:.2f}-{m['t1']:.2f} s: {label}"
             for k, ((label, _v), m) in enumerate(zip(versions,
                                                     marks["versions"]))]
    prompt = CHOOSE_PROMPT.format(
        n=len(versions), platform=platform, gap=AB_GAP_S,
        levels="matched in loudness" if matched else
        "each at the level it was rendered (the balance is what changed)",
        channels="stereo" if marks["channels"] == 2 else "mono",
        index="\n".join(lines), goal=goal)
    out = {"available": True, "model": native.adapter.name,
           "basis": "heard by model", "labels": [l for l, _v in versions],
           "clip": marks, "audio_seconds": round(
               len(clip) / marks["channels"] / rate, 2), "choice": None}
    try:
        reply = native.adapter.listen(
            wav_bytes(clip, rate, stereo=marks["channels"] == 2), prompt)
        text = reply[reply.index("{"):reply.rindex("}") + 1]
        data = json.loads(text)
        raw = data.get("choice")
        conf = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
        ranking = [int(x) - 1 for x in data.get("ranking", [])
                   if isinstance(x, (int, float))
                   and 1 <= int(x) <= len(versions)]
        if isinstance(raw, str) and raw.strip().lower() == "uncertain":
            choice = None
        else:
            choice = int(raw) - 1
            if not 0 <= choice < len(versions):
                raise ValueError(f"choice {raw!r} is not a version")
        out.update(choice=choice, confidence=conf, ranking=ranking,
                   uncertain=choice is None,
                   statement=str(data.get("statement", ""))[:240])
    except Exception as error:          # recorded, never replaced
        out.update(problem=f"no usable answer: {str(error)[:160]}",
                   uncertain=True)
    return out
