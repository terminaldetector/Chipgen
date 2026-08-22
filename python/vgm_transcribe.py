"""
vgm_transcribe.py — turn a VGM register log back into a readable score.

vgm_import.py already lifts the INSTRUMENTS out of a Genesis VGM. This
lifts the MUSIC: which note, on which channel, at which moment, and at
what level. The output is chipgen tracker text plus the bank it needs, so
a real Mega Drive track becomes something you can read, edit, diff, and
learn from.

That last one is the point. A model asked to write Genesis music has
almost nothing to go on: the corpus is audio and register dumps, neither
of which shows the arrangement. A few dozen VGMs through this become a few
dozen scores in the same notation the model is asked to produce.

## What it can and cannot recover

A register log is what the driver did, not what the composer wrote, so
some things are inferred and this says which:

  * Notes are exact. F-Number and block are in the log; the pitch they
    produce is arithmetic, and the nearest semitone is almost always the
    one that was meant. `--report` prints how far off the nearest
    semitones actually were, so a track using heavy pitch bends is
    visible rather than silently rounded.

  * Velocity is estimated from Total Level on the carriers at key-on,
    relative to the patch's own level. A driver that does volume with the
    YM2612's own registers is read correctly; one that does it by swapping
    patches will look like a patch change.

  * The tempo grid is inferred, by looking for the row length that
    explains the onsets with the least leftover. See `infer_grid`. A track
    with rubato, or one driven at an odd rate, will land on a grid that is
    musically wrong even though every note is in the right place in time.

  * Vibrato IS recovered, and only vibrato. At register level it is a
    pitch deviation that keeps crossing back through zero, which is what
    separates it from a bend — a bend goes one way and stays. Depth and
    speed are measured from the log and written as a `vib` directive.
    Portamento and volume ramps are not recovered; they arrive as the
    same stream of small writes and are dropped.
"""

import json
import math
import os
import sys

import events as events_mod
import opn2
import sn76489
import vgm as vgm_mod
import vgm_import
import vgm_player

#: Candidate row lengths, in seconds. Spans roughly 300 BPM at sixteenths
#: down to 40 BPM at quarters, which covers everything a Mega Drive driver
#: is likely to have been clocked at.
_MIN_ROW, _MAX_ROW = 0.020, 0.400
#: Two candidate rows whose coverage differs by less than this are treated
#: as explaining the onsets equally well, and the longer one wins.
GRID_EQUIVALENCE = 0.02

#: How close to a row line an onset has to be, as a fraction of a row, to
#: count as on the grid. This is a tolerance, not a threshold on the fit:
#: the fit is the FRACTION of onsets that make it, which is what survives
#: the off-grid minority every real track has.
GRID_TOLERANCE = 0.08


class Note:
    __slots__ = ("time", "channel", "kind", "note", "octave", "velocity",
                 "cents_off", "instrument")

    def __init__(self, time, channel, kind, note=None, octave=0, velocity=127,
                 cents_off=0.0, instrument=None):
        self.time = time
        self.channel = channel      # "fm0".."fm5", "psg0".."psg2", "noise", "dac"
        self.kind = kind            # "on" | "off"
        self.note = note
        self.octave = octave
        self.velocity = velocity
        self.cents_off = cents_off
        self.instrument = instrument

    def __repr__(self):
        if self.kind == "off":
            return f"<{self.channel} off @{self.time:.3f}>"
        return (f"<{self.channel} {self.note}-{self.octave} "
                f"v{self.velocity} @{self.time:.3f}>")


def frequency_to_note(frequency: float):
    """Hz -> (name, octave, cents away from that note).

    The cents are kept rather than thrown away: they are the difference
    between "this driver plays in tune" and "this driver bends", and a
    transcription that hides it looks more confident than it is.
    """
    if frequency <= 0:
        return None, 0, 0.0
    semitones = 12.0 * math.log2(frequency / 440.0) + 57.0   # octave 4 = A440
    nearest = int(round(semitones))
    cents = (semitones - nearest) * 100.0
    octave, index = divmod(nearest, 12)
    if not 0 <= octave <= 9:
        return None, 0, 0.0
    return events_mod.NOTE_NAMES[index], octave, cents


# --------------------------------------------------------------------------
# Walking the log
# --------------------------------------------------------------------------
class _FMChannel:
    __slots__ = ("fnum", "block", "latch", "on", "total_level", "algorithm",
                 "keyed_frequency")

    def __init__(self):
        self.keyed_frequency = 0.0
        self.fnum = 0
        self.block = 0
        self.latch = 0          # 0xA4 is written first and latched
        self.on = False
        self.total_level = [0, 0, 0, 0]
        self.algorithm = 0

    def frequency(self, clock: float) -> float:
        return opn2.fnum_block_to_freq(self.fnum, self.block, clock)


