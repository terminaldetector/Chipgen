"""Notes, roles, chords and patterns — the musical layer above the chip."""


def _corpus():
    import score_model
    return score_model.load_all(score_model.corpus_paths())


def test_notes_carry_lengths_not_just_onsets():
    import score_model

    scores = _corpus()
    assert scores, "no corpus scores found"
    notes = [n for s in scores for v in s.voices.values() for n in v]
    assert len(notes) > 10000
    assert all(n.length >= 1 for n in notes), "a note cannot be zero rows long"
    assert all(n.end > n.row for n in notes)
    # Not every note the same length, or duration is not being read at all.
    assert len({n.length for n in notes}) > 5


def test_a_voice_never_overlaps_itself():
    # One FM channel plays one note. If the model reports two notes
    # sounding at once on one voice, the key-on/key-off pairing is wrong.
    for score in _corpus()[:20]:
        for name, notes in score.voices.items():
            for a, b in zip(notes, notes[1:]):
                assert a.end <= b.row or a.row == b.row, \
                    f"{name} in {score.title}: {a} overlaps {b}"


def test_the_voice_called_bass_is_the_lowest_one():
    # The classifier has no access to pitch rank as a label, so agreeing
    # with it is evidence the features mean what they claim.
    import statistics
    import roles

    agree = total = 0
    for score in _corpus():
        fm = {n: v for n, v in score.voices.items()
              if n.startswith("fm") and len(v) >= roles.MIN_NOTES}
        if len(fm) < 2:
            continue
        lowest = min(fm, key=lambda n: statistics.median(x.pitch for x in fm[n]))
        found = [n for n in fm if roles.best_role(n, score)[0] == "bass"]
        if found:
            total += 1
            agree += lowest in found
    assert total >= 20, f"only {total} tracks produced a bass at all"
    assert agree / total > 0.85, \
        f"the 'bass' was the lowest voice only {agree}/{total} times"


def test_chords_are_labelled_correctly_and_ambiguity_is_reported():
    import harmony

    exact = {
        (0, 4, 7): (0, "maj"), (0, 3, 7): (0, "min"), (0, 7): (0, "5"),
        (0, 4, 7, 10): (0, "dom7"), (0, 4, 7, 11): (0, "maj7"),
        (0, 3, 6): (0, "dim"), (2, 5, 9): (2, "min"), (7, 11, 2): (7, "maj"),
    }
    for pitches, (root, quality) in exact.items():
        ranked = harmony.label(pitches, bass=pitches[0])
        assert ranked, f"no label for {pitches}"
        assert (ranked[0][0], ranked[0][1]) == (root, quality), \
            f"{pitches} -> {ranked[0]}, expected {root} {quality}"
        assert ranked[0][2] > 0.5, \
            f"{pitches} is exactly one chord but reported {ranked[0][2]:.2f}"

    # C minor 7 and Eb major 6 are literally the same four pitch classes.
    # Claiming either outright would be a false certainty.
    ranked = harmony.label((0, 3, 7, 10), bass=0)
    assert ranked[0][2] < 0.8, "an ambiguous chord was reported as certain"
    assert ranked[1][2] > 0.15, "the alternative reading was not reported"


def test_a_power_chord_is_not_a_triad_missing_a_note():
    # Chip harmony is mostly incomplete. Reading a bare fifth as a major
    # chord invents a third that the composer did not write.
    import harmony

    root, quality, _ = harmony.label((0, 7), bass=0)[0]
    assert (root, quality) == (0, "5")


def test_duration_weighting_keeps_a_melody_out_of_the_chord():
    # A passing note is short. If it counts as much as a held chord tone,
    # a C major triad under a melody touching B reads as Cmaj7.
    import harmony
    import score_model

    class Meta:
        bpm, lpb = 120.0, 4
        title = "t"

        def ticks_per_row(self):
            return 1

    N = score_model.Note
    score = score_model.Score(
        voices={"fm0": [N(0, 16, 48, 100)], "fm1": [N(0, 16, 52, 100)],
                "fm2": [N(0, 16, 55, 100)],
                "fm3": [N(0, 1, 59, 100)]},          # 1-row passing B
        drums=[], bar=16, lpb=4, bpm=120.0, rows=16, title="t", path="")
    found = harmony.chords(score)
    assert found, "no chord found"
    assert found[0].quality == "maj", \
        f"the passing note turned it into {found[0].quality}"


def test_patterns_are_transposable():
    import patterns

    bank = patterns.build(_corpus())
    assert len(bank) > 500
    example = next(p for p in bank.by_key.values() if p.melodic)
    here = example.transpose_to(48)
    there = example.transpose_to(55)
    assert [p for _, p, _ in here] != [p for _, p, _ in there]
    assert ([b - a for (_, a, _), (_, b, _) in zip(here, there)]
            == [7] * len(here)), "transposing changed the shape"


def test_rhythm_and_contour_transfer_between_tracks_but_their_fusion_does_not():
    # The measurement that decides what a pattern bank should store. If
    # this ever inverts, the bank is built at the wrong level.
    import patterns

    bank = patterns.build(_corpus())
    fused = bank.coverage(lambda p: (p.role, p.intervals, p.onsets))
    rhythm = bank.coverage(lambda p: (p.role, p.onsets))
    contour = bank.coverage(lambda p: (p.role, p.contour() or None))
    assert rhythm > 0.2, f"rhythm cells cover only {rhythm:.1%}"
    assert contour > 0.2, f"contours cover only {contour:.1%}"
    assert fused < rhythm / 4, \
        f"fused patterns cover {fused:.1%} against rhythm's {rhythm:.1%}"


def test_syncopation_uses_the_scores_own_rows_per_beat():
    import patterns

    on_beat = patterns.Pattern(intervals=(0, 0, 0, 0), onsets=(0, 2, 4, 6),
                               lengths=(2, 2, 2, 2), span_rows=8, lpb=2,
                               role="bass", source="t")
    assert on_beat.syncopation == 0.0
    off_beat = patterns.Pattern(intervals=(0, 0, 0, 0), onsets=(1, 3, 5, 7),
                                lengths=(2, 2, 2, 2), span_rows=8, lpb=2,
                                role="bass", source="t")
    assert off_beat.syncopation == 1.0
