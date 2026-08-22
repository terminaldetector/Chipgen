"""sanity.py: catches an arrangement that will render clean and sound wrong."""

import sanity
from events import (DACEnable, DACSample, End, FMInstrumentSelect, FMNoteOff,
                    FMNoteOn, FMPan, PSGNoiseOff, PSGNoiseOn, PSGToneOff,
                    PSGToneOn, Wait)


def _reconstructed_gpt_pattern():
    """Shape of the real track this module was written to catch: noise
    gated on and never off, DAC enabled once and never disabled, no pan."""
    events = [FMInstrumentSelect(channel=0, instrument="bass"),
              FMNoteOn(channel=0, note="A", octave=2),
              PSGNoiseOn(white=True, rate=1, volume=4),
              DACSample(name="kick")]
    for _ in range(150):                          # ~6.25s at 192 ticks/s
        events += [DACSample(name="hat"), Wait(ticks=8)]
    events += [FMNoteOff(channel=0), PSGNoiseOff(), End()]
    return events


def test_catches_a_channel_that_never_gates_off():
    warnings = sanity.check(_reconstructed_gpt_pattern(), 192.0)
    joined = " | ".join(warnings)
    assert "FM0" in joined and "drone" in joined
    assert "noise channel" in joined and "not intermittent" in joined
    assert "DAC" in joined and "%" in joined
    assert "FMPan" in joined


def test_does_not_flag_a_pad_re_struck_every_bar():
    # Zero release time between retriggers is the same raw duty cycle as a
    # drone, but it is a normal held-pad arrangement, not a mistake — this
    # is what the first version of the check got wrong on this project's
    # own demo tracks.
    events = [FMInstrumentSelect(channel=2, instrument="strings")]
    for bar in range(8):
        events.append(FMNoteOn(channel=2, note="A", octave=3))
        events.append(Wait(ticks=192))
        events.append(FMNoteOff(channel=2))
        events.append(FMNoteOn(channel=2, note="A", octave=3))  # re-struck immediately
    events += [Wait(ticks=192), FMNoteOff(channel=2), End()]
    warnings = sanity.check(events, 192.0)
    assert not any("FM2" in w for w in warnings), warnings


def test_short_hits_do_not_trip_the_dac_check():
    # Long enough to clear MIN_TRACK_SECONDS, with real silence between
    # hits — genuinely different from the continuous-stream case, not just
    # short enough to dodge the length floor.
    events = [FMInstrumentSelect(channel=0, instrument="bass")]
    for _ in range(20):
        events += [DACSample(name="kick"), Wait(ticks=96)]  # ~14.4s total
    events.append(End())
    warnings = sanity.check(events, 192.0)
    assert not any("DAC" in w for w in warnings), warnings


def test_very_short_tracks_are_not_judged_at_all():
    # A percentage is a bad statistic on a fraction of a second: the
    # built-in EXAMPLE is 0.8s and its DAC legitimately never gets a gap
    # (kick is 220ms, the beat is faster than that), which is ordinary
    # drum programming, not a wall of noise.
    events = [FMInstrumentSelect(channel=0, instrument="bass"),
              PSGNoiseOn(white=True, rate=1, volume=4),
              DACSample(name="kick"), Wait(ticks=20),
              DACSample(name="hat"), Wait(ticks=20), End()]
    assert sanity.check(events, 192.0) == []


def test_panning_silences_the_stereo_warning():
    events = [FMInstrumentSelect(channel=0, instrument="bass"),
              FMPan(channel=0, left=True, right=False)]
    for _ in range(60):
        events += [FMNoteOn(channel=0, note="A", octave=2), Wait(ticks=12),
                   FMNoteOff(channel=0), Wait(ticks=12)]
    events.append(End())
    warnings = sanity.check(events, 192.0)
    assert not any("FMPan" in w for w in warnings), warnings


def test_empty_and_trivial_input_does_not_crash():
    assert sanity.check([End()], 192.0) == []
    assert sanity.check([], 192.0) == []


def test_compose_surfaces_sanity_warnings():
    import chipgen
    # 100 rows @ 150 BPM / lpb 4 is ~9.5s — comfortably past MIN_TRACK_SECONDS,
    # so the check actually runs rather than being skipped as too short.
    score = ("bpm 150\nlpb 4\ninst fm0 bass\ncols fm0 noise\n\n" +
            "\n".join("A-2 w1" for _ in range(100)) + "\n===  ===\n")
    result = chipgen.compose(score)
    assert any("noise channel" in w for w in result.warnings), result.warnings


