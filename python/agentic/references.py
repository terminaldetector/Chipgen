"""references.py — other people's music next to the project: kept with
where it came from, heard on its own grid, measured on the same scale as
ours, never copied.

A reference is one of three things, and says which:

    audio    a recording (MP3, decoded by LAME; or WAV). Nothing but the
             sound: its beat grid is estimated from the audio
             (features.beat_grid), with a confidence, constant tempo
             assumed
    trk      a Chipgen score (.trk) with its FM bank — the corpus's
             transcriptions of Mega Drive soundtracks. Rendered by this
             engine; its rows give the grid, its columns the voices
    module   an IT, XM, S3M or MOD. Rendered by libopenmpt (openmpt.py),
             its played rows give the grid, its channels the voices

A source is extra evidence, not the truth about the sound: a transcription
can put the grid off the beat, an IT's row highlight is an editing aid, a
vibrato command can be too shallow to hear. So what the source suggests is
written down as claims — the tempo, a voice's vibrato, its density, an echo
between two voices, a channel's pan — and each claim is checked against the
audio (the voice rendered alone where it can be): confirmed, not
confirmed, or not measurable, with the reading.

The user says what a reference is for: tags from features.DIMENSIONS
(timbre, modulation, rhythm, arrangement, development, space), a note in
their words, and optionally the regions to listen to. Without regions, one
is chosen: the first 8 bars where the music is as busy as it usually is
(an intro is skipped).

Everything lives in <project>/references/<id>/ (the file as given, the
rendered or decoded audio, notes and stems) and the record in
state["references"]. None of it is the project's music: no revision
carries it, and the repository ignores audio. The melodies of a reference
are read only so that a change can be refused if it copies them
(guards.no_reference_copy).
"""

import array
import hashlib
import json
import os
import shutil
import subprocess
import time
import wave
from typing import Dict, List, Optional

from . import features as F
from .state import AgenticError

RATE = 44100
KINDS = ("audio", "trk", "module")
TAGS = tuple(F.DIMENSIONS)
_AUDIO = (".mp3", ".wav")
_MODULES = (".it", ".xm", ".s3m", ".mod", ".mptm")
#: Seconds of a reference kept (rendered or decoded) unless asked for more.
DEFAULT_SECONDS = 90.0
#: Bars in an automatically chosen region.
REGION_BARS = 8
#: Voices whose claims are checked against their own audio, at most.
CHECKED_VOICES = 3


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def kind_of(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in _AUDIO:
        return "audio"
    if ext == ".trk":
        return "trk"
    if ext in _MODULES:
        return "module"
    raise AgenticError("bad_reference", f"{os.path.basename(path)}: a "
                       f"reference is an MP3 or WAV recording, a .trk score "
                       f"or a module (.it .xm .s3m .mod)", path=path)


# -- audio files --------------------------------------------------------------------
def _write_wav(path: str, stereo, rate: int = RATE):
    """Interleaved stereo floats -> 16-bit WAV."""
    import audio
    if audio.HAVE_NUMPY:
        import numpy as np
        pcm = (np.clip(np.asarray(stereo, dtype=np.float64), -1.0, 1.0)
               * 32767.0).astype("<i2").tobytes()
    else:
        pcm = array.array("h", (max(-32768, min(32767, int(v * 32767.0)))
                                for v in stereo))
        if pcm.itemsize != 2:        # pragma: no cover
            raise AgenticError("pcm", "16-bit samples expected")
        import sys
        if sys.byteorder == "big":   # pragma: no cover
            pcm.byteswap()
        pcm = pcm.tobytes()
    with wave.open(path, "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)


def _frames_to_floats(raw: bytes, channels: int, width: int) -> list:
    """PCM bytes -> interleaved stereo floats (mono doubled)."""
    if width == 2:
        data = array.array("h")
        data.frombytes(raw)
        import sys
        if sys.byteorder == "big":   # pragma: no cover
            data.byteswap()
        scale = 1.0 / 32768.0
        values = [v * scale for v in data]
    elif width == 3:
        values = []
        for i in range(0, len(raw) - 2, 3):
            v = int.from_bytes(raw[i:i + 3], "little", signed=True)
            values.append(v / 8388608.0)
    elif width == 1:
        values = [(b - 128) / 128.0 for b in raw]
    else:
        raise AgenticError("bad_wav", f"{width * 8}-bit WAV is not read; "
                           f"use 8, 16 or 24-bit PCM")
    if channels == 2:
        return values
    if channels == 1:
        out = []
        for v in values:
            out.append(v)
            out.append(v)
        return out
    # more channels: the first two
    out = []
    for i in range(0, len(values) - channels + 1, channels):
        out.append(values[i])
        out.append(values[i + 1])
    return out


def read_wav(path: str, t0: float = 0.0, t1: Optional[float] = None) -> list:
    """Interleaved stereo floats of [t0, t1) seconds of a WAV."""
    with wave.open(path, "rb") as handle:
        rate = handle.getframerate()
        n = handle.getnframes()
        a = max(0, min(n, int(round(t0 * rate))))
        b = n if t1 is None else max(a, min(n, int(round(t1 * rate))))
        handle.setpos(a)
        raw = handle.readframes(b - a)
        return _frames_to_floats(raw, handle.getnchannels(),
                                 handle.getsampwidth())


def _resample(stereo: list, rate_in: int, rate_out: int = RATE) -> list:
    import audio
    left, right = F.split(stereo)
    out = []
    for ch in (left, right):
        buf = audio.from_floats(ch, channels=1)
        res = audio.resample(buf, rate_in, rate_out)
        out.append(res.tolist() if hasattr(res, "tolist") else list(res))
    return [v for pair in zip(*out) for v in pair]


def _decode(src: str, dst: str, seconds: float) -> dict:
    """A recording -> 44.1 kHz stereo WAV at `dst`. -> how it was done."""
    ext = os.path.splitext(src)[1].lower()
    how = {}
    work = src
    if ext == ".mp3":
        tool = shutil.which("lame")
        if tool:
            tmp = dst + ".decoded.wav"
            subprocess.run([tool, "--decode", "--silent", src, tmp],
                           check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=600)
            version = subprocess.run([tool, "--version"], capture_output=True,
                                     text=True).stdout.splitlines()
            how["decoder"] = (version[0].strip() if version else "lame")
        elif shutil.which("ffmpeg"):
            tmp = dst + ".decoded.wav"
            subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-i", src,
                            "-acodec", "pcm_s16le", tmp], check=True,
                           timeout=600)
            how["decoder"] = "ffmpeg"
        else:
            raise AgenticError("no_decoder", "an MP3 reference needs lame "
                               "or ffmpeg on PATH to be decoded; add it as "
                               "a WAV instead")
        work = tmp
    with wave.open(work, "rb") as handle:
        rate = handle.getframerate()
        full = handle.getnframes() / rate
    stereo = read_wav(work, 0.0, seconds)
    if rate != RATE:
        stereo = _resample(stereo, rate, RATE)
        how["resampled"] = f"{rate} -> {RATE} Hz (audio.resample)"
    _write_wav(dst, stereo)
    if work != src:
        os.remove(work)
    how.update(full_seconds=round(full, 3),
               kept_seconds=round(len(stereo) / 2 / RATE, 3))
    return how


