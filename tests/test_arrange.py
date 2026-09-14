"""Fitting music onto a chip that cannot hold all of it.

Every test here exists because the thing it checks was wrong first. The
arranger's whole value is that it reports what it cost, so a test that
only asserted "it returned a score" would pass on an arranger that threw
half the music away.
"""

import os

import support

ROOT = support.ROOT


def _genesis():
    import score_model
    return score_model.load(os.path.join(
        ROOT, "corpus", "core", "scores",
        "alien_soldier_mega_drive_genesis_07_runner_ad2025.trk"))


def _nes():
    import score_model
    return score_model.load(os.path.join(
        ROOT, "corpus", "core", "scores",
        "batman___return_of_the_joker__nes__02_demo_bgm.json"))


def _score(voices, drums=(), rows=64, bpm=150.0, lpb=4):
    import score_model
    return score_model.Score(voices=voices, drums=list(drums), bar=lpb * 4,
                             lpb=lpb, bpm=bpm, rows=rows, title="t", path="")


def _note(row, pitch, velocity=127, length=2):
    import score_model
    return score_model.Note(row=row, length=length, pitch=pitch,
                            velocity=velocity)


# --------------------------------------------------------------------------
# A score already written for the target is not ours to improve
# --------------------------------------------------------------------------
def test_arrange_same_chip_is_identity():
    """Arranging a Genesis score for the Genesis must change nothing.

    It did not. Role classification ran first and reshuffled the parts —
    fm1 came out on psg1 — which is the arranger overruling the composer
    on a score that was already correct for the chip.
    """
    import arrange
    source = _genesis()
    fitted, report = arrange.arrange(source, "YM2612")

    for voice in source.voices:
        assert report.placed[voice] == voice, \
            f"{voice} moved to {report.placed.get(voice)}"
    assert not report.dropped, report.dropped
    assert not report.transposed, report.transposed
    assert not report.flattened, report.flattened
    for voice, notes in source.voices.items():
        assert [n.pitch for n in fitted.voices[voice]] == \
               [n.pitch for n in notes]


def test_identity_does_not_refit_the_range():
    """The declared ranges are editorial; the composer's are not.

    psg0's range here says C2-C6 — the pitch above which neighbouring
    semitones stop resolving. A Genesis score that writes above it was
    still played by the chip, so folding those notes on a same-chip pass
    was this module second-guessing a score that needed nothing.
    """
    import arrange
    fitted, report = arrange.arrange(_genesis(), "YM2612")
    assert not report.folded, report.folded


def test_nes_score_arranged_for_nes_keeps_its_channels():
    """The NES corpus names its voices; the tracker numbers them."""
    import arrange
    fitted, report = arrange.arrange(_nes(), "RP2A03")
    assert report.placed["pulse1"] == "nes0"
    assert report.placed["pulse2"] == "nes1"
    assert report.placed["triangle"] == "nes2"
    assert report.placed["noise"] == "nes3"


# --------------------------------------------------------------------------
# Range: transpose, never clamp
# --------------------------------------------------------------------------
def test_below_the_pulse_floor_is_transposed_not_clamped():
    """Below its floor the NES's 11-bit timer clamps and the note sounds
    888 cents SHARP rather than low — measured. So a bass line written
    under the floor has to move by an octave, and every note that comes
    out must be inside the channel. C-0 is under every NES voice: the
    pulses stop at A-1, the triangle at A-0."""
    import arrange
    low = [_note(row * 4, row % 5) for row in range(16)]          # C-0..E-0
    fitted, report = arrange.arrange(_score({"lead": low}), "RP2A03")

    column = report.placed["lead"]
    channel = next(c for c in arrange.TARGETS["RP2A03"]
                   if c.column == column)
    assert report.transposed["lead"] > 0, report.lines()
    for note in fitted.voices[column]:
        assert channel.low <= note.pitch <= channel.high, note


