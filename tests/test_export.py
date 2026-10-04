"""What a build says about a module: the key map, the sidecar, the peak, the
strict mode and the presets. The CLI is called in-process; the tests that
need a player to measure a peak skip without libopenmpt."""

import contextlib
import io
import json
import os
import tempfile

import support
from schism import export, notation, render
from schism import model as M
from schism.__main__ import main

SCORE = """title Export test
tempo 120
channels 3
smp thump wave=kick
smp tick wave=rim
inst 1 name=Lead wave=pulse duty=0.25 oct=3-5 nna=cut fade=60 cutoff=90
inst 2 name=Drums kit=C-2:thump,D-2:tick
inst 3 name=Pad wave=saw oct=2-4 fade=40
pattern a rows 32
C-5 01 v64 ... | C-2 02 v64 ... | C-3 03 v40 ...
... .. ... ... | ... .. ... ... | ... .. ... ...
E-5 01 v64 ... | D-2 02 v64 ... | ... .. ... ...
rest 29
end
order a
"""

#: the same, with a drum on a key the kit does not have
SILENT_KEY = SCORE.replace("E-5 01 v64 ... | D-2 02 v64 ...",
                           "E-5 01 v64 ... | G-2 02 v64 ...")


def _cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


def _score_file(tmp, text, name="song.sch"):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


# -- the key map -----------------------------------------------------------------------
def test_a_kit_is_known_by_its_keys_and_a_pitched_instrument_is_not():
    module, report = notation.compile_text(SCORE)
    assert len(report.keymaps) == 1, report.keymaps
    line = report.keymaps[0]
    assert "Drums" in line and "C-2 thump" in line and "D-2 tick" in line, line
    assert not export.is_kit(module.instruments[0])
    assert not export.is_kit(module.instruments[2])
    assert export.is_kit(module.instruments[1])
    assert "keys: " + line in report.text()


def test_the_runs_of_a_pitched_instrument_are_its_octaves_with_the_ends_stretched():
    module, _ = notation.compile_text(SCORE)
    runs = export.key_runs(module.instruments[0])          # oct=3-5
    # the lowest sample plays everything below its octave, the highest
    # everything above it
    assert [(a, b) for a, b, _ in runs] == [(1, 48), (49, 60), (61, 120)], runs
    assert len({s for _, _, s in runs}) == 3


def test_the_key_map_of_a_forged_kit_names_every_drum():
    module, report = notation.compile_text(
        "tempo 120\nchannels 1\ninst 1 name=Kit forge=kit:tight_kit\n"
        "pattern a rows 32\nC-2 01 v64 ...\nrest 31\nend\norder a\n")
    line = report.keymaps[0]
    for key, drum in (("C-2", "kick"), ("D-2", "snare"), ("F#2", "hat"),
                      ("A#2", "open_hat"), ("C#3", "cymbal")):
        assert f"{key} tight_kit_{drum}" in line, (key, line)


# -- check and strict --------------------------------------------------------------------
def test_a_note_on_a_key_with_no_sample_is_said_and_strict_refuses_it():
    with tempfile.TemporaryDirectory() as tmp:
        path = _score_file(tmp, SILENT_KEY)
        code, out, _ = _cli("check", path)
        assert code == 0 and "has no sample for G-2" in out
        assert "PLAYABLE, WITH 1 WARNING" in out, out
        code, out, err = _cli("check", path, "--strict")
        assert code == 5 and "strict" in err


def test_a_clean_score_is_plainly_playable_even_under_strict():
    with tempfile.TemporaryDirectory() as tmp:
        path = _score_file(tmp, SCORE)
        code, out, err = _cli("check", path, "--strict")
        assert code == 0, (out, err)
        assert out.rstrip().endswith("PLAYABLE") and "WARNING" not in out


def test_a_strict_build_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        path = _score_file(tmp, SILENT_KEY)
        target = os.path.join(tmp, "out.it")
        code, _, err = _cli("build", path, "-o", target, "--strict")
        assert code == 5 and "stops the build" in err
        assert os.listdir(tmp) == ["song.sch"], os.listdir(tmp)


