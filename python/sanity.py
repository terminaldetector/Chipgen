"""
sanity.py — catch a composition that will render correctly and sound wrong.

The chips have no opinion about the music. Tell a channel to stay on for
sixty seconds and it stays on for sixty seconds, faithfully — the engine's
job is to execute what it is told, not to have taste. So a composition can
pass every rendering check (no clipping, no dropped writes, correct
tuning) and still come out sounding like a wall of hiss, because the
SCORE, not the chip, left the noise channel running the whole time.

This module exists because that is exactly what happened once: a model
wrote a track where the PSG noise channel gated on and never gated off
for the full 60 seconds, and the 8-bit DAC was enabled at 2.7s and never
disabled again — 691,096 samples streamed back to back with under 0.05%
real silence. Both facts were invisible in the rendered audio's level
meters (no clipping, steady RMS) and only showed up as elevated spectral
flatness. They were NOT invisible in the event list: a channel key-on
with no matching key-off for the rest of the piece is a one-line check.

check() runs on the event list BEFORE rendering and returns the same kind
of human-readable warning strings events.parse() does, so they surface
through Result.warnings and the CLI without a second code path.

This is a lint, not a gate: nothing here refuses to render. A continuous
drone or a wash of noise for the whole track might be exactly the sound
someone wants — the point is that it should be a choice, not a surprise
discovered by staring at a spectrogram.
"""

from typing import Dict, List, NamedTuple

import events as events_mod
import samples as samples_mod

#: A channel considered "always on" for this fraction of the track or more
#: gets flagged. High rather than exactly 1.0: a track that is on for
#: 99.5% of its length with one brief gap has the same problem as one
#: that never gates off at all.
DUTY_CYCLE_THRESHOLD = 0.95

#: A channel re-triggered this many times or fewer, while still hitting the
#: duty-cycle threshold above, reads as one held drone rather than an
#: arrangement. More retriggers than this and it is a busy line — a
#: chord pad re-struck every bar, a running bassline — which is a normal,
#: deliberate arrangement choice rather than a channel nobody ever let
#: rest.
#:
#: Every note-on counts as a retrigger here, whether or not a note-off
#: preceded it: opn2.YM2612.note_on() defaults to retrigger=True, which
#: key-offs and re-attacks even a channel that is already sounding, so a
#: legato bassline with zero silent gaps still produces a fresh envelope
#: attack on every new pitch. Counting only off->on transitions would call
#: that "one continuous span" and flag ordinary busy basslines as drones —
#: which the first version of this check did, on this project's own demo
#: tracks.
RETRIGGER_CEILING = 3

#: Same idea for the DAC: fraction of track duration spent with the DAC
#: enabled, regardless of how many discrete samples were triggered inside
#: that span. Enabling once at t=2.7s and never disabling again scores
#: ~0.95 by this measure on a 60s track, which is exactly the case that
#: motivated this module.
DAC_DUTY_CYCLE_THRESHOLD = 0.60

#: Below this track length, every duty-cycle check is skipped outright.
#: A percentage is a bad statistic on a short clip: this project's own
#: built-in example is 0.8s of two-bar drum fill using the 220ms "kick"
#: sample at a fast tempo, so consecutive hits legitimately overlap and
#: the DAC never gets a gap to report as "off" — 100% by the numbers, and
#: correct, ordinary drum programming. The problem this module exists to
#: catch is a channel with no rest across TENS OF SECONDS; below this
#: floor there usually has not been time for that problem to exist yet.
MIN_TRACK_SECONDS = 5.0

#: A channel whose instrument is a bass patch but which never plays below
#: this note is not doing a bass's job — it is a mid-range part with a bass
#: timbre, and the mix will have a hole underneath it. C3 rather than
#: something lower because plenty of real basslines live in the C2-C3
#: octave; the complaint is only about never going down there at all.
BASS_REGISTER_CEILING = ("C", 3)

#: Two parts whose median pitch is within this many semitones are competing
#: for the same space. A fifth is the usual point where separate lines stop
#: reading as separate.
REGISTER_COLLISION_SEMITONES = 7

#: Marked sections whose loudest and quietest differ by less than this are
#: structurally flat, whatever the section names claim.
SECTION_DYNAMIC_RANGE_DB = 3.0

