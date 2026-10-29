"""Channel 3's special mode — four frequencies out of one FM voice."""


def _levelled_operators(channel=2, total_level=6, levels=None):
    """`op` directives that put all four operators on equal footing.

    Needed for any measurement of WHICH operator is where: a stock patch
    gives its operators different levels and envelopes, so the partials
    come out at wildly different heights and say nothing about the
    addressing.

    `mul 1` is not optional here, and leaving it out is how this file
    first measured the mode wrongly. The supplementary register sets an
    operator's BASE frequency; `mul` then multiplies it. bell_pluck's
    operator 3 runs at mul 7, so a G-6 written to it sounds at 11 kHz and
    nothing at all appears at 1568 Hz. Force every multiplier to 1 and the
    written pitches are the partials.

    `levels` gives each operator its own total level, which is the only
    way to tell WHICH operator sounds at a pitch — see the mapping test.
    """
    lines = []
    for operator in (1, 2, 3, 4):
        level = total_level if levels is None else levels[operator]
        for field, value in (("mul", 1), ("tl", level), ("ar", 31),
                             ("d1r", 0), ("d2r", 0), ("sl", 0), ("rr", 7)):
            lines.append(f"op fm{channel} {operator} {field} {value}")
    return "\n".join(lines)


def _partial_db(result, hertz, width=0.03):
    """Level at `hertz`, in dB relative to the loudest partial."""
    import math
    import analysis

    freqs, mags = analysis.spectrum(analysis.to_mono(result.audio),
                                    result.sample_rate)
    peak = max(float(v) for v in mags)
    best = 0.0
    for f, m in zip(freqs, mags):
        f = float(f)
        if hertz * (1 - width) < f < hertz * (1 + width):
            best = max(best, float(m))
    return 20.0 * math.log10(best / peak) if best > 0 and peak > 0 else -99.0


def test_the_operator_to_register_map_is_the_one_we_measured():
    # Established by experiment, not from a published mapping: four
    # operators on algorithm 7 at levels 9 dB apart, every supplementary
    # register pair pointed at a different pitch, then the level measured
    # at each pitch names the operator sitting there. $A8/$AC is operator
    # THREE — the supplementary registers carry the same op1, op3, op2
    # interleave the operator offsets do, which is what makes a plausible
    # guess land on the wrong one.
    import opn2

    assert opn2.YM2612.CH3_OPERATOR_REGISTERS[1] == (0xA9, 0xAD)
    assert opn2.YM2612.CH3_OPERATOR_REGISTERS[2] == (0xAA, 0xAE)
    assert opn2.YM2612.CH3_OPERATOR_REGISTERS[3] == (0xA8, 0xAC)
    # Operator 4 has no pair of its own and follows the channel.
    assert opn2.YM2612.CH3_OPERATOR_REGISTERS[4] == (0xA2, 0xA6)


def test_the_level_at_each_pitch_names_the_operator_sitting_there():
    """The experiment that established the mapping, as a test.

    A presence test cannot do this job: write three pitches to three
    registers and all three partials appear whichever register got which,
    so permuting the map is invisible. Giving each operator its own level
    is what makes the assignment observable — the level measured at a
    pitch identifies the operator that was sent there. Checked: with the
    map permuted to the plausible ascending guess ($A8/$AC as operator 1)
    the ordering below scrambles to -19.2 / 0.0 / -9.4 and this fails.
    """
    import chipgen

    # 12 total-level steps is 9 dB, comfortably more than the spread the
    # parallel carriers and key scaling add.
    levels = {1: 0, 2: 12, 3: 24, 4: 36}
    score = ("bpm 60\nlpb 4\ninst fm2 sub_bass\nalg fm2 7\n"
             + _levelled_operators(levels=levels)
             + "\nch3 special\nch3op 1 A-5\nch3op 2 D#6\nch3op 3 G-6\n"
               "cols fm2\nA-4\n...\n...\n...\nend\n")
    result = chipgen.compose(score)

    # Operator 4 has no supplementary register and takes the cell's note.
    measured = [(operator, _partial_db(result, hertz)) for operator, hertz
                in ((1, 880.00), (2, 1244.51), (3, 1567.98), (4, 440.00))]
    for (operator, loud), (next_operator, quiet) in zip(measured,
                                                        measured[1:]):
        assert loud > quiet + 4.0, (
            f"operator {operator} (tl {levels[operator]}) measured "
            f"{loud:.1f} dB and operator {next_operator} "
            f"(tl {levels[next_operator]}) measured {quiet:.1f} dB — the "
            f"levels do not follow the total levels written, so the "
            f"supplementary registers are not mapped as recorded")


