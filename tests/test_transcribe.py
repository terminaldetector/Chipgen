"""vgm_transcribe.py: reading music back out of a register log.

The strongest check available is a closed loop — build a score here, let
chipgen render it to a VGM, transcribe that VGM, and see whether the notes
come back. Anything that survives that is the transcriber agreeing with
the engine rather than with itself.
"""

import math
import os

import events as E
import support
import tracker
import vgm_transcribe as vt


def test_frequencies_map_to_the_right_notes():
    for frequency, name, octave in ((440.0, "A", 4), (523.25, "C", 5),
                                    (110.0, "A", 2), (1318.51, "E", 6)):
        got_name, got_octave, cents = vt.frequency_to_note(frequency)
        assert (got_name, got_octave) == (name, octave), (frequency, got_name,
                                                          got_octave)
        assert abs(cents) < 1.0, cents


def test_a_bent_note_reports_how_far_it_was_bent():
    # A transcription that silently rounds looks more certain than it is,
    # and heavy pitch bending is exactly what that hides. Deliberately not
    # testing the exact halfway point: that is a coin flip between two
    # neighbours, and asserting which way it lands would be testing
    # Python's rounding rule rather than this function.
    name, octave, cents = vt.frequency_to_note(440.0 * 2 ** (0.4 / 12))
    assert (name, octave) == ("A", 4)
    assert 35 < cents < 45, cents

    name, octave, cents = vt.frequency_to_note(440.0 * 2 ** (-0.4 / 12))
    assert (name, octave) == ("A", 4)
    assert -45 < cents < -35, cents


def test_the_grid_is_found_when_there_is_one():
    row = 0.125
    notes = [vt.Note(row * i, "fm0", "on", "A", 4) for i in range(64)]
    found, bpm, lpb, fit = vt.infer_grid(notes)
    assert abs(found - row) < 0.002, found
    assert fit > 0.95, fit
    assert abs(bpm - 60.0 / (row * lpb)) < 0.5


def test_a_minority_of_off_grid_notes_does_not_hide_the_grid():
    # Grace notes, flams and frame-rate arpeggios are genuinely off-grid,
    # and real Mega Drive tracks have enough of them that scoring by
    # AVERAGE error reports "no grid" for music that plainly has one. This
    # is the case that made the metric change to coverage.
    row = 0.1
    notes = [vt.Note(row * i, "fm0", "on", "A", 4) for i in range(80)]
    notes += [vt.Note(row * i + 0.037, "fm1", "on", "C", 5) for i in range(20)]
    found, _bpm, _lpb, fit = vt.infer_grid(notes)
    assert abs(found - row) < 0.002, found
    assert fit > 0.7, fit


def test_random_onsets_do_not_produce_a_confident_grid():
    import random

    generator = random.Random(7)
    notes = [vt.Note(generator.uniform(0, 30), "fm0", "on", "A", 4)
             for _ in range(200)]
    _found, _bpm, _lpb, fit = vt.infer_grid(notes)
    # Uniform times sit within the tolerance of SOME line about 2*tol of
    # the time whatever the period, so a low ceiling here is the point.
    assert fit < 0.45, fit


def test_the_subdivision_lands_the_tempo_where_music_lives():
    for row in (0.0625, 0.125, 0.25):
        notes = [vt.Note(row * i, "fm0", "on", "A", 4) for i in range(64)]
        _found, bpm, lpb, _fit = vt.infer_grid(notes)
        assert vt.MIN_BPM <= bpm <= vt.MAX_BPM, (row, bpm, lpb)


# -- the PSG hold rule -------------------------------------------------------
def _voice():
    notes = []
    return _PSGHarness(vt._PSGVoice(0, notes), notes)


class _PSGHarness:
    def __init__(self, voice, notes):
        self.voice = voice
        self.notes = notes


def test_vibrato_does_not_become_a_run_of_notes():
    # A driver rewriting the tone register every frame to wobble the pitch
    # is one note, not sixty a second. Measured on Streets of Rage 2, 69%
    # of pitch changes last under 45 ms with a median of one 60 Hz frame.
    harness = _voice()
    voice = harness.voice
    voice.key_on(0.0, 57, ("A", 4, 0.0), 4)
    time = 0.0
    for _ in range(60):                     # a second of 30 Hz wobble
        time += 1 / 60.0
        voice.pitch(time, 58, ("A#", 4, 0.0))
        voice.tick(time)
        time += 1 / 60.0
        voice.pitch(time, 57, ("A", 4, 0.0))
        voice.tick(time)
    assert len(harness.notes) == 1, \
        f"vibrato produced {len(harness.notes)} notes"


