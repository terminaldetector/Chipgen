"""`pattern` and `order` — writing form without writing every row."""


def _score(order="verse verse chorus", extra=""):
    return (
        "bpm 150\nlpb 4\ninst fm0 deep_bass\ncols fm0 dac\n"
        "\npattern verse\nD-2  kick\n...  ...\n"
        "\npattern chorus\nA-2  kick\n...  snare\n"
        f"\norder {order}\n{extra}end\n")


def test_the_order_decides_what_plays_and_how_often():
    import events as E
    import tracker

    events, _meta = tracker.loads(_score("verse verse chorus verse*2"))
    notes = [f"{e.note}-{e.octave}" for e in events if isinstance(e, E.FMNoteOn)]
    assert notes == ["D-2", "D-2", "A-2", "D-2", "D-2"], notes


def test_a_score_without_patterns_is_untouched():
    import tracker

    plain = "bpm 150\nlpb 4\ninst fm0 deep_bass\ncols fm0\nD-2\n...\nend\n"
    events, meta = tracker.loads(plain)
    assert events and meta.bpm == 150


def test_what_follows_the_order_still_comes_last():
    # `end` after the order belongs at the end. Sending everything after
    # the order line into the preamble put it BEFORE the music and
    # rendered six events of silence.
    import chipgen

    result = chipgen.compose(_score("verse chorus"))
    assert result.duration > 0.3, \
        f"the score rendered {result.duration:.2f}s — the tail ran first"


def test_a_pattern_replays_its_directives_each_time_it_is_used():
    # This is what lets a `mark` or an instrument change belong to a
    # section rather than to the whole piece.
    import events as E
    import tracker

    score = ("bpm 150\nlpb 4\ninst fm0 deep_bass\ncols fm0\n"
             "\npattern a\nmark here\nD-2\n"
             "\norder a a a\nend\n")
    events, _meta = tracker.loads(score)
    marks = [e for e in events if isinstance(e, E.Marker)]
    assert len(marks) == 3, f"{len(marks)} markers from three plays"


def test_line_numbers_survive_expansion():
    # A pattern used four times is still one piece of text the author
    # wrote, and an error in it has to point there.
    import tracker

    score = ("cols fm0\n\npattern a\nD-2\nNOPE\n\norder a a a a\nend\n")
    try:
        tracker.loads(score)
    except tracker.TrackerError as error:
        assert "line 5" in str(error), str(error)
    else:
        raise AssertionError("a bad cell inside a pattern was accepted")


def test_every_way_of_getting_it_wrong_says_what_is_wrong():
    import tracker

    cases = {
        "cols fm0\n\npattern a\nD-2\n\norder a b\n": "not defined",
        "cols fm0\n\npattern a\nD-2\n\npattern b\nA-2\n\norder a\n": "not in any",
        "cols fm0\n\npattern a\nD-2\n": "never plays them",
        "cols fm0\n\npattern a\nD-2\n\npattern a\nA-2\n\norder a\n": "already defined",
        "cols fm0\n\npattern\nD-2\n\norder a\n": "needs a name",
        "cols fm0\n\npattern a\nD-2\n\norder a*999\n": "the limit is",
        "cols fm0\n\npattern a\nD-2\n\norder\n": "at least one",
    }
    for score, wanted in cases.items():
        try:
            tracker.loads(score)
        except tracker.TrackerError as error:
            assert wanted in str(error), f"{score!r}: {error}"
        else:
            raise AssertionError(f"{score!r} was accepted")


def test_an_unplayed_pattern_is_an_error_not_a_shrug():
    # The symptom otherwise is a section missing from the render while
    # everything else works, which is the failure mode this project keeps
    # running into.
    import tracker

    score = ("cols fm0\n\npattern used\nD-2\n\npattern forgotten\nA-2\n"
             "\norder used\nend\n")
    try:
        tracker.loads(score)
    except tracker.TrackerError as error:
        assert "forgotten" in str(error)
    else:
        raise AssertionError("a defined-but-unplayed pattern was accepted")


def test_the_form_reaches_the_audio():
    # Four pattern definitions and one order line should produce a piece
    # whose sections a level meter can tell apart.
    import chipgen

    score = (
        "bpm 150\nlpb 4\ninst fm0 deep_bass\ninst fm1 square_lead\n"
        "cols fm0 fm1 dac\n"
        "\npattern loud\nmark loud\nD-2:110 D-5:90 kick\n...     ...    kick\n"
        "\npattern quiet\nmark quiet\nD-2:20  ===    ...\n...     ...    ...\n"
        "\norder loud loud quiet loud\nend\n")
    result = chipgen.compose(score)
    stats = result.profile()
    names = [s.label for s in stats]
    assert names == ["loud", "loud", "quiet", "loud"], names
    quiet = next(s for s in stats if s.label == "quiet")
    loud = next(s for s in stats if s.label == "loud")
    assert quiet.rms < loud.rms * 0.5, \
        f"the quiet section measured {quiet.rms:.3f} against {loud.rms:.3f}"