#: Fraction of FM5's sounding time that may be swallowed by the DAC before
#: it is worth mentioning. The YM2612 has no mixer here: register 0x2B bit 7
#: hands channel 6 (FM5, zero-based) to the 8-bit DAC outright, and the FM
#: voice stops reaching the output pins entirely — measured at exactly
#: 0.00000 RMS on a YM3438 with a note held and the DAC parked at centre.
#: So a drum on the `dac` column and a note on `fm5` at the same moment is
#: not a balance problem to mix around, it is one part deleting the other.
#: A few percent is ordinary overlap at the edges of hits; a third of the
#: part's life means the part was written into a channel it cannot have.
FM6_DAC_MASK_THRESHOLD = 0.30

#: Patch names that are meant to hold down the low end. Checked against the
#: instrument actually assigned, so a lead sitting low is not mistaken for
#: a bass and vice versa.
BASS_PATCH_NAMES = frozenset({"bass", "sub_bass", "deep_bass", "slap_bass",
                              "techno_bass", "slap", "acid_bass"})
LEAD_PATCH_NAMES = frozenset({"distorted_lead", "square_lead", "saw_lead",
                              "lead"})

#: How many voices have to pile into one narrow pitch band before it is
#: worth saying so, and how narrow that band is. Name-free on purpose:
#: the two checks above look up the patch assigned to a channel, so they
#: see nothing at all on an imported bank whose patches are called hl_01
#: and hl_05 — and crowding is a property of where the parts SIT, not of
#: what they are called.
#:
#: An octave is the band. Three or more melodic voices whose median
#: pitches all fall inside one octave are not three parts, they are one
#: chord voiced in unison-ish, and on a chip with six voices total that
#: is most of the arrangement spent on a single band.
CROWDED_VOICES = 3
CROWDED_SPAN_SEMITONES = 12


def _midi(note: str, octave: int) -> int:
    """Note name + octave -> a comparable integer. Octave 4 holds A440."""
    return octave * 12 + events_mod.NOTE_NAMES.index(note)


def _median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _dac_sample_seconds(name: str, rate_override: int) -> float:
    """How long one DACSample event actually plays, or None if `name` is not
    a real kit entry — that is a render-time error, not this module's to
    raise twice, so it is silently excluded from the DAC-busy measurement."""
    try:
        sample = samples_mod.KIT[name]
    except KeyError:
        return None
    rate = rate_override or sample.rate
    return (len(sample.data) / float(rate)) if rate > 0 else None


# --------------------------------------------------------------------------
# Silent failures: the render succeeds and the note is not there, or is not
# the note that was written. These are not judgements about an arrangement
# and the 5-second floor does not apply to them — a four-row score with no
# instrument is as silent as a four-minute one.
# --------------------------------------------------------------------------

#: The lowest note each voice plays at its written pitch. Below these the
#: divider register clamps and the note sounds SHARP rather than low.
#: Measured, not read off a datasheet: PSG C-2 renders at 109.34 Hz, which
#: is A-2 and +889.6 cents from what was written; the NES pulse clamps the
#: same way below A-1 and the triangle below A-0.
FLOORS = {"psg": ("A", 2), "nes_pulse": ("A", 1), "nes_triangle": ("A", 0)}

#: The highest PSG note within 10 cents of its written pitch. G#6 measured
#: +8.6 cents, A-6 -12.0, C-7 +14.5; adjacent semitones do not collide
#: until C9, so this is intonation drift, not lost notes.
PSG_IN_TUNE_CEILING = ("G#", 6)


class Finding(NamedTuple):
    """One silent failure, in a form a model can act on.

    `rule` is shared with prompts.RULES: the briefing that tells a model
    the rule and the check that catches it breaking use the same name, so
    the two cannot drift into teaching one thing and testing another.
    """
    rule: str
    severity: str             #: "error" — the music is wrong — or "warning"
    voice: str                #: the score column it happened on
    message: str
    fix: str
    count: int = 1
    at: float = 0.0           #: seconds into the track, first occurrence

    def text(self) -> str:
        times = f" ({self.count}x, first at {self.at:.2f}s)" \
            if self.count > 1 else f" (at {self.at:.2f}s)"
        return f"{self.message}{times} — {self.fix}"


def _below(note: str, octave: int, floor) -> bool:
    return _midi(note, octave) < _midi(floor[0], floor[1])


def _cell(note: str, octave: int) -> str:
    """A pitch the way the score wrote it: `C-2`, `G#6`."""
    return f"{note}{'-' if len(note) == 1 else ''}{octave}"