# -- the sidecar -------------------------------------------------------------------------------
def test_a_build_writes_a_sidecar_that_says_what_the_module_holds():
    with tempfile.TemporaryDirectory() as tmp:
        path = _score_file(tmp, SCORE)
        target = os.path.join(tmp, "out.it")
        code, out, _ = _cli("build", path, "-o", target, "--peak", "off")
        assert code == 0, out
        side = target + ".json"
        assert os.path.exists(side) and f"wrote {side}" in out
        with open(side, encoding="utf-8") as handle:
            data = json.load(handle)
    assert data["format"] == export.SIDECAR_FORMAT
    assert data["module"]["file"] == "out.it"
    assert data["module"]["channels_used"] == 3
    lead, drums, pad = data["instruments"]
    assert not lead["kit"] and drums["kit"]
    assert [k["from"] for k in drums["keys"]] == ["C-2", "D-2"]
    assert [k["sample_name"] for k in drums["keys"]] == ["thump", "tick"]
    assert lead["recipe"]["wave"] == "pulse"
    assert lead["recipe"]["params"]["duty"] == 0.25, lead["recipe"]
    assert lead["filter"] == {"cutoff": 90, "resonance": 0, "envelope": False}
    assert lead["fadeout"] == 60 and lead["nna"] == "cut"
    assert pad["recipe"]["octaves"] == [2, 4]
    samples = {s["number"]: s for s in data["samples"]}
    assert all(s["channels"] == 1 and s["bits"] in (8, 16)
               for s in samples.values())
    looped = [s for s in samples.values() if s["loop"]]
    assert looped and looped[0]["loop"]["mode"] == "forward"
    assert looped[0]["loop"]["end"] <= looped[0]["frames"]


def test_a_sidecar_can_be_left_out():
    with tempfile.TemporaryDirectory() as tmp:
        path = _score_file(tmp, SCORE)
        target = os.path.join(tmp, "out.it")
        code, _, _ = _cli("build", path, "-o", target, "--peak", "off",
                          "--no-sidecar")
        assert code == 0 and not os.path.exists(target + ".json")


def test_info_prints_the_kit_keys_of_a_module_file():
    with tempfile.TemporaryDirectory() as tmp:
        path = _score_file(tmp, SCORE)
        target = os.path.join(tmp, "out.it")
        _cli("build", path, "-o", target, "--peak", "off")
        code, out, _ = _cli("info", target)
    assert code == 0 and "keys instrument 02 Drums: C-2 thump, D-2 tick" in out


# -- the peak ---------------------------------------------------------------------------------------
def test_a_build_fits_the_peak_unless_the_score_says_mv():
    support.need_openmpt()
    with tempfile.TemporaryDirectory() as tmp:
        path = _score_file(tmp, SCORE)
        target = os.path.join(tmp, "out.it")
        code, out, _ = _cli("build", path, "-o", target, "--no-sidecar")
        assert code == 0 and "peak " in out and "mix volume" in out, out
        module, _ = notation.compile_text(SCORE)
        fit = render.fit_peak(module, 0.5)
        assert abs(fit.after - 0.5) / 0.5 < 0.06, fit
        assert fit.mix_after != 48 and "->" in fit.text()
        # the score's own mv is respected
        path = _score_file(tmp, SCORE.replace("channels 3", "channels 3\nmv 40"),
                           "mv.sch")
        code, out, _ = _cli("build", path, "-o", target)
        assert code == 0 and "mix volume 40, as the score says" in out, out
        with open(target + ".json", encoding="utf-8") as handle:
            assert json.load(handle)["module"]["mix_volume"] == 40
        # unless it is told to fit anyway
        code, out, _ = _cli("build", path, "-o", target, "--peak", "0.5")
        assert code == 0 and "peak " in out
        with open(target + ".json", encoding="utf-8") as handle:
            data = json.load(handle)
        assert abs(data["module"]["peak"]["after"] - 0.5) / 0.5 < 0.06


def test_a_peak_the_mix_volume_cannot_reach_is_said():
    support.need_openmpt()
    module, _ = notation.compile_text(SCORE)
    module.mix_volume = 1
    fit = render.fit_peak(module, 0.89)
    assert fit.mix_after == 128 or fit.reached, fit
    quiet = render.PeakFit(0.2, 0.5, 48, 128, 0.89, 3)
    assert not quiet.reached and "128 is the limit" in quiet.text()


def test_a_bad_peak_is_refused_before_anything_is_written():
    with tempfile.TemporaryDirectory() as tmp:
        path = _score_file(tmp, SCORE)
        for bad in ("loud", "7", "0"):
            code, _, err = _cli("build", path, "-o", os.path.join(tmp, "x.it"),
                                "--peak", bad)
            assert code == 2 and "--peak" in err, (bad, err)
        assert os.listdir(tmp) == ["song.sch"]


# -- the presets -------------------------------------------------------------------------------------
def _channels(n, notes_each=1):
    lines = ["tempo 120", f"channels {n}", "inst 1 name=Lead wave=pulse "
             "duty=0.125 cutoff=80 res=20 oct=3-5",
             "pattern a rows 32"]
    row = " | ".join("C-4 01 v40 ..." for _ in range(n))
    lines += [row, "rest 31", "end", "order a"]
    return "\n".join(lines) + "\n"


def test_the_nes_preset_takes_four_channels_and_no_more():
    module, _ = notation.compile_text(_channels(5))
    try:
        export.apply_preset(module, "nes")
    except export.ExportError as error:
        assert "5 channels" in str(error) and "four" in str(error)
    else:
        raise AssertionError("five channels went through")