def test_transposition_moves_whole_octaves_only():
    """A part moved by anything but an octave is a different part."""
    import arrange
    notes = [_note(row * 4, row) for row in range(12)]
    fitted, report = arrange.arrange(_score({"lead": notes}), "RP2A03")
    column = report.placed["lead"]
    shift = fitted.voices[column][0].pitch - notes[0].pitch
    assert shift % 12 == 0, shift
    assert shift == report.transposed["lead"] * 12


def test_a_part_wider_than_the_channel_folds_and_says_how_many():
    """Five octaves on one channel is normal in a transcribed register
    log. Folding the outliers is the honest fallback; doing it without a
    count is not."""
    import arrange
    wide = [_note(row * 4, 20 + row * 6) for row in range(14)]  # 20..98
    fitted, report = arrange.arrange(_score({"lead": wide}), "RP2A03")
    column = report.placed["lead"]
    channel = next(c for c in arrange.TARGETS["RP2A03"] if c.column == column)
    assert report.folded["lead"] > 0
    assert any("folded" in line for line in report.lines())
    for note in fitted.voices[column]:
        assert channel.low <= note.pitch <= channel.high


def test_best_shift_prefers_the_octave_that_strands_fewest_notes():
    """The first rule here only accepted a shift that fit the WHOLE span,
    so a part whose body sat one octave low — with a single stray note
    high — was folded wholesale instead of being moved up an octave and
    folding the stray.

    nes0 holds A-1 to C-6. The body here is ten notes below that floor
    and the stray is one note already inside it: standing still strands
    the ten, moving up an octave strands the one.
    """
    import arrange
    channel = next(c for c in arrange.TARGETS["RP2A03"] if c.column == "nes0")
    notes = [_note(r, 10 + r) for r in range(10)] + [_note(10, 70)]
    assert arrange._best_shift(notes, channel) == (1, 1)


# --------------------------------------------------------------------------
# Dynamics: two chips, two meanings for the same number
# --------------------------------------------------------------------------
def test_velocity_conversion_preserves_dB_not_the_number():
    """The YM2612 reads 1-127 as a fader linear in amplitude; the
    SN76489 reads 0-15 as an attenuator, 0 loudest, -2 dB a step. Copying
    the number across turns a crescendo into a diminuendo."""
    import arrange
    # Half amplitude is -6 dB either way: fader 64, attenuator step 3.
    assert arrange.convert_velocity(64, arrange.FADER,
                                    arrange.ATTENUATOR) == 3
    assert arrange.convert_velocity(127, arrange.FADER,
                                    arrange.ATTENUATOR) == 0
    assert arrange.convert_velocity(0, arrange.ATTENUATOR,
                                    arrange.FADER) == 127
    # -14 dB on the attenuator is 127 * 10^(-14/20) on the fader.
    assert abs(arrange.convert_velocity(7, arrange.ATTENUATOR,
                                        arrange.FADER) - 25) <= 1


def test_conversion_never_writes_the_attenuators_mute():
    """15 is a hard mute, so a note-on written at 15 is a dropped note.
    The quietest a converted note may land is 14."""
    import arrange
    for level in range(1, 8):
        assert arrange.convert_velocity(level, arrange.FADER,
                                        arrange.ATTENUATOR) <= 14


def test_loudness_order_survives_the_conversion():
    import arrange
    faders = [127, 96, 64, 32, 16, 8]
    steps = [arrange.convert_velocity(v, arrange.FADER, arrange.ATTENUATOR)
             for v in faders]
    assert steps == sorted(steps), steps        # quieter fader, more attenuation


def test_noise_means_a_different_chip_in_a_different_score():
    """Both the SN76489 and the NES have a channel called `noise`, and
    they read the volume number in opposite directions. The name cannot
    settle it; the company it keeps can."""
    import arrange
    nes = arrange.source_domains(_nes())
    assert nes["noise"] == arrange.FADER
    genesis = arrange.source_domains(_score(
        {"fm0": [_note(0, 40)], "noise": [_note(0, 60)]}))
    assert genesis["noise"] == arrange.ATTENUATOR