#: Which operators reach the output, per algorithm — the carriers, whose
#: Total Level is the channel's actual loudness.
_CARRIERS = ((3,), (3,), (3,), (3,), (2, 3), (1, 2, 3), (1, 2, 3), (0, 1, 2, 3))


def transcribe(path_or_bytes, max_seconds: float = 600.0):
    """Walk a VGM and return (notes, info)."""
    raw = vgm_player.load(path_or_bytes)
    header = vgm_mod.read_header(raw)
    ym_clock = float(header.get("ym2612_clock") or opn2.NTSC_CHIP_CLOCK)
    psg_clock = float(header.get("psg_clock") or sn76489.NTSC_PSG_CLOCK)

    fm = [_FMChannel() for _ in range(6)]
    psg_reg = [0, 0, 0]
    psg_voices = [_PSGVoice(i, None) for i in range(3)]
    noise_on = False
    psg_latch = 0
    dac_last = None
    dac_hits = 0
    dac_enabled = False

    notes = []
    #: Per-voice pitch deviation from the sounding note, in cents. Kept so
    #: the wobble the hold rule discards can be recovered as what it is.
    traces = {f"psg{i}": [] for i in range(3)}
    traces.update({f"fm{i}": [] for i in range(6)})
    for index, voice in enumerate(psg_voices):
        voice.notes = notes
        voice.trace = traces[f"psg{index}"]
    elapsed = 0.0
    dac_writes = 0
    max_samples = int(max_seconds * vgm_mod.DEFAULT_SAMPLE_RATE)

    for command in vgm_player.iter_commands(raw, header, max_samples):
        kind = command[0]
        if kind == "wait":
            elapsed += command[1] / float(vgm_mod.DEFAULT_SAMPLE_RATE)
            for voice in psg_voices:
                voice.tick(elapsed)
            continue

        if kind == "psg":
            byte = command[1]
            if byte & 0x80:
                psg_latch = (byte >> 5) & 3
                if byte & 0x10:                       # volume
                    level = byte & 0x0F
                    if psg_latch == 3:
                        was, noise_on = noise_on, level < 15
                        if noise_on and not was:
                            notes.append(Note(elapsed, "noise", "on",
                                              "C", 5, _psg_velocity(level)))
                        elif was and not noise_on:
                            notes.append(Note(elapsed, "noise", "off"))
                    else:
                        voice = psg_voices[psg_latch]
                        was, now = voice.sounding, level < 15
                        voice.level = level
                        if now and not was:
                            semitone, spelling = _spell(
                                psg_reg[psg_latch], psg_clock)
                            voice.key_on(elapsed, semitone, spelling, level)
                        elif was and not now:
                            voice.key_off(elapsed)
                else:                                  # tone, low nibble
                    if psg_latch < 3:
                        psg_reg[psg_latch] = (psg_reg[psg_latch] & 0x3F0) | (byte & 15)
                        psg_voices[psg_latch].pitch(
                            elapsed, *_spell(psg_reg[psg_latch], psg_clock))
            else:                                      # tone, high six bits
                if psg_latch < 3:
                    psg_reg[psg_latch] = (psg_reg[psg_latch] & 0x0F) | ((byte & 0x3F) << 4)
                    psg_voices[psg_latch].pitch(
                        elapsed, *_spell(psg_reg[psg_latch], psg_clock))
            continue

        if kind != "ym":
            continue
        _, port, addr, data = command
        bank = 1 if port >= 2 else 0

        if bank == 0 and addr == 0x2B:
            # Some drivers gate the DAC off between drums; that edge is a
            # cleaner hit boundary than any gap in the byte stream.
            enabled = bool(data & 0x80)
            if enabled and not dac_enabled:
                dac_last = None
            dac_enabled = enabled
            continue
        if bank == 0 and addr == 0x2A:
            dac_writes += 1
            # A drum is a burst of PCM bytes and the stream stops between
            # bursts, so a byte arriving after a real gap starts a new hit.
            #
            # The limit of this: two drums closer together than the first
            # one's own length leave no gap at all, because the driver
            # simply starts feeding the second sample's bytes. Those read
            # as one hit. Separating them needs the PCM decoded and matched
            # against a kit, which is a different problem from reading a
            # register log.
            if dac_last is None or elapsed - dac_last > DAC_GAP:
                dac_hits += 1
                notes.append(Note(elapsed, "dac", "on", "C", 5, 127))
            dac_last = elapsed
            continue
        if bank == 0 and addr == 0x28:
            index = data & 7
            if index in (3, 7):
                continue
            channel = index if index < 3 else index - 1
            state = fm[channel]
            if data & 0xF0:
                state.on = True
                state.keyed_frequency = state.frequency(ym_clock)
                name, octave, cents = frequency_to_note(state.keyed_frequency)
                if name:
                    notes.append(Note(elapsed, f"fm{channel}", "on", name,
                                      octave, _fm_velocity(state), cents))
            elif state.on:
                state.on = False
                traces[f"fm{channel}"].append((elapsed, None))
                notes.append(Note(elapsed, f"fm{channel}", "off"))
            continue

        index = addr & 3
        if index == 3:
            continue
        state = fm[bank * 3 + index]
        if 0xA4 <= addr <= 0xA6:
            state.latch = data
        elif 0xA0 <= addr <= 0xA2:
            state.fnum = ((state.latch & 7) << 8) | data
            state.block = (state.latch >> 3) & 7
            # A pitch write while the note is held is expression, not a
            # new note. Recorded as cents from where the note started.
            if state.on and state.keyed_frequency > 0:
                now = state.frequency(ym_clock)
                if now > 0:
                    traces[f"fm{bank * 3 + index}"].append(
                        (elapsed, 1200.0 * math.log2(now / state.keyed_frequency)))
        elif 0x40 <= addr < 0x50:
            state.total_level[(addr >> 2) & 3] = data & 0x7F
        elif 0xB0 <= addr <= 0xB2:
            state.algorithm = data & 7

    detected = []
    for channel, trace in traces.items():
        if len(trace) > 8:
            detected.extend(detect_vibrato(trace, channel))
    detected.sort(key=lambda d: d.time)

    info = {
        "vibrato": detected,
        "duration": elapsed,
        "ym_clock": ym_clock,
        "psg_clock": psg_clock,
        "dac_writes": dac_writes,
        "dac_hits": dac_hits,
        "gd3": _gd3_of(raw, header),
    }
    return notes, info


