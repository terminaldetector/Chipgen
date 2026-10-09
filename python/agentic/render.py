"""render.py — a musical range rendered on the real chips, with the state
the song is in when the range starts.

Nuked-OPN2 is cycle-accurate and pays for it: measured here, 50 s of music
took 20 s to render, and a voice rendered alone costs as much as the whole
mix (the chip clocks all six channels either way). An A/B that re-rendered
the song from the top for every candidate would spend minutes per
decision. So a range is rendered the way a DAW chases MIDI:

    1. every state event before the window (instrument selects, operator
       writes, pan, volume, LFO, effect settings, tempo) is replayed at
       the window's start, in order and instantly;
    2. every note still held at the window's start is keyed again there;
    3. the window — a pre-roll, the range, a short tail — plays as
       written; then every voice is keyed off.

The pre-roll is there so that what was approximated at the window's start
(a held note's envelope restarting, a slide restarting, a drum hit that
started earlier and is not replayed) has settled before the range itself.
How close that is to the song played from the top is measured, not
assumed: `chase_error` renders both and reports the difference inside the
range, and the tests hold it.

A render is cached by what was played (the exact event list and the
sequencer settings), so a baseline is rendered once however many
candidates are compared against it, and an unchanged stem is never
rendered twice.
"""

import array
import hashlib
import json
import math
import os
import time
from typing import Iterable, Optional

import events as E
import levels
from sequencer import Sequencer

from .state import AgenticError

RATE = 44100
DEFAULT_PREROLL_S = 2.0
DEFAULT_TAIL_S = 0.5

#: Note events: re-keyed at the window if held, never replayed otherwise.
_ONS = (E.FMNoteOn, E.PSGToneOn, E.PSGNoiseOn)
_OFFS = (E.FMNoteOff, E.PSGToneOff, E.PSGNoiseOff)
#: Never replayed from before the window: timing, markers, one-shot PCM.
_SKIP = (E.Wait, E.Marker, E.LoopPoint, E.End, E.DACSample, E.Tempo)


class Render:
    """Audio of a window and where it sits in the piece."""

    def __init__(self, buf, start_s: float, rng: dict, voices, method: str,
                 info: dict, key: str, seconds: float, cached: bool):
        self.buf = buf
        self.rate = RATE
        self.start_s = start_s
        self.range = rng
        self.voices = voices
        self.method = method
        self.info = info
        self.key = key
        self.seconds = seconds
        self.cached = cached

    def segment(self, t0: float, t1: float, mono: bool = True):
        """Samples between two times of the piece (absolute seconds)."""
        a = max(0, int(round((t0 - self.start_s) * RATE)))
        b = max(a, min(len(self.buf), int(round((t1 - self.start_s) * RATE))))
        data = self.buf.data
        if not mono:
            return data[a * 2:b * 2]
        out = array.array("f", bytes(4 * (b - a)))
        for i in range(b - a):
            out[i] = 0.5 * (data[2 * (a + i)] + data[2 * (a + i) + 1])
        return out

    def in_range(self, mono: bool = True):
        return self.segment(self.range["t0"], self.range["t1"], mono)

    def describe(self) -> dict:
        return {"method": self.method, "voices": self.voices,
                "window_s": [round(self.start_s, 3),
                             round(self.start_s + len(self.buf) / RATE, 3)],
                "range_s": [self.range["t0"], self.range["t1"]],
                "cached": self.cached,
                "render_seconds": round(self.seconds, 3), **self.info}


def _keep(event, keep: Optional[set], drop: Optional[set]) -> bool:
    owner = levels._voice_of(event)
    if owner is None:
        return True
    if keep is not None:
        return owner in keep
    if drop is not None:
        return owner not in drop
    return True


def _columns(timeline, voices: Optional[Iterable[str]]):
    """Voice names (or columns) -> the columns levels.py isolates by."""
    if voices is None:
        return None
    by_voice = {v: ins["channel"]
                for v, ins in timeline.content["instruments"].items()}
    out = set()
    for v in voices:
        out.add(by_voice.get(v, v))
    return out