def test_the_nes_preset_makes_samples_eight_bit_and_drops_the_filter():
    module, _ = notation.compile_text(_channels(4))
    before = [s.data for s in module.samples]
    notes = export.apply_preset(module, "nes")
    assert all(s.bits == 8 for s in module.samples)
    assert any("8 bit" in n for n in notes) and any("filter" in n for n in notes)
    inst = module.instruments[0]
    assert inst.cutoff is None and inst.resonance is None
    # rounding, not truncation: 16-bit v becomes (v + 128) >> 8
    original = before[0]
    for index in (0, len(original) // 3, len(original) // 2):
        v = original[index]
        expected = max(-128, min(127, (v + 128) >> 8))
        assert module.samples[0].data[index] == expected, (v, expected)
    assert "duty 01 Lead: 0.125" in module.message


def test_the_sega_preset_wants_one_drum_channel_and_seven_channels():
    text = ("tempo 120\nchannels 3\nsmp k wave=kick\nsmp r wave=rim\n"
            "inst 1 name=Drums kit=C-2:k,D-2:r\n"
            "pattern a rows 32\n"
            "C-2 01 v64 ... | C-2 01 v64 ... | ... .. ... ...\n"
            "rest 31\nend\norder a\n")
    module, _ = notation.compile_text(text)
    try:
        export.apply_preset(module, "sega")
    except export.ExportError as error:
        assert "channels 1, 2" in str(error) and "one DAC" in str(error)
    else:
        raise AssertionError("drums on two channels went through")
    module, _ = notation.compile_text(_channels(8))
    try:
        export.apply_preset(module, "sega")
    except export.ExportError as error:
        assert "8 channels" in str(error)
    else:
        raise AssertionError("eight channels went through")


def test_a_drum_made_for_the_dac_is_a_drum_to_the_sega_preset_too():
    head = ("tempo 120\nchannels 3\n"
            "inst 1 name=Kick wave=kick\ninst 2 name=Snare wave=noise len=0.3\n"
            "pattern a rows 32\n")
    tail = "rest 30\nend\norder a\n"
    dac = {1: {"rate": 11000, "bits": 8}, 2: {"rate": 13300, "bits": 8}}
    apart = notation.compile_text(
        head + "C-5 01 v64 ... | ... .. ... ... | ... .. ... ...\n"
        "... .. ... ... | C-5 02 v64 ... | ... .. ... ...\n" + tail)[0]
    apart.dac = dac
    problems = export.over_limits(apart, "sega")
    assert len(problems) == 1 and "channels 1, 2" in problems[0] \
        and "one DAC" in problems[0], problems
    try:
        export.apply_preset(apart, "sega")
    except export.ExportError as error:
        assert error.problems == problems
    else:
        raise AssertionError("two DAC drums on two channels went through")
    # both on one channel is what the console does
    together = notation.compile_text(
        head + "C-5 01 v64 ... | ... .. ... ... | ... .. ... ...\n"
        "C-5 02 v64 ... | ... .. ... ... | ... .. ... ...\n" + tail)[0]
    together.dac = dac
    assert export.over_limits(together, "sega") == []
    export.apply_preset(together, "sega")


def test_the_limits_can_be_read_without_changing_the_module():
    module, _ = notation.compile_text(_channels(5))
    bits = [s.bits for s in module.samples]
    assert export.over_limits(module, "schism") == []
    assert export.over_limits(module, "sega") == []
    problems = export.over_limits(module, "nes")
    assert len(problems) == 1 and "5 channels" in problems[0]
    assert [s.bits for s in module.samples] == bits
    try:
        export.over_limits(module, "amiga")
    except export.ExportError as error:
        assert "no preset" in str(error)
    else:
        raise AssertionError("an unknown preset went through")


def test_the_schism_preset_changes_nothing_and_an_unknown_one_is_named():
    module, _ = notation.compile_text(_channels(6))
    bits = [s.bits for s in module.samples]
    assert export.apply_preset(module, "schism") == []
    assert [s.bits for s in module.samples] == bits
    try:
        export.apply_preset(module, "amiga")
    except export.ExportError as error:
        assert "nes" in str(error) and "sega" in str(error)
    else:
        raise AssertionError("an unknown preset went through")


def test_a_preset_that_does_not_fit_stops_the_build_and_says_why():
    with tempfile.TemporaryDirectory() as tmp:
        path = _score_file(tmp, _channels(5))
        code, _, err = _cli("build", path, "-o", os.path.join(tmp, "o.it"),
                            "--preset", "nes", "--peak", "off")
        assert code == 6 and "5 channels" in err
        assert os.listdir(tmp) == ["song.sch"]
        code, out, _ = _cli("build", path, "-o", os.path.join(tmp, "o.it"),
                            "--preset", "schism", "--peak", "off")
        assert code == 0
        with open(os.path.join(tmp, "o.it.json"), encoding="utf-8") as handle:
            assert json.load(handle)["preset"] == "schism"