def silent_failures(events: List[events_mod.Event],
                    ticks_per_second: float = 192.0) -> List[Finding]:
    """Every place the render will succeed and the music will not be there.

    Structured rather than a string so a checker can hand a model the
    column and the fix; check() flattens them into its warnings so every
    other path — the CLI, the interface, the bridge — sees them too.
    """
    E = events_mod
    rate = ticks_per_second
    clock = 0.0
    voiced = set()            # FM channels with an instrument or live ops
    tally = {}                # (rule, voice) -> [count, first_at, sample]

    def hit(rule, voice, detail=None):
        entry = tally.get((rule, voice))
        if entry is None:
            tally[(rule, voice)] = [1, clock, detail]
        else:
            entry[0] += 1

    for event in events:
        if isinstance(event, E.Wait):
            clock += event.ticks / rate
            continue
        if isinstance(event, E.Tempo):
            rate = max(1.0, float(event.ticks_per_second))
            continue
        if isinstance(event, E.End):
            break
        if isinstance(event, (E.FMInstrumentSelect, E.FMOperator,
                              E.FMAlgorithm)):
            voiced.add(event.channel)
        elif isinstance(event, E.FMNoteOn):
            if event.channel not in voiced:
                hit("fm_needs_inst", f"fm{event.channel}")
        elif isinstance(event, E.PSGToneOn):
            voice = f"psg{event.channel}"
            if event.volume >= 15:
                hit("psg_15_is_silent", voice)
            if _below(event.note, event.octave, FLOORS["psg"]):
                hit("psg_floor", voice, _cell(event.note, event.octave))
            elif not _below(event.note, event.octave, PSG_IN_TUNE_CEILING) \
                    and _midi(event.note, event.octave) > \
                    _midi(*PSG_IN_TUNE_CEILING):
                hit("psg_detune", voice, _cell(event.note, event.octave))
        elif isinstance(event, E.PSGNoiseOn):
            if event.volume >= 15:
                hit("psg_15_is_silent", "noise")
        elif isinstance(event, E.NESNoteOn):
            voice = {"pulse1": "nes0", "pulse2": "nes1",
                     "triangle": "nes2"}.get(event.voice, event.voice)
            if event.voice == "triangle":
                if _below(event.note, event.octave, FLOORS["nes_triangle"]):
                    hit("nes_triangle_floor", voice,
                        _cell(event.note, event.octave))
                if event.velocity != 127:
                    hit("nes_triangle_velocity", voice)
            elif _below(event.note, event.octave, FLOORS["nes_pulse"]):
                hit("nes_pulse_floor", voice, _cell(event.note, event.octave))

    out = []
    for (rule, voice), (count, at, detail) in sorted(
            tally.items(), key=lambda kv: (kv[1][1], kv[0])):
        spec = SILENT_RULES[rule]
        out.append(Finding(
            rule=rule, severity=spec["severity"], voice=voice,
            message=spec["message"].format(voice=voice, detail=detail),
            fix=spec["fix"].format(voice=voice, detail=detail),
            count=count, at=at))
    return out


#: The wording for each silent failure. One place, because the checker,
#: check()'s warnings and the briefings all quote it.
SILENT_RULES = {
    "fm_needs_inst": {
        "severity": "error",
        "message": "{voice} plays notes before any `inst {voice} <name>` — "
                   "an FM channel with no instrument is SILENT",
        "fix": "add `inst {voice} <instrument>` above the first row",
    },
    "psg_15_is_silent": {
        "severity": "error",
        "message": "{voice} has notes at volume 15 — the PSG field is an "
                   "ATTENUATOR, so 15 is silence and 0 is loudest",
        "fix": "write the note bare (`C-5`) for full volume, or `:2`-`:8` "
               "for quieter",
    },
    "psg_floor": {
        "severity": "error",
        "message": "{voice} plays {detail}, below the PSG floor A-2 — the "
                   "10-bit divider clamps and it sounds as A-2, up to "
                   "+890 cents SHARP",
        "fix": "move the part up an octave, or give the bass to an FM "
               "channel",
    },
    "psg_detune": {
        "severity": "warning",
        "message": "{voice} plays {detail}, above G#6 — the PSG divider "
                   "drifts 12-19 cents out of tune up there",
        "fix": "keep PSG parts at or below G#6, or move the line to FM",
    },
    "nes_pulse_floor": {
        "severity": "error",
        "message": "{voice} plays {detail}, below the pulse floor A-1 — "
                   "the 11-bit timer clamps and the note sounds SHARP, "
                   "not low",
        "fix": "move the part up an octave, or give the bass to nes2 "
               "(the triangle reaches A-0)",
    },
    "nes_triangle_floor": {
        "severity": "error",
        "message": "{voice} plays {detail}, below the triangle floor A-0 "
                   "— the timer clamps and the note sounds SHARP",
        "fix": "move the part up an octave",
    },
    "nes_triangle_velocity": {
        "severity": "warning",
        "message": "{voice} has velocities, and the triangle has no volume "
                   "register — they are ignored",
        "fix": "drop the `:N` on nes2; shape the bass with note lengths "
               "instead",
    },
}


