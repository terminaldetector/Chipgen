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
    score = ("bpm 150\nlpb 4\ninst fm0 bass\ncols fm0 noise\n\n" +
            "\n".join("A-2 w1" for _ in range(40)) + "\n===  ===\n")
    result = chipgen.compose(score)
    assert any("noise channel" in w for w in result.warnings), result.warnings
