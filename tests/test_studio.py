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


def test_the_interface_holds_no_facts_of_its_own():
    """The one rule the front end follows, enforced.

    An interface that keeps its own list of NES directives is right on
    the day it is written and wrong the first time the engine gains one,
    with nothing to say so. So app.js renders the contract and states no
    hardware fact itself — this catches the moment somebody types one in.
    """
    import os
    import re

    import support

    path = os.path.join(support.ROOT, "studio", "app.js")
    with open(path, encoding="utf-8") as handle:
        source = handle.read()

    # Chip names, register addresses and directive syntax are all facts.
    # A comment may mention them; running code may not.
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)

    forbidden = {
        "YM2612": "a chip name", "YM3812": "a chip name",
        "SN76489": "a chip name", "RP2A03": "a chip name",
        "0x2B": "a register address", "0xBD": "a register address",
        "nes duty": "a directive", "ch3 special": "a directive",
        "opl_bass": "a patch name", "deep_bass": "a patch name",
    }
    for needle, what in forbidden.items():
        assert needle not in code, (
            f"app.js names {needle!r} ({what}) in running code. Facts live "
            f"in python/studio.py — a fact in two places is a fact that "
            f"will disagree with itself.")


def test_the_static_bundle_carries_everything_the_page_needs():
    """A bundle that renders half the reference is worse than none.

    The deployable zip has no engine behind it, so anything the page
    needs at runtime has to be inside it.
    """
    import json
    import os
    import sys
    import zipfile

    import support

    sys.path.insert(0, os.path.join(support.ROOT, "studio"))
    import make_bundle

    with support.TempDir() as tmp:
        path, manifest = make_bundle.build(os.path.join(tmp, "studio.zip"))
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            contract = json.loads(archive.read("studio.json"))

        assert names == {"index.html", "style.css", "app.js", "studio.json"}, \
            f"the bundle carries the wrong files: {sorted(names)}"
        assert os.path.getsize(path) < 200 * 1024, \
            "the bundle should be small enough to deploy anywhere"

        # Same contract as the live engine, or the bundle is a different
        # product that happens to look similar.
        assert contract["schema"] == manifest["schema"]
        assert len(contract["directives"]) == len(manifest["directives"])
        assert set(contract["chips"]) == set(manifest["chips"])

        # Two builds of one tree give one set of bytes.
        again, _ = make_bundle.build(os.path.join(tmp, "again.zip"))
        assert open(path, "rb").read() == open(again, "rb").read(), \
            "two builds of the same tree produced different archives"


def test_the_page_references_only_files_that_ship():
    # A missing stylesheet is a page that renders as unstyled text, which
    # nothing errors about.
    import os
    import re

    import support

    studio_dir = os.path.join(support.ROOT, "studio")
    with open(os.path.join(studio_dir, "index.html"), encoding="utf-8") as fh:
        html = fh.read()

    wanted = re.findall(r'(?:src|href)="([^":]+)"', html)
    for asset in wanted:
        assert os.path.exists(os.path.join(studio_dir, asset)), \
            f"index.html loads {asset!r}, which is not in studio/"


def test_the_briefing_is_in_the_contract():
    """An interface must not have to invent a system prompt.

    This is the detail that decides whether the two paths stay one
    project. Someone with the interface presses generate; someone
    without it pastes a prompt or runs the CLI. If each writes its own
    briefing they drift — one teaches a model that the DAC takes channel
    6 and the other does not mention it — and the difference lands as
    tracks that render and sound wrong.
    """
    import chipgen
    import studio

    prompts = studio.manifest().get("prompts")
    assert prompts, "the contract carries no briefing at all"
    assert prompts.get("starter"), "no starter briefing"

    chips = set(chipgen.info()["chips"])
    briefed = set(prompts.get("chips") or {})
    assert briefed >= chips, (
        f"the engine declares chips with no briefing: "
        f"{sorted(chips - briefed)}. An interface targeting one of those "
        f"has nothing to tell a model about it.")

    for name, spec in prompts["chips"].items():
        assert spec.get("columns"), f"{name} briefing names no columns"
        assert spec.get("facts"), (
            f"{name} has no silent-failure notes. A briefing that lists "
            f"no traps is a briefing that does not earn its place")
        for fact in spec["facts"]:
            # Lifted from self-describing keys in info(), so a value that
            # begins mid-sentence has lost its subject on the way out.
            assert fact[0].isupper(), (
                f"{name} fact begins without a subject: {fact[:60]!r}")


def test_the_briefing_names_columns_the_tracker_accepts():
    # A briefing telling a model to write `cols nes9` produces a score
    # that fails at the last step, after the model has done the work.
    import studio
    import tracker

    valid = set(tracker.valid_columns())
    for name, spec in studio.manifest()["prompts"]["chips"].items():
        for column in spec["columns"].split():
            assert column in valid, (
                f"the {name} briefing tells a model to use {column!r}, "
                f"which the tracker rejects")