def check(events: List[events_mod.Event],
          ticks_per_second: float = 192.0) -> List[str]:
    """Return human-readable warnings about the event list's arrangement.

    Does not render anything — this is pure bookkeeping over the event
    stream, so it costs microseconds even on a long track.
    """
    # Silent failures first and regardless of length: the 5-second floor
    # exists so a four-row test is not judged as an arrangement, and a
    # silent channel is not a judgement.
    warnings: List[str] = [finding.text() for finding in
                           silent_failures(events, ticks_per_second)]
    rate = ticks_per_second
    clock = 0.0

    fm_on_since: Dict[int, float] = {}
    fm_on_time = [0.0] * 6
    fm_retriggers = [0] * 6
    psg_on_since: Dict[int, float] = {}
    psg_on_time = [0.0] * 3
    psg_retriggers = [0] * 3
    noise_on_since = None
    noise_on_time = 0.0
    noise_retriggers = 0
    #: The DAC's busy time is measured from each sample's OWN duration, not
    #: from enable/disable events. Composers never write DACEnable by hand —
    #: DACSample manages it — so tracking only explicit enable/disable calls
    #: made every drum pattern look "100% enabled" the instant two short
    #: samples landed closer together than either one's own length, which is
    #: normal at a fast tempo with this project's ~200ms built-in kit. A
    #: retrigger truncates whatever was still playing (the sequencer replaces
    #: it outright — see sequencer.py's start_dac), so spans never overlap:
    #: each new DACSample first closes out however much of the PREVIOUS
    #: sample's duration actually elapsed before this one, then opens its own.
    dac_span_start = None
    dac_span_end = None
    dac_on_time = 0.0
    #: Both sides of the FM6/DAC collision, as spans rather than totals —
    #: the question is not how long each was busy but how much of that time
    #: they were busy TOGETHER, and two durations cannot answer that.
    #: Channel 5 only: it is the sole channel the DAC can take over.
    dac_spans = []
    fm5_spans = []
    any_pan = False
    any_dac_sample = False
    fm_pitches = {}           # channel -> [midi numbers played]
    # The same question asked of the other two chips. Crowding was an
    # FM-only check, so six OPL2 voices inside one octave, or both NES
    # pulses in unison with the triangle, passed with nothing said.
    opl_pitches = {}          # "OPL3" -> [midi numbers]
    nes_pitches = {}          # "nes0" -> [midi numbers]
    fm_instrument = {}        # channel -> instrument name last assigned

    def close_dac_span(at):
        nonlocal dac_on_time, dac_span_start, dac_span_end
        if dac_span_start is not None:
            stop = min(dac_span_end, at)
            if stop > dac_span_start:
                dac_on_time += stop - dac_span_start
                dac_spans.append((dac_span_start, stop))
        dac_span_start = dac_span_end = None

    layers = set()            # channels that double another part (forge)
    for event in events:
        E = events_mod
        if isinstance(event, E.Marker) \
                and event.label.startswith("forge:layers "):
            layers.update(event.label.split()[1:])
            continue
        if isinstance(event, E.Wait):
            clock += event.ticks / rate
            continue
        if isinstance(event, E.Tempo):
            rate = max(1.0, float(event.ticks_per_second))
            continue
        if isinstance(event, E.End):
            break

        if isinstance(event, E.FMNoteOn):
            # Every note-on counts, including a retrigger of an already-
            # sounding channel — see the RETRIGGER_CEILING note above.
            fm_retriggers[event.channel] += 1
            fm_on_since.setdefault(event.channel, clock)
            if f"fm{event.channel}" not in layers:
                fm_pitches.setdefault(event.channel, []).append(
                    _midi(event.note, event.octave))
        elif isinstance(event, E.OPLNoteOn):
            if f"opl{event.channel}" not in layers:
                opl_pitches.setdefault(f"OPL{event.channel}", []).append(
                    _midi(event.note, event.octave))
        elif isinstance(event, E.NESNoteOn):
            column = {"pulse1": "nes0", "pulse2": "nes1",
                      "triangle": "nes2"}.get(event.voice, event.voice)
            # Written pitch is sounding pitch on all three: the engine
            # sets the triangle's timer for its /32 divider (measured,
            # triangle C-4 at 261.36 Hz), so no octave correction here.
            if event.voice not in layers:
                nes_pitches.setdefault(column, []).append(
                    _midi(event.note, event.octave))
        elif isinstance(event, E.FMInstrumentSelect):
            fm_instrument[event.channel] = event.instrument
        elif isinstance(event, E.FMNoteOff):
            start = fm_on_since.pop(event.channel, None)
            if start is not None:
                fm_on_time[event.channel] += clock - start
                if event.channel == 5 and clock > start:
                    fm5_spans.append((start, clock))
        elif isinstance(event, E.PSGToneOn):
            psg_retriggers[event.channel] += 1
            psg_on_since.setdefault(event.channel, clock)
        elif isinstance(event, E.PSGToneOff):
            start = psg_on_since.pop(event.channel, None)
            if start is not None:
                psg_on_time[event.channel] += clock - start
        elif isinstance(event, E.PSGNoiseOn):
            noise_retriggers += 1
            if noise_on_since is None:
                noise_on_since = clock
        elif isinstance(event, E.PSGNoiseOff):
            if noise_on_since is not None:
                noise_on_time += clock - noise_on_since
                noise_on_since = None
        elif isinstance(event, E.DACSample):
            any_dac_sample = True
            close_dac_span(clock)          # retriggering cuts the previous one off
            duration = _dac_sample_seconds(event.name, event.rate)
            if duration is not None:
                dac_span_start, dac_span_end = clock, clock + duration
        elif isinstance(event, E.DACEnable):
            if not event.enable:
                close_dac_span(clock)      # an explicit disable cuts it off too
        elif isinstance(event, E.FMPan):
            if not (event.left and event.right) or event.left != event.right:
                any_pan = True

    total = clock
    if total < MIN_TRACK_SECONDS:
        return warnings

    for channel, start in fm_on_since.items():
        fm_on_time[channel] += total - start
        if channel == 5 and total > start:
            fm5_spans.append((start, total))
    for channel, start in psg_on_since.items():
        psg_on_time[channel] += total - start
    if noise_on_since is not None:
        noise_on_time += total - noise_on_since
    close_dac_span(total)   # count whatever was still playing at the end

    for channel, on_time in enumerate(fm_on_time):
        duty = on_time / total
        if duty >= DUTY_CYCLE_THRESHOLD and fm_retriggers[channel] <= RETRIGGER_CEILING:
            warnings.append(
                f"FM{channel} is one continuous {duty*100:.0f}%-of-the-track "
                f"drone ({fm_retriggers[channel]} note-on total) — if that "
                f"is meant to be a held pad, fine; if not, it never gets a "
                f"chance to breathe")

    for channel, on_time in enumerate(psg_on_time):
        duty = on_time / total
        if duty >= DUTY_CYCLE_THRESHOLD and psg_retriggers[channel] <= RETRIGGER_CEILING:
            warnings.append(
                f"PSG{channel} is one continuous {duty*100:.0f}%-of-the-track "
                f"drone ({psg_retriggers[channel]} note-on total) — same "
                f"issue as a continuous FM channel")

    noise_duty = noise_on_time / total
    if noise_duty >= DUTY_CYCLE_THRESHOLD:
        # No retrigger-ceiling exception here, unlike FM/PSG tone channels
        # above: a retrigger there usually carries a NEW PITCH, real
        # musical movement a listener can hear. PSGNoiseOn has no pitch —
        # white/rate/volume repeated unchanged, row after row, is not
        # variation, it is the same hiss re-asserted for no audible reason.
        # High duty cycle means "this channel never rests" regardless of
        # how many near-identical PSGNoiseOn calls produced that duty cycle.
        warnings.append(
            f"the noise channel is on for {noise_duty*100:.0f}% of the "
            f"track ({noise_retriggers} PSGNoiseOn total) with no real "
            f"rest — this alone reads as a constant hiss under the whole "
            f"mix, not intermittent hats. Gate it like real drums: brief "
            f"PSGNoiseOn/Off pairs spread through the track, with actual "
            f"gaps, not a channel that is always on")

    dac_duty = dac_on_time / total
    if any_dac_sample and dac_duty >= DAC_DUTY_CYCLE_THRESHOLD:
        warnings.append(
            f"the DAC is enabled for {dac_duty*100:.0f}% of the track's "
            f"duration — if that is one continuous stream rather than "
            f"short drum hits with real gaps between them, FM channel 6 "
            f"has been a lo-fi 8-bit noise bed for most of the piece, not "
            f"a drum channel. Space DACSample events out and let the DAC "
            f"finish between hits")

    fm5_time = sum(end - start for start, end in fm5_spans)
    if fm5_spans and dac_spans:
        masked = _overlap_seconds(fm5_spans, dac_spans)
        share = masked / fm5_time if fm5_time else 0.0
        if share >= FM6_DAC_MASK_THRESHOLD:
            warnings.append(
                f"FM5 and the DAC are both in use, and {share*100:.0f}% of "
                f"FM5's {fm5_time:.1f}s of note time happens while a DAC "
                f"sample is playing — on a YM2612 those cannot coexist. "
                f"Register 0x2B bit 7 hands channel 6 to the DAC outright, "
                f"so for that {masked:.1f}s the FM part is not quiet, it is "
                f"absent. Move the part to FM0-FM4 (Genesis drivers keep "
                f"channel 6 for drums precisely for this reason), or drop "
                f"the DAC drums and play them on the noise channel")

    # Only where there is FM to pan: NES and OPL2 scores have no FM
    # channel, and telling a model to add FMPan to one sends it after a
    # control its chip does not have.
    if len(events) > 200 and not any_pan and any(fm_on_time):
        warnings.append(
            "no FMPan event anywhere — every FM channel defaults to "
            "centre, so the mix has no stereo width at all. Not wrong, "
            "just worth knowing: PSG is mono-summed regardless, but "
            "spreading FM channels with FMPan is free separation")

    warnings.extend(_register_warnings(fm_pitches, fm_instrument))
    warnings.extend(_crowding_warnings(opl_pitches, chip="OPL2",
                                       whole="nine"))
    warnings.extend(_crowding_warnings(nes_pitches, chip="NES",
                                       whole="three"))
    return warnings


