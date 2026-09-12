"""The effect column — per-cell effects, and whether they reach the audio."""

import math


def _render(cell, patch="saw_lead"):
    import chipgen
    score = (f"bpm 120\nlpb 4\ninst fm0 {patch}\ncols fm0\n"
             f"{cell}\n...\n...\n...\nend\n")
    return chipgen.compose(score)


def _pitch_spread(result, windows=20):
    """Peak-to-peak pitch movement across the render, in cents.

    Twenty short windows, not four long ones. A 16 Hz vibrato completes
    eight cycles inside a half-second window, so the average pitch there
    is the centre and the swing cancels: measured that way, a vibrato of
    64 cents reported 0.2. The window has to be shorter than the effect.
    """
    import analysis
    mono = list(analysis.to_mono(result.audio))
    width = len(mono) // windows
    found = []
    for i in range(windows):
        value = analysis.fundamental(mono[i * width:(i + 1) * width],
                                     result.sample_rate)
        if value:
            found.append(value)
    if len(found) < 3:
        return 0.0
    return 1200.0 * math.log2(max(found) / min(found))


def test_a_cell_carries_its_own_effects():
    import events as E
    import tracker

    events, _meta = tracker.loads(
        "cols fm0 fm1\nA-2:100/1F0  C-5/455\nend\n")
    slide = [e for e in events if isinstance(e, E.Portamento)]
    vibrato = [e for e in events if isinstance(e, E.Vibrato)]
    assert len(slide) == 1 and slide[0].target == "fm0"
    assert len(vibrato) == 1 and vibrato[0].target == "fm1"
    # The note keeps its velocity: the effect suffix must not eat it.
    ons = [e for e in events if isinstance(e, E.FMNoteOn)]
    assert ons[0].velocity == 100


def test_effects_come_after_the_note_they_belong_to():
    # note_on restarts the modulators. An effect emitted before the note
    # would be wiped by it and the cell would do nothing.
    import events as E
    import tracker

    events, _meta = tracker.loads("cols fm0\nC-4/455\nend\n")
    order = [type(e).__name__ for e in events]
    assert order.index("FMNoteOn") < order.index("Vibrato")


def test_effects_work_on_a_held_note_and_on_a_note_off():
    import events as E
    import tracker

    events, _meta = tracker.loads(
        "cols fm0\nC-4\n.../4A3\n===/A0C\nend\n")
    assert any(isinstance(e, E.Vibrato) for e in events), "hold cell lost its effect"
    assert any(isinstance(e, E.VolumeSlide) for e in events), "off cell lost its effect"


def test_several_effects_stack_on_one_cell():
    import events as E
    import tracker

    events, _meta = tracker.loads("cols fm0\nC-4/3A0/4C4\nend\n")
    assert any(isinstance(e, E.Portamento) for e in events)
    assert any(isinstance(e, E.Vibrato) for e in events)


def test_vibrato_is_calibrated_against_the_corpus():
    # 455 is speed 5, depth 5. The transcribed corpus puts the median
    # vibrato at 34 cents and 6 Hz across 4,839 spans, so this code has to
    # land there or the scale is pointing somewhere the music is not.
    import fx

    events = fx.to_events("fm0", "455")
    assert len(events) == 1
    assert events[0].speed_hz == 6.0
    assert events[0].depth_cents == 40.0


def test_an_effect_actually_bends_the_audio():
    flat = _pitch_spread(_render("A-4"))
    vibrato = _pitch_spread(_render("A-4/455"))
    assert flat < 5.0, f"a plain note moved {flat:.1f} cents"
    assert vibrato > 40.0, f"vibrato 455 only moved {vibrato:.1f} cents"


def test_slides_go_the_direction_they_say():
    import analysis
    import math as _math

    def drift(cell):
        result = _render(cell)
        mono = list(analysis.to_mono(result.audio))
        third = len(mono) // 3
        first = analysis.fundamental(mono[:third], result.sample_rate)
        last = analysis.fundamental(mono[-third:], result.sample_rate)
        if not first or not last:
            return 0.0
        return 1200.0 * _math.log2(last / first)

    assert drift("A-4/118") > 30.0, "1xx did not slide up"
    assert drift("A-4/218") < -30.0, "2xx did not slide down"


def test_a_volume_slide_fades_the_note():
    import analysis

    result = _render("A-4/A0C")
    mono = list(analysis.to_mono(result.audio))
    quarter = len(mono) // 4
    head = max(abs(v) for v in mono[:quarter])
    tail = max(abs(v) for v in mono[-quarter:])
    assert tail < head * 0.6, f"A0C faded {head:.3f} to only {tail:.3f}"


def test_an_unimplemented_effect_says_so_instead_of_doing_nothing():
    # A cell that parses and silently does nothing is the worst outcome:
    # the score looks right, the render succeeds, the effect is absent.
    import tracker

    for cell in ("A-4/047", "A-4/C06"):
        try:
            tracker.loads(f"cols fm0\n{cell}\nend\n")
        except tracker.TrackerError as error:
            assert "not implemented" in str(error)
        else:
            raise AssertionError(f"{cell} was silently accepted")


def test_a_malformed_effect_names_the_cell():
    import tracker

    for cell, wrong in (("A-4/1400", "three characters"),
                        ("A-4/9F0", "not an"),
                        ("A-4/1GG", "non-hex")):
        try:
            tracker.loads(f"cols fm0\n{cell}\nend\n")
        except tracker.TrackerError as error:
            assert wrong in str(error), f"{cell}: {error}"
        else:
            raise AssertionError(f"{cell} was accepted")


def test_pan_is_refused_on_a_voice_that_cannot_pan():
    import tracker

    try:
        tracker.loads("cols psg0\nA-4/880\nend\n")
    except tracker.TrackerError as error:
        assert "pan" in str(error)
    else:
        raise AssertionError("8xx was accepted on a PSG channel")
