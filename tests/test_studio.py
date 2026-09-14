"""The interface contract: everything a front end renders, from the engine.

The point of these tests is that a graphical front end built on
studio.manifest() cannot drift from the engine, because there is nothing
in it to drift — no second copy of the directive list, no hand-written
table of channels. That only holds if the contract is actually checked
against the parser, which is what this file does.
"""

import json


def test_every_directive_in_the_catalogue_actually_parses():
    """An interface copies these literally into a score.

    So a directive here that the tracker will not accept is a card with a
    copy button that produces a broken track — the failure lands on
    whoever pressed it, several steps from the typo.
    """
    import studio
    import tracker

    for entry in studio.directives():
        code = entry["code"]
        if entry["kind"] == "directive":
            score = _score_around(code, entry)
        else:
            score = _score_with_cell(code, entry)
        try:
            events, _meta = tracker.loads(score)
        except Exception as error:
            raise AssertionError(
                f"{entry['kind']} {code!r} ({entry['category']}) does not "
                f"parse: {error}") from None
        assert events, f"{code!r} parsed to no events at all"


def _column_for(entry):
    chip = entry["chip"]
    return {"RP2A03": "nes0", "YM3812": "opl0"}.get(chip, "fm0")


def _score_around(code, entry):
    """A minimal score with the directive in it, on the right chip."""
    column = _column_for(entry)
    setup = {"fm0": "inst fm0 bass\n", "opl0": "inst opl0 opl_organ\n",
             "nes0": ""}[column]
    note = {"fm0": "A-3", "opl0": "A-3", "nes0": "A-3"}[column]
    # `pattern`/`order` need each other to be legal.
    if code.startswith("pattern "):
        return (f"bpm 120\nlpb 4\n{setup}cols {column}\n"
                f"{code}\n{note}\n...\norder verse\nend\n")
    return (f"bpm 120\nlpb 4\n{setup}cols {column}\n"
            f"{note}\n{code}\n...\nend\n")


def _score_with_cell(code, entry):
    column = {"RP2A03": "nes2", "YM3812": "opl0"}.get(entry["chip"], "fm0")
    # The cell examples name their own column by what they are.
    if code.startswith(("4m", "12:")):
        column = "nes3"
    elif code.startswith("kick@"):
        column = "dac"
    elif code == "kick":
        column = "nes4"
    elif code == "D-2":
        column = "nes2"
    setup = {"fm0": "inst fm0 bass\n", "opl0": "inst opl0 opl_organ\n"}.get(
        column, "")
    return f"bpm 120\nlpb 4\n{setup}cols {column}\n{code}\n...\nend\n"


def test_every_voice_names_a_column_the_tracker_accepts():
    # voices() is what an interface draws as its channel bus, and the
    # column is what it writes into a generated score. A name here that
    # the parser rejects is a fader wired to nothing.
    import studio
    import tracker

    valid = set(tracker.valid_columns())
    columns = [v["column"] for v in studio.voices()]
    assert len(columns) == len(set(columns)), "a column is listed twice"
    for column in columns:
        assert column in valid, \
            f"studio.voices() offers {column!r}, which the tracker rejects"
    # And the other way: nothing the parser accepts is missing from the
    # bus, or an interface built from this cannot reach part of the chip.
    assert set(columns) == valid, (
        f"the tracker accepts columns the channel bus does not list: "
        f"{sorted(valid - set(columns))}")


def test_every_preset_names_real_columns_and_a_real_chip():
    """Presets are authored by hand, which is exactly why they are checked.

    A preset is meant to be editable without touching Python. The cost of
    that is that nothing stops a typo, so the suite is what stops it.
    """
    import chipgen
    import studio
    import tracker

    valid = set(tracker.valid_columns())
    chips = set(chipgen.info()["chips"])
    seen = set()
    for preset in studio.presets():
        for field in ("id", "title", "chip", "prompt", "bpm", "voices"):
            assert field in preset, \
                f"preset {preset.get('id', '?')!r} has no {field!r}"
        assert preset["id"] not in seen, \
            f"two presets share the id {preset['id']!r}"
        seen.add(preset["id"])
        assert preset["chip"] in chips, (
            f"preset {preset['id']!r} names chip {preset['chip']!r}, which "
            f"the engine does not declare. Have: {sorted(chips)}")
        for column in preset["voices"]:
            assert column in valid, (
                f"preset {preset['id']!r} names column {column!r}, which "
                f"the tracker rejects")
        assert 20 <= preset["bpm"] <= 400, \
            f"preset {preset['id']!r} has an implausible bpm"


def test_the_manifest_is_json_and_carries_what_an_interface_needs():
    import studio

    data = studio.manifest()
    encoded = json.dumps(data)          # raises if anything is not JSON
    assert len(encoded) > 2000, "the manifest is suspiciously small"

    for key in ("schema", "version", "chips", "voices", "directives",
                "presets", "instruments", "samples", "effects", "health",
                "reference", "example"):
        assert key in data, f"the manifest has no {key!r}"

    assert isinstance(data["schema"], int)
    # The OPL2 was missing from chips for a while: the engine fully
    # supported it and an interface built from info() would never have
    # known the chip existed.
    assert set(data["chips"]) >= {"YM2612", "SN76489", "RP2A03", "YM3812"}, \
        f"a supported chip is undeclared: {sorted(data['chips'])}"


def test_the_example_in_the_manifest_renders():
    # It is the first thing an interface will put in its editor.
    import chipgen
    import studio

    result = chipgen.compose(studio.manifest()["example"])
    assert result.peak > 0.05, "the manifest's example renders near-silent"


def test_health_does_not_run_the_test_suite():
    """An interface asking "are you healthy" must not cost two minutes.

    The test count is read from a stamp file, and its absence is reported
    as None rather than by shelling out to the runner.
    """
    import time

    import studio

    start = time.time()
    got = studio.health()
    assert time.time() - start < 5.0, \
        "health() took seconds — it is doing real work"
    assert "cores" in got and "python" in got
    assert got["tests"] is None or isinstance(got["tests"], dict)