def _overlap_seconds(a_spans, b_spans) -> float:
    """Total time covered by both span lists at once.

    A sweep rather than a nested loop: each list is already in event order,
    so walking them together is linear, and a long track's drum column runs
    to thousands of spans.
    """
    a = sorted(a_spans)
    b = sorted(b_spans)
    total = 0.0
    i = j = 0
    while i < len(a) and j < len(b):
        start = max(a[i][0], b[j][0])
        stop = min(a[i][1], b[j][1])
        if stop > start:
            total += stop - start
        # Advance whichever span ends first; the other may still overlap
        # the next one along.
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return total


def _register_warnings(fm_pitches, fm_instrument) -> List[str]:
    """Where each part sits, and whether two of them sit on top of each other.

    Separate from the duty-cycle checks above because it asks a different
    question: not "does this channel ever rest" but "is this channel doing
    the job its instrument implies". A bass patch that never descends past
    the middle of the keyboard leaves the bottom of the mix empty, and a
    lead sharing a register with the bass makes both harder to hear — both
    survive every timing check because neither is a timing problem.
    """
    warnings: List[str] = []
    ceiling = _midi(*BASS_REGISTER_CEILING)

    bass_channels = {ch: name for ch, name in fm_instrument.items()
                     if name in BASS_PATCH_NAMES and fm_pitches.get(ch)}
    lead_channels = {ch: name for ch, name in fm_instrument.items()
                     if name in LEAD_PATCH_NAMES and fm_pitches.get(ch)}

    for channel, name in sorted(bass_channels.items()):
        lowest = min(fm_pitches[channel])
        if lowest >= ceiling:
            note = events_mod.NOTE_NAMES[lowest % 12]
            warnings.append(
                f"FM{channel} plays {name!r} but never goes below "
                f"{note}{lowest // 12} — a bass patch used entirely above "
                f"{BASS_REGISTER_CEILING[0]}{BASS_REGISTER_CEILING[1]} "
                f"leaves the bottom of the mix empty. Either drop the part "
                f"an octave or two, or use a lead patch and let something "
                f"else carry the low end")

    # And the same question without reference to any patch name, because
    # the two checks above only fire on the built-in bank's names and an
    # imported one is exactly where this goes wrong unseen.
    warnings.extend(_crowding_warnings(fm_pitches))

    for bass_channel, bass_name in sorted(bass_channels.items()):
        bass_centre = _median(fm_pitches[bass_channel])
        for lead_channel, lead_name in sorted(lead_channels.items()):
            distance = _median(fm_pitches[lead_channel]) - bass_centre
            if abs(distance) < REGISTER_COLLISION_SEMITONES:
                warnings.append(
                    f"FM{lead_channel} ({lead_name!r}) and FM{bass_channel} "
                    f"({bass_name!r}) sit {abs(distance):.0f} semitones "
                    f"apart on average — closer than a fifth, so they mask "
                    f"each other instead of reading as two parts. Move the "
                    f"lead up an octave or the bass down one")

    return warnings