def test_the_triangle_drops_velocities_rather_than_approximating_them():
    """It has no volume register — measured bit-identical at 8 and 127."""
    import arrange
    quiet = [_note(row * 4, 40, velocity=20) for row in range(8)]
    fitted, report = arrange.arrange(_score({"bass": quiet}), "RP2A03")
    assert report.placed["bass"] == "nes2"
    assert all(n.velocity == 127 for n in fitted.voices["nes2"])
    assert any("no volume register" in line for line in report.lines())


# --------------------------------------------------------------------------
# Polyphony
# --------------------------------------------------------------------------
def test_flattening_keeps_the_outer_note_and_counts_the_rest():
    import arrange
    chord = []
    for row in range(0, 32, 4):
        chord += [_note(row, 60), _note(row, 64), _note(row, 67)]
    fitted, report = arrange.arrange(_score({"lead": chord}), "RP2A03")
    column = report.placed["lead"]
    kept = fitted.voices[column]
    assert report.flattened["lead"] == len(chord) - len(kept)
    assert len(kept) == 8
    # Sitting above middle C, so the top of the chord is what survives.
    assert all(n.pitch == 67 for n in kept), [n.pitch for n in kept]


def test_a_low_chord_keeps_its_bottom():
    import arrange
    chord = []
    for row in range(0, 32, 4):
        chord += [_note(row, 30), _note(row, 34), _note(row, 37)]
    fitted, report = arrange.arrange(_score({"bass": chord}), "RP2A03")
    kept = fitted.voices[report.placed["bass"]]
    pitches = {n.pitch for n in kept}
    assert pitches == {30}, pitches


def test_flattening_leaves_no_overlapping_notes():
    """One channel holds one note: a note still sounding when the next
    begins is a key-on the hardware will swallow."""
    import arrange
    notes = [_note(0, 60, length=16), _note(4, 62, length=16),
             _note(8, 64, length=16)]
    fitted, report = arrange.arrange(_score({"lead": notes}), "RP2A03")
    kept = fitted.voices[report.placed["lead"]]
    for a, b in zip(kept, kept[1:]):
        assert a.row + a.length <= b.row, (a, b)


# --------------------------------------------------------------------------
# Drums, and the channels that cannot play them
# --------------------------------------------------------------------------
def test_opl2_has_no_sampler_and_says_so():
    """Its rhythm mode (register 0xBD) is the one layer this engine does
    not implement, so a drum track arranged for it is dropped — and
    removed from the score, not carried along as a lie in the data."""
    import arrange
    fitted, report = arrange.arrange(_genesis(), "YM3812")
    assert "drums" in report.dropped
    assert "0xBD" in report.dropped["drums"]
    assert fitted.drums == []


def test_drums_go_to_the_sampler_not_the_first_free_percussion_channel():
    import arrange
    fitted, report = arrange.arrange(_genesis(), "YM2612")
    assert any("dac" in line for line in report.lines()), report.lines()
    assert fitted.drums == _genesis().drums


def test_a_mirrored_drum_list_is_kept_once():
    """nes_transcribe builds its drum list FROM the noise voice, so an
    NES score carries the same hits twice. Placing both doubles every
    drum."""
    import arrange
    source = _nes()
    assert [n.row for n in source.voices["noise"]] == source.drums
    fitted, report = arrange.arrange(source, "RP2A03")
    assert fitted.drums == []
    assert len(fitted.voices["nes3"]) == len(source.drums)
    assert any("kept once" in line for line in report.lines())


def test_a_noise_track_never_wins_a_melodic_channel():
    """It has more hits than the melody in plenty of scores, and ranking
    is by note count."""
    import arrange
    source = _score({"noise": [_note(r, 60) for r in range(0, 120, 2)],
                     "lead": [_note(r, 60) for r in range(0, 40, 4)],
                     "pulse1": [_note(r, 50) for r in range(0, 40, 4)]})
    fitted, report = arrange.arrange(source, "RP2A03")
    assert report.placed["noise"] == "nes3"
    assert report.placed["lead"] in ("nes0", "nes1", "nes2")


