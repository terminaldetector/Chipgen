"""Writing FM registers while the note sounds — the live-FM layer."""


def test_the_operator_number_is_the_block_diagram_one():
    # The register offsets ascend op1, op3, op2, op4. A score saying
    # "operator 2" means the one in the diagram, and addressing offset
    # 0x04 instead of 0x08 changes a different operator — plausibly, and
    # silently.
    import opn2

    chip = opn2.YM2612(chip_type="ym3438")
    try:
        for operator, expected in ((1, 0x00), (2, 0x08), (3, 0x04), (4, 0x0C)):
            chip.set_operator(0, operator, "tl", 33)
            assert (0, 0x40 + expected) in chip._shadow, \
                f"op{operator} did not write offset 0x{expected:02X}"
            assert chip._shadow[(0, 0x40 + expected)] == 33
    finally:
        chip.close()


def test_writing_one_field_preserves_the_one_sharing_its_register():
    # dt shares a byte with mul, ks with ar, am with d1r, sl with rr. The
    # chip cannot be read back, so the other half has to come from a
    # shadow — writing mul alone must not zero the detune.
    import opn2

    chip = opn2.YM2612(chip_type="ym3438")
    try:
        for first, second, addr in (("dt", "mul", 0x30), ("ks", "ar", 0x50),
                                    ("am", "d1r", 0x60), ("sl", "rr", 0x80)):
            chip.set_operator(0, 1, first, 3)
            chip.set_operator(0, 1, second, 5)
            byte = chip._shadow[(0, addr)]
            low = {"mul": 0xF, "ar": 0x1F, "d1r": 0x1F, "rr": 0xF}[second]
            shift = {"dt": 4, "ks": 6, "am": 7, "sl": 4}[first]
            assert (byte >> shift) & 0x7 == 3 or first in ("am",), \
                f"{first} was lost when {second} was written: 0x{byte:02X}"
            assert byte & low == 5, \
                f"{second} did not land: 0x{byte:02X}"
    finally:
        chip.close()


def test_algorithm_change_keeps_the_feedback_it_was_not_given():
    import instruments
    import opn2

    chip = opn2.YM2612(chip_type="ym3438")
    try:
        patch = instruments.get("deep_bass")
        chip.set_instrument(0, patch)
        value = chip.set_algorithm(0, algorithm=4)
        assert value & 0x7 == 4
        assert (value >> 3) & 0x7 == patch.feedback, \
            "feedback was zeroed by an algorithm change"
    finally:
        chip.close()


def test_an_algorithm_change_moves_the_carriers():
    # carrier_indices() is what velocity and set_volume attenuate. After
    # an algorithm change the carriers are different operators, so the
    # stored patch has to follow or the next note is attenuated on a
    # modulator.
    import instruments
    import opn2

    chip = opn2.YM2612(chip_type="ym3438")
    try:
        patch = instruments.get("deep_bass").copy()
        chip.set_instrument(0, patch)
        before = patch.carrier_indices()
        chip.set_algorithm(0, algorithm=7)
        assert patch.carrier_indices() != before
        assert len(patch.carrier_indices()) == 4, "algorithm 7 is all carriers"
    finally:
        chip.close()


def test_the_op_directive_reaches_the_score():
    import events as E
    import tracker

    events, _meta = tracker.loads(
        "cols fm0\ninst fm0 deep_bass\nC-3\nop fm0 4 tl 12\n...\n"
        "alg fm0 4 6\n...\nend\n")
    ops = [e for e in events if isinstance(e, E.FMOperator)]
    algs = [e for e in events if isinstance(e, E.FMAlgorithm)]
    assert len(ops) == 1 and ops[0].operator == 4 and ops[0].field == "tl"
    assert len(algs) == 1 and algs[0].algorithm == 4 and algs[0].feedback == 6


def test_an_operator_write_lands_between_the_rows_around_it():
    # A directive flushes the pending rows first, so it takes effect where
    # it is written rather than at the start of the pattern.
    import events as E
    import tracker

    events, _meta = tracker.loads(
        "cols fm0\ninst fm0 deep_bass\nC-3\n...\nop fm0 1 tl 40\n...\nend\n")
    kinds = [type(e).__name__ for e in events]
    waits_before = sum(1 for k in kinds[:kinds.index("FMOperator")]
                       if k == "Wait")
    assert waits_before >= 1, "the operator write ran before any row elapsed"


def test_a_mid_note_operator_write_changes_the_timbre():
    # The point of the whole layer. A louder modulator is brighter and a
    # quieter one is darker, measured on the second half of one held note.
    import analysis
    import chipgen

    def brightness_of_second_half(directive):
        score = (f"bpm 60\nlpb 4\ninst fm0 deep_bass\ncols fm0\n"
                 f"C-3\n...\n{directive}\n...\n...\nend\n")
        result = chipgen.compose(score)
        mono = list(analysis.to_mono(result.audio))
        tail = mono[len(mono) // 2:]
        f0 = analysis.fundamental(tail, result.sample_rate)
        if not f0:
            return 0.0
        return analysis.spectral_centroid(tail, result.sample_rate) / f0

    plain = brightness_of_second_half("; nothing")
    louder = brightness_of_second_half("op fm0 1 tl 10")
    quieter = brightness_of_second_half("op fm0 1 tl 90")
    assert louder > plain * 1.1, \
        f"a louder modulator did not brighten the note ({plain:.2f} -> {louder:.2f})"
    assert quieter < plain, \
        f"a quieter modulator did not darken it ({plain:.2f} -> {quieter:.2f})"


def test_a_bad_operator_or_field_is_refused_with_the_options():
    import tracker

    for score, wanted in (
            ("cols fm0\nop fm0 9 tl 20\nend\n", "1-4"),
            ("cols fm0\nop fm0 1 zz 20\nend\n", "Valid:"),
            ("cols fm0\nalg fm0 9\nend\n", "0-7"),
    ):
        try:
            tracker.loads(score)
        except tracker.TrackerError as error:
            assert wanted in str(error), f"{score!r}: {error}"
        else:
            raise AssertionError(f"{score!r} was accepted")


def test_the_next_note_reloads_the_patch_over_an_operator_write():
    # Hardware behaviour, and the reason real drivers rewrite these every
    # tick rather than once. Worth pinning so nobody "fixes" it.
    import instruments
    import opn2

    chip = opn2.YM2612(chip_type="ym3438")
    try:
        patch = instruments.get("deep_bass")
        chip.set_instrument(0, patch)
        chip.set_operator(0, 1, "tl", 99)
        assert chip._shadow[(0, 0x40)] == 99
        chip.set_instrument(0, patch)
        assert chip._shadow[(0, 0x40)] != 99, \
            "a patch reload left the hand-written Total Level in place"
    finally:
        chip.close()