def test_a_pitch_that_holds_does_become_a_note():
    harness = _voice()
    voice = harness.voice
    voice.key_on(0.0, 57, ("A", 4, 0.0), 4)
    voice.pitch(0.5, 60, ("C", 5, 0.0))
    for step in range(1, 8):
        voice.tick(0.5 + step * 0.01)
    assert len(harness.notes) == 2, harness.notes
    second = harness.notes[1]
    assert second.note == "C" and second.octave == 5
    # Timestamped where the pitch actually changed, not where it was
    # confirmed to have held.
    assert abs(second.time - 0.5) < 1e-9, second.time


def test_a_key_off_ends_the_note():
    harness = _voice()
    harness.voice.key_on(0.0, 57, ("A", 4, 0.0), 4)
    harness.voice.key_off(1.0)
    assert [n.kind for n in harness.notes] == ["on", "off"]


# -- end to end --------------------------------------------------------------
def _score(bars: int = 8) -> str:
    """A fixture with something on EVERY row.

    infer_grid reads the row length out of the onsets, so a score that
    only plays on every other row genuinely describes a grid twice as
    coarse — there is nothing in a list of onsets that says otherwise. It
    also refuses to guess from a dozen moments, which is the right refusal
    and makes a four-bar fixture useless.
    """
    lines = ["ticks 240", "bpm 150", "lpb 4",
             "inst fm0 bass", "inst fm1 square_lead",
             "cols fm0 fm1 psg0 dac", ""]
    bass = ["A-2", "C-3", "E-3", "G-3"]
    lead = ["A-4", "C-5", "E-5", "G-5"]
    high = ["E-5", "A-5", "C-6", "B-5"]
    for bar in range(bars):
        for row in range(8):
            drum = "kick" if row == 0 else ("snare" if row == 4 else "...")
            lines.append(f"{bass[(bar + row) % 4]:5s} {lead[row % 4]:5s} "
                         f"{high[(row + bar) % 4]:5s} {drum}")
        lines.append("===   ===   ===   ...")
    return "\n".join(lines) + "\n"


SCORE = _score()


def _transcribe_score(text=SCORE, directory=None):
    import chipgen

    path = os.path.join(directory, "t.vgm")
    chipgen.compose(text, vgm=path)
    return vt.transcribe(path, max_seconds=60)


def test_the_notes_a_score_played_come_back_out_of_its_vgm():
    events, meta = tracker.loads(SCORE)
    row = meta.ticks_per_row() / meta.ticks_per_second

    expected, tick = set(), 0
    for event in events:
        if isinstance(event, E.Wait):
            tick += event.ticks
            continue
        if isinstance(event, E.End):
            break
        at = tick // meta.ticks_per_row()
        if isinstance(event, E.FMNoteOn):
            expected.add((at, f"fm{event.channel}",
                          event.octave * 12 + E.NOTE_NAMES.index(event.note)))
        elif isinstance(event, E.PSGToneOn):
            expected.add((at, f"psg{event.channel}",
                          event.octave * 12 + E.NOTE_NAMES.index(event.note)))

    with support.TempDir() as directory:
        notes, info = _transcribe_score(directory=directory)

    got = set()
    for note in notes:
        if note.kind != "on" or not note.note or note.channel == "dac":
            continue
        got.add((int(round(note.time / row)), note.channel,
                 note.octave * 12 + E.NOTE_NAMES.index(note.note)))

    assert expected, "the fixture plays no notes"
    assert expected <= got, sorted(expected - got)
    # A kick and a snare per bar. They are 400 ms apart, so each sample
    # finishes before the next starts; closer than that and the byte
    # stream has no gap for the hit detector to see.
    assert info["dac_hits"] >= 8, info["dac_hits"]


def test_the_tempo_of_a_known_score_is_recovered():
    with support.TempDir() as directory:
        notes, _info = _transcribe_score(directory=directory)
    row, bpm, lpb, fit = vt.infer_grid(notes)
    assert abs(row - 0.1) < 0.003, row
    # 100 ms rows at lpb 4 is 150 BPM; whatever subdivision was chosen has
    # to describe the same grid.
    assert abs(60.0 / (row * lpb) - bpm) < 0.5, (row, bpm, lpb)
    assert abs(bpm * lpb - 600.0) < 5.0, (bpm, lpb)
    assert fit > 0.9, fit


def test_a_transcription_parses_back_as_tracker_text():
    import chipgen

    with support.TempDir() as directory:
        path = os.path.join(directory, "t.vgm")
        chipgen.compose(SCORE, vgm=path)
        record = vt.transcribe_file(path, max_seconds=60, with_bank=False)

    assert record["tracker"], "no score was produced"
    events, meta = tracker.loads(record["tracker"])
    assert any(isinstance(e, E.FMNoteOn) for e in events)
    assert meta.bpm > 0
    # The caveats belong in the file, not only in the manifest: whoever
    # opens one of these should see what was and was not recovered.
    assert "not recovered" in record["tracker"].lower()


