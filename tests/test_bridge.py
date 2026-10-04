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

        # A ratchet, not a budget: it exists to catch a corpus file or a
        # WAV wandering into the archive, so it is kept just above the
        # real size rather than left loose. Raised from 400 KB when the
        # channel-3 special mode and the OPL2 effect column were
        # documented — both are silent-failure notes, which is the one
        # kind of prose worth the bytes. Composition at 401 KB: 228 KB
        # python/, 75 KB tests/ (the executable spec, and what a model
        # runs to confirm the emulation works in its sandbox), 26 KB
        # bridge/, 25 KB core/, 24 KB of docs.
        size = os.path.getsize(first)
        # Raised to 470 KB for levels.py and the DAC fader, which exist
        # because "the drums are there but the instruments aren't" took a
        # manual channel-by-channel investigation to answer and should
        # have been one command; then to 500 KB when the examples/ .trk
        # scores started shipping — the archive had an examples directory
        # with no scores in it, and a README naming five files that were
        # not there. All eight of those files cost 11.8 KB compressed.
        # and to 540 KB for the arrangement layer: arrange.py, the
        # Score->events inverse, and the tracker's missing NES dump half.
        # Verified as source rather than data — the ten largest entries
        # are all .py or .md, the biggest being tracker.py at 17.3 KB.
        # Then to 600 KB for the model-facing half: per-family briefings,
        # reply.py's checker, the GBNF grammar, the OpenAI-compatible
        # client, the integrity guard and their tests — 31.9 KB of new
        # files compressed, the largest reply.py at 7.9 KB — plus the
        # generated AGENTS.md and integrity.json (5.4 KB together).
        # Then to 630 KB for MP3 delivery: mp3.py, its generated tables,
        # and its test with the reference decoder — 22 KB compressed, to
        # stop a render costing ten megabytes a minute to hand back.
        # Then to 800 KB for the instrument/driver synthesis layer: 25
        # modules and their tests (104 KB compressed), the starter bank of
        # 42 instruments and nine example scenarios (data, ~25 KB), and the
        # guide a model reads before it asks for a sound the bank lacks.
        assert size < 800 * 1024, f"the bridge archive grew to {size // 1024} KB"

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


# --------------------------------------------------------------------------
# What the archive tells a model before it reads anything, and how it
# notices when the model has changed the engine anyway.
# --------------------------------------------------------------------------
def _built_archive(directory):
    sys.path.insert(0, BRIDGE)
    import make_zip
    return make_zip.build(os.path.join(directory, "bridge.zip"))


def test_the_archive_carries_agents_md_and_a_work_directory():
    """AGENTS.md is what several agent tools read before anything else —
    the one document a model that skims is sure to see."""
    import zipfile

    import prompts

    with support.TempDir() as directory:
        with zipfile.ZipFile(_built_archive(directory)) as archive:
            agents = archive.read("chipgen/AGENTS.md").decode("utf-8")
            names = set(archive.namelist())
    assert agents == prompts.agents_md()
    assert "Never edit" in agents and "--check" in agents
    assert "chipgen/work/README.md" in names


def test_agents_md_is_not_in_the_repository():
    """In a checkout an agent is meant to change the engine; a file at
    the root telling it not to would be wrong there."""
    if os.path.exists(os.path.join(support.ROOT, "bridge", "integrity.json")):
        support.skip("this is the bridge archive, which carries AGENTS.md "
                     "by design")
    assert not os.path.exists(os.path.join(support.ROOT, "AGENTS.md"))


def test_the_archive_checksums_the_engine():
    import json
    import zipfile

    with support.TempDir() as directory:
        with zipfile.ZipFile(_built_archive(directory)) as archive:
            files = json.loads(archive.read(
                "chipgen/bridge/integrity.json"))["files"]
            data = archive.read("chipgen/python/tracker.py")
    import integrity
    assert files["python/tracker.py"] == integrity.digest(data)
    assert "core/ym3438.c" in files and "tests/run_tests.py" in files
    assert not any(name.endswith(".md") for name in files), \
        "docs are not the engine; editing them changes no render"


def test_an_edited_engine_is_named_by_check():
    """Not refused — refusing invites deleting the checksum next — but
    named, where the model and the person reading it both see it."""
    import zipfile

    import integrity

    with support.TempDir() as directory:
        with zipfile.ZipFile(_built_archive(directory)) as archive:
            archive.extractall(directory)
        root = os.path.join(directory, "chipgen")
        assert integrity.changed(root) == []

        mixer = os.path.join(root, "python", "mixer.py")
        with open(mixer, "a", encoding="utf-8") as handle:
            handle.write("\nLOUDER = True  # a model's improvement\n")
        assert integrity.changed(root) == ["python/mixer.py"]

        score = os.path.join(root, "work", "song.trk")
        with open(score, "w", encoding="utf-8") as handle:
            handle.write("bpm 120\nlpb 4\ninst fm0 bass\ncols fm0\nC-4\n")
        finished = subprocess.run(
            [sys.executable, "python/chipgen.py", "work/song.trk",
             "--check"], cwd=root, capture_output=True, text=True,
            timeout=300)
    assert finished.returncode == 1, finished.stdout
    assert "engine_modified" in finished.stdout
    assert "python/mixer.py" in finished.stdout


def test_a_checkout_pays_nothing_for_the_guard():
    import integrity

    assert integrity.changed() == []
    assert integrity.warning() == ""
