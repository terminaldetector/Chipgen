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
             bands: bool = False) -> dict:
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
                "channel", v).startswith(("fm", "psg"))
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
            noise_long = (n["column"] == "noise"
                          and length_rows >= LONG_HOLD_BARS * rows_per_bar)
            tone_long = (n["column"].startswith(("fm", "psg"))
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
    return {"verified": ok, "adapter": adapter.name, "answers": answers,
            "why": "answered every probe" if ok else
            "did not answer every probe correctly"}


NATIVE_PROMPT = """You are listening to {seconds:.1f} s of a chiptune piece \
(Sega Mega Drive: YM2612 FM + SN76489). Its intent: {intent}.
Time 0 of the clip is {t0:.2f} s into the piece. Voices: {voices}.
Listen for melody, rhythm, timbre, harmony, phrasing, clarity of each \
voice, transitions, form and dynamics. Report only what you hear.
Answer with JSON only: {{"observations": [{{"kind": "<buried|harsh|\
muddy|too_loud|too_quiet|timing|transition|form|other>", "voices": \
["<voice>"], "t0": <s from clip start>, "t1": <s>, "statement": "<short>", \
"confidence": <0..1>, "cause": "<your guess or null>"}}]}}"""


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

    def hear(self, timeline, rng: dict, intent: str = "") -> dict:
        mix = R.render_range(timeline, rng, cache=self.cache)
        samples = mix.in_range()
        prompt = NATIVE_PROMPT.format(
            seconds=rng["t1"] - rng["t0"], intent=intent or "not stated",
            t0=rng["t0"], voices=", ".join(timeline.voices()))
        reply = self.adapter.listen(wav_bytes(samples), prompt)
        observations, problem = [], None
        try:
            text = reply[reply.index("{"):reply.rindex("}") + 1]
            data = json.loads(text)
            for o in data.get("observations", []):
                a = rng["t0"] + float(o.get("t0", 0.0))
                b = rng["t0"] + float(o.get("t1", rng["t1"] - rng["t0"]))
                observations.append({
                    "kind": str(o.get("kind", "other")),
                    "voices": [v for v in o.get("voices", [])
                               if isinstance(v, str)],
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
                "measurements": {}, "cost": {"audio_seconds": round(
                    rng["t1"] - rng["t0"], 2)}}


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

    def hear(self, timeline, rng: dict, intent: str = "") -> dict:
        measured = self.tool.hear(timeline, rng)
        heard = self.native.hear(timeline, rng, intent)
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