class Detected:
    """An effect found in the log, alongside the notes."""
    __slots__ = ("time", "channel", "kind", "depth_cents", "speed_hz",
                 "duration")

    def __init__(self, time, channel, kind, depth_cents=0.0, speed_hz=0.0,
                 duration=0.0):
        self.time = time
        self.channel = channel
        self.kind = kind             # "vibrato" | "vibrato_off"
        self.depth_cents = depth_cents
        self.speed_hz = speed_hz
        self.duration = duration

    def __repr__(self):
        if self.kind == "vibrato_off":
            return f"<{self.channel} vib off @{self.time:.2f}>"
        return (f"<{self.channel} vib {self.depth_cents:.0f}c "
                f"{self.speed_hz:.1f}Hz @{self.time:.2f} "
                f"for {self.duration:.2f}s>")


#: A wobble has to swing at least this far to be worth calling vibrato.
#: Below it the driver is doing fine tuning, not expression.
VIBRATO_MIN_DEPTH = 8.0
#: ...and oscillate at least this many times, so a single pitch correction
#: is not mistaken for one.
VIBRATO_MIN_CYCLES = 2.5
#: Vibrato lives in this band. Slower reads as a slide, faster is beyond
#: what a 60 Hz driver can articulate.
VIBRATO_MIN_HZ, VIBRATO_MAX_HZ = 2.0, 16.0
#: And it stays inside about a semitone. A "wobble" swinging two octaves
#: is a channel alternating between two notes — an arpeggio, which is a
#: different thing with its own notation — and calling it vibrato would
#: put a two-octave warble into the score where a chord shimmer belongs.
VIBRATO_MAX_DEPTH = 150.0


