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


# --------------------------------------------------------------------------
# chord / arp shorthand
# --------------------------------------------------------------------------
def test_chord_expands_across_the_named_channels():
    events, _ = tracker.loads(
        "bpm 150\nlpb 4\ncols fm0\n\nchord A-3 min fm2 fm3 fm4\nA-2\n===\n")
    played = [(e.channel, e.note, e.octave) for e in events
              if isinstance(e, E.FMNoteOn) and e.channel in (2, 3, 4)]
    assert played == [(2, "A", 3), (3, "C", 4), (4, "E", 4)], played


def test_chord_octave_wraps_correctly():
    # B minor from B-3 must put the third and fifth in the NEXT octave.
    events, _ = tracker.loads(
        "cols fm0\n\nchord B-3 min fm1 fm2 fm3\nA-2\n===\n")
    played = [(e.note, e.octave) for e in events
              if isinstance(e, E.FMNoteOn) and e.channel in (1, 2, 3)]
    assert played == [("B", 3), ("D", 4), ("F#", 4)], played


def test_chord_extends_upward_when_given_extra_channels():
    events, _ = tracker.loads(
        "cols fm0\n\nchord C-3 maj fm1 fm2 fm3 fm4 fm5\nA-2\n===\n")
    played = [(e.note, e.octave) for e in events
              if isinstance(e, E.FMNoteOn) and e.channel in (1, 2, 3, 4, 5)]
    assert played == [("C", 3), ("E", 3), ("G", 3), ("C", 4), ("E", 4)], played


def test_chord_off_releases_every_named_channel():
    events, _ = tracker.loads(
        "cols fm0\n\nchord A-3 min fm2 fm3\nA-2\nchord off fm2 fm3\n===\n")
    released = [e.channel for e in events if isinstance(e, E.FMNoteOff)]
    assert 2 in released and 3 in released


def test_chord_aliases_and_bad_quality():
    events, _ = tracker.loads("cols fm0\n\nchord A-3 m7 fm1 fm2 fm3 fm4\nA-2\n===\n")
    played = [(e.note, e.octave) for e in events
              if isinstance(e, E.FMNoteOn) and e.channel in (1, 2, 3, 4)]
    assert played == [("A", 3), ("C", 4), ("E", 4), ("G", 4)], played

    try:
        tracker.loads("cols fm0\n\nchord A-3 wat fm1\nA-2\n===\n")
    except tracker.TrackerError as exc:
        assert "unknown chord quality" in str(exc)
    else:
        raise AssertionError("an unknown quality should be an error")


def test_arp_subdivides_the_row_with_pitch_not_retriggers():
    events, meta = tracker.loads(
        "bpm 150\nlpb 4\ninst fm0 bell_pluck\ncols fm0\n\n"
        "arp fm0 0 3 7\nA-4\n...\narp fm0 off\n===\n")
    cents = [e.cents for e in events if isinstance(e, E.FMPitch)]
    assert cents[:6] == [0.0, 300.0, 700.0, 0.0, 300.0, 700.0], cents
    # one note-on for two arpeggiated rows: the pitch moves, the note does not
    assert len([e for e in events if isinstance(e, E.FMNoteOn)]) == 1
    assert cents[-1] == 0.0, "arp off must return the channel to its own pitch"


def test_arp_preserves_row_duration_exactly():
    import events as events_mod
    plain, meta = tracker.loads(
        "bpm 150\nlpb 4\ninst fm0 bell_pluck\ncols fm0\n\nA-4\n...\n...\n===\n")
    arped, _ = tracker.loads(
        "bpm 150\nlpb 4\ninst fm0 bell_pluck\ncols fm0\n\n"
        "arp fm0 0 3 7\nA-4\n...\n...\n===\n")
    assert events_mod.total_ticks(plain) == events_mod.total_ticks(arped), \
        "arpeggiating a row must not change how long it lasts"


