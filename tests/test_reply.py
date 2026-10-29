"""A model's reply in; a playable score, or the exact reason not.

Stable generation is a loop — write, check, fix — and this is the check.
Each test is a reply shape models actually produce.
"""

GOOD_NES = """bpm 144
lpb 4
nes duty nes0 2
cols nes0 nes1 nes2 nes3 nes4
E-5:100 C-5:70 A-2 ... kick
...     ...    ... 9   ...
G-5     E-5    ... === snare
===     ===    === 9   ...
"""


def test_a_fenced_block_inside_prose_is_the_score():
    import reply

    text = "Here is your track!\n\n```trk\n" + GOOD_NES + "```\n\nEnjoy!"
    score, notes = reply.extract(text)
    assert score.strip() == GOOD_NES.strip()
    assert any("trk" in n for n in notes)


def test_a_code_block_is_not_mistaken_for_the_score():
    import reply

    text = ("```python\nimport chipgen\n```\n\n```\n" + GOOD_NES + "```")
    score, _ = reply.extract(text)
    assert score.startswith("bpm 144")


def test_a_reply_cut_off_mid_fence_is_kept_and_flagged():
    import reply

    score, notes = reply.extract("```trk\n" + GOOD_NES)
    assert score.startswith("bpm 144")
    assert any("cut off" in n for n in notes)


def test_prose_around_a_bare_score_is_dropped_and_counted():
    import reply

    score, notes = reply.extract("Sure! Here it is:\n" + GOOD_NES +
                                 "Let me know if you want changes.")
    assert score.startswith("bpm 144")
    assert "Let me know" not in score
    assert any("before" in n for n in notes)
    assert any("after" in n for n in notes)


def test_code_instead_of_a_score_is_an_error_with_a_fix():
    import reply

    verdict = reply.check("```python\nwrite_score('song.trk')\n```")
    assert not verdict.ok
    assert verdict.errors[0].rule == "no_score"


def test_repairs_only_what_has_one_meaning_and_says_so():
    import reply

    score, notes = reply.repair("cols fm0\nC–4 // lead\nG♯3\n")
    assert "C-4" in score and "G#3" in score
    assert "//" not in score
    assert len(notes) == 2


def test_repair_keeps_line_numbers():
    """Feedback says "line 7"; if repair dropped a line, line 7 of the
    repaired score is line 8 of the model's own."""
    import reply

    original = "cols fm0 fm1\n| --- | --- |\nC-4 E-4\n"
    repaired, _ = reply.repair(original)
    assert len(repaired.splitlines()) == len(original.splitlines())


def test_a_section_label_becomes_a_comment():
    import reply

    score, notes = reply.repair("cols fm0\n[Verse]\nBar 2:\nC-4\n")
    lines = score.splitlines()
    assert lines[1].startswith(";") and lines[2].startswith(";")
    assert lines[3] == "C-4"


def test_a_directive_is_never_mistaken_for_a_label():
    """`loop` is a directive and a word a label could start with."""
    import reply

    score, _ = reply.repair("cols fm0\nloop\nC-4\n")
    assert score.splitlines()[1] == "loop"


def test_row_numbers_come_off_only_when_the_count_then_fits():
    import reply

    score, notes = reply.repair("cols fm0 fm1\n01: C-4 E-4\n02 | ... ...\n")
    assert score.splitlines()[1:] == ["C-4 E-4", "... ..."]
    # A NES noise period in the first column is a cell, not a row number.
    score, _ = reply.repair("cols nes3 nes4\n9 kick\n")
    assert score.splitlines()[1] == "9 kick"


def test_every_parse_error_comes_back_in_one_pass():
    """The parser stops at the first error. Ten round trips for ten
    errors is how a small model runs out of context before it runs out of
    mistakes."""
    import reply

    score = ("bpm 120\nlpb 4\ninst fm0 bass\ninst fm1 bass\n"
             "cols fm0 fm1\nC-4\nC-4 E-4\nE-4\nC-4 xyz\n")
    verdict = reply.check(score)
    lines = sorted(i.line for i in verdict.errors)
    assert lines == [6, 8, 9], verdict.report()


def test_a_typo_in_the_last_row_is_reported_not_dropped():
    """The first extractor read `C-4 xyz` at the end of a bare score as
    prose after it, and the typo vanished instead of being reported."""
    import reply

    score, notes = reply.extract("bpm 120\nlpb 4\ncols fm0 fm1\n"
                                 "C-4 E-4\nC-4 xyz\n")
    assert score.splitlines()[-1] == "C-4 xyz", notes


