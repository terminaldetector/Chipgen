"""The score language: cells, timing, recipes, and every kind of mistake."""

import support
from schism import model as M, notation, recipes

GOOD = """title Test
tempo 120
rpb 4
channels 2
smp thump wave=kick len=0.2
inst 1 name=Lead wave=saw oct=3-6 nna=cut venv=0:64,40:30s,60:30s,90:0 fade=40
inst 2 name=Kit kit=C-2:thump,D-2:snare
pattern a rows 32
{rows}
end
order a a
"""


def _rows(first="C-5 01 v64 H44 | C-2 02 ... ..."):
    rows = [first] + ["... .. ... ... | ... .. ... ..."] * 31
    return "\n".join(rows)


def _compile(text):
    return notation.compile_text(text)


def _errors(text):
    try:
        _compile(text)
    except notation.NotationError as error:
        return error.diagnostics
    raise AssertionError("the score compiled")


# -- cells --------------------------------------------------------------------
def test_a_cell_is_four_fields_and_prints_the_way_trackers_do():
    cell = notation.parse_cell("C#4 07 p32 J37")
    assert (cell.note, cell.instrument) == (M.note_number("C#4"), 7)
    assert M.volume_column_text(cell.volume) == "p32"
    assert M.effect_text(cell.effect, cell.param) == "J37"
    assert cell.text() == "C#4 07 p32 J37"
    assert notation.parse_cell("...").empty()
    assert notation.parse_cell("... .. ... ...").empty()


def test_lowercase_flats_and_short_empties_are_forgiven():
    assert notation.parse_cell("c-5 01 .. ..").note == M.note_number("C-5")
    assert notation.parse_cell("Db5 .. ... ...").note == M.note_number("C#5")
    assert notation.parse_cell("Bb3 .. ... ...").note == M.note_number("A#3")
    for bad in ("C-5 01 v64", "C-5 01 v64 H44 extra", "H-5 01 v64 ..."):
        try:
            notation.parse_cell(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} was accepted")


# -- timing -------------------------------------------------------------------
def test_tempo_is_solved_exactly_where_it_can_be():
    for bpm in (60, 90, 100, 118, 120, 124, 128, 140, 150, 174, 200):
        speed, tempo, actual = notation.solve_timing(bpm, 4)
        assert speed == 6 and tempo == bpm and actual == bpm, bpm
    speed, tempo, actual = notation.solve_timing(33, 4)
    assert 32 <= tempo <= 255 and abs(actual - 33) / 33 < 0.02
    speed, tempo, actual = notation.solve_timing(120, 8)
    assert abs(actual - 120) < 0.5 and 24 * tempo / (speed * 8) == actual
    try:
        notation.solve_timing(1000, 4)
    except ValueError:
        pass
    else:
        raise AssertionError("1000 BPM was solved")


def test_a_score_compiles_and_reports_what_it_made():
    module, report = _compile(GOOD.format(rows=_rows()))
    assert (report.speed, report.tempo, report.channels) == (6, 120, 2)
    assert report.patterns == 1 and report.orders == 2 and report.rows == 64
    assert abs(report.seconds - 64 * 6 * 2.5 / 120) < 1e-9
    assert len(module.instruments) == 2 and report.warnings == ()
    assert module.patterns[0].cell(0, 0).text() == "C-5 01 v64 H44"
    assert module.message == ""


def test_the_kit_maps_each_key_to_its_sample_at_natural_pitch():
    module, _ = _compile(GOOD.format(rows=_rows()))
    kit = module.instruments[1]
    kick = module.samples[kit.note_map[M.note_number("C-2") - 1][1] - 1]
    snare = module.samples[kit.note_map[M.note_number("D-2") - 1][1] - 1]
    assert kick is not snare and kick.frames != snare.frames
    for key in ("C-2", "D-2"):
        assert kit.note_map[M.note_number(key) - 1][0] == 60      # C-5
    assert kit.note_map[M.note_number("E-2") - 1][1] == 0         # unmapped