def test_arp_skips_subdivision_when_the_row_is_too_short():
    # Sub-tick rows cannot be split three ways; holding is better than
    # emitting a run of zero-length waits.
    events, _ = tracker.loads(
        "bpm 240\nlpb 16\ninst fm0 bell_pluck\ncols fm0\n\n"
        "arp fm0 0 3 7\nA-4\n===\n")
    waits = [e.ticks for e in events if isinstance(e, E.Wait)]
    assert all(w > 0 for w in waits), waits


def test_dac_cells_carry_a_volume_and_round_trip():
    # Every other column has a `:level` suffix; the DAC column used to be
    # the one place a score could not say "this hit is quieter", which
    # made an intro with soft hats impossible to write in notation.
    events, meta = tracker.loads(
        "bpm 150\nlpb 4\ncols dac\nkick\nhat:0.4\nsnare:0.75\n...\n")
    hits = [(e.name, e.volume) for e in events
            if isinstance(e, E.DACSample)]
    assert hits == [("kick", 1.0), ("hat", 0.4), ("snare", 0.75)]

    again, _ = tracker.loads(tracker.dumps(events))
    assert [(e.name, e.volume) for e in again
            if isinstance(e, E.DACSample)] == hits


def test_a_bad_dac_volume_is_reported_with_its_line():
    for bad in ("hat:loud", "hat:1.5", "hat:-0.2"):
        try:
            tracker.loads(f"cols dac\n{bad}\n")
        except tracker.TrackerError as exc:
            assert "line 2" in str(exc), (bad, str(exc))
        else:
            raise AssertionError(f"{bad!r} should not have parsed")


def test_dumps_subdivides_the_grid_to_keep_arpeggios():
    # `arp` puts FMPitch events INSIDE a row. Dumping those onto the row
    # grid they were subdividing snapped every one to a row boundary, so
    # the text that came back played different notes at different times
    # while every event still round-tripped by count — the failure was
    # invisible to any check that counted events instead of listening.
    #
    # 120 BPM at lpb 4 and 192 ticks/s gives 24 ticks per row, so a
    # 3-step arp needs a grid three times finer.
    src = ("bpm 120\nlpb 4\ninst fm0 square_lead\ncols fm0\n"
           "arp fm0 0 4 7\nA-4\nC-5\nE-5\n===\n")
    events, meta = tracker.loads(src)
    assert meta.ticks_per_row() == 24

    text = tracker.dumps(events, meta)
    assert "lpb 12" in text, text.split("\n")[:6]

    # Every pitch change keeps the offset it was written at.
    def pitch_times(evs):
        tick, out = 0, []
        for e in evs:
            if isinstance(e, E.Wait):
                tick += e.ticks
            elif isinstance(e, E.FMPitch):
                out.append((tick, round(e.cents)))
        return out

    again, _ = tracker.loads(text)
    assert pitch_times(again) == pitch_times(events)


def test_a_prime_row_length_cannot_be_subdivided():
    # The honest other half: 150 BPM at lpb 4 and 192 ticks/s rounds to 19
    # ticks per row, and 19 is prime. No integer grid holds a 3-step arp
    # there, so dumps() leaves the rate alone rather than pretending. The
    # fix belongs in the score (`ticks 240` gives a row of 24) and the
    # module docstring says so.
    src = ("bpm 150\nlpb 4\ninst fm0 square_lead\ncols fm0\n"
           "arp fm0 0 4 7\nA-4\nC-5\n===\n")
    events, meta = tracker.loads(src)
    assert meta.ticks_per_row() == 19
    assert tracker._grid_refinement(events, 19) == 1
    assert "lpb 4" in tracker.dumps(events, meta)


def test_refinement_leaves_an_ordinary_score_alone():
    # No off-grid events, so no reason to touch the row rate — a plain
    # score must dump at the lpb it was written at.
    src = ("bpm 120\nlpb 4\ninst fm0 square_lead\ncols fm0\n"
           "A-4\n...\nC-5\n===\n")
    events, meta = tracker.loads(src)
    assert tracker._grid_refinement(events, meta.ticks_per_row()) == 1
    assert "lpb 4" in tracker.dumps(events, meta)