def test_a_patch_multiplier_moves_the_partial_off_the_written_pitch():
    """Why `mul 1` is in the helper, pinned so nobody removes it.

    This is the mode's sharpest edge for anyone writing a score: the
    supplementary register takes an operator's BASE frequency, not a
    partial. bell_pluck's operator 3 runs at mul 7, so a G-6 written
    there does not sound at 1568 Hz at all — it sounds at 10976 Hz.
    Measured swing between the two spellings is over 80 dB.

    Operator 3 and not 2: FMInstrument.operators is in register order
    (op1, op3, op2, op4), so the mul 7 sitting second in bell_pluck's
    list belongs to operator three. The tracker's `op` directive takes
    block-diagram numbering and undoes that interleave, which is why the
    directives below read 3.
    """
    import chipgen

    common = ("bpm 60\nlpb 4\ninst fm2 bell_pluck\nalg fm2 7\n"
              "op fm2 3 tl 6\nop fm2 3 ar 31\nop fm2 3 d1r 0\n"
              "op fm2 3 d2r 0\nop fm2 3 sl 0\n"
              "ch3 special\nch3op 3 G-6\ncols fm2\n")
    tail = "A-4\n...\n...\n...\nend\n"
    as_patched = chipgen.compose(common + tail)              # mul 7
    forced = chipgen.compose(common + "op fm2 3 mul 1\n" + tail)

    written = 1567.98
    assert _partial_db(forced, written) > -12.0, (
        f"with mul 1 the written G-6 should be a strong partial, got "
        f"{_partial_db(forced, written):.1f} dB")
    assert _partial_db(as_patched, written) < -40.0, (
        f"with mul 7 nothing should sound at the written pitch, got "
        f"{_partial_db(as_patched, written):.1f} dB")
    assert _partial_db(as_patched, written * 7) > -12.0, (
        f"with mul 7 the operator should sound at 7x the written pitch, "
        f"got {_partial_db(as_patched, written * 7):.1f} dB there")


def test_each_operator_lands_on_the_pitch_it_was_given():
    # The feature itself. Levels are deliberately not asserted: they
    # depend on the patch's envelopes and on key scaling, and the claim
    # here is about WHERE each operator sounds.
    import chipgen

    score = ("bpm 60\nlpb 4\ninst fm2 bell_pluck\nalg fm2 7\n"
             + _levelled_operators()
             + "\ncols fm2\nch3 special\n"
               "ch3op 1 A-5\nch3op 2 D#6\nch3op 3 G-6\n"
               "A-4\n...\n...\n...\nend\n")
    result = chipgen.compose(score)
    for hertz, who in ((880.0, "op1 at A-5"), (1244.5, "op2 at D#6"),
                       (1568.0, "op3 at G-6"), (440.0, "op4 at the note")):
        assert _partial_db(result, hertz) > -45.0, \
            f"{who} is not in the spectrum at {hertz:.0f} Hz"


def test_special_mode_changes_the_spectrum():
    import chipgen

    common = ("bpm 60\nlpb 4\ninst fm2 bell_pluck\nalg fm2 7\n"
              + _levelled_operators() + "\ncols fm2\n")
    plain = chipgen.compose(common + "A-4\n...\n...\n...\nend\n")
    special = chipgen.compose(
        common + "ch3 special\nch3op 1 A-5\nch3op 2 D#6\nch3op 3 G-6\n"
                 "A-4\n...\n...\n...\nend\n")
    # A-5 is written to operator 1 only in special mode.
    assert _partial_db(special, 880.0) > _partial_db(plain, 880.0) + 10.0, \
        "special mode did not move operator 1"


def test_the_mode_register_keeps_the_timer_bits_it_shares():
    # 0x27 holds channel 3's mode in bits 6-7 and the timer controls in
    # the low bits. Writing the mode must not clear a running timer.
    import opn2

    chip = opn2.YM2612(chip_type="ym3438")
    try:
        chip.write(0, 0x27, 0x0F)          # timers loaded and enabled
        value = chip.set_ch3_mode("special")
        assert value & 0x3F == 0x0F, f"timer bits were lost: 0x{value:02X}"
        assert value & 0xC0 == 0x40, f"mode bits wrong: 0x{value:02X}"
        back = chip.set_ch3_mode("normal")
        assert back & 0x3F == 0x0F
        assert back & 0xC0 == 0x00
    finally:
        chip.close()


def test_writing_the_frequencies_before_or_after_the_note_both_work():
    # Worth pinning because the first attempt at measuring this mode keyed
    # on first and read nonsense, which looked like an ordering rule.
    import chipgen

    common = ("bpm 60\nlpb 4\ninst fm2 bell_pluck\nalg fm2 7\n"
              + _levelled_operators() + "\ncols fm2\nch3 special\n")
    before = chipgen.compose(
        common + "ch3op 1 A-5\nA-4\n...\n...\nend\n")
    after = chipgen.compose(
        common + "A-4\nch3op 1 A-5\n...\n...\nend\n")
    for result, label in ((before, "before"), (after, "after")):
        assert _partial_db(result, 880.0) > -45.0, \
            f"operator 1 is missing when written {label} the note"


def test_a_bad_mode_or_operator_is_refused_with_the_options():
    import tracker

    for score, wanted in (
            ("cols fm2\nch3 sideways\nend\n", "Valid:"),
            ("cols fm2\nch3op 9 A-5\nend\n", "1-4"),
            ("cols fm2\nch3op 1 zz\nend\n", "not a note"),
            ("cols fm2\nch3op 1\nend\n", "an operator 1-4 and a note"),
    ):
        try:
            tracker.loads(score)
        except tracker.TrackerError as error:
            assert wanted in str(error), f"{score!r}: {error}"
        else:
            raise AssertionError(f"{score!r} was accepted")


def test_csm_is_accepted_and_says_what_it_needs():
    import opn2

    chip = opn2.YM2612(chip_type="ym3438")
    try:
        value = chip.set_ch3_mode("csm")
        assert value & 0xC0 == 0x80
    finally:
        chip.close()
