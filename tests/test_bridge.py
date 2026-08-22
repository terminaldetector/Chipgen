"""The zip bridge: unzip into an empty directory and everything works."""

import json
import os
import subprocess
import sys
import zipfile

import support

BRIDGE = os.path.join(support.ROOT, "bridge")


def test_every_test_file_is_registered_with_the_bare_runner():
    # pytest auto-discovers test_*.py; tests/run_tests.py does not — it
    # only runs what is listed in its own MODULES constant. test_sanity.py
    # existed for a full session, ran fine under pytest, and was silently
    # never executed by `python3 tests/run_tests.py` because nobody added
    # it to that list — the exact failure mode this test exists to catch,
    # on this project's own test suite.
    import run_tests

    on_disk = {
        os.path.splitext(name)[0]
        for name in os.listdir(support.ROOT + "/tests")
        if name.startswith("test_") and name.endswith(".py")
    }
    missing = on_disk - set(run_tests.MODULES)
    assert not missing, (
        f"{missing} exist(s) as test files but tests/run_tests.py's "
        f"MODULES list does not mention them — `python3 tests/run_tests.py` "
        f"is silently not running them, even though pytest would")


def test_manifest_on_disk_matches_the_code():
    # bridge/manifest.json is what a model reads when it has the archive
    # but has not run anything yet. If it drifts from chipgen.info(), the
    # model is being told about instruments and events that do not exist.
    import chipgen
    with open(os.path.join(BRIDGE, "manifest.json"), encoding="utf-8") as fh:
        stored = json.load(fh)
    live = chipgen.info()
    for key in ("events", "instruments", "dac_samples", "tracker_syntax",
                "inputs", "outputs", "example", "version"):
        assert stored[key] == live[key], \
            f"manifest.json is stale for {key!r} — rerun bridge/bootstrap.py --manifest"


def test_manifest_documents_every_event_and_instrument():
    import events
    import instruments
    import samples
    with open(os.path.join(BRIDGE, "manifest.json"), encoding="utf-8") as fh:
        stored = json.load(fh)
    assert set(stored["events"]) == set(events.event_types())
    assert set(stored["instruments"]) == set(instruments.BANK)
    assert set(stored["dac_samples"]) == set(samples.names())


def test_start_here_names_things_that_exist():
    import instruments
    import samples
    with open(os.path.join(support.ROOT, "START_HERE.md"), encoding="utf-8") as fh:
        text = fh.read()
    for name in instruments.BANK:
        assert f"`{name}`" in text, f"START_HERE.md never mentions the {name} patch"
    for name in samples.names():
        assert name in text, f"START_HERE.md never mentions the {name} sample"
    for path in ("bridge/bootstrap.py", "python/chipgen.py", "bridge/manifest.json"):
        assert path in text
        assert os.path.exists(os.path.join(support.ROOT, path)), \
            f"START_HERE.md points at {path}, which is not there"


def test_the_built_in_example_actually_renders():
    # It is the first thing a model will copy. It has to be correct.
    import chipgen
    result = chipgen.compose(chipgen.EXAMPLE)
    assert not result.warnings, f"the example needs repairs: {result.warnings}"
    assert result.peak > 0.1 and result.duration > 0.5


def test_archive_is_lean_and_reproducible():
    sys.path.insert(0, BRIDGE)
    import make_zip

    with support.TempDir() as directory:
        first = make_zip.build(os.path.join(directory, "a.zip"))
        second = make_zip.build(os.path.join(directory, "b.zip"))
        assert open(first, "rb").read() == open(second, "rb").read(), \
            "two builds of the same tree must produce identical archives"

        size = os.path.getsize(first)
        assert size < 400 * 1024, f"the bridge archive grew to {size // 1024} KB"

        with zipfile.ZipFile(first) as archive:
            names = archive.namelist()
        assert "chipgen/START_HERE.md" in names
        assert "chipgen/bridge/bootstrap.py" in names
        assert "chipgen/bridge/manifest.json" in names
        assert "chipgen/python/chipgen.py" in names
        assert "chipgen/core/ym3438.c" in names
        assert "chipgen/core/NUKED_OPN2_LICENSE" in names, \
            "the LGPL core cannot ship without its licence"
        # Things that would just waste a model's context
        assert not any(n.endswith(".wav") for n in names)
        assert not any(n.endswith(".so") for n in names)
        assert not any("__pycache__" in n for n in names)