def detect_vibrato(trace, channel):
    """Find vibrato spans in one channel's pitch trace.

    `trace` is [(time, cents_from_the_sounding_note)]. Vibrato is a
    deviation that keeps crossing back through zero: the sign changes are
    what separate it from a bend, which goes one way and stays.

    This is the other half of the hold rule in _PSGVoice. That rule knows
    a wobble is not a run of notes and throws it away; this keeps what it
    threw, because a Genesis lead without its vibrato is not the same
    lead.
    """
    found = []
    run_start = None
    crossings = []
    peak = 0.0
    # The peak as of the last time the deviation crossed zero. Measuring
    # the running peak instead lets whatever follows the wobble — usually
    # a bend, going one way and not coming back — inflate the depth: a
    # 60-cent vibrato followed by a 250-cent slide reported 255.
    peak_at_crossing = 0.0
    previous = 0.0

    def close(end_time):
        nonlocal run_start, crossings, peak, peak_at_crossing
        if run_start is not None and len(crossings) >= 2:
            span = crossings[-1] - run_start
            cycles = len(crossings) / 2.0
            speed = cycles / span if span > 0 else 0.0
            if (VIBRATO_MIN_DEPTH <= peak_at_crossing <= VIBRATO_MAX_DEPTH
                    and cycles >= VIBRATO_MIN_CYCLES
                    and VIBRATO_MIN_HZ <= speed <= VIBRATO_MAX_HZ):
                found.append(Detected(run_start, channel, "vibrato",
                                      round(peak_at_crossing, 1),
                                      round(speed, 2), round(span, 3)))
                found.append(Detected(crossings[-1], channel, "vibrato_off"))
        run_start = None
        crossings = []
        peak = peak_at_crossing = 0.0

    for time, cents in trace:
        if cents is None:
            close(time)
            previous = 0.0
            continue
        if abs(cents) < 1e-9 and abs(previous) < 1e-9:
            continue
        if run_start is None:
            run_start = time
        if (previous < 0 <= cents) or (previous > 0 >= cents):
            crossings.append(time)
            peak_at_crossing = peak
        peak = max(peak, abs(cents))
        # A deviation that stops coming back has become a bend, not a
        # wobble: close the run and let it start again if it resumes.
        if crossings and time - crossings[-1] > 0.5:
            close(crossings[-1])
        previous = cents
    close(trace[-1][0] if trace else 0.0)
    return found


class _PSGVoice:
    """One PSG tone channel, with the rule that a pitch must HOLD to count.

    Measured across four Streets of Rage 2 tracks: 69% of the pitch
    changes a driver writes last under 45 ms, and their median run is
    16.7 ms — exactly one 60 Hz frame. That is a driver doing vibrato and
    portamento by rewriting the tone register every frame, not a melody
    playing sixty notes a second. So a new pitch becomes a note only once
    it has survived MIN_HOLD, and a pitch that wanders off and comes back
    is discarded as the vibrato it is.
    """
    __slots__ = ("index", "notes", "current", "pending", "pending_since",
                 "pending_note", "level", "sounding", "trace")

    def __init__(self, index, notes, trace=None):
        self.index = index
        self.notes = notes
        self.trace = trace
        self.current = None          # semitone currently counted as sounding
        self.pending = None          # semitone waiting to prove itself
        self.pending_since = 0.0
        self.pending_note = None     # (name, octave, cents)
        self.level = 15
        self.sounding = False

    def _emit(self, when, semitone, spelling):
        name, octave, cents = spelling
        self.notes.append(Note(when, f"psg{self.index}", "on", name, octave,
                               _psg_velocity(self.level), cents))
        self.current = semitone
        self.pending = None

    def key_on(self, when, semitone, spelling, level):
        self.level = level
        self.sounding = True
        if semitone is not None:
            self._emit(when, semitone, spelling)

    def key_off(self, when):
        if self.trace is not None:
            self.trace.append((when, None))     # a gap ends any wobble
        if self.sounding:
            self.notes.append(Note(when, f"psg{self.index}", "off"))
        self.sounding = False
        self.current = None
        self.pending = None

    def pitch(self, when, semitone, spelling):
        if not self.sounding or semitone is None:
            return
        if self.trace is not None and self.current is not None:
            # The TRUE deviation, not the rounded one. Tracing whole
            # semitones quantised every vibrato depth to a multiple of
            # 100 cents, which is not a measurement, it is the rounding
            # showing through.
            self.trace.append((when, (semitone - self.current) * 100.0
                               + spelling[2]))
        if semitone == self.current:
            self.pending = None            # came home: that was vibrato
            return
        if self.pending != semitone:
            self.pending = semitone
            self.pending_since = when
            self.pending_note = spelling

    def tick(self, when):
        if (self.pending is not None
                and when - self.pending_since >= PSG_MIN_HOLD):
            # Timestamped where it started, not where it was confirmed.
            self._emit(self.pending_since, self.pending, self.pending_note)


#: How long a new PSG pitch must hold before it counts as a note rather
#: than as a vibrato swing. See _PSGVoice for the measurement behind it.
PSG_MIN_HOLD = 0.045


#: A DAC byte arriving more than this long after the previous one starts a
#: new hit. Longer than the gap between bytes inside a sample (a 16 kHz
#: kit is 60us apart) and shorter than the space between drums in even
#: fast music.
DAC_GAP = 0.030


