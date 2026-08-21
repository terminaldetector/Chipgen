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

from typing import Dict, List

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


def check(events: List[events_mod.Event],
          ticks_per_second: float = 192.0) -> List[str]:
    """Return human-readable warnings about the event list's arrangement.

    Does not render anything — this is pure bookkeeping over the event
    stream, so it costs microseconds even on a long track.
    """
    warnings: List[str] = []
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
    any_pan = False
    any_dac_sample = False

    def close_dac_span(at):
        nonlocal dac_on_time, dac_span_start, dac_span_end
        if dac_span_start is not None:
            dac_on_time += max(0.0, min(dac_span_end, at) - dac_span_start)
        dac_span_start = dac_span_end = None

    for event in events:
        E = events_mod
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
        elif isinstance(event, E.FMNoteOff):
            start = fm_on_since.pop(event.channel, None)
            if start is not None:
                fm_on_time[event.channel] += clock - start
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

    if len(events) > 200 and not any_pan:
        warnings.append(
            "no FMPan event anywhere — every FM channel defaults to "
            "centre, so the mix has no stereo width at all. Not wrong, "
            "just worth knowing: PSG is mono-summed regardless, but "
            "spreading FM channels with FMPan is free separation")

    return warnings