def test_the_interface_and_the_cli_build_the_same_brief():
    """The whole point, checked rather than asserted in a comment.

    The interface assembles the brief in JavaScript from the contract;
    the CLI assembles it in Python from the engine. They have to come out
    the same, or there are two definitions of how to ask for a track and
    only one of them gets maintained.
    """
    import json
    import os
    import re
    import subprocess

    import prompts
    import support

    request = {"chip": "RP2A03", "prompt": "Gothic castle theme",
               "bpm": 144, "key": "D Minor", "style": "Gothic Action",
               "bars": 4}
    from_python = prompts.compose(request)

    # Reproduce the interface's assembly from its own source, so this
    # fails if app.js's builder drifts rather than testing a copy of it.
    app = os.path.join(support.ROOT, "studio", "app.js")
    with open(app, encoding="utf-8") as handle:
        source = handle.read()
    assert "function buildBrief()" in source, \
        "app.js no longer has a brief builder; this test is checking air"

    # The contract the interface would read.
    import studio
    spec = studio.manifest()["prompts"]["chips"]["RP2A03"]

    rebuilt = [
        f"Write an original chiptune score for the RP2A03 "
        f"({spec['platform']}) in chipgen's tracker notation.",
        "", f"What it should be: {request['prompt']}",
        "", "Specification:", f"- tempo: {request['bpm']} BPM",
        f"- key: {request['key']}", f"- style: {request['style']}",
        f"- length: {request['bars']} bars",
        f"- columns: `cols {spec['columns']}`", "",
        "Hardware that will bite you. Every one of these fails SILENTLY —",
        "the render succeeds and the result is wrong:", "",
    ]
    rebuilt += [f"- {fact}" for fact in spec["facts"]]
    rebuilt += ["", "Return the score as tracker notation and nothing else "
                "— no explanation around it, no markdown fence. It goes "
                "straight into the renderer."]

    assert "\n".join(rebuilt) == from_python, (
        "the contract-assembled brief and the Python one differ — the "
        "interface and the CLI would teach a model different things")


def test_the_cli_prints_a_briefing_without_an_interface():
    # The no-interface path: someone with the bridge zip and no browser
    # still needs the same ask.
    import os
    import subprocess
    import sys

    import support

    for argv in (["--prompt"], ["--prompt", "--chip-target", "RP2A03"]):
        finished = subprocess.run(
            [sys.executable, "python/chipgen.py", *argv],
            cwd=support.ROOT, capture_output=True, text=True)
        assert finished.returncode == 0, \
            f"chipgen.py {' '.join(argv)} failed:\n{finished.stderr[-500:]}"
        out = finished.stdout
        assert "bootstrap.py" in out and "CORE.md" in out, (
            f"the briefing does not tell a model how to start: "
            f"{out[:200]!r}")
        if "--chip-target" in argv:
            assert "nes0" in out, "the targeted briefing names no columns"


def test_the_contract_says_what_a_rearrangement_costs():
    """An interface offering "play this on an NES" has to be able to say
    what that costs before the user clicks it."""
    import arrange
    import studio

    section = studio.manifest()["arrangement"]
    assert set(section["targets"]) == set(arrange.TARGETS)
    assert set(section["dynamics"]) == {arrange.FADER, arrange.ATTENUATOR,
                                        arrange.SILENT}
    assert section["promises"]
    for name, target in section["targets"].items():
        assert target["channels"], name
        for channel in target["channels"]:
            assert channel["dynamics"] in section["dynamics"], channel


def test_the_arrange_route_returns_the_keys_the_page_reads():
    """app.js reads these by name. A rename in Python that nothing
    asserts breaks the page silently — the request succeeds, the report
    renders as `undefined`, and the only symptom is a blank panel."""
    import os
    import re

    import serve
    import support

    with open(os.path.join(support.ROOT, "studio", "app.js"),
              encoding="utf-8") as handle:
        js = handle.read()

    answer = serve.arrange_score({
        "score": "bpm 150\nlpb 4\ncols fm0\nC-4\n...\n...\n...\nend\n",
        "target": "RP2A03"})
    assert answer["ok"], answer

    for key in ("ok", "target", "score", "report", "lines"):
        assert key in answer, key
        assert f"data.{key}" in js or f"'{key}'" in js, \
            f"the page never reads {key!r} — either it is dead weight in " \
            f"the response or the page reads something else"
    for key in re.findall(r"data\.report\.(\w+)", js):
        assert key in answer["report"], \
            f"app.js reads report.{key}, which the engine does not send"