def _spell(register: int, clock: float):
    """Tone register -> (semitone, (name, octave, cents)), or (None, ...)."""
    if register < 1:
        return None, (None, 0, 0.0)
    name, octave, cents = frequency_to_note(
        sn76489.tone_n_to_freq(register, clock))
    if not name:
        return None, (None, 0, 0.0)
    return octave * 12 + events_mod.NOTE_NAMES.index(name), (name, octave, cents)


def _psg_velocity(level: int) -> int:
    """PSG attenuator (0 loudest, 15 silent) -> the 1-15 the tracker writes."""
    return max(0, min(15, level))


def _fm_velocity(state) -> int:
    """Estimate 1-127 from the carriers' Total Level at key-on.

    Only the carriers matter: a modulator's level is timbre. Taking the
    quietest carrier rather than the average, because one carrier held
    down is what actually caps how loud the channel can be.
    """
    carriers = _CARRIERS[state.algorithm & 7]
    level = max(state.total_level[i] for i in carriers)
    # Total Level is 0.75 dB per step; invert the same curve the engine
    # uses to turn a 0-127 fader into attenuation.
    decibels = level * 0.75
    return max(1, min(127, int(round(127.0 * (10.0 ** (-decibels / 20.0))))))


def _gd3_of(raw, header):
    try:
        tag = vgm_mod.read_gd3(raw, header)
    except Exception:
        return {}
    if not tag:
        return {}
    return {k: v for k, v in tag.items() if v}


# --------------------------------------------------------------------------
# Finding the grid
# --------------------------------------------------------------------------
def infer_grid(notes, lpb: int = None):
    """Guess the row length from the note onsets.

    Returns (row_seconds, bpm, lpb, coverage). `coverage` is the fraction
    of onsets that land near a row line — the confidence, and the thing to
    filter a corpus on.

    Scored by coverage rather than by average error, because average error
    is decided by the minority of events that are NOT on the grid. Drum
    flams, grace notes and PSG arpeggios written at frame rate are all
    genuinely off-grid, and on real Mega Drive tracks there are enough of
    them to drag a mean to the same 0.25 that random times give — which is
    what the first version of this did, reporting "no grid" for music that
    obviously has one.

    The period is ambiguous by factors of two: every divisor of a true row
    also scores well, and nothing in a list of onsets says whether a row
    is an eighth or a sixteenth. So the longest well-scoring period wins,
    and `lpb` is then chosen to land the tempo somewhere a human would
    write down.
    """
    onsets = sorted({round(n.time, 5) for n in notes if n.kind == "on"})
    if len(onsets) < 16:
        return None, None, lpb or 4, 0.0

    scored = []
    period = _MIN_ROW
    while period <= _MAX_ROW:
        hit = 0
        for time in onsets:
            position = time / period
            if abs(position - round(position)) <= GRID_TOLERANCE:
                hit += 1
        scored.append((period, hit / len(onsets)))
        period += 0.0005

    if not scored:
        return None, None, lpb or 4, 0.0
    best_score = max(score for _period, score in scored)
    if best_score <= 0.0:
        return None, None, lpb or 4, 0.0
    # Every divisor of a true row scores just as well, and the search runs
    # short-to-long, so taking the first-best returns the SHORTEST — on
    # exact onsets that is the 20 ms floor, which is not a row anyone
    # wrote. Take the longest period that still explains the onsets.
    margin = best_score - GRID_EQUIVALENCE
    best_period = max(period for period, score in scored if score >= margin)
    best_score = next(score for period, score in scored if period == best_period)

    if lpb is not None:
        return best_period, 60.0 / (best_period * lpb), lpb, best_score

    # Pick the subdivision that puts the tempo where music usually lives.
    for candidate in (4, 8, 2, 16, 3, 6, 12):
        bpm = 60.0 / (best_period * candidate)
        if MIN_BPM <= bpm <= MAX_BPM:
            return best_period, bpm, candidate, best_score
    return best_period, 60.0 / (best_period * 4), 4, best_score


#: The tempo range `infer_grid` will accept when choosing a subdivision.
#: Wide enough for a slow menu theme and a fast shooter stage, narrow
#: enough to rule out the same grid read as 39 or 320 BPM.
MIN_BPM, MAX_BPM = 70.0, 210.0