def test_a_finding_after_a_set_aside_line_lands_on_its_own_line():
    import reply

    score = ("bpm 120\nlpb 4\ncols fm0 psg0\n"
             "broken\n... C-5:15\n")
    verdict = reply.check(score)
    by_rule = {i.rule: i for i in verdict.errors}
    assert by_rule["psg_15_is_silent"].line == 5, verdict.report()


def test_an_unknown_instrument_comes_back_with_the_nearest_names():
    import reply

    verdict = reply.check("bpm 120\nlpb 4\ninst fm0 piano\ncols fm0\nC-4\n")
    issue = verdict.errors[0]
    assert issue.rule == "closed_lists" and "e_piano" in issue.message


def test_an_fm_instrument_on_an_opl_column_is_named_as_such():
    import reply

    verdict = reply.check("bpm 120\nlpb 4\ninst opl0 bass\ncols opl0\nC-4\n")
    assert "OPL2" in verdict.errors[0].message
    assert "opl_bass" in verdict.errors[0].message


def test_a_column_from_another_chip_is_an_error_for_that_chip():
    import reply

    verdict = reply.check("bpm 120\nlpb 4\ncols nes0 psg0\nC-4 C-4\n",
                          chip="RP2A03")
    assert any(i.rule == "chip_columns" for i in verdict.errors)


def test_fm5_with_the_dac_is_an_error():
    import reply

    verdict = reply.check("bpm 120\nlpb 4\ninst fm5 bass\nvol dac 55\n"
                          "cols fm5 dac\nC-4 kick\n", chip="YM2612")
    assert any(i.rule == "fm5_vs_dac" for i in verdict.errors)


def test_silent_failures_come_back_on_the_right_line():
    import reply

    score = ("bpm 120\nlpb 4\ncols fm0 psg0\n"
             "... ...\nC-4 ...\n... C-5:15\n")
    verdict = reply.check(score, chip="YM2612")
    by_rule = {i.rule: i for i in verdict.errors}
    assert by_rule["fm_needs_inst"].line == 5, verdict.report()
    assert by_rule["psg_15_is_silent"].line == 6, verdict.report()


def test_a_score_too_short_for_the_request_is_an_error():
    import reply

    verdict = reply.check(GOOD_NES, chip="RP2A03", request={"bars": 4})
    assert any(i.rule == "row_count" for i in verdict.errors)


def test_a_correct_score_is_playable():
    import reply

    verdict = reply.check(GOOD_NES, chip="RP2A03")
    assert verdict.ok, verdict.report()
    assert verdict.stats["rows"] == 4


def test_feedback_never_mangles_the_hold_token():
    """The first version collapsed '..' to '.' to tidy punctuation, and
    turned `...` — the empty cell — into `..` in the instructions."""
    import reply

    verdict = reply.check("bpm 120\nlpb 4\ninst fm0 bass\ncols fm0 fm1\n"
                          "C-4\n")
    text = verdict.feedback()
    assert "`...`" in text
    assert " .. " not in text and "`..`" not in text


def test_feedback_quotes_the_line():
    """A model counts lines in its reply, not in the extracted score."""
    import reply

    verdict = reply.check("bpm 120\nlpb 4\ninst fm0 piano\ncols fm0\nC-4\n")
    assert "`inst fm0 piano`" in verdict.feedback()


def test_feedback_is_short():
    """It goes back into the model's context every round."""
    import prompts
    import reply

    bad = "bpm 120\nlpb 4\ncols fm0 fm1 psg0\n" + "C-4 E-4 C-5:15\n" * 40
    text = reply.check(bad, chip="YM2612").feedback()
    assert prompts.estimate_tokens(text) < 300, len(text)


def test_every_rule_the_checker_reports_is_one_a_model_was_told():
    import prompts
    import reply
    import sanity

    known = set(prompts.RULES) | set(sanity.SILENT_RULES) | {
        "parse", "sanity", "no_score", "silent", "engine_modified"}
    for fix_rule in ("cols_first", "chip_columns", "nes_noise_cells",
                     "note_format", "closed_lists"):
        assert fix_rule in known
    messages = ["3 cells but 2 columns", "unknown column 'x'",
                "'C-4' is not a NES noise cell", "'x' is not a note",
                "unknown sample 'x'", "something else"]
    for message in messages:
        assert reply._rule_for(message) in known, message