def test_instrument_numbers_stay_put_whatever_order_they_are_written_in():
    text = GOOD.format(rows=_rows()).replace(
        "inst 1 name=Lead", "inst 5 name=Late").replace("C-5 01", "C-5 05")
    module, _ = _compile(text)
    assert [i.name for i in module.instruments][:5][4] == "Late"
    assert module.instruments[1].name == "Kit"
    assert len(module.instruments) == 5


# -- mistakes -----------------------------------------------------------------
def test_every_problem_is_reported_in_one_pass_with_its_line_and_a_fix():
    bad = """title bad
tempo 120
inst 1 name=Lead wave=saww
inst 2 wave=saw nna=wrong oct=9-1
inst 3 wave=kick foo=1
inst 4 sample=nothing
pattern a rows 32
C-5 01 v64 ... | ...
C-5 07 v99 H4 | ... .. ... ...
end
pattern b rows 40
... .. ... ...
end
order a c
"""
    found = _errors(bad)
    assert len(found) >= 8
    lines = {d.line for d in found}
    assert {3, 4, 5, 6, 7, 9, 14} <= lines
    by_line = {d.line: d for d in found}
    assert "saww" in by_line[3].message
    assert "wrong" in by_line[4].message or "9-1" in by_line[4].message
    assert "foo" in by_line[5].message and "any instrument:" in by_line[5].fix
    assert "define it first" in by_line[6].fix
    assert "32 rows and has 2" in by_line[7].message
    assert "v99" in by_line[9].message
    assert by_line[3].source.startswith("inst 1")
    text = notation.NotationError(found).args[0]
    assert text.startswith(f"{len(found)} problems in the score")


def test_an_instrument_that_failed_is_not_blamed_again_on_every_note():
    text = GOOD.format(rows=_rows()).replace("wave=saw", "wave=saww")
    found = _errors(text)
    assert [d.line for d in found] == [6]


def test_orders_and_patterns_must_agree():
    base = GOOD.format(rows=_rows())
    assert any("not defined" in d.message
               for d in _errors(base.replace("order a a", "order a z")))
    extra = base + "\npattern b rows 32\n" + "\n".join(
        ["... .. ... ... | ... .. ... ..."] * 32) + "\nend\n"
    assert any("would not be played" in d.message for d in _errors(extra))
    assert any("no `order` line" in d.message
               for d in _errors(base.replace("order a a", "")))
    assert any("32-200" in d.message for d in _errors(
        base.replace("rows 32", "rows 16")))


def test_rest_writes_empty_rows_and_is_counted_like_any_other_row():
    short = GOOD.format(rows="C-5 01 v64 H44 | C-2 02 ... ...\nrest 31")
    module, report = _compile(short)
    assert module.patterns[0].rows == 32 and report.rows == 64
    assert module.patterns[0].cell(0, 0).text() == "C-5 01 v64 H44"
    assert all(cell.empty() for row in module.patterns[0].cells[1:]
               for cell in row)
    longhand, _ = _compile(GOOD.format(rows=_rows()))
    assert module.patterns[0].cells[0] == longhand.patterns[0].cells[0]
    # the count is part of the total, so a miscount is named
    found = _errors(GOOD.format(rows="C-5 01 v64 H44 | C-2 02 ... ...\nrest 30"))
    assert any("32 rows and has 31" in d.message for d in found)
    for bad in ("rest 0", "rest x", "rest 999"):
        found = _errors(GOOD.format(rows=_rows() + "\n" + bad))
        assert any("rest wants a count" in d.message for d in found), bad


def test_a_row_with_the_wrong_number_of_channels_is_named():
    found = _errors(GOOD.format(rows=_rows("C-5 01 v64 ...")))
    assert any("1 cells but this score has 2 channels" in d.message
               for d in found)