# --------------------------------------------------------------------------
# Emitting a score
# --------------------------------------------------------------------------
def to_events(notes, row_seconds: float, ticks_per_row: int = 24,
              patch_names=None, detected=None):
    """Quantise transcribed notes onto the row grid as chipgen events.

    `patch_names` maps "fm0".."fm5" to an instrument name, so the score
    references the bank vgm_import.py lifted from the same file.
    """
    E = events_mod
    rows = {}
    for note in notes:
        row = int(round(note.time / row_seconds))
        rows.setdefault(row, []).append(note)
    for effect in detected or ():
        rows.setdefault(int(round(effect.time / row_seconds)), []).append(effect)

    out = []
    for channel, name in sorted((patch_names or {}).items()):
        if channel.startswith("fm"):
            out.append(E.FMInstrumentSelect(channel=int(channel[2:]),
                                            instrument=name))

    previous = 0
    for row in sorted(rows):
        gap = row - previous
        if gap > 0:
            out.append(E.Wait(ticks=gap * ticks_per_row))
        previous = row
        for note in rows[row]:
            out.extend(_note_to_events(note))
    out.append(E.Wait(ticks=ticks_per_row))
    out.append(E.End())
    return out


def _note_to_events(note):
    E = events_mod
    if isinstance(note, Detected):
        if note.kind == "vibrato_off":
            return [E.Vibrato(target=note.channel, depth_cents=0.0,
                              speed_hz=0.0)]
        return [E.Vibrato(target=note.channel, depth_cents=note.depth_cents,
                          speed_hz=note.speed_hz)]
    channel = note.channel
    if channel.startswith("fm"):
        index = int(channel[2:])
        if note.kind == "off":
            return [E.FMNoteOff(channel=index)]
        return [E.FMNoteOn(channel=index, note=note.note, octave=note.octave,
                           velocity=max(1, min(127, note.velocity)))]
    if channel.startswith("psg"):
        index = int(channel[3:])
        if note.kind == "off":
            return [E.PSGToneOff(channel=index)]
        return [E.PSGToneOn(channel=index, note=note.note, octave=note.octave,
                            volume=max(0, min(15, note.velocity)))]
    if channel == "noise":
        if note.kind == "off":
            return [E.PSGNoiseOff()]
        return [E.PSGNoiseOn(white=True, rate=1,
                             volume=max(0, min(15, note.velocity)))]
    if channel == "dac" and note.kind == "on":
        # Which drum it was is not in the log — the PCM is, but matching it
        # against a kit is a different problem. "kick" is a placeholder the
        # score's reader can swap.
        return [E.DACSample(name="kick")]
    return []


def to_tracker(notes, info, grid, patch_names=None, title: str = "",
               ticks_per_row: int = 24):
    """The whole thing as tracker text, ready to render or read."""
    import tracker as tracker_mod

    row_seconds, bpm, lpb, coverage = grid
    if not row_seconds:
        raise ValueError("no grid could be inferred; too few notes")

    events = to_events(notes, row_seconds, ticks_per_row, patch_names,
                       detected=info.get("vibrato"))
    meta = tracker_mod.Metadata()
    meta.bpm = round(bpm, 2)
    meta.lpb = lpb
    meta.ticks_per_second = ticks_per_row / row_seconds
    gd3 = info.get("gd3") or {}
    meta.title = title or gd3.get("title", "")
    meta.author = gd3.get("author", "")
    meta.game = gd3.get("game", "")

    header = [
        f"# Transcribed from a VGM register log by python/vgm_transcribe.py.",
        f"# {len(([n for n in notes if n.kind == 'on']))} notes and "
        f"{len([d for d in info.get('vibrato', ()) if d.kind == 'vibrato'])} "
        f"vibrato spans over "
        f"{info['duration']:.1f}s; the grid fits {coverage*100:.0f}% of the",
        f"# onsets at {row_seconds*1000:.1f} ms per row.",
        f"# Vibrato is recovered with its measured depth and speed.",
        f"# Portamento and volume ramps are not recovered, and which drum",
        f"# a DAC hit was is not recoverable from a register log at all.",
        "",
    ]
    return "\n".join(header) + tracker_mod.dumps(events, meta)