def test_dac_measures_actual_sample_duration_not_event_gaps():
    # Two short samples fired back to back, closer together than either
    # one's own length, is what a fast drum pattern with this project's
    # ~200ms built-in kit legitimately looks like — the first version of
    # this check measured only explicit enable/disable events, which
    # nobody writes by hand, so it saw "one enable, no disable" and called
    # any drum-heavy track a continuous noise bed.
    import samples

    kick_seconds = samples.KIT["kick"].duration
    events = [FMInstrumentSelect(channel=0, instrument="bass")]
    gap_ticks = int(round((kick_seconds * 2.5) * 192))   # real silence each hit
    for _ in range(30):
        events += [DACSample(name="kick"), Wait(ticks=gap_ticks)]
    events.append(End())
    warnings = sanity.check(events, 192.0)
    assert not any("DAC" in w for w in warnings), warnings


def test_dac_still_catches_back_to_back_streaming_with_no_real_gap():
    import samples

    hat_seconds = samples.KIT["hat"].duration
    events = [FMInstrumentSelect(channel=0, instrument="bass")]
    tight_ticks = max(1, int(round((hat_seconds * 0.3) * 192)))  # retriggers mid-sample
    for _ in range(400):
        events += [DACSample(name="hat"), Wait(ticks=tight_ticks)]
    events.append(End())
    warnings = sanity.check(events, 192.0)
    assert any("DAC" in w for w in warnings), \
        "back-to-back retriggering with no real gap should still be flagged"


def test_acceptance_prompt_scenario_produces_no_warnings():
    # The exact shape bridge/PROMPT.md's acceptance-test prompt asks a
    # model to build: gated noise, gapped DAC hits, some panning, more
    # than one FM/PSG channel in use, 30+ seconds long. If this ever
    # starts warning, the prompt's own claim ("the output will be clean")
    # stops being true and needs rewriting alongside the fix.
    bpm, tps = 150.0, 192.0

    def ticks(beats):
        return max(1, round(beats * 60.0 / bpm * tps))

    events = [FMInstrumentSelect(channel=0, instrument="bass"),
              FMInstrumentSelect(channel=1, instrument="distorted_lead"),
              FMInstrumentSelect(channel=2, instrument="strings"),
              FMPan(channel=1, left=True, right=False),
              FMPan(channel=2, left=False, right=True)]
    for bar in range(22):
        events.append(FMNoteOn(channel=2, note="A", octave=3))
        for step in range(16):
            events.append(FMNoteOn(channel=0, note="A", octave=2))
            if step % 4 == 0:
                events.append(FMNoteOn(channel=1, note="E", octave=4))
            events.append(PSGToneOn(channel=0, note="A", octave=5, volume=4))
            events.append(PSGToneOn(channel=1, note="E", octave=5, volume=6))
            if step % 4 == 2:
                events.append(PSGNoiseOn(white=True, rate=1, volume=6))
            if step % 8 == 0:
                events.append(DACSample(name="kick"))
            elif step % 8 == 4:
                events.append(DACSample(name="snare"))
            events.append(Wait(ticks=ticks(0.25)))
            events.append(FMNoteOff(channel=0))
            if step % 4 == 0:
                events.append(FMNoteOff(channel=1))
            events.append(PSGToneOff(channel=0))
            events.append(PSGToneOff(channel=1))
            if step % 4 == 2:
                events.append(PSGNoiseOff())
        events.append(FMNoteOff(channel=2))
    events.append(End())

    assert sanity.check(events, tps) == []


# --------------------------------------------------------------------------
# Register and dynamics
# --------------------------------------------------------------------------
def _two_part(bass_octave, lead_octave, bass="deep_bass", lead="saw_lead", n=90):
    events = [FMInstrumentSelect(channel=0, instrument=bass),
              FMInstrumentSelect(channel=1, instrument=lead)]
    for _ in range(n):
        events += [FMNoteOn(channel=0, note="A", octave=bass_octave),
                   FMNoteOn(channel=1, note="E", octave=lead_octave),
                   Wait(ticks=24), FMNoteOff(channel=0), FMNoteOff(channel=1)]
    events.append(End())
    return events


def _only(warnings, needle):
    return [w for w in warnings if needle in w]


def test_flags_a_bass_patch_that_never_plays_low():
    warnings = sanity.check(_two_part(4, 5), 192.0)
    assert _only(warnings, "never goes below"), warnings


def test_does_not_flag_a_bass_that_uses_its_register():
    warnings = sanity.check(_two_part(1, 5), 192.0)
    assert not _only(warnings, "never goes below"), warnings


