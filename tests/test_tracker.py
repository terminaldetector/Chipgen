"""The tracker notation: parsing, error messages, and a stable round trip."""

import events as E
import tracker

SCORE = """\
; a two-bar groove
title Round Trip
author tests
bpm 172
lpb 4
inst fm0 bass
inst fm1 square_lead
pan fm1 C 0 3
lfo on 4
cols fm0 fm1 psg0 noise dac

A-2  ...  A-4    w1   kick
...  ...  C-5:4  ...  hat
C-3  A-4  E-5    w1   snare
...  ...  A-5:4  ...  hat
loop
G-2  ...  G-4    w1   kick
A#2  G-4  D-5    w1   snare
...  ===  ===    ===  hat
"""


def test_parses_directives_and_rows():
    events, meta = tracker.loads(SCORE)
    assert meta.bpm == 172 and meta.lpb == 4
    assert meta.title == "Round Trip" and meta.author == "tests"
    kinds = [type(e).__name__ for e in events]
    for expected in ("FMInstrumentSelect", "FMPan", "FMLFO", "FMNoteOn",
                     "PSGToneOn", "PSGNoiseOn", "DACSample", "LoopPoint",
                     "Wait", "End"):
        assert expected in kinds, f"{expected} never came out of the parser"


def test_sharp_in_a_note_is_not_a_comment():
    # `#` starts a comment, but `A#2` is a note. Getting this wrong makes
    # every score with a sharp in it fail with a column-count error.
    events, _ = tracker.loads("cols fm0\n\nA#2\n")
    notes = [e for e in events if isinstance(e, E.FMNoteOn)]
    assert len(notes) == 1 and notes[0].note == "A#"


def test_semicolon_comments_and_blank_lines_are_ignored():
    events, _ = tracker.loads("cols fm0\n\n; nothing here\nA-2  ; trailing\n\n")
    assert len([e for e in events if isinstance(e, E.FMNoteOn)]) == 1


def test_hold_versus_note_off():
    events, _ = tracker.loads("cols fm0\n\nA-2\n...\n===\n")
    kinds = [type(e).__name__ for e in events if not isinstance(e, E.Wait)]
    assert kinds == ["FMNoteOn", "FMNoteOff", "End"], \
        "... must hold the note; only === releases it"


def test_unterminated_notes_are_released_at_the_end():
    events, _ = tracker.loads("cols fm0 psg0 noise\n\nA-2 A-4 w1\n")
    tail = [type(e).__name__ for e in events[-4:]]
    assert tail == ["FMNoteOff", "PSGToneOff", "PSGNoiseOff", "End"], \
        "a score must not end on a stuck note"


def test_row_timing_matches_bpm():
    events, meta = tracker.loads("bpm 120\nlpb 4\ncols fm0\n\nA-2\nC-3\n")
    # 120 BPM, 4 rows per beat -> one row is an eighth of a second
    assert abs(meta.ticks_per_row() / meta.ticks_per_second - 0.125) < 1e-9
    assert abs(E.duration_seconds(events, meta.ticks_per_second) - 0.25) < 0.01


def test_errors_name_the_line():
    for text, needle in [("cols fm0 fm1\n\nA-2\n", "2 columns"),
                         ("cols fm0\n\nH-2\n", "not a note"),
                         ("cols noise\n\nz9\n", "noise cell"),
                         ("cols bogus\n", "unknown column")]:
        try:
            tracker.loads(text)
        except tracker.TrackerError as exc:
            assert needle in str(exc), f"{needle!r} missing from: {exc}"
            assert "line " in str(exc), "an error must say which line"
        else:
            raise AssertionError(f"{text!r} should not have parsed")


def test_dump_reaches_a_fixed_point():
    # The first dump can differ from the input (an implicit release becomes
    # an explicit row); from then on it must be stable, or every edit cycle
    # would grow the file.
    events, meta = tracker.loads(SCORE)
    first = tracker.dumps(events, meta)
    events2, meta2 = tracker.loads(first)
    second = tracker.dumps(events2, meta2)
    assert first == second, "dump/parse must converge after one pass"
    events3, _ = tracker.loads(second)
    assert [e.to_dict() for e in events2] == [e.to_dict() for e in events3]


def test_dump_keeps_mid_pattern_directives():
    text = tracker.dumps(*tracker.loads(SCORE))
    assert "loop" in text, "the loop marker must survive a dump"
    assert "pan fm1 C 0 3" in text
    assert "lfo on 4" in text
    assert "inst fm0 bass" in text