# --------------------------------------------------------------------------
# One file, and then a corpus of them
# --------------------------------------------------------------------------
def transcribe_file(path: str, max_seconds: float = 600.0, lpb: int = None,
                    with_bank: bool = True):
    """Everything for one VGM: notes, grid, bank, tracker text, stats."""
    notes, info = transcribe(path, max_seconds=max_seconds)
    grid = infer_grid(notes, lpb=lpb)

    bank, patch_names = {}, {}
    if with_bank:
        try:
            patches = vgm_import.extract(path, max_seconds=max_seconds)
        except Exception:
            patches = []
        bank = vgm_import.to_bank(patches, calibrate=False)
        # Name each FM channel after the patch it used most. `patches` is
        # already sorted most-used first, so the first one seen on a
        # channel is the one it spent longest on — which is the closest a
        # single `inst` line can come to a channel that swapped patches.
        for patch in patches:
            for channel in patch.channels:
                patch_names.setdefault(f"fm{channel}", patch.instrument.name)

    text = None
    if grid[0]:
        text = to_tracker(notes, info, grid, patch_names,
                          title=os.path.splitext(os.path.basename(path))[0])

    on = [n for n in notes if n.kind == "on"]
    channels = sorted({n.channel for n in on})
    # A track's own tuning first, then bends measured against it. Several
    # of these games are not tuned to A440 — Gleylancer sits a consistent
    # +16.7 cents sharp with only 2-3 cents of spread, which is a note
    # table, not a performance. Calling that "83% of notes bent" would be
    # true of the arithmetic and false about the music.
    tuning, spread = _tuning(on)
    bent = sum(1 for n in on if abs(n.cents_off - tuning) > 25)
    return {
        "source": os.path.basename(path),
        "duration": round(info["duration"], 2),
        "notes": len(on),
        "notes_per_second": round(len(on) / max(1e-6, info["duration"]), 2),
        "channels": channels,
        "dac_hits": info["dac_hits"],
        "row_seconds": grid[0],
        "bpm": round(grid[1], 2) if grid[1] else None,
        "lpb": grid[2],
        "grid_fit": round(grid[3], 3),
        "vibrato_spans": len([d for d in info.get("vibrato", ())
                              if d.kind == "vibrato"]),
        "bent_notes": bent,
        "bent_fraction": round(bent / max(1, len(on)), 3),
        "tuning_cents": round(tuning, 1),
        "bend_spread_cents": round(spread, 1),
        "patches": len(bank),
        "gd3": info.get("gd3", {}),
        "tracker": text,
        "bank": bank,
    }