def test_flags_lead_and_bass_in_the_same_register():
    warnings = sanity.check(_two_part(2, 2), 192.0)
    assert _only(warnings, "semitones apart"), warnings


def test_does_not_flag_parts_that_are_properly_separated():
    warnings = sanity.check(_two_part(1, 5), 192.0)
    assert not _only(warnings, "semitones apart"), warnings


def test_register_checks_respect_the_assigned_instrument():
    # A lead patch played low is a choice, not a mistake — only patches
    # that promise to be a bass get held to the bass register.
    warnings = sanity.check(_two_part(4, 5, bass="saw_lead", lead="square_lead"),
                            192.0)
    assert not _only(warnings, "never goes below"), warnings


def test_flat_sections_are_reported():
    import profile as prof

    class Stat:
        def __init__(self, label, rms):
            self.label, self.rms, self.peak = label, rms, rms * 3
            self.start = self.end = 0.0

    flat = [Stat("intro", 0.29), Stat("drop", 0.30), Stat("breakdown", 0.28)]
    assert sanity.check_sections(flat), "sections within 0.7 dB should warn"

    dynamic = [Stat("intro", 0.05), Stat("drop", 0.30), Stat("breakdown", 0.02)]
    assert not sanity.check_sections(dynamic), "a real breakdown should not warn"

    assert sanity.check_sections([Stat("only", 0.2)]) == [], \
        "one section cannot have a dynamic range"
    assert sanity.check_sections([]) == []


def test_flags_an_fm5_part_the_dac_deletes():
    # The trap: a bassline written on FM5 with drums on the DAC column.
    # It renders, it looks right in the score, and channel 6 is handed to
    # the DAC for most of it — so most of those notes never reach the
    # output pins at all. Measured, not assumed: on a YM3438 an enabled
    # DAC takes a held FM5 note to exactly 0.0 RMS.
    events = [FMInstrumentSelect(channel=5, instrument="bass")]
    for _ in range(30):                     # ~15s at 192 ticks/s
        events += [DACSample(name="kick"),  # 220ms of sample...
                   FMNoteOn(channel=5, note="A", octave=2),
                   Wait(ticks=96),          # ...under a 500ms note
                   FMNoteOff(channel=5)]
    events.append(End())
    warnings = sanity.check(events, 192.0)
    joined = " | ".join(warnings)
    assert "FM5" in joined and "0x2B" in joined, warnings


def test_fm5_and_dac_that_stay_out_of_each_other_s_way_are_fine():
    # Same two parts, alternating instead of stacked: the FM5 note plays
    # in the gap after the drum has finished. That is ordinary Genesis
    # arrangement and must not warn.
    events = [FMInstrumentSelect(channel=5, instrument="bass")]
    for _ in range(30):
        events += [DACSample(name="hat"),   # 45ms
                   Wait(ticks=48),          # 250ms — sample is long done
                   FMNoteOn(channel=5, note="A", octave=2),
                   Wait(ticks=48),
                   FMNoteOff(channel=5)]
    events.append(End())
    warnings = sanity.check(events, 192.0)
    assert not any("0x2B" in w for w in warnings), warnings


def test_fm5_check_needs_both_parts_present():
    # Drums alone, or an FM5 part alone, is not a collision.
    drums = [DACSample(name="kick")]
    for _ in range(30):
        drums += [DACSample(name="kick"), Wait(ticks=96)]
    drums.append(End())
    assert not any("0x2B" in w for w in sanity.check(drums, 192.0))

    fm_only = [FMInstrumentSelect(channel=5, instrument="bass")]
    for _ in range(30):
        fm_only += [FMNoteOn(channel=5, note="A", octave=2), Wait(ticks=96),
                    FMNoteOff(channel=5)]
    fm_only.append(End())
    assert not any("0x2B" in w for w in sanity.check(fm_only, 192.0))


def test_overlap_sweep_handles_the_awkward_shapes():
    # One long span covering several short ones, a span straddling a gap,
    # empty input, and spans that only touch at an endpoint.
    assert sanity._overlap_seconds([(0, 10)], [(2, 3), (4, 5), (20, 30)]) == 2.0
    assert sanity._overlap_seconds([(0, 1), (2, 3)], [(0.5, 2.5)]) == 1.0
    assert sanity._overlap_seconds([], [(0, 5)]) == 0.0
    assert sanity._overlap_seconds([(0, 5)], [(5, 10)]) == 0.0