def _crowding_warnings(fm_pitches, chip: str = "FM",
                       whole: str = "six") -> List[str]:
    """Voices stacked into one narrow band mask each other.

    Six FM voices is the whole chip, so spending three or more of them
    inside one octave is most of the arrangement in a single band —
    a chord voiced almost in unison rather than three parts. It reads as
    one thick sound with nothing distinguishable in it, and every timing
    and register check above passes, because nothing is wrong with any
    individual channel.

    Deliberately about pitch, not patches: the checks above look the
    instrument up by name and so are blind on any imported bank.
    """
    warnings: List[str] = []
    centres = {channel: _median(pitches)
               for channel, pitches in sorted(fm_pitches.items())
               if pitches}
    if len(centres) < CROWDED_VOICES:
        return warnings

    # The widest group that still fits inside the span.
    ordered = sorted(centres.items(), key=lambda pair: pair[1])
    best = []
    for start in range(len(ordered)):
        group = [ordered[start]]
        for channel, centre in ordered[start + 1:]:
            if centre - ordered[start][1] <= CROWDED_SPAN_SEMITONES:
                group.append((channel, centre))
        if len(group) > len(best):
            best = group

    if len(best) < CROWDED_VOICES:
        return warnings
    names = ", ".join(f"FM{channel}" if isinstance(channel, int)
                      else str(channel) for channel, _ in best)
    span = best[-1][1] - best[0][1]
    voices = f"{chip} voices" if chip != "FM" else "FM voices"
    whole_chip = "" if chip == "FM" else \
        f" The chip has {whole} melodic voices in all."
    warnings.append(
        f"{len(best)} {voices} ({names}) sit within {span:.0f} semitones "
        f"of each other — inside one octave. That is not {len(best)} parts, "
        f"it is one chord voiced almost in unison, and it reads as a single "
        f"thick sound with nothing distinguishable in it.{whole_chip} "
        f"Spread them: bass an octave or two down, lead an octave up, and "
        f"let the middle hold two voices at most")
    return warnings


def check_sections(stats, minimum_range_db: float = SECTION_DYNAMIC_RANGE_DB):
    """Warn when marked sections all measure the same loudness.

    Takes profile.py's SegmentStats rather than events, because "is the
    breakdown actually quieter than the drop" is a question about rendered
    audio — the event list says what was written, not what came out. A
    score can name six sections and have every one of them measure within
    a decibel of the others, which means the structure exists on paper and
    nowhere else.
    """
    import math

    usable = [s for s in stats if s.rms > 0]
    if len(usable) < 2:
        return []

    loudest = max(usable, key=lambda s: s.rms)
    quietest = min(usable, key=lambda s: s.rms)
    spread = 20.0 * math.log10(loudest.rms / quietest.rms)
    if spread >= minimum_range_db:
        return []
    return [
        f"all {len(usable)} marked sections measure within {spread:.1f} dB "
        f"of each other ({quietest.label} at {quietest.rms:.4f}, "
        f"{loudest.label} at {loudest.rms:.4f}) — the arrangement names "
        f"sections but does not actually go anywhere. Drop parts out for a "
        f"breakdown, or add them for a chorus; naming a section does not "
        f"make it one"
    ]