def chased_events(timeline, row0: int, row1: int, keep=None, drop=None):
    """The event list that plays rows [row0, row1) with the song's state
    chased in at row0. -> (events, info)."""
    events = timeline.events
    rows = timeline.rows
    start = rows[row0]["event"]
    stop = rows[row1]["event"] if row1 < len(rows) else len(events)
    out = []
    tps = float(timeline.meta.ticks_per_second)
    held = {}
    replayed = 0
    skipped_dac = 0
    for ev in events[:start]:
        if isinstance(ev, E.Tempo):
            tps = float(ev.ticks_per_second)
            continue
        if isinstance(ev, E.DACSample):
            if _keep(ev, keep, drop):
                skipped_dac += 1
            continue
        if isinstance(ev, _SKIP):
            continue
        column = levels._voice_of(ev)
        if isinstance(ev, _OFFS):
            held.pop(column, None)
            continue
        if isinstance(ev, _ONS):
            held[column] = ev
            continue
        if _keep(ev, keep, drop):
            out.append(ev)
            replayed += 1
    head = [E.Tempo(ticks_per_second=tps)] + out
    rekeyed = []
    for column, ev in sorted(held.items()):
        if _keep(ev, keep, drop):
            head.append(ev)
            rekeyed.append(column)
    window = [ev for ev in events[start:stop]
              if not isinstance(ev, E.End) and _keep(ev, keep, drop)]
    tail = []
    for ch in range(6):
        tail.append(E.FMNoteOff(channel=ch))
    for ch in range(3):
        tail.append(E.PSGToneOff(channel=ch))
    tail.append(E.PSGNoiseOff())
    tail.append(E.Wait(ticks=max(1, int(round(0.3 * tps)))))
    tail.append(E.End())
    tail = [ev for ev in tail if _keep(ev, keep, drop)
            or isinstance(ev, (E.Wait, E.End))]
    return head + window + tail, {"replayed": replayed,
                                  "rekeyed": rekeyed,
                                  "dac_hits_before_window_not_replayed":
                                  skipped_dac}


def _flat(buf):
    """The render as an interleaved stereo audio.Buffer whichever backend
    made it (numpy's (N, 2) array or the pure-Python Buffer), so the
    segment arithmetic and the cache files are the same either way."""
    import audio
    if audio.is_fallback(buf):
        return buf
    data = array.array("f")
    data.frombytes(buf.astype("float32").reshape(-1).tobytes())
    return audio.Buffer(data, 2)


def _key(event_list, settings: dict) -> str:
    h = hashlib.sha256()
    h.update(json.dumps(settings, sort_keys=True).encode())
    for ev in event_list:
        h.update(json.dumps(ev.to_dict(), sort_keys=True).encode())
    return h.hexdigest()[:20]


class Cache:
    """Renders on disk (a project's renders/) or in memory."""

    def __init__(self, folder: Optional[str] = None):
        self.folder = folder
        self.memory = {}
        self.hits = 0
        self.misses = 0
        self.render_seconds = 0.0
        if folder:
            os.makedirs(folder, exist_ok=True)

    def get(self, key: str):
        if key in self.memory:
            return self.memory[key]
        if self.folder:
            path = os.path.join(self.folder, key + ".f32")
            if os.path.exists(path):
                data = array.array("f")
                with open(path, "rb") as handle:
                    data.frombytes(handle.read())
                import audio
                buf = audio.Buffer(data, 2)
                self.memory[key] = buf
                return buf
        return None

    def put(self, key: str, buf):
        self.memory[key] = buf
        if self.folder:
            tmp = os.path.join(self.folder, key + ".f32.tmp")
            with open(tmp, "wb") as handle:
                handle.write(buf.data.tobytes())
            os.replace(tmp, os.path.join(self.folder, key + ".f32"))


_DEFAULT_CACHE = Cache()