def test_cold_unzip_bootstraps_and_renders():
    """The bridge, end to end, the way a model meets it."""
    sys.path.insert(0, BRIDGE)
    import make_zip

    with support.TempDir() as directory:
        archive = make_zip.build(os.path.join(directory, "bridge.zip"))
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(directory)
        root = os.path.join(directory, "chipgen")

        boot = subprocess.run([sys.executable, "bridge/bootstrap.py", "--json"],
                              cwd=root, capture_output=True, text=True)
        assert boot.returncode == 0, f"bootstrap failed:\n{boot.stderr}"
        report = json.loads(boot.stdout)
        assert report["ready"], report
        assert report["self_test"]["ok"]
        assert report["self_test"]["peak"] > 0.05

        with open(os.path.join(root, "song.trk"), "w", encoding="utf-8") as fh:
            fh.write("bpm 150\nlpb 4\ninst fm0 bass\ncols fm0 psg0 dac\n\n"
                     "A-2 A-4 kick\n... C-5 hat\n=== === ...\n")

        render = subprocess.run(
            [sys.executable, "python/chipgen.py", "song.trk",
             "-o", "song.wav", "--vgm", "song.vgm"],
            cwd=root, capture_output=True, text=True)
        assert render.returncode == 0, f"render failed:\n{render.stderr}"
        assert os.path.getsize(os.path.join(root, "song.wav")) > 1000
        assert os.path.getsize(os.path.join(root, "song.vgm")) > 100


def test_bootstrap_survives_a_sandbox_with_no_compiler():
    sys.path.insert(0, BRIDGE)
    import make_zip

    with support.TempDir() as directory:
        archive = make_zip.build(os.path.join(directory, "bridge.zip"))
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(directory)
        root = os.path.join(directory, "chipgen")

        empty_path = os.path.join(directory, "empty-bin")
        os.makedirs(empty_path, exist_ok=True)
        environment = dict(os.environ, PATH=empty_path, CC="/nonexistent",
                           CHIPGEN_BACKEND="fallback")
        proc = subprocess.run([sys.executable, "bridge/bootstrap.py", "--json"],
                              cwd=root, capture_output=True, text=True,
                              env=environment)
        assert proc.returncode == 0, f"bootstrap failed without a compiler:\n{proc.stderr}"
        report = json.loads(proc.stdout)
        assert report["ready"], report
        assert report["cores"]["ym2612"] == "fallback"
        assert report["self_test"]["ok"], "the pure-Python path must still make sound"


def test_cloud_prompt_is_generated_from_the_code():
    # The prompt used to be a string literal, so adding an instrument left
    # the model unaware it existed — a silent failure. It is now derived,
    # and this is the check that keeps it derived.
    import cloud_generator
    import instruments
    import samples

    for fmt in ("tracker", "json"):
        prompt = cloud_generator.build_system_prompt(fmt, 192.0)
        for name in instruments.BANK:
            assert name in prompt, f"the {fmt} prompt never mentions {name}"
        for name in samples.names():
            assert name in prompt, f"the {fmt} prompt never mentions {name}"

    json_prompt = cloud_generator.build_system_prompt("json", 192.0)
    import events
    for type_name in events.event_types():
        assert type_name in json_prompt, \
            f"the JSON prompt never mentions the {type_name} event"


def test_cloud_response_parsing_survives_the_usual_model_slips():
    import cloud_generator
    fenced = '```json\n[{"type": "Wait", "ticks": 5}]\n```'
    events, warnings = cloud_generator.parse_and_validate(
        cloud_generator._extract_json_array(fenced))
    assert [type(e).__name__ for e in events] == ["Wait", "End"]
    assert warnings, "the missing End should be reported"

    chatty = 'Here is your pattern:\n[{"type": "Wait", "ticks": 5}]\nEnjoy!'
    assert cloud_generator._extract_json_array(chatty) == [{"type": "Wait", "ticks": 5}]

    assert cloud_generator.strip_fences("```\nrows\n```") == "rows"
