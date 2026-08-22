"""profile.py: the audio-domain verification sanity.py structurally cannot do."""

import profile as prof
import support
from events import (End, FMInstrumentSelect, FMNoteOff, FMNoteOn, Marker, Wait)
from sequencer import Sequencer


def _drop_breakdown_drop(leak: bool):
    """A loud section, a section meant to be near-silent, a loud section.
    `leak=True` reproduces the exact bug this module exists to catch: the
    breakdown never gets an explicit note-off, so the last drop note rings
    through the whole "quiet" section."""
    ticks = 48   # a beat at 240 ticks/s
    events = [FMInstrumentSelect(channel=0, instrument="metal_stab"),
              Marker(label="drop")]
    for _ in range(4):
        events += [FMNoteOn(channel=0, note="A", octave=2), Wait(ticks=ticks),
                   FMNoteOff(channel=0)]
    if leak:
        events.append(FMNoteOn(channel=0, note="A", octave=2))   # never released
    events.append(Marker(label="breakdown"))
    for _ in range(4):
        events.append(Wait(ticks=ticks))
    if leak:
        events.append(FMNoteOff(channel=0))
    events.append(Marker(label="drop2"))
    for _ in range(4):
        events += [FMNoteOn(channel=0, note="A", octave=2), Wait(ticks=ticks),
                   FMNoteOff(channel=0)]
    events.append(End())
    return events


def test_segments_from_markers_needs_at_least_two():
    assert prof.segments_from_markers([Marker(label="only one"), End()], 192.0) == []
    assert prof.segments_from_markers([End()], 192.0) == []


def test_segments_from_markers_spans_to_end():
    events = [Marker(label="a"), Wait(ticks=96), Marker(label="b"),
             Wait(ticks=96), End()]
    segments = prof.segments_from_markers(events, 192.0)
    assert [s.label for s in segments] == ["a", "b"]
    assert segments[0].start == 0.0 and segments[0].end == 0.5
    assert segments[1].start == 0.5 and segments[1].end == 1.0


def test_bar_segments_covers_the_whole_duration():
    segments = prof.bar_segments(10.0, bpm=120.0, beats_per_bar=4)
    assert segments[0].start == 0.0
    assert abs(segments[-1].end - 10.0) < 1e-9
    # no gaps, no overlaps
    for a, b in zip(segments, segments[1:]):
        assert abs(a.end - b.start) < 1e-9


def test_catches_a_note_that_leaked_into_a_quiet_section():
    # This is the exact bug a real session found by hand-rolling numpy:
    # sanity.py's whole-track view cleared it (the channel has plenty of
    # retriggers elsewhere), and it only shows up in the rendered audio.
    seq = Sequencer()
    leaked = seq.render(_drop_breakdown_drop(leak=True))
    fixed = seq.render(_drop_breakdown_drop(leak=False))

    events = _drop_breakdown_drop(leak=True)
    segments = prof.segments_from_markers(events, 192.0)
    leaked_stats = {s.label: s for s in prof.profile(leaked, seq.target_rate, segments)}

    events2 = _drop_breakdown_drop(leak=False)
    segments2 = prof.segments_from_markers(events2, 192.0)
    fixed_stats = {s.label: s for s in prof.profile(fixed, seq.target_rate, segments2)}

    drop_rms = leaked_stats["drop"].rms
    assert leaked_stats["breakdown"].rms > drop_rms * 0.3, \
        "the leaked note should make the breakdown measure loud, not quiet"
    assert fixed_stats["breakdown"].rms < drop_rms * 0.1, \
        "with a real off, the breakdown should measure near-silent"
    assert fixed_stats["breakdown"].rms < leaked_stats["breakdown"].rms / 3, \
        "fixing the leak should be visible as a much quieter breakdown"


def test_auto_profile_prefers_markers_over_bars():
    events = _drop_breakdown_drop(leak=False)
    seq = Sequencer()
    buf = seq.render(events)
    stats = prof.auto_profile(buf, seq.target_rate, events, 192.0, bpm=999.0)
    assert [s.label for s in stats] == ["drop", "breakdown", "drop2"], \
        "markers should win even when a (deliberately wrong) bpm is also given"


def test_auto_profile_falls_back_to_bars_without_markers():
    events = [FMInstrumentSelect(channel=0, instrument="bass")]
    for _ in range(8):
        events += [FMNoteOn(channel=0, note="A", octave=2), Wait(ticks=48),
                   FMNoteOff(channel=0), Wait(ticks=48)]
    events.append(End())
    seq = Sequencer()
    buf = seq.render(events)
    assert prof.auto_profile(buf, seq.target_rate, events, 192.0, bpm=None) == []
    stats = prof.auto_profile(buf, seq.target_rate, events, 192.0, bpm=120.0)
    assert len(stats) > 0
    assert stats[0].label == "0"


def test_format_table_handles_empty_input():
    assert prof.format_table([]) == "(no segments)"


def test_tracker_mark_directive_round_trips():
    import tracker
    text = ("bpm 150\nlpb 4\ninst fm0 bass\ncols fm0\n\n"
           "mark intro\nA-2\n...\nmark drop\nC-3\n...\n===\n")
    events, meta = tracker.loads(text)
    labels = [e.label for e in events if isinstance(e, Marker)]
    assert labels == ["intro", "drop"]

    first = tracker.dumps(events, meta)
    events2, meta2 = tracker.loads(first)
    assert tracker.dumps(events2, meta2) == first, "mark must reach a fixed point too"
    assert "mark intro" in first and "mark drop" in first


def test_chipgen_result_profile_uses_score_bpm():
    import chipgen
    score = ("bpm 200\nlpb 4\ninst fm0 bass\ncols fm0\n\n"
            "mark a\nA-2\n...\nmark b\nC-3\n...\n===\n")
    result = chipgen.compose(score)
    stats = result.profile()
    assert [s.label for s in stats] == ["a", "b"]