# --------------------------------------------------------------------------
# Dropping parts
# --------------------------------------------------------------------------
def test_dropped_voices_are_named_with_a_reason():
    """Three melodic channels and a noise channel on the NES; this score
    has seven melodic voices and a noise track."""
    import arrange
    fitted, report = arrange.arrange(_genesis(), "RP2A03")
    assert len(report.placed) == 4, report.lines()
    assert len(report.dropped) == 4
    for voice, reason in report.dropped.items():
        assert reason and voice not in fitted.voices
    assert not report.lossless


def test_every_placed_voice_reaches_the_score():
    import arrange
    for target in arrange.TARGETS:
        fitted, report = arrange.arrange(_genesis(), target)
        assert sorted(report.placed.values()) == sorted(fitted.voices), target
        assert all(fitted.voices[c] for c in fitted.voices), target


# --------------------------------------------------------------------------
# The arranged score has to render
# --------------------------------------------------------------------------
def test_an_arranged_score_round_trips_through_the_tracker():
    """The tracker could parse nes0-nes4 and never write one back out, so
    an arranged NES score dumped to a file with a correct header and no
    notes in it."""
    import arrange
    import score_model
    import tracker

    fitted, report = arrange.arrange(_genesis(), "RP2A03")
    meta = tracker.Metadata()
    meta.bpm, meta.lpb = fitted.bpm, fitted.lpb
    text = tracker.dumps(score_model.to_events(fitted), meta)
    assert "nes0" in text and "nes4" in text

    with support.TempDir() as directory:
        path = os.path.join(directory, "arranged.trk")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        back = score_model.load(path)

    assert len(back.drums) == len(fitted.drums)
    assert sum(len(v) for v in back.voices.values()) == \
        sum(len(v) for v in fitted.voices.values())


def test_targets_describes_every_channel_for_an_interface():
    import arrange
    described = arrange.targets()
    assert set(described) == set(arrange.TARGETS)
    for name, target in described.items():
        assert target["melodic_voices"] >= 3
        for channel in target["channels"]:
            assert channel["dynamics"] in (arrange.FADER, arrange.ATTENUATOR,
                                           arrange.SILENT)
            assert channel["chip"]
    assert described["RP2A03"]["melodic_voices"] == 3
    assert described["YM2612"]["melodic_voices"] == 8
    assert described["YM3812"]["melodic_voices"] == 9


def test_report_json_carries_every_compromise():
    import arrange
    _fitted, report = arrange.arrange(_genesis(), "RP2A03")
    data = report.to_json()
    assert data["target"] == "RP2A03" and data["lossless"] is False
    for key in ("placed", "transposed", "folded", "flattened", "converted",
                "dropped", "notes"):
        assert key in data


# --------------------------------------------------------------------------
# The pipeline either side of the arranger
# --------------------------------------------------------------------------
def test_a_score_json_renders_instead_of_producing_silence():
    """chipgen read `{"voices": ...}` as an event list, found no events,
    and rendered 1 event and 0.00 seconds — for all 74 NES corpus files,
    with "wrote" printed on the way out."""
    import chipgen

    path = os.path.join(ROOT, "corpus", "core", "scores",
                        "final_fantasy__nes__16_battle.json")
    with open(path, encoding="utf-8") as handle:
        result = chipgen.compose(handle.read())
    assert result.duration > 10.0, result.duration
    assert len(result.events) > 100, len(result.events)
    # Un-normalised: compose() only masters when asked, and the NES's
    # three voices sit around a tenth of full scale on their own. The
    # bar is "audibly not silence", not "loud".
    assert result.peak > 0.05, result.peak


def test_score_to_events_is_the_inverse_of_from_events():
    import score_model
    import tracker

    source = _genesis()
    meta = tracker.Metadata()
    meta.bpm, meta.lpb = source.bpm, source.lpb
    back = score_model.from_events(score_model.to_events(source), meta)

    for voice, notes in source.voices.items():
        assert [(n.row, n.pitch, n.velocity) for n in back.voices[voice]] == \
               [(n.row, n.pitch, n.velocity) for n in notes], voice
    assert back.drums == source.drums