# -- sources: a Chipgen score -------------------------------------------------------------
class _BankScope:
    """Install a reference's FM bank for one render and take it out again:
    a reference's instruments must not become the project's."""

    def __init__(self, path: Optional[str]):
        self.path = path

    def __enter__(self):
        import instruments
        self.saved = dict(instruments.BANK)
        if self.path:
            instruments.load_bank(self.path)
        return self

    def __exit__(self, *exc):
        import instruments
        instruments.BANK.clear()
        instruments.BANK.update(self.saved)


def _trk_parse(text: str):
    import events as E
    import levels
    import tracker
    rows: List[dict] = []
    events, meta = tracker.loads(text, rows=rows)
    tps = float(meta.ticks_per_second)
    tick, seconds = 0, 0.0
    at = []
    tick_seconds = {}
    for ev in events:
        at.append(seconds)
        tick_seconds.setdefault(tick, seconds)
        if isinstance(ev, E.Wait):
            tick += ev.ticks
            seconds += ev.ticks / tps
        elif isinstance(ev, E.Tempo):
            tps = max(1.0, float(ev.ticks_per_second))
    end = seconds
    tps0 = float(meta.ticks_per_second)
    row_times = []
    for r in rows:
        t = tick_seconds.get(r["tick"])
        row_times.append(t if t is not None else r["tick"] / tps0)
    ons = (E.FMNoteOn, E.PSGToneOn, E.PSGNoiseOn)
    offs = (E.FMNoteOff, E.PSGToneOff, E.PSGNoiseOff)
    notes, open_ = [], {}
    directives: Dict[str, List[str]] = {}

    def close(column, t):
        n = open_.pop(column, None)
        if n is not None:
            n["end"] = t
            notes.append(n)
    row_of = {}
    for k, r in enumerate(rows):
        row_of.setdefault(r["event"], k)
    current_row = 0
    vibrato: Dict[str, dict] = {}
    for i, ev in enumerate(events):
        current_row = row_of.get(i, current_row)
        column = levels._voice_of(ev)
        if column is None and isinstance(ev, E.Vibrato):
            column = ev.target
        if column is None:
            continue
        name = type(ev).__name__
        if name in ("Vibrato", "Tremolo", "Portamento", "Arpeggio",
                    "FMPan", "VolumeSlide"):
            directives.setdefault(column, []).append(name)
        if isinstance(ev, E.Vibrato):
            if ev.depth_cents > 0 and ev.speed_hz > 0:
                vibrato[column] = {"depth_cents": ev.depth_cents,
                                   "speed_hz": ev.speed_hz,
                                   "delay_s": ev.delay}
                if column in open_:          # switched on under a held note
                    open_[column]["vibrato"] = dict(vibrato[column],
                                                    from_s=round(at[i], 4))
            else:
                vibrato.pop(column, None)
        if isinstance(ev, offs):
            close(column, at[i])
        elif isinstance(ev, ons) or isinstance(ev, E.DACSample):
            close(column, at[i])
            if isinstance(ev, E.DACSample):
                pitch, label, vel = None, ev.name, None
            elif isinstance(ev, E.PSGNoiseOn):
                pitch, label, vel = None, "noise", None
            else:
                pitch = ev.octave * 12 + E.NOTE_NAMES.index(ev.note)
                label = f"{ev.note}{ev.octave}"
                vel = getattr(ev, "velocity", None)
                if vel is None:
                    vel = getattr(ev, "volume", None)
            n = {"voice": column, "channel": column, "start": at[i],
                 "pitch": pitch, "name": label, "velocity": vel,
                 "row_index": current_row}
            if column in vibrato:
                n["vibrato"] = dict(vibrato[column])
            if isinstance(ev, E.DACSample):
                n["end"] = at[i] + 0.1
                notes.append(n)
            else:
                open_[column] = n
    for column in list(open_):
        close(column, end)
    notes.sort(key=lambda n: (n["start"], str(n["channel"])))
    return events, meta, rows, row_times, notes, directives, end


def _cut(events, seconds: float, tps: float):
    """The events up to `seconds` (setup kept), ended there."""
    import events as E
    out, t = [], 0.0
    for ev in events:
        if isinstance(ev, E.Wait):
            if t >= seconds:
                break
            left = seconds - t
            ticks = ev.ticks if ev.ticks / tps <= left else \
                max(1, int(left * tps))
            out.append(E.Wait(ticks=ticks))
            t += ticks / tps
            continue
        if isinstance(ev, E.End):
            break
        out.append(ev)
    out.append(E.End())
    return out


def _render_events(events, tps: float, bank: Optional[str]):
    """Render events with the reference's bank -> interleaved stereo
    array('f') (4 bytes a sample: a minute and a half is 32 MB, not the
    quarter of a gigabyte a list of floats would take)."""
    import chipgen
    from . import render as R
    with _BankScope(bank):
        result = chipgen.compose(events, ticks_per_second=tps)
    return R._flat(result.audio).data


def _trk_source(path: str, bank: Optional[str], seconds: float) -> dict:
    text = open(path, encoding="utf-8").read()
    events, meta, rows, row_times, notes, directives, end = _trk_parse(text)
    lpb = int(meta.lpb)
    per_bar = lpb * 4
    starts = [row_times[k] for k in range(0, len(rows), per_bar)]
    stereo = _render_events(_cut(events, seconds, meta.ticks_per_second),
                            meta.ticks_per_second, bank)
    return {"audio": stereo, "notes": [n for n in notes
                                       if n["start"] < seconds],
            "rows": [{"t": round(t, 5), "row": k}
                     for k, t in enumerate(row_times) if t < seconds],
            "directives": directives, "full_seconds": end,
            "engine": "Chipgen Mega Drive (Nuked-OPN2 + SN76489), with the "
                      "score's FM bank",
            "grid": {"source": "score", "bpm": float(meta.bpm),
                     "rows_per_beat": lpb, "rows_per_bar": per_bar,
                     "beats_per_bar": 4, "beat_known": True,
                     "beat_s": round(60.0 / float(meta.bpm), 5),
                     "bars": [round(t, 4) for t in starts if t < seconds],
                     "how": f"the score's rows: bpm {meta.bpm:g}, lpb "
                            f"{lpb}; 4 beats a bar assumed (a .trk stores "
                            f"no metre)"},
            "title": meta.title or os.path.splitext(
                os.path.basename(path))[0]}