# -- silent failures ----------------------------------------------------------
def test_a_note_before_any_instrument_is_a_warning():
    module, report = _compile(GOOD.format(rows=_rows("C-5 .. ... ... | ...")))
    assert any("before any instrument" in w for w in report.warnings)


def test_a_key_off_that_cannot_end_the_note_is_a_warning():
    rows = ["C-5 01 v64 ... | ..."] + ["... .. ... ... | ..."] * 3 \
        + ["=== .. ... ... | ..."] + ["... .. ... ... | ..."] * 27
    text = GOOD.replace("venv=0:64,40:30s,60:30s,90:0 fade=40", "") \
        .format(rows="\n".join(rows))
    _, report = _compile(text)
    assert any("does nothing" in w and "fade=" in w for w in report.warnings)
    # the same note-off on an instrument that does fade is fine
    ok = GOOD.format(rows="\n".join(rows))
    assert not any("does nothing" in w for w in _compile(ok)[1].warnings)


def test_a_note_the_instrument_has_no_sample_for_is_a_warning():
    # inst 1 covers octaves 3-6 as a *range of samples*, but the lowest and
    # highest samples are stretched to the ends of the keyboard; a kit is
    # not: its unmapped keys are silent
    rows = ["... .. ... ... | E-2 02 ... ..."] + ["... .. ... ... | ..."] * 31
    _, report = _compile(GOOD.format(rows="\n".join(rows)))
    assert any("no sample for E-2" in w for w in report.warnings)


# -- limits -------------------------------------------------------------------
def _many(instruments, octaves="0-9"):
    lines = [f"inst {n} wave=sine oct={octaves}"
             for n in range(1, instruments + 1)]
    return ("tempo 120\nchannels 1\n" + "\n".join(lines)
            + "\npattern a rows 32\nC-5 01 v64 ...\nrest 31\nend\norder a\n")


def test_more_samples_than_schism_plays_is_an_error_with_the_way_out():
    found = _errors(_many(30))                  # ten samples an instrument
    assert any("Schism Tracker plays at most 235" in d.message for d in found)
    assert any("narrow oct=" in d.fix for d in found)


def test_more_samples_than_impulse_tracker_loads_is_a_warning():
    module, report = _compile(_many(11))        # 110 samples: over 99, under 235
    assert len(module.samples) > 99
    assert any("Impulse Tracker 2.14 itself loads only 99" in w
               for w in report.warnings)
    _, small = _compile(_many(3))
    assert small.warnings == ()


# -- recipes ------------------------------------------------------------------
def test_every_recipe_builds_with_its_defaults():
    """The table is the documentation, so a row that does not build is a
    lie in the docs."""
    for kind in recipes.WAVES:
        lib = recipes.Library()
        mod = M.Module()
        options = {"wave": kind}
        recipes.compile_instrument(1, options, lib, mod)
        assert mod.samples and mod.instruments[0].note_map[60][1] > 0, kind


def test_envelope_text_means_what_the_docs_say():
    env = recipes.parse_envelope("0:64,10:60,40:30s,80:30s,120:0", "venv")
    assert env.nodes == [(0, 64), (10, 60), (40, 30), (80, 30), (120, 0)]
    assert env.sustain == (2, 3) and env.loop is None
    pan = recipes.parse_envelope("0:-20,50:20l,90:-20l", "penv")
    assert pan.loop == (1, 2)
    filt = recipes.parse_envelope("0:10,20:60", "fenv")
    assert filt.filter
    for bad in ("0:64,0:20", "5:64", "0:99", "0:64,20", "0:64;10:3", ""):
        try:
            recipes.parse_envelope(bad, "venv")
        except recipes.RecipeError:
            continue
        raise AssertionError(f"{bad!r} was accepted")


def test_ienv_and_fenv_are_one_slot_and_say_so():
    text = GOOD.format(rows=_rows()).replace(
        "fade=40", "fade=40 ienv=0:0,10:10 fenv=0:10,10:20")
    found = _errors(text)
    assert "same envelope slot" in found[0].message