def test_the_row_grid_survives_the_round_trip():
    """Emitting at a flat 24 ticks a row and reading back at the score's
    own 19 stretched every note by 26% — the music came back a quarter
    longer and nothing said so."""
    import score_model
    import tracker

    source = _genesis()
    meta = tracker.Metadata()
    meta.bpm, meta.lpb = source.bpm, source.lpb
    back = score_model.from_events(score_model.to_events(source), meta)
    assert back.rows == source.rows, (source.rows, back.rows)


def test_arranging_through_the_server_returns_the_report_too():
    """An endpoint that handed back only the score would let an interface
    show a rearrangement that quietly lost the counter-melody."""
    import serve

    with open(os.path.join(
            ROOT, "corpus", "core", "scores",
            "alien_soldier_mega_drive_genesis_07_runner_ad2025.trk"),
            encoding="utf-8") as handle:
        text = handle.read()

    answer = serve.arrange_score({"score": text, "target": "RP2A03"})
    assert answer["ok"] and answer["target"] == "RP2A03"
    assert "cols" in answer["score"] and "nes0" in answer["score"]
    assert answer["report"]["dropped"], "four voices had nowhere to go"
    assert answer["lines"], "the report must be printable as it stands"

    refused = serve.arrange_score({"score": text, "target": "Z80"})
    assert not refused["ok"] and "Z80" in refused["error"]


def test_the_arranged_score_the_server_returns_renders():
    """The report can be perfect and the score still be unplayable."""
    import chipgen
    import serve

    source = ("bpm 150\nlpb 4\ncols fm0 fm1\n"
              + "\n".join("C-4 E-4" for _ in range(8)) + "\nend\n")
    answer = serve.arrange_score({"score": source, "target": "RP2A03"})
    assert answer["ok"], answer
    result = chipgen.compose(answer["score"])
    assert result.peak > 0.05, result.peak


def test_a_genesis_arrangement_has_no_nes_columns_in_it():
    """`noise` is a channel on both chips, so emitting one arranged for
    the Genesis wrote an `nes3` column into a score whose other three
    columns were FM — a file the NES half of the engine would answer."""
    import score_model
    import serve

    with open(os.path.join(
            ROOT, "corpus", "core", "scores",
            "batman___return_of_the_joker__nes__02_demo_bgm.json"),
            encoding="utf-8") as handle:
        answer = serve.arrange_score({"score": handle.read(),
                                      "target": "YM2612"})
    assert answer["ok"], answer
    columns = next(line for line in answer["score"].splitlines()
                   if line.startswith("cols "))
    assert "nes" not in columns, columns
    assert "noise" in columns, columns

    back = score_model.loads(answer["score"])
    assert sorted(back.voices) == ["fm0", "fm2", "fm3", "noise"], \
        sorted(back.voices)
    # The hits are on the noise voice, not in the drum list: this score
    # carried them twice and the arranger kept them once.
    assert len(back.voices["noise"]) == 27, len(back.voices["noise"])
    assert back.drums == []


def test_a_json_score_keeps_its_title_through_an_arrangement():
    """Parsing it as an event list first threw the title away: a JSON
    score carries one and an event list has nowhere to put it."""
    import score_model

    path = os.path.join(ROOT, "corpus", "core", "scores",
                        "batman___return_of_the_joker__nes__02_demo_bgm.json")
    with open(path, encoding="utf-8") as handle:
        score = score_model.loads(handle.read())
    assert score.title == "02 Demo BGM", score.title


def test_a_psg_noise_track_is_not_lost_on_the_way_into_a_score():
    """`from_events` knew PSGToneOn and not PSGNoiseOn, so every Genesis
    score's noise track — the hats and the snare — vanished the moment it
    became a Score. Nothing reported it: the score simply had one voice
    fewer than the file."""
    import score_model

    source = _genesis()
    assert "noise" in source.voices, sorted(source.voices)
    assert len(source.voices["noise"]) > 0

    import arrange
    _fitted, report = arrange.arrange(source, "RP2A03")
    assert report.placed["noise"] == "nes3"