# -- sources: a module ----------------------------------------------------------------------
def _module_pans(data: bytes, fmt: str, channels: int) -> Dict[str, dict]:
    """Where the format puts each channel before any command moves it."""
    out = {}
    if fmt == "it" and len(data) >= 0x80:
        for ch in range(min(64, channels)):
            v = data[0x40 + ch]
            if v >= 128:
                continue                       # disabled
            if v == 100:
                out[f"ch{ch + 1}"] = {"pan": 0.0, "surround": True}
            else:
                out[f"ch{ch + 1}"] = {"pan": round((min(64, v) - 32) / 32.0,
                                                   3)}
    elif fmt == "s3m" and len(data) >= 0x60:
        for ch in range(min(32, channels)):
            v = data[0x40 + ch]
            if v >= 16:
                continue
            out[f"ch{ch + 1}"] = {"pan": -0.5 if v < 8 else 0.5}
    elif fmt == "mod":
        for ch in range(channels):
            out[f"ch{ch + 1}"] = {"pan": -1.0 if ch % 4 in (0, 3) else 1.0,
                                  "how": "Amiga: channels 1 and 4 left, 2 "
                                         "and 3 right"}
    return out


def _module_source(path: str, seconds: float) -> dict:
    from . import openmpt as O
    if not O.available():
        raise AgenticError("no_libopenmpt", "a module reference is played "
                           "by libopenmpt, which is not installed (apt "
                           "install libopenmpt0)")
    data = open(path, "rb").read()
    with O.Module(data) as m:
        stereo, rows = m.render(RATE, seconds)
        dur = len(stereo) / 2 / RATE
        notes = m.notes(rows, dur)
        rpb, rpm, how = m.beat()
        fmt = m.format
        full = m.duration
        channels = m.channels
        title = m.metadata("title")
        author = m.metadata("artist")
        names = {i: m.instrument_name(i) or m.sample_name(i)
                 for i in range(1, 100)}
    starts = []
    for r in rows:
        if r["row"] % rpm == 0:
            starts.append(round(r["t"], 4))
    beats = [r["t"] for r in rows if r["row"] % rpb == 0]
    gaps = sorted(b - a for a, b in zip(beats, beats[1:]) if b > a)
    beat_s = gaps[len(gaps) // 2] if gaps else None
    for n in notes:
        n["instrument_name"] = names.get(n["instrument"], "")
    return {"audio": stereo, "notes": notes, "rows": rows,
            "full_seconds": full, "format": fmt,
            "engine": f"libopenmpt {O.version()} ({fmt.upper()})",
            "pans": _module_pans(data, fmt, channels),
            "grid": {"source": "score", "rows_per_beat": rpb,
                     "rows_per_bar": rpm, "beats_per_bar": max(1, rpm // rpb),
                     "beat_known": "assumed" not in how,
                     "beat_s": round(beat_s, 5) if beat_s else None,
                     "bpm": round(60.0 / beat_s, 2) if beat_s else None,
                     "bars": starts,
                     "how": f"the rows libopenmpt played (its own clock); "
                            f"{rpb} rows a beat, {rpm} a bar: {how}; a "
                            f"pattern's first row starts a bar"},
            "title": title or os.path.splitext(os.path.basename(path))[0],
            "author": author}


# -- what the source says about its voices -------------------------------------------------
_VIBRATO = {"it": ("H", "U"), "s3m": ("H", "U"), "xm": ("4",), "mod": ("4",)}
_ARPEGGIO = {"it": ("J",), "s3m": ("J",), "xm": ("0",), "mod": ("0",)}


def _voices(notes: List[dict], grid: dict, directives=None,
            fmt: str = "") -> Dict[str, dict]:
    beat = grid.get("beat_s") or 0.5
    out: Dict[str, dict] = {}
    by: Dict[str, List[dict]] = {}
    for n in notes:
        by.setdefault(n["voice"], []).append(n)
    for v, ns in by.items():
        pitched = sorted(n["pitch"] for n in ns if n.get("pitch") is not None)
        span = max(1e-6, ns[-1]["start"] - ns[0]["start"])
        entry = {"notes": len(ns),
                 "notes_per_beat": round(len(ns) / max(1.0, span / beat), 2),
                 "median_pitch": pitched[len(pitched) // 2] if pitched
                 else None,
                 "median_length_s": round(sorted(
                     n["end"] - n["start"] for n in ns)[len(ns) // 2], 3)}
        if fmt:
            vib = _VIBRATO.get(fmt, ())
            arp = _ARPEGGIO.get(fmt, ())
            with_vib = [n for n in ns if any(
                e[:1] in vib and len(e) == 3 and e[1:] != "00"
                for e in n.get("effects", []))]
            entry["vibrato_share"] = round(len(with_vib) / len(ns), 3)
            depths = [int(e[2], 16) for n in with_vib
                      for e in n["effects"] if e[:1] in vib and len(e) == 3]
            if depths:
                entry["vibrato_depth_param"] = sorted(depths)[
                    len(depths) // 2]
            entry["arpeggio_share"] = round(len([n for n in ns if any(
                e[:1] in arp and len(e) == 3 and e[1:] != "00"
                for e in n.get("effects", []))]) / len(ns), 3)
        if directives is not None:
            kinds = sorted(set(directives.get(v, [])))
            if kinds:
                entry["directives"] = kinds
            vib = [n["vibrato"] for n in ns if n.get("vibrato")]
            entry["vibrato_share"] = round(len(vib) / len(ns), 3)
            if vib:
                depths = sorted(x["depth_cents"] for x in vib)
                entry["vibrato_depth_cents"] = round(
                    depths[len(depths) // 2], 1)
                entry["vibrato_speed_hz"] = round(sorted(
                    x["speed_hz"] for x in vib)[len(vib) // 2], 2)
        out[v] = entry
    return out


def _echoes(notes: List[dict], rows_of: Dict[int, float]) -> List[dict]:
    """Voice pairs where one repeats the other a few rows later."""
    by: Dict[str, List[dict]] = {}
    for n in notes:
        if n.get("pitch") is not None and n.get("row_index") is not None:
            by.setdefault(n["voice"], []).append(n)
    found = []
    voices = [v for v in by if len(by[v]) >= 8]

    def level(ns):
        known = [n["velocity"] for n in ns if n.get("velocity") is not None]
        return sum(known) / len(known) if known else None
    for a in voices:
        index = {(n["row_index"], n["pitch"] % 12) for n in by[a]}
        for b in voices:
            if a == b:
                continue
            unison = sum(1 for n in by[b] if (n["row_index"], n["pitch"] % 12)
                         in index) / len(by[b])
            if unison >= 0.5:
                continue            # a doubling, not an echo
            la, lb = level(by[a]), level(by[b])
            if la is not None and lb is not None and lb >= la:
                continue            # an echo is quieter than its source
            best = None
            for d in range(1, 9):
                hits = sum(1 for n in by[b]
                           if (n["row_index"] - d, n["pitch"] % 12) in index)
                share = hits / len(by[b])
                if best is None or share > best[1]:
                    best = (d, share)
            if best and best[1] >= 0.6:
                times = [n["start"] for n in by[b]]
                k = len(times) // 2
                delay = None
                for n in by[a]:
                    if n["row_index"] + best[0] == by[b][k]["row_index"]:
                        delay = round(by[b][k]["start"] - n["start"], 4)
                        break
                found.append({"voice": b, "echoes": a, "rows": best[0],
                              "share": round(best[1], 3),
                              "delay_s": delay,
                              "level": [round(la, 1) if la is not None
                                        else None,
                                        round(lb, 1) if lb is not None
                                        else None]})
    found.sort(key=lambda e: -e["share"])
    return found


# -- the record ------------------------------------------------------------------------------
def _folder(project, rid: str, *parts) -> str:
    return project.path("references", rid, *parts)


def all_(project) -> List[dict]:
    return project.state.setdefault("references", [])


def get(project, rid: str) -> dict:
    for r in all_(project):
        if r["id"] == rid:
            return r
    raise AgenticError("no_reference", f"no reference {rid!r}; the project "
                       f"has {[r['id'] for r in all_(project)]}",
                       reference=rid)


def notes(project, rid: str) -> List[dict]:
    path = _folder(project, rid, "notes.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def read(project, rid: str, t0: float = 0.0,
         t1: Optional[float] = None) -> list:
    """The reference's audio over [t0, t1), interleaved stereo floats."""
    ref = get(project, rid)
    return read_wav(project.path(ref["files"]["audio"]), t0, t1)


def bars(project, rid: str) -> List[float]:
    """The second each bar starts on, from the grid the record uses."""
    return list(get(project, rid)["grid"]["bars"])


def bar_range(project, rid: str, first: int, count: int) -> dict:
    """Bars `first`..`first + count - 1` (from 1) -> {t0, t1, bars}."""
    ref = get(project, rid)
    starts = ref["grid"]["bars"]
    if not 1 <= first <= len(starts):
        raise AgenticError("out_of_reference", f"{rid} has {len(starts)} "
                           f"bars in its kept {ref['seconds']:.1f} s; bar "
                           f"{first} is not one", reference=rid)
    last = min(len(starts), first + count - 1)
    t0 = starts[first - 1]
    t1 = starts[last] if last < len(starts) else ref["seconds"]
    return {"t0": t0, "t1": t1, "bars": [first, last],
            "grid": ref["grid"]["source"]}


def _audio_grid(project, ref: dict, t0: float, t1: float) -> dict:
    """The beat grid the audio itself suggests over [t0, t1)."""
    seg = read_wav(project.path(ref["files"]["audio"]), t0, t1)
    g = F.beat_grid(F.mono_of(seg), RATE,
                    beats_per_bar=ref["grid"].get("beats_per_bar", 4))
    if "bpm" in g:
        g["first_downbeat_s"] = round(t0 + g["first_downbeat_s"], 3)
        g["window"] = [round(t0, 3), round(t1, 3)]
    return g


def _tempo_claim(project, ref: dict, region: dict) -> dict:
    g = _audio_grid(project, ref, max(0.0, region["t0"] - 1.0),
                    min(ref["seconds"], region["t1"] + 1.0))
    source = ref.get("source_grid") or ref["grid"]
    score = source.get("beat_s")
    claim = {"kind": "tempo", "voices": [],
             "claim": (f"a beat of {score:.3f} s ({60.0 / score:.1f} BPM) "
                       f"by the source" + ("" if source.get("beat_known")
                                           else " (its rows per beat "
                                                "assumed)"))
             if score else "the source's beat",
             "source_evidence": {"beat_s": score, "how": source.get("how")},
             "grid_used": {k: ref["grid"].get(k) for k in
                           ("source", "bpm", "beat_s", "how")}}
    if "bpm" not in g or not score:
        claim["audio_check"] = {"verdict": "not measurable",
                                "reading": g.get("why"),
                                "method": "features.beat_grid"}
        return claim
    ratio = g["beat_s"] / score
    verdict, why = "not confirmed", ("the audio's beat and the score's "
                                     "are not in a simple ratio")
    for r, word, because in (
            (1.0, "confirmed", "the same beat"),
            (2.0, "confirmed at half time", "the audio's strongest period "
                                            "is two of the score's beats"),
            (0.5, "confirmed at double time", "the audio's strongest "
                                              "period is half a beat"),
            (1.5, "related, not confirmed",
             "the audio's strongest period is 3/2 of the score's beat: a "
             "dotted figure (3+3+2) does this; the score's grid is kept"),
            (2.0 / 3.0, "related, not confirmed",
             "the audio's strongest period is 2/3 of the score's beat: "
             "triplets do this; the score's grid is kept")):
        if abs(ratio / r - 1.0) <= 0.03:
            verdict, why = word, because
            break
    claim["audio_check"] = {"verdict": verdict, "why": why,
                            "reading": {"beat_s": g["beat_s"],
                                        "bpm": g["bpm"],
                                        "confidence": g["confidence"],
                                        "ratio": round(ratio, 3)},
                            "method": "onset-envelope autocorrelation on "
                                      "the rendered audio of the region"}
    return claim


#: Rows per beat a module's grid may take when its format stores none.
_ROWS_PER_BEAT = (2, 3, 4, 6, 8, 12, 16)


def _bars_from_rows(rows: List[dict], rows_per_bar: int,
                    seconds: float) -> List[float]:
    return [round(r["t"], 4) for r in rows
            if r["row"] % rows_per_bar == 0 and r["t"] < seconds]


def _settle_grid(project, record: dict, rows: List[dict]):
    """Check the source's grid against the beat the audio suggests and
    settle the one fragments are cut on (the source's claims go on to be
    checked one by one; this one decides where the bars are):

      the source's beat agrees (the same, double or half)
            -> the source's rows; for a score, the bars are moved by whole
               beats to the audio's downbeat when it is confident
      the format stores no beat (XM, S3M, MOD: 4 rows assumed)
            -> the rows per beat whose beat matches the audio's (or, if
               none does, the one nearest 120 BPM); bars from the
               patterns' first rows, as before
      the source gives a beat the audio does not have (a transcription
      whose grid does not fit the music)
            -> the audio's grid (constant tempo from its first downbeat),
               the source's kept for the record."""
    score = dict(record["grid"])
    window = min(record["seconds"], 40.0)
    g = _audio_grid(project, record, 0.0, window)
    record["grid_check"] = {"audio": {k: g.get(k) for k in
                                      ("bpm", "beat_s", "confidence",
                                       "first_downbeat_s", "why")}}
    if "bpm" not in g or not score.get("beat_s") or not rows:
        record["grid_check"]["verdict"] = "not measurable"
        return
    beat = score["beat_s"]
    ratio = g["beat_s"] / beat
    agrees = next((r for r in (1.0, 2.0, 0.5)
                   if abs(ratio / r - 1.0) <= 0.03), None)
    gaps = sorted(b["t"] - a["t"] for a, b in zip(rows, rows[1:])
                  if b["t"] > a["t"])
    row_s = gaps[len(gaps) // 2] if gaps else None
    record["source_grid"] = {k: score.get(k) for k in
                             ("bpm", "beat_s", "rows_per_beat",
                              "rows_per_bar", "beat_known", "how")}
    if not score.get("beat_known") and row_s:
        best = None
        for rpb in _ROWS_PER_BEAT:
            for r in (1.0, 2.0, 0.5):
                if abs(g["beat_s"] / (rpb * row_s) / r - 1.0) <= 0.03:
                    if best is None or abs(r - 1.0) < best[1]:
                        best = (rpb, abs(r - 1.0))
        if best:
            rpb, how = best[0], (f"rows per beat chosen to match the "
                                 f"audio's beat ({g['bpm']:.1f} BPM)")
        else:
            import math
            rpb = min(_ROWS_PER_BEAT, key=lambda k: abs(math.log2(
                60.0 / (k * row_s) / 120.0)))
            how = (f"the audio's beat ({g['bpm']:.1f} BPM) fits no whole "
                   f"number of rows; the rows per beat nearest 120 BPM")
        rpm = rpb * 4
        record["grid"] = dict(score, rows_per_beat=rpb, rows_per_bar=rpm,
                              beat_s=round(rpb * row_s, 5),
                              bpm=round(60.0 / (rpb * row_s), 2),
                              bars=_bars_from_rows(rows, rpm,
                                                   record["seconds"]),
                              how=score["how"].split(":")[0] + f": {how}; "
                              f"4 beats a bar assumed")
        record["grid_check"]["verdict"] = "settled from the audio"
        return
    if agrees is not None:
        record["grid_check"]["verdict"] = (
            "confirmed" if agrees == 1.0 else
            f"confirmed at {'half' if agrees == 2.0 else 'double'} time")
        if record["kind"] == "trk" and g.get("confidence", 0) >= 0.5 and \
                score["bars"]:
            # move the bars by whole beats onto the audio's downbeat
            bar = beat * score["beats_per_bar"]
            off = ((g["first_downbeat_s"] - score["bars"][0]) % bar) / beat
            k = int(round(off)) % score["beats_per_bar"]
            if k:
                per = score["rows_per_beat"]
                shifted = [r for r in rows
                           if (r["row"] - k * per) % score["rows_per_bar"]
                           == 0 and r["row"] >= k * per]
                record["grid"] = dict(score, bars=[
                    round(r["t"], 4) for r in shifted
                    if r["t"] < record["seconds"]],
                    how=score["how"] + f"; bars moved {k} beat(s) to the "
                                       f"audio's downbeat")
        return
    bar_s = g["beat_s"] * score.get("beats_per_bar", 4)
    starts = []
    t = g["first_downbeat_s"] % bar_s
    while t < record["seconds"] - 0.5 * bar_s:
        starts.append(round(t, 4))
        t += bar_s
    record["grid"] = {"source": "audio", "bpm": g["bpm"],
                      "beat_s": g["beat_s"],
                      "beats_per_bar": score.get("beats_per_bar", 4),
                      "confidence": g["confidence"], "bars": starts,
                      "how": f"the audio's own beat ({g['bpm']:.1f} BPM, "
                             f"confidence {g['confidence']:.2f}, constant "
                             f"tempo from its first downbeat): the "
                             f"source's grid ({60.0 / beat:.1f} BPM) does "
                             f"not fit it"}
    record["grid_check"]["verdict"] = "not confirmed: the audio's grid is used"


def snap(project, rid: str, t: float, window: float = 4.0) -> dict:
    """A bar start from the grid, moved to the nearest beat the audio
    has around it (within a quarter beat) — for a grid estimated from
    the audio, where a constant tempo drifts. A source grid is exact
    and is returned as it is."""
    ref = get(project, rid)
    if ref["grid"]["source"] != "audio":
        return {"t": t, "moved_ms": 0.0}
    beat = ref["grid"]["beat_s"]
    a = max(0.0, t - 0.5 * window)
    seg = read_wav(project.path(ref["files"]["audio"]), a, a + window)
    env, hop, _low = F.onset_envelope(F.mono_of(seg), RATE)
    best, best_shift = None, 0.0
    steps = int(0.25 * beat / hop)
    for k in range(-steps, steps + 1):
        total = 0.0
        x = (t - a) + k * hop
        while x < len(env) * hop:
            if x >= 0:
                total += env[int(x / hop)]
            x += beat
        x = (t - a) + k * hop - beat
        while x >= 0:
            total += env[int(x / hop)]
            x -= beat
        if best is None or total > best:
            best, best_shift = total, k * hop
    return {"t": round(t + best_shift, 4),
            "moved_ms": round(1000.0 * best_shift, 1)}


# -- stems ------------------------------------------------------------------------------------
def stem(project, rid: str, voice: str, t1: float) -> list:
    """The reference with only `voice` heard, from 0 to `t1` seconds,
    interleaved stereo (a module's channel through libopenmpt's muting; a
    score's column rendered alone, its setup kept). Cached in stems/."""
    ref = get(project, rid)
    if ref["kind"] == "audio":
        raise AgenticError("no_stems", f"{rid} is a recording: its voices "
                           f"cannot be heard apart", reference=rid)
    folder = _folder(project, rid, "stems")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{voice}_{t1:.2f}.wav")
    if os.path.exists(path):
        return read_wav(path)
    source = project.path(ref["files"]["source"])
    if ref["kind"] == "module":
        from . import openmpt as O
        ch = int(voice[2:]) - 1
        with O.Module(open(source, "rb").read(), keep=[ch]) as m:
            stereo, _rows = m.render(RATE, t1, clock=False)
    else:
        import levels
        text = open(source, encoding="utf-8").read()
        events, meta, *_rest = _trk_parse(text)
        alone = levels.isolate(_cut(events, t1, meta.ticks_per_second),
                               voice)
        bank = project.path(ref["files"]["bank"]) if ref["files"].get(
            "bank") else None
        stereo = _render_events(alone, meta.ticks_per_second, bank)
    _write_wav(path, stereo)
    return read_wav(path)


# -- claims checked against the audio -----------------------------------------------------------
def _in(ns, t0, t1):
    return [n for n in ns if t0 <= n["start"] < t1]


def _vibrato_check(project, rid, ref, voice, ns, region) -> dict:
    import analysis
    long_notes = [n for n in _in(ns, region["t0"], region["t1"])
                  if n.get("pitch") is not None
                  and n["end"] - n["start"] >= 0.25][:6]
    if not long_notes:
        return {"verdict": "not measurable",
                "reading": "no note of 250 ms or longer in the region",
                "method": "pitch track on the voice alone"}
    t1 = max(n["end"] for n in long_notes) + 0.3
    mono = F.mono_of(stem(project, rid, voice, t1))
    measured = []
    for n in long_notes:
        a, b = int(n["start"] * RATE), int(n["end"] * RATE)
        seg = mono[a + int(0.05 * RATE):b]
        if len(seg) < 2048:
            continue
        f0 = analysis.fundamental(seg, RATE)
        if not f0:
            continue
        track = F.pitch_track(mono, RATE, f0, n["start"] + 0.03,
                              n["end"] - 0.02, origin=n["start"])
        v = F.vibrato(track)
        if v.get("depth_cents") is not None:
            measured.append(v)
    if not measured:
        return {"verdict": "not measurable", "reading": "no steady pitch "
                "to track on the voice alone", "method": "pitch track"}
    depths = sorted(v["depth_cents"] for v in measured)
    rates = sorted(v["rate_hz"] for v in measured if v.get("rate_hz"))
    depth = depths[len(depths) // 2]
    rate = rates[len(rates) // 2] if rates else None
    reading = {"depth_cents": depth, "notes": len(measured),
               "rate_hz": rate}
    if depth > 150.0:
        verdict, why = "not confirmed", (
            f"the pitch moves {depth:.0f} cents: a slide, an arpeggio or "
            f"the sample's own pitch change, not a vibrato")
    elif depth >= 8.0 and rate is not None and 2.0 <= rate <= 12.0:
        verdict, why = "confirmed", (f"a {depth:.0f}-cent swing at "
                                     f"{rate:.1f} Hz")
    elif depth >= 8.0:
        verdict, why = "not confirmed", "the pitch moves, but not " \
                                        "periodically at 2-12 Hz"
    else:
        verdict, why = "not confirmed", (f"the pitch stays within "
                                         f"{depth:.0f} cents")
    return {"verdict": verdict, "why": why, "reading": reading,
            "method": "the voice rendered alone; a 10 ms pitch track per "
                      "long note (its fundamental found first: a module's "
                      "samples set its true pitch); depth = 90th percentile "
                      "of the swing, 8 cents counted as audible vibrato"}


def _density_check(project, rid, ref, voice, ns, region) -> dict:
    src = [n["start"] for n in _in(ns, region["t0"], region["t1"])]
    if len(src) < 3:
        return {"verdict": "not measurable", "reading": "under 3 notes in "
                "the region", "method": "onsets on the voice alone"}
    seg = stem(project, rid, voice, region["t1"])
    mono = F.mono_of(seg)[int(region["t0"] * RATE):]
    env, hop, _low = F.onset_envelope(mono, RATE)
    found = [region["t0"] + t for t in F.peaks(env, hop)]
    hits = sum(1 for t in src if any(abs(t - u) <= 0.04 for u in found))
    recall = hits / len(src)
    precision = (sum(1 for u in found if any(abs(t - u) <= 0.04
                                              for t in src)) / len(found)
                 if found else 0.0)
    out = {"verdict": "confirmed" if recall >= 0.7 and precision >= 0.6
           else "not confirmed",
           "reading": {"source_notes": len(src), "audio_onsets": len(found),
                       "recall": round(recall, 3),
                       "precision": round(precision, 3)},
           "method": "spectral-flux onsets on the voice alone against the "
                     "source's note starts, within 40 ms"}
    info = ref.get("voices", {}).get(voice, {})
    if len(found) > 1.3 * len(src) and info.get("arpeggio_share", 0) >= 0.2:
        out["why"] = (f"arpeggio commands on {info['arpeggio_share']:.0%} of "
                      f"its notes: their pitch steps are heard as onsets, "
                      f"so the voice sounds denser than its notes")
    elif len(found) > 1.3 * len(src):
        out["why"] = ("more onsets than notes: the instrument's own "
                      "transients (a drum sample's hits, a retrigger) read "
                      "as onsets")
    elif recall < 0.7:
        out["why"] = "notes the score starts are not heard as onsets " \
                     "(soft attacks, or ties into held notes)"
    return out


def _envelope(mono, hop: int = 441):
    return [sum(abs(v) for v in mono[i:i + hop]) / hop
            for i in range(0, len(mono) - hop, hop)]


def _echo_check(project, rid, ref, e, region) -> dict:
    if not e.get("delay_s"):
        return {"verdict": "not measurable", "reading": "no delay read",
                "method": "envelope correlation"}
    t1 = region["t1"]
    a = _envelope(F.mono_of(stem(project, rid, e["echoes"], t1))[
        int(region["t0"] * RATE):])
    b = _envelope(F.mono_of(stem(project, rid, e["voice"], t1))[
        int(region["t0"] * RATE):])
    n = min(len(a), len(b))
    if n < 100:
        return {"verdict": "not measurable", "reading": "region too short",
                "method": "envelope correlation"}
    a, b = a[:n], b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    a = [x - ma for x in a]
    b = [x - mb for x in b]
    best, best_lag = None, 0
    for lag in range(0, min(100, n // 2)):        # 0..1 s in 10 ms steps
        c = sum(a[i] * b[i + lag] for i in range(n - lag)) / (n - lag)
        if best is None or c > best:
            best, best_lag = c, lag
    lag_s = best_lag * 0.01
    return {"verdict": "confirmed" if abs(lag_s - e["delay_s"]) <= 0.03
            else "not confirmed",
            "reading": {"envelope_lag_s": round(lag_s, 3),
                        "source_delay_s": e["delay_s"]},
            "method": "the two voices rendered alone; the lag that best "
                      "lines up their 10 ms level envelopes"}


def _pan_check(project, rid, ref, voice, pan, region) -> dict:
    seg = stem(project, rid, voice, region["t1"])
    left, right = F.split(seg[int(region["t0"] * RATE) * 2:])
    el = sum(v * v for v in left)
    er = sum(v * v for v in right)
    if el + er <= 1e-12:
        return {"verdict": "not measurable", "reading": "silent here",
                "method": "left/right energy of the voice alone"}
    import math
    balance = 10 * math.log10((er + 1e-12) / (el + 1e-12))
    side = "right" if balance > 1.5 else "left" if balance < -1.5 \
        else "centre"
    said = "right" if pan > 0.15 else "left" if pan < -0.15 else "centre"
    return {"verdict": "confirmed" if side == said else "not confirmed",
            "reading": {"right_over_left_db": round(balance, 2),
                        "heard_side": side},
            "method": "the voice rendered alone: right over left energy"}


def _claims(project, rid: str, ref: dict, ns: List[dict], region: dict,
            pans: Dict[str, dict], check: bool) -> List[dict]:
    """What the source suggests, each claim checked against the audio
    when `check`: the tempo; vibrato on the voices that carry it (longest
    notes first: a vibrato needs time to be heard); the density of the
    busiest voices; their pan where the format sets one; echoes."""
    out = [_tempo_claim(project, ref, region)] if check else []
    voices = ref.get("voices", {})

    def mine(v):
        return [n for n in ns if n["voice"] == v]
    vib = sorted((v for v, i in voices.items()
                  if i.get("vibrato_share", 0) >= 0.2
                  and i.get("median_pitch") is not None),
                 key=lambda v: -voices[v]["median_length_s"])
    for v in vib[:2]:
        info = voices[v]
        c = {"kind": "vibrato", "voices": [v],
             "claim": f"{v} plays vibrato on {info['vibrato_share']:.0%} "
                      f"of its notes",
             "source_evidence": {k: info.get(k) for k in
                                 ("vibrato_share", "vibrato_depth_param",
                                  "vibrato_depth_cents", "vibrato_speed_hz",
                                  "directives") if info.get(k) is not None}}
        if check:
            c["audio_check"] = _vibrato_check(project, rid, ref, v, mine(v),
                                              region)
        out.append(c)
    busy = sorted((v for v in voices if voices[v]["notes"] >= 8),
                  key=lambda v: -voices[v]["notes"])[:CHECKED_VOICES]
    for v in busy:
        info = voices[v]
        c = {"kind": "density", "voices": [v],
             "claim": f"{v} plays {info['notes_per_beat']:.2f} notes a beat",
             "source_evidence": {"notes_per_beat": info["notes_per_beat"]}}
        if check:
            c["audio_check"] = _density_check(project, rid, ref, v, mine(v),
                                              region)
        out.append(c)
        if v in pans and abs(pans[v].get("pan", 0.0)) >= 0.3 and check:
            side = "right" if pans[v]["pan"] > 0 else "left"
            c = {"kind": "pan", "voices": [v],
                 "claim": f"{v} sits {side} by the format's channel pan",
                 "source_evidence": pans[v]}
            c["audio_check"] = _pan_check(project, rid, ref, v,
                                          pans[v]["pan"], region)
            out.append(c)
    for e in ref.get("echoes", [])[:2]:
        c = {"kind": "echo", "voices": [e["voice"], e["echoes"]],
             "claim": f"{e['voice']} repeats {e['echoes']} {e['rows']} "
                      f"row(s) later ({e['share']:.0%} of its notes)",
             "source_evidence": e}
        if check:
            c["audio_check"] = _echo_check(project, rid, ref, e, region)
        out.append(c)
    return out


# -- regions -------------------------------------------------------------------------------------
def _auto_region(ref: dict, ns: List[dict], audio_path: str) -> dict:
    starts = ref["grid"]["bars"]
    if len(starts) < 2:
        return {"name": "main", "bars": [1, 1], "t0": 0.0,
                "t1": ref["seconds"], "chosen_by": "the whole kept audio"}
    if ns:
        per_bar = [len(_in(ns, a, b)) for a, b in zip(starts, starts[1:])]
        how = "the source's notes per bar"
    else:
        mono = F.mono_of(read_wav(audio_path, 0.0, min(starts[-1], 60.0)))
        env, hop, _ = F.onset_envelope(mono, RATE)
        times = F.peaks(env, hop)
        per_bar = [len([t for t in times if a <= t < b])
                   for a, b in zip(starts, starts[1:])]
        how = "onsets per bar in the audio"
    usual = sorted(per_bar)[len(per_bar) // 2]
    first = next((i for i, c in enumerate(per_bar) if c >= 0.75 * usual), 0)
    last = min(len(starts) - 1, first + REGION_BARS)
    return {"name": "main", "bars": [first + 1, last],
            "t0": starts[first], "t1": starts[last] if last < len(starts)
            else ref["seconds"],
            "chosen_by": f"the first {last - first} bars as busy as the "
                         f"piece usually is ({how}; an intro is skipped)"}


def _region_of(ref: dict, spec: dict) -> dict:
    starts = ref["grid"]["bars"]
    if "bars" in spec:
        b0, b1 = spec["bars"]
        if not (1 <= b0 <= b1 <= len(starts)):
            raise AgenticError("bad_region", f"bars {b0}-{b1}: the kept "
                               f"audio has {len(starts)} bars")
        t0 = starts[b0 - 1]
        t1 = starts[b1] if b1 < len(starts) else ref["seconds"]
    else:
        t0, t1 = float(spec["t0"]), float(spec["t1"])
        if not 0 <= t0 < t1 <= ref["seconds"] + 1e-6:
            raise AgenticError("bad_region", f"{t0}-{t1} s is outside the "
                               f"kept {ref['seconds']:.1f} s")
        b0 = max(1, sum(1 for s in starts if s <= t0 + 1e-6))
        b1 = max(b0, sum(1 for s in starts if s < t1 - 1e-6))
    return {"name": spec.get("name", f"bars {b0}-{b1}"), "bars": [b0, b1],
            "t0": round(t0, 4), "t1": round(t1, 4),
            "tags": [t for t in spec.get("tags", []) if t in TAGS],
            "note": spec.get("note", ""), "chosen_by": "the user"}


# -- adding one ------------------------------------------------------------------------------------
def add(project, path: str, kind: Optional[str] = None, tags=(),
        note: str = "", title: str = "", author: str = "",
        licence: str = "", url: str = "", regions=None,
        bank: Optional[str] = None, seconds: float = DEFAULT_SECONDS,
        check: bool = True) -> dict:
    """Keep a reference in the project: copy it, decode or render it,
    read its grid and voices from the source when there is one, choose or
    take its regions, write the source's claims and (with `check`) check
    them against the audio. -> the record."""
    if not os.path.isfile(path):
        raise AgenticError("no_file", f"no file at {path}", path=path)
    kind = kind or kind_of(path)
    if kind not in KINDS:
        raise AgenticError("bad_reference", f"kind is one of "
                           f"{', '.join(KINDS)}, not {kind!r}")
    bad = [t for t in tags if t not in TAGS]
    if bad:
        raise AgenticError("bad_tag", f"{bad}: a reference is for "
                           f"{', '.join(TAGS)}", tags=bad)
    refs = all_(project)
    rid = f"ref{len(refs) + 1}"
    folder = _folder(project, rid)
    os.makedirs(folder, exist_ok=True)
    base = os.path.basename(path)
    record = {"id": rid, "kind": kind, "tags": list(tags), "note": note,
              "added": _now(),
              "origin": {"file": base, "sha256": _sha(path),
                         "bytes": os.path.getsize(path), "url": url,
                         "licence": licence},
              "files": {}}
    audio_path = os.path.join(folder, "audio.wav")
    pans: Dict[str, dict] = {}
    ns: List[dict] = []
    if kind == "audio":
        how = _decode(path, audio_path, seconds)
        record.update(engine="a recording, decoded" +
                      (f" by {how['decoder']}" if how.get("decoder") else
                       " (WAV read as it is)"),
                      decoding=how, title=title or os.path.splitext(base)[0],
                      author=author)
        record["seconds"] = how["kept_seconds"]
        record["full_seconds"] = how["full_seconds"]
        record["files"]["audio"] = os.path.relpath(audio_path, project.root)
        g = F.beat_grid(F.mono_of(read_wav(audio_path, 0.0,
                                           min(record["seconds"], 40.0))))
        if "bpm" not in g:
            raise AgenticError("no_grid", f"{base}: {g.get('why')}")
        bar_s = g["beat_s"] * g["beats_per_bar"]
        first = g["first_downbeat_s"]
        starts = []
        t = first
        while t < record["seconds"] - 0.5 * bar_s:
            starts.append(round(t, 4))
            t += bar_s
        record["grid"] = {"source": "audio", "bpm": g["bpm"],
                          "beat_s": g["beat_s"],
                          "beats_per_bar": g["beats_per_bar"],
                          "confidence": g["confidence"],
                          "bars": starts,
                          "how": "estimated from the first 40 s of the "
                                 "audio (onset autocorrelation, comb "
                                 "phase, low-band accents); constant tempo "
                                 "assumed after that; 4 beats a bar "
                                 "assumed"}
    else:
        src = os.path.join(folder, "source" + os.path.splitext(base)[1])
        shutil.copy(path, src)
        record["files"]["source"] = os.path.relpath(src, project.root)
        if kind == "trk":
            bank_rel = None
            if bank:
                if not os.path.isfile(bank):
                    raise AgenticError("no_file", f"no bank at {bank}")
                bdst = os.path.join(folder, "bank.json")
                shutil.copy(bank, bdst)
                bank_rel = os.path.relpath(bdst, project.root)
                record["files"]["bank"] = bank_rel
                record["origin"]["bank"] = os.path.basename(bank)
            got = _trk_source(src, bdst if bank else None, seconds)
            record["directives"] = got["directives"]
        else:
            got = _module_source(src, seconds)
            record["format"] = got["format"]
            pans = got["pans"]
        _write_wav(audio_path, got["audio"])
        record["files"]["audio"] = os.path.relpath(audio_path, project.root)
        record.update(engine=got["engine"], title=title or got["title"],
                      author=author or got.get("author", ""),
                      seconds=round(len(got["audio"]) / 2 / RATE, 3),
                      full_seconds=round(got["full_seconds"], 3),
                      grid=got["grid"])
        ns = got["notes"]
        with open(os.path.join(folder, "notes.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(ns, handle)
        record["voices"] = _voices(ns, record["grid"],
                                   got.get("directives"),
                                   record.get("format", ""))
        record["echoes"] = _echoes(ns, {})
        record["source_notes"] = len(ns)
    record["cut"] = record["full_seconds"] > record["seconds"] + 0.05
    if kind != "audio":
        _settle_grid(project, record, got["rows"])
    record["regions"] = [_region_of(record, r) for r in (regions or [])] \
        or [_auto_region(record, ns, audio_path)]
    refs.append(record)
    if kind != "audio":
        record["claims"] = _claims(project, rid, record, ns,
                                   record["regions"][0], pans, check)
    project.save()
    return record


def card(ref: dict) -> dict:
    """A short view of a record for a reply."""
    claims = [{"kind": c["kind"], "claim": c["claim"],
               "verdict": (c.get("audio_check") or {}).get("verdict",
                                                           "not checked")}
              for c in ref.get("claims", [])]
    return {"id": ref["id"], "title": ref["title"], "kind": ref["kind"],
            "engine": ref["engine"], "tags": ref["tags"],
            "note": ref["note"], "seconds": ref["seconds"],
            "cut": ref.get("cut"), "origin": {k: ref["origin"].get(k) for k
                                              in ("file", "licence", "url")},
            "grid": {k: ref["grid"].get(k) for k in
                     ("source", "bpm", "beat_s", "beats_per_bar",
                      "confidence", "how")},
            "bars": len(ref["grid"]["bars"]),
            "regions": [{k: r.get(k) for k in ("name", "bars", "t0", "t1",
                                               "chosen_by")}
                        for r in ref["regions"]],
            "voices": len(ref.get("voices", {})), "claims": claims}