def test_a_corpus_records_what_it_rejected_and_why():
    import chipgen

    with support.TempDir() as directory:
        good = os.path.join(directory, "good.vgm")
        chipgen.compose(SCORE, vgm=good)
        thin = os.path.join(directory, "thin.vgm")
        chipgen.compose("cols fm0\ninst fm0 bass\nA-2\n...\n===\n", vgm=thin)

        out = os.path.join(directory, "corpus")
        manifest = vt.build_corpus([good, thin], out, max_seconds=60,
                                   min_notes=8)

    assert manifest["tracks"] == 2
    assert manifest["accepted"] == 1, manifest["entries"]
    rejected = [e for e in manifest["entries"] if not e.get("accepted")]
    assert rejected and "reason" in rejected[0], rejected
    assert manifest["caveats"], "a corpus must say what it does not carry"
    assert manifest["total_notes"] > 0


def test_a_track_tuned_off_a440_is_reported_as_tuning_not_as_bending():
    # Several of these games sit a consistent few cents sharp; Gleylancer
    # is +16.7 with only 2-3 cents of spread. Calling that "83% of notes
    # bent" would be true of the arithmetic and false about the music.
    steady = [vt.Note(i * 0.1, "fm0", "on", "A", 4, cents_off=16.7)
              for i in range(40)]
    tuning, spread = vt._tuning(steady)
    assert abs(tuning - 16.7) < 0.1 and spread < 1.0

    import random
    generator = random.Random(3)
    bending = [vt.Note(i * 0.1, "fm0", "on", "A", 4,
                       cents_off=generator.uniform(-45, 45)) for i in range(60)]
    _tuning_value, wide = vt._tuning(bending)
    assert wide > 15, wide


# -- recovering vibrato ------------------------------------------------------
def _wobble(depth, hz, seconds, rate=60.0):
    return [(i / rate, depth * math.sin(2 * math.pi * hz * i / rate))
            for i in range(int(seconds * rate))]


def test_a_wobble_is_recovered_with_its_depth_and_speed():
    found = vt.detect_vibrato(_wobble(60.0, 6.0, 2.0), "psg0")
    spans = [d for d in found if d.kind == "vibrato"]
    assert len(spans) == 1, found
    assert abs(spans[0].speed_hz - 6.0) < 0.3, spans[0].speed_hz
    # 57 rather than 60: a 60-cent sine sampled at 60 Hz rarely lands on
    # its own peak, and reporting the peak actually seen is the honest
    # reading of a log that only has those samples in it.
    assert 50 < spans[0].depth_cents <= 61, spans[0].depth_cents
    assert any(d.kind == "vibrato_off" for d in found)


def test_a_bend_is_not_mistaken_for_a_wobble():
    # A deviation that goes one way and stays is a slide. The sign changes
    # are the whole difference.
    one_way = [(i / 60.0, i * 4.0) for i in range(120)]
    assert not vt.detect_vibrato(one_way, "fm0")


def test_a_wobble_followed_by_a_bend_reports_only_the_wobble():
    # The bend used to inflate the depth: a 60-cent vibrato followed by a
    # 250-cent slide came back as 255 cents of vibrato.
    trace = _wobble(60.0, 6.0, 2.0)
    trace += [(2.0 + i / 60.0, 100.0 + i * 5.0) for i in range(60)]
    spans = [d for d in vt.detect_vibrato(trace, "psg0") if d.kind == "vibrato"]
    assert len(spans) == 1
    assert spans[0].depth_cents < 70, spans[0].depth_cents


def test_fine_tuning_is_not_called_vibrato():
    assert not vt.detect_vibrato(_wobble(3.0, 6.0, 2.0), "fm0")


def test_an_arpeggio_is_not_called_vibrato():
    # Two notes an octave apart, alternating, oscillate exactly like a
    # wobble does. It is an arpeggio, it has its own notation, and a
    # two-octave "vibrato" in a score would be nonsense.
    assert not vt.detect_vibrato(_wobble(1200.0, 6.0, 2.0), "psg0")


def test_a_recovered_wobble_becomes_a_vibrato_event():
    import events as events_module

    detected = [vt.Detected(0.5, "fm1", "vibrato", 55.0, 6.0, 1.0),
                vt.Detected(1.5, "fm1", "vibrato_off")]
    notes = [vt.Note(i * 0.1, "fm1", "on", "A", 4) for i in range(24)]
    events = vt.to_events(notes, 0.1, 24, detected=detected)
    vibratos = [e for e in events if isinstance(e, events_module.Vibrato)]
    assert len(vibratos) == 2, vibratos
    assert vibratos[0].target == "fm1"
    assert abs(vibratos[0].depth_cents - 55.0) < 0.1
    assert abs(vibratos[0].speed_hz - 6.0) < 0.1
    assert vibratos[1].depth_cents == 0.0, "the vibrato is never turned off"