def render_range(timeline, rng: dict, voices=None, exclude=None,
                 method: str = "chase", preroll_s: float = DEFAULT_PREROLL_S,
                 tail_s: float = DEFAULT_TAIL_S,
                 cache: Optional[Cache] = None) -> Render:
    """Render `rng` (timeline.parse_range) of the piece. `voices` keeps only
    those voices (a stem); `exclude` drops them (a background); neither is
    the mix. `method` is "chase" (the window, state chased in) or "full"
    (the song from the top, cut)."""
    if voices is not None and exclude is not None:
        raise AgenticError("bad_render", "give voices or exclude, not both")
    cache = cache or _DEFAULT_CACHE
    keep = _columns(timeline, voices)
    drop = _columns(timeline, exclude)
    rows = timeline.rows
    row_s = rows[0]["t1"] - rows[0]["t0"] if rows else 0.1
    if method == "chase":
        back = int(math.ceil(preroll_s / max(row_s, 1e-6)))
        ahead = int(math.ceil(tail_s / max(row_s, 1e-6)))
        w0 = max(0, rng["row0"] - back)
        w1 = min(len(rows), rng["row1"] + ahead)
        event_list, info = chased_events(timeline, w0, w1, keep, drop)
        start_s = rows[w0]["t0"]
        info = dict(info, preroll_s=round(rows[rng["row0"]]["t0"] - start_s,
                                          3))
    elif method == "full":
        event_list = [ev for ev in timeline.events if _keep(ev, keep, drop)]
        start_s = 0.0
        info = {"preroll_s": round(rng["t0"], 3)}
    else:
        raise AgenticError("bad_render", f"method is chase or full, not "
                           f"{method!r}")
    settings = {"tps": timeline.meta.ticks_per_second, "rate": RATE,
                "method": method}
    key = _key(event_list, settings)
    started = time.time()
    buf = cache.get(key)
    cached = buf is not None
    if buf is None:
        seq = Sequencer(ticks_per_second=timeline.meta.ticks_per_second,
                        target_rate=RATE)
        buf = _flat(seq.render(event_list))
        cache.put(key, buf)
        cache.misses += 1
        cache.render_seconds += time.time() - started
    else:
        cache.hits += 1
    label = ("mix" if voices is None and exclude is None else
             {"only": sorted(voices)} if voices is not None else
             {"without": sorted(exclude)})
    return Render(buf, start_s, rng, label, method, info, key,
                  time.time() - started, cached)


def rms_db(samples) -> float:
    if not len(samples):
        return -180.0
    total = 0.0
    for v in samples:
        total += v * v
    value = math.sqrt(total / len(samples))
    return 20.0 * math.log10(value) if value > 1e-9 else -180.0


def frame_levels(samples, rate: int = RATE, frame_s: float = 0.02):
    """RMS level of each frame, in dB."""
    n = max(1, int(rate * frame_s))
    out = []
    for a in range(0, len(samples) - n + 1, n):
        total = 0.0
        for v in samples[a:a + n]:
            total += v * v
        value = math.sqrt(total / n)
        out.append(20.0 * math.log10(value) if value > 1e-9 else -180.0)
    return out


def envelope_difference(a, b, below_peak_db: float = 40.0) -> dict:
    """How far two renders' level envelopes are apart: mean and 95th
    percentile of |dB difference| over the 20 ms frames within
    `below_peak_db` of the loudest frame. Frames quieter than that are
    left out: in a near-silence the residue of the DC removal (which
    subtracts each render's own mean) differs by 10 dB at -57 dBFS between
    a window and a whole song (measured), which is not a difference in the
    music."""
    la, lb = frame_levels(a), frame_levels(b)
    top = max(la + lb) if la or lb else -180.0
    floor_db = top - below_peak_db
    diffs = sorted(abs(x - y) for x, y in zip(la, lb)
                   if max(x, y) > floor_db)
    if not diffs:
        return {"mean_db": 0.0, "p95_db": 0.0, "frames": 0}
    return {"mean_db": round(sum(diffs) / len(diffs), 2),
            "p95_db": round(diffs[min(len(diffs) - 1,
                                      int(0.95 * len(diffs)))], 2),
            "frames": len(diffs)}


def chase_error(timeline, rng: dict, voices=None,
                preroll_s: float = DEFAULT_PREROLL_S,
                cache: Optional[Cache] = None) -> dict:
    """How far the chased render of `rng` is from the song played from the
    top, inside the range: the waveform null (the level of full - chased
    against full), and the level envelopes' agreement.

    Measured on a test piece: FM voices null to -27..-39 dB; the PSG's
    square and noise do not null at all (+2.5 and +3 dB), because the
    chase starts their oscillator phase and noise register from reset, so
    the same note comes back shifted in phase — and their level envelopes
    still agree. The envelope is the reading that says whether the chased
    render sounds like the song; the null says whether it is the song,
    sample for sample."""
    full = render_range(timeline, rng, voices=voices, method="full",
                        cache=cache)
    chased = render_range(timeline, rng, voices=voices, method="chase",
                          preroll_s=preroll_s, cache=cache)
    a = full.in_range()
    b = chased.in_range()
    n = min(len(a), len(b))
    diff = array.array("f", (a[i] - b[i] for i in range(n)))
    return {"range_s": [rng["t0"], rng["t1"]], "preroll_s": preroll_s,
            "voices": voices or "mix",
            "full_rms_db": round(rms_db(a[:n]), 2),
            "null_db": round(rms_db(diff) - rms_db(a[:n]), 2),
            "envelope": envelope_difference(a[:n], b[:n]),
            "full_render_s": round(full.seconds, 2),
            "chase_render_s": round(chased.seconds, 2)}