def build_corpus(paths, out_dir: str, max_seconds: float = 600.0,
                 min_notes: int = 32, min_fit: float = 0.25, lpb: int = None,
                 progress=None):
    """Transcribe many VGMs into a directory of scores plus a manifest.

    Everything that fails a quality gate is recorded in the manifest with
    the reason rather than dropped, so the corpus says what it excluded
    and why — a training set that silently discards its hard cases
    misrepresents the thing it is meant to describe.
    """
    os.makedirs(out_dir, exist_ok=True)
    scores_dir = os.path.join(out_dir, "scores")
    banks_dir = os.path.join(out_dir, "banks")
    os.makedirs(scores_dir, exist_ok=True)
    os.makedirs(banks_dir, exist_ok=True)

    entries = []
    for index, path in enumerate(paths, start=1):
        if progress:
            progress(index, len(paths), path)
        try:
            record = transcribe_file(path, max_seconds=max_seconds, lpb=lpb)
        except Exception as exc:                       # a corrupt or exotic file
            entries.append({"source": os.path.basename(path),
                            "accepted": False,
                            "reason": f"{type(exc).__name__}: {exc}"})
            continue

        reason = None
        if record["notes"] < min_notes:
            reason = f"only {record['notes']} notes"
        elif not record["tracker"]:
            reason = "no tempo grid could be inferred"
        elif record["grid_fit"] < min_fit:
            reason = (f"grid fits only {record['grid_fit']*100:.0f}% of onsets")

        stem = _stem(path)
        record["accepted"] = reason is None
        if reason:
            record["reason"] = reason
        else:
            score_path = os.path.join(scores_dir, stem + ".trk")
            with open(score_path, "w", encoding="utf-8") as handle:
                handle.write(record["tracker"])
            record["score"] = os.path.relpath(score_path, out_dir)
            if record["bank"]:
                bank_path = os.path.join(banks_dir, stem + ".json")
                import instruments as instruments_mod
                instruments_mod.save_bank(bank_path, record["bank"])
                record["bank_file"] = os.path.relpath(bank_path, out_dir)

        record.pop("tracker", None)
        record.pop("bank", None)
        entries.append(record)

    accepted = [e for e in entries if e.get("accepted")]
    manifest = {
        "tracks": len(entries),
        "accepted": len(accepted),
        "rejected": len(entries) - len(accepted),
        "total_notes": sum(e.get("notes", 0) for e in accepted),
        "total_seconds": round(sum(e.get("duration", 0) for e in accepted), 1),
        "produced_by": "python/vgm_transcribe.py",
        "caveats": [
            "Notes and their timing are recovered from the register log.",
            "Velocity is estimated from carrier Total Level at key-on.",
            "The tempo grid is inferred; `grid_fit` is the confidence.",
            "Vibrato is recovered, with its measured depth and speed. "
            "Portamento and volume ramps are not.",
            "Which drum a DAC hit was is not recovered; all read as `kick`.",
            "Two DAC hits closer together than the first sample's length "
            "leave no gap in the byte stream and read as one hit.",
            "`tuning_cents` is the track's own pitch reference against "
            "A440; several of these games are not tuned to it.",
        ],
        "entries": entries,
    }
    with open(os.path.join(out_dir, "manifest.json"), "w",
              encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
    return manifest


def _tuning(notes):
    """(median cents off A440, median deviation from that) over the notes.

    A tight spread around a non-zero median is the track's tuning; a wide
    spread is a player who bends.
    """
    cents = [n.cents_off for n in notes if n.note]
    if len(cents) < 8:
        return 0.0, 0.0
    ordered = sorted(cents)
    median = ordered[len(ordered) // 2]
    deviations = sorted(abs(c - median) for c in cents)
    return median, deviations[len(deviations) // 2]


def _stem(path: str) -> str:
    name = os.path.splitext(os.path.basename(path))[0]
    parent = os.path.basename(os.path.dirname(path))
    out = []
    for char in f"{parent}_{name}".lower():
        out.append(char if char.isalnum() else "_")
    return "_".join(p for p in "".join(out).split("_") if p)[:90] or "track"


def main(argv):
    import argparse
    import glob as glob_mod

    parser = argparse.ArgumentParser(
        prog="vgm_transcribe",
        description="Turn VGM register logs into readable tracker scores.")
    parser.add_argument("source", help="a .vgm/.vgz file, or a directory")
    parser.add_argument("-o", "--out",
                        help="write the score here (single file), or the "
                             "corpus directory (with --corpus)")
    parser.add_argument("--corpus", action="store_true",
                        help="walk a directory and build a corpus with a "
                             "manifest")
    parser.add_argument("--lpb", type=int,
                        help="force rows per beat instead of inferring it")
    parser.add_argument("--max-seconds", type=float, default=600.0)
    parser.add_argument("--min-notes", type=int, default=32)
    parser.add_argument("--min-fit", type=float, default=0.25,
                        help="reject tracks whose grid explains less than "
                             "this fraction of the onsets")
    args = parser.parse_args(argv)

    if args.corpus or os.path.isdir(args.source):
        paths = sorted(glob_mod.glob(os.path.join(args.source, "**", "*.vg[mz]"),
                                     recursive=True))
        if not paths:
            print(f"no .vgm or .vgz under {args.source}")
            return 1
        out_dir = args.out or "corpus"

        def show(index, total, path):
            print(f"  [{index:3d}/{total}] {os.path.basename(path)[:56]}",
                  flush=True)

        manifest = build_corpus(paths, out_dir, max_seconds=args.max_seconds,
                                min_notes=args.min_notes,
                                min_fit=args.min_fit, lpb=args.lpb,
                                progress=show)
        print(f"\n{manifest['accepted']}/{manifest['tracks']} tracks accepted, "
              f"{manifest['total_notes']} notes over "
              f"{manifest['total_seconds']:.0f}s")
        print(f"wrote {out_dir}/manifest.json, {out_dir}/scores/, "
              f"{out_dir}/banks/")
        rejected = [e for e in manifest["entries"] if not e.get("accepted")]
        if rejected:
            print(f"\nrejected {len(rejected)}:")
            for entry in rejected[:10]:
                print(f"  {entry['source'][:52]:54s} {entry.get('reason', '')}")
            if len(rejected) > 10:
                print(f"  ... and {len(rejected) - 10} more "
                      f"(all listed in the manifest)")
        return 0

    record = transcribe_file(args.source, max_seconds=args.max_seconds,
                             lpb=args.lpb)
    print(f"{record['source']}: {record['notes']} notes over "
          f"{record['duration']:.1f}s on {len(record['channels'])} channels")
    if record["bpm"]:
        print(f"  grid {record['row_seconds']*1000:.1f} ms/row -> "
              f"{record['bpm']:.1f} BPM at lpb {record['lpb']} "
              f"(fits {record['grid_fit']*100:.0f}% of onsets)")
    else:
        print("  no tempo grid could be inferred")
    print(f"  {record['patches']} FM patches, {record['dac_hits']} DAC hits, "
          f"{record['bent_fraction']*100:.0f}% of notes bent >25 cents")
    if args.out and record["tracker"]:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(record["tracker"])
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