# --------------------------------------------------------------------------
# Where the notes come from: MIDI
# --------------------------------------------------------------------------
def _smf(tracks, division=480, fmt=1):
    """A Standard MIDI File, assembled here so the suite needs no fixture."""
    import struct

    def vlq(n):
        out = bytearray([n & 0x7F])
        n >>= 7
        while n:
            out.insert(0, (n & 0x7F) | 0x80)
            n >>= 7
        return bytes(out)

    def chunk(events):
        body = b"".join(vlq(d) + e for d, e in events) + vlq(0) + b"\xFF\x2F\x00"
        return b"MTrk" + struct.pack(">I", len(body)) + body

    return (b"MThd" + struct.pack(">IHHh", 6, fmt, len(tracks), division)
            + b"".join(chunk(t) for t in tracks))


def _demo_midi(division=480):
    d = division
    tempo = [(0, b"\xFF\x51\x03" + (500000).to_bytes(3, "big")),
             (0, b"\xFF\x03\x04Test")]
    bass = [(0, b"\xFF\x03\x04Bass")]
    for note in (36, 38, 40, 41):
        bass.append((0, bytes([0x90, note, 90])))
        bass.append((d, bytes([0x80, note, 0])))
    lead = [(0, b"\xFF\x03\x04Lead")]
    for note in (72, 74, 76, 77, 79, 77, 76, 74):
        lead.append((0, bytes([0x91, note, 100])))
        lead.append((d // 2, bytes([0x81, note, 0])))
    for note in (72, 76, 79):                       # a chord, held
        lead.append((0, bytes([0x91, note, 110])))
    lead.append((d * 2, bytes([0x81, 72, 0])))
    lead.append((0, bytes([0x81, 76, 0])))
    lead.append((0, bytes([0x81, 79, 0])))
    drums = [(0, b"\xFF\x03\x05Drums"), (40, bytes([0x99, 36, 100]))]
    for _ in range(7):
        drums.append((d // 2, bytes([36, 100])))    # running status
    return _smf([tempo, bass, lead, drums], d)


def test_midi_arrives_as_a_score():
    import midi_import

    result = midi_import.read(_demo_midi())
    score = result.score
    assert score.title == "Test"
    assert score.bpm == 120.0
    assert sorted(score.voices) == ["bass", "lead"], sorted(score.voices)
    assert len(score.voices["bass"]) == 4
    assert len(score.voices["lead"]) == 11          # 8 notes + a 3-note chord
    assert len(score.drums) == 8, "running status dropped hits"


def test_midi_pitch_lands_where_the_engine_puts_it():
    """A MIDI note number is 12 above this project's pitch units — MIDI
    69 and score pitch 57 are both A-4 at 440 Hz. Off by twelve and every
    import is an octave out, which sounds plausible and is wrong."""
    import midi_import

    score = midi_import.read(_demo_midi()).score
    assert [n.pitch for n in score.voices["bass"]] == [24, 26, 28, 29]


def test_channel_ten_becomes_the_drum_track():
    """General MIDI puts drums there by convention, not by a bit in the
    file, so nothing in the data says it — and read as a melodic part it
    is a bass line of random semitones."""
    import midi_import

    result = midi_import.read(_demo_midi())
    assert "ch10" not in result.score.voices
    assert len(result.score.drums) == 8
    assert any("channel 10" in line for line in result.lines())


def test_quantisation_distance_is_reported():
    """A performance does not sit on a grid. Quantising it silently is
    how an import comes out stiff with nothing to point at."""
    import midi_import

    result = midi_import.read(_demo_midi())
    # The drum track starts 40 ticks into a 120-tick row.
    assert abs(result.moved - 40 / 120) < 0.01, result.moved
    assert any("moved a note" in line for line in result.lines())


def test_a_track_name_places_the_part():
    """`roles.classify_score` declines to judge a four-note part, which
    is exactly when the name is all there is. Without it the lead landed
    on the NES triangle — the one channel with no volume register — and
    the bass on a pulse."""
    import arrange
    import midi_import

    score = midi_import.read(_demo_midi()).score
    _fitted, report = arrange.arrange(score, "RP2A03")
    assert report.placed["bass"] == "nes2", report.lines()
    assert report.placed["lead"] in ("nes0", "nes1"), report.lines()


def test_a_part_with_moving_velocities_avoids_a_deaf_channel():
    import arrange

    moving = [_note(r * 4, 60, velocity=40 + r * 8) for r in range(8)]
    flat = [_note(r * 4, 40, velocity=100) for r in range(8)]
    _fitted, report = arrange.arrange(
        _score({"one": moving, "two": flat}), "RP2A03")
    assert report.placed["one"] != "nes2", report.lines()


def test_smpte_timing_is_refused_rather_than_guessed():
    """The whole row grid hangs off the division, so reading a negative
    one as if it were ticks-per-quarter produces a score at some
    arbitrary tempo that looks fine."""
    import midi_import

    data = bytearray(_demo_midi())
    data[12:14] = (-3077).to_bytes(2, "big", signed=True)   # 25 fps, 80 tpf
    try:
        midi_import.read(bytes(data))
    except midi_import.MidiError as error:
        assert "SMPTE" in str(error)
    else:
        assert False, "SMPTE timing was read as ticks-per-quarter"


def test_a_file_that_is_not_midi_says_so():
    import midi_import

    for junk in (b"", b"RIFF....WAVE", b"MThd\x00\x00\x00\x02\x00\x00"):
        try:
            midi_import.read(junk)
        except midi_import.MidiError:
            continue
        assert False, f"{junk!r} was accepted as a MIDI file"


def test_the_whole_chain_from_midi_to_audio():
    """The point of all of it: notes from somewhere else, onto a chip,
    into sound."""
    import arrange
    import chipgen
    import score_model
    import tracker

    score = midi_import_read()
    fitted, _report = arrange.arrange(score, "RP2A03")
    meta = tracker.Metadata()
    meta.bpm, meta.lpb = fitted.bpm, fitted.lpb
    text = tracker.dumps(score_model.to_events(fitted), meta)
    result = chipgen.compose(text)
    assert result.duration > 1.0, result.duration
    assert result.peak > 0.05, result.peak


def midi_import_read():
    import midi_import
    return midi_import.read(_demo_midi()).score


def test_the_server_takes_a_midi_upload():
    """The browser cannot POST bytes through a JSON body any other way,
    and this is the only route into the engine that does not start from
    text."""
    import base64

    import serve

    payload = base64.b64encode(_demo_midi()).decode("ascii")
    answer = serve.arrange_score({"midi": payload, "target": "RP2A03"})
    assert answer["ok"], answer
    assert answer["imported"], "the import's own report has to come back too"
    assert any("channel 10" in line for line in answer["imported"])
    assert answer["report"]["placed"]["bass"] == "nes2"

    bad = serve.arrange_score({"midi": "not base64!!", "target": "RP2A03"})
    assert not bad["ok"] and "base64" in bad["error"]

    junk = serve.arrange_score({
        "midi": base64.b64encode(b"RIFFWAVE").decode("ascii"),
        "target": "RP2A03"})
    assert not junk["ok"] and "MIDI" in junk["error"]


def test_a_score_of_parts_refuses_to_become_silence():
    """A Score read from MIDI has parts — "bass", "lead" — not channels.
    Turning one into events without fitting it to a chip first produced
    an empty event list, a successful render, and 0.00 seconds of audio
    with "wrote" printed on the way out."""
    import score_model

    score = midi_import_read()
    try:
        score_model.to_events(score)
    except ValueError as error:
        assert "arrange" in str(error), error
        assert "bass" in str(error) and "lead" in str(error), error
    else:
        assert False, "a score of unassigned parts produced events anyway"
