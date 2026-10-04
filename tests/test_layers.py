"""Stacks, chords, stereo pairs and reversed samples: the editing of PCM.

A blend of dials keeps nothing of either parent; a stack keeps both. These
tests build stacks out of the starter bank's instruments (calibrated and
ready, so nothing here needs a player) and read the samples back: is the
first part of the stack the clap, the rest the snare; does the loop of the
body come through whole; does a chord have each note on its pitch; are the
two sides of a stereo pair a little flat and a little sharp. The few that
listen to a module render it through libopenmpt and skip without it.
"""

import json
import math
import os
import tempfile

import support
from schism import it_read, it_write, notation
from schism.forge import (bank as bank_mod, cli, dsp, fam_layer, mixing,
                          notation_hook, patch as patch_mod, spectral, splice,
                          validate)
from schism.forge.family import get_family

SR = 44100


def _need():
    support.need_openmpt()


def _source(name, role=None, options=None):
    entry = notation_hook.starter().get(name)
    family = get_family(entry.family)
    compiled = family.from_dict(family.to_dict(entry.compiled))
    return fam_layer.Source(name, compiled, entry.genome, role, options)


def _stack(*specs, edits=None):
    """_stack(("clap", "click"), ("snare", "body", {"ms": 40})) ->
    (Genome, Compiled)"""
    sources = [_source(*spec) if isinstance(spec, tuple) else _source(spec)
               for spec in specs]
    return fam_layer.build_stack(sources, "stack", edits)


def _floats(built, channel="data"):
    return splice.floats(getattr(built, channel))


def _corr(a, b):
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    da = math.sqrt(sum(v * v for v in a)) or 1.0
    db = math.sqrt(sum(v * v for v in b)) or 1.0
    return sum(x * y for x, y in zip(a, b)) / (da * db)


def _drum(name, low=1, high=120):
    entry = notation_hook.starter().get(name)
    return patch_mod.make_sample(entry.compiled.patch.family,
                                 entry.compiled.patch.params, low, high)


def _rate(built, note, pitched=True):
    return splice.native_rate(built, note, pitched)


def _line(x, rate, near_hz, size=65536, start=0):
    """Frequency of the strongest spectral line near `near_hz`."""
    return dsp.peak_frequency(x, near_hz, rate, size, start)


def _cents(f, reference):
    return 1200.0 * math.log2(f / reference)


def _loop_period(built):
    start, end = built.loop
    return _floats(built)[start:end]


# -- the stack ---------------------------------------------------------------------------
def test_a_clap_over_a_snare_is_the_crack_of_one_and_the_tone_of_the_other():
    genome, compiled = _stack(("clap", "click"), ("snare", "body"))
    stack = patch_mod.make_sample("layer", compiled.patch.params, 1, 120)
    clap, snare = _floats(_drum("clap")), _floats(_drum("snare"))
    x = _floats(stack)
    first = int(0.015 * SR)
    # the first 15 ms is the clap (band-limited), not the snare
    limited = splice.convert(clap, SR, SR, spectral.BAND_LIMIT_HZ)
    assert _corr(x[:first], limited[:first]) > 0.95
    assert abs(_corr(x[:first], snare[:first])) < 0.3
    # from 100 to 300 ms it is the snare, not the clap
    a, z = int(0.1 * SR), int(0.3 * SR)
    assert _corr(x[a:z], snare[a:z]) > 0.99
    assert abs(_corr(x[a:z], clap[a:z])) < 0.1
    assert compiled.family == "layer" and genome.family == "layer"
    assert compiled.style["pitched"] is False        # it is still a drum


def test_a_stack_of_drums_is_a_drum_to_the_kit_and_the_score():
    _, compiled = _stack(("clap", "click"), ("snare", "body"))
    assert compiled.patch.params["reg_note"] == \
        notation_hook.starter().get("snare").compiled.patch.params["reg_note"]
    # the hook that tunes a kit's drum to 44.1 kHz recognises it
    assert compiled.patch.params.get("reg_note") is not None


def test_the_loop_of_a_body_comes_through_a_click_over_it_whole():
    _, compiled = _stack(("kick_hard", "click"), ("acid_bass", "body"))
    body = notation_hook.starter().get("acid_bass").compiled.patch
    assert compiled.patch.params["looped"] and compiled.patch.params["pitched"]
    checked = 0
    for low, high in patch_mod.groups(compiled.patch.bands)[:3]:
        stack = patch_mod.make_sample("layer", compiled.patch.params, low,
                                      high)
        plain = patch_mod.make_sample("tone", body.params, low, high)
        assert stack.loop is not None
        # the same loop, to the rounding of the sample and a change of gain
        a, b = _loop_period(stack), _loop_period(plain)
        assert len(a) == len(b)
        k = sum(x * y for x, y in zip(a, b)) / sum(y * y for y in b)
        assert max(abs(x - k * y) for x, y in zip(a, b)) < 2e-4
        # and it begins after the click and the hand-over are done
        assert stack.loop[0] >= int(0.035 * _rate(stack, (low + high) // 2))
        assert [i for i in validate.check_patch(
            compiled.patch, [(low, high, stack)])
            if i.code == "loop_click"] == []
        checked += 1
    assert checked == 3


def test_a_click_is_heard_at_the_start_and_the_body_takes_over():
    _, compiled = _stack(("kick_hard", "click"), ("acid_bass", "body"))
    low, high = patch_mod.groups(compiled.patch.bands)[1]
    base = (low + high) // 2
    stack = patch_mod.make_sample("layer", compiled.patch.params, low, high)
    body = patch_mod.make_sample(
        "tone", notation_hook.starter().get("acid_bass").compiled.patch.params,
        low, high)
    rate = _rate(stack, base)
    x, y = _floats(stack), _floats(body)
    gate = int(0.020 * rate)
    # the body is not there for its first 20 ms; the click is
    assert max(abs(v) for v in x[:gate]) > 0.05
    # after the hand-over (35 ms) what plays is the body, scaled
    a = int(0.040 * rate)
    z = a + 4096
    assert _corr(x[a:z], y[a:z]) > 0.999


def test_roles_are_decided_from_the_sounds_when_they_are_not_given():
    s = fam_layer.assign_roles([_source("kick_hard"), _source("acid_bass")])
    assert [x.role for x in s] == ["click", "body"]
    s = fam_layer.assign_roles([_source("snare"), _source("clap")])
    assert sorted(x.role for x in s) == ["body", "click"]
    # the longer sound is the body, the shorter the click
    assert {x.name: x.role for x in s} == {"snare": "body", "clap": "click"}
    s = fam_layer.assign_roles([_source("kick", None), _source("hat"),
                                _source("saw_lead")])
    assert [x.role for x in s] == ["click", "noise", "body"] \
        or sorted(x.role for x in s) == ["body", "click", "noise"] \
        or sorted(x.role for x in s) == ["body", "click", "sub"]
    assert next(x for x in s if x.name == "saw_lead").role == "body"


def test_two_sounds_that_loop_cannot_share_a_sample():
    for first, second in (("acid_bass", "pad"), ("saw_lead", "supersaw")):
        try:
            fam_layer.build_stack([_source(first, "body"),
                                   _source(second, "tail")])
        except fam_layer.PatchError as error:
            assert "one loop" in str(error)
        else:
            raise AssertionError("two loops were stacked")
    try:
        fam_layer.assign_roles([_source("acid_bass"), _source("pad")])
    except fam_layer.PatchError as error:
        assert "loop" in str(error)
    else:
        raise AssertionError("two loops were given roles")


def test_a_stack_needs_one_body_and_known_roles_and_options():
    for specs, word in (([("kick", "click"), ("snare", "click")], "body"),
                        ([("kick", "body"), ("snare", "body")], "body"),
                        ):
        try:
            _stack(*specs)
        except fam_layer.PatchError as error:
            assert word in str(error)
        else:
            raise AssertionError("a bad stack was built")
    for bad, word in ((("snare", "wobble"), "role"),
                      (("snare", "body", {"ms": 5}), "option"),
                      (("snare", "click", {"ms": 9999}), "outside")):
        try:
            if len(bad) == 3:
                fam_layer.build_stack([_source("kick", "click"),
                                       _source("snare", bad[1], bad[2])])
            else:
                fam_layer.build_stack([_source("kick", "click"),
                                       _source(*bad)])
        except fam_layer.PatchError as error:
            assert word in str(error)
        else:
            raise AssertionError("a bad part was accepted")


def test_a_tail_comes_in_under_the_body_at_the_level_the_score_gives():
    def loop_rms(level):
        _, compiled = fam_layer.build_stack([
            _source("kick", "body"),
            _source("saw_lead", "tail", {"level": level})])
        low, high = patch_mod.groups(compiled.patch.bands)[1]
        stack = patch_mod.make_sample("layer", compiled.patch.params, low, high)
        assert stack.loop is not None
        period = _loop_period(stack)
        played = compiled.patch.params["gain"] / 128.0       # as the gain has it
        return played * math.sqrt(sum(v * v for v in period) / len(period)), \
            stack, compiled
    quiet, _, _ = loop_rms(-15.0)
    loud, stack, compiled = loop_rms(-3.0)
    assert abs(20 * math.log10(loud / quiet) - 12.0) < 1.0
    # the stack is the tail's: its loop, its pitch, its envelope
    assert compiled.patch.params["looped"] and compiled.patch.params["pitched"]
    # and the kick begins it, at its recorded pitch
    kick = _floats(_drum("kick"))
    x = _floats(stack)
    n = int(0.03 * SR)
    assert _corr(x[:n], kick[:n]) > 0.9


def _added(stack, name):
    """What a stack adds to its body, as played: stack minus the body."""
    compiled = stack[1]
    made = patch_mod.make_sample("layer", compiled.patch.params, 1, 120)
    body = notation_hook.starter().get(name).compiled.patch
    plain = patch_mod.make_sample("drum", body.params, 1, 120)
    played = compiled.patch.params["gain"] / 128.0
    own = body.inst.get("gain", 128) / 128.0
    return [a * played - b * own
            for a, b in zip(_floats(made), _floats(plain))]


def _share(x, lo, hi):
    n = dsp.next_pow2(len(x))
    mags, w = dsp.spectrum(x + [0.0] * (n - len(x)), 0, n, SR)
    total = sum(m * m for m in mags) or 1.0
    return sum(m * m for m in mags[int(lo / w):int(hi / w) + 1]) / total


def test_noise_is_the_top_of_a_sound_and_sub_its_bottom():
    air = _added(_stack(("snare", "body"),
                        ("open_hat", "noise", {"hp": 6000})), "snare")
    assert _share(air, 0, 3000) < 0.02 and _share(air, 5000, 20000) > 0.9
    under = _added(_stack(("snare", "body"),
                          ("kick", "sub", {"lp": 100})), "snare")
    assert _share(under, 400, 20000) < 0.02 and _share(under, 20, 250) > 0.9


# -- chords --------------------------------------------------------------------------------
def test_a_chord_of_a_tone_has_each_note_on_its_pitch():
    patch = notation_hook.starter().get("saw_lead").compiled.patch
    # no vibrato: a line that wavers has sidebands the size of itself
    steady = {**patch.params, "pm": 0.0, "am": 0.0}
    low, high = 49, 60
    base = (low + high) // 2
    f0 = spectral.hz_of_note(base)
    made = patch_mod.make_sample("tone", {**steady, "chord": {
        "notes": "min9", "root": -3}}, low, high)
    assert made.loop is not None and made.right is None
    rate = _rate(made, base)
    x = _floats(made)[made.loop[0]:]
    for semis in (-3, 0, 4, 7, 11):
        f = f0 * 2.0 ** (semis / 12.0)
        assert abs(_cents(_line(x, rate, f, 65536), f)) < 3.0, semis
    # each key plays the chord on that key: another band, its own pitches
    low, high = 61, 72
    base = (low + high) // 2
    made = patch_mod.make_sample("tone", {**steady, "chord": "maj"},
                                 low, high)
    rate = _rate(made, base)
    x = _floats(made)[made.loop[0]:]
    f0 = spectral.hz_of_note(base)
    for semis in (0, 4, 7):
        f = f0 * 2.0 ** (semis / 12.0)
        assert abs(_cents(_line(x, rate, f, 65536), f)) < 3.0, semis


def test_a_chord_of_a_struck_sound_has_each_note_on_its_pitch_and_dies_away():
    patch = notation_hook.starter().get("bell").compiled.patch
    low, high = patch_mod.groups(patch.bands)[2]
    base = (low + high) // 2
    f0 = spectral.hz_of_note(base)
    plain = patch_mod.make_sample("modal", patch.params, low, high)
    made = patch_mod.make_sample("modal", {**patch.params, "chord": "maj7"},
                                 low, high)
    assert made.loop is None
    rate = _rate(made, base)
    x = _floats(made)
    for semis in (0, 4, 7, 11):
        f = f0 * 2.0 ** (semis / 12.0)
        assert abs(_cents(_line(x, rate, f, 16384), f)) < 4.0, semis
    assert max(abs(v) for v in made.data[-8:]) < 0.02 * 32767
    assert len(made.data) >= len(plain.data) // 2


def test_a_chord_needs_a_name_it_knows_or_notes_it_can_play():
    for text in ("min9", "0,3,7,10,14", [0, 4, 7]):
        assert len(splice.parse_chord(text)) >= 3
    for text in ("minor eleventy", "0", "0,99"):
        try:
            splice.parse_chord(text)
        except splice.SpliceError:
            pass
        else:
            raise AssertionError(text)
    assert splice.chord_notes({"notes": "min9", "root": -3}) == \
        [-3, 0, 4, 7, 11]


# -- stereo ----------------------------------------------------------------------------------
def test_the_two_sides_of_a_stereo_tone_are_a_little_flat_and_a_little_sharp():
    patch = notation_hook.starter().get("saw_lead").compiled.patch
    steady = {**patch.params, "pm": 0.0, "am": 0.0}
    low, high = 49, 60
    base = (low + high) // 2
    f0 = spectral.hz_of_note(base)
    made = patch_mod.make_sample("tone", {**steady, "stereo": 6.0},
                                 low, high)
    assert made.right is not None and len(made.right) == len(made.data)
    assert made.loop is not None
    rate = _rate(made, base)
    start = made.loop[0]
    left = _cents(_line(_floats(made)[start:], rate, f0, 65536), f0)
    right = _cents(_line(_floats(made, "right")[start:], rate, f0, 65536), f0)
    # a loop moves in whole steps of its pitch: the nearest it can hold to 6
    assert -12.0 < left < -3.0 and 3.0 < right < 12.0
    assert abs(left + right) < 1.5            # and it is centred on the key
    peak_l = max(abs(v) for v in made.data)
    peak_r = max(abs(v) for v in made.right)
    assert abs(peak_l - peak_r) < 0.06 * 32767


def test_a_stereo_one_shot_is_the_sound_twice_a_few_cents_apart():
    patch = notation_hook.starter().get("bell").compiled.patch
    low, high = patch_mod.groups(patch.bands)[2]
    base = (low + high) // 2
    f0 = spectral.hz_of_note(base)
    made = patch_mod.make_sample("modal", {**patch.params, "stereo": 8.0},
                                 low, high)
    rate = _rate(made, base)
    assert made.right is not None and len(made.right) == len(made.data)
    left = _cents(_line(_floats(made), rate, f0, 16384), f0)
    right = _cents(_line(_floats(made, "right"), rate, f0, 16384), f0)
    assert abs(left + 8.0) < 2.5 and abs(right - 8.0) < 2.5


def test_a_stereo_sample_goes_through_the_file_and_back_in_a_score():
    module, report = notation.compile_text(_score(
        "inst 1 name=Wide forge=pad stereo=6"))
    assert any(s.right is not None for s in module.samples)
    back = it_read.read(it_write.build(module))
    wide = [s for s in back.samples if s.right is not None]
    assert wide and all(len(s.right) == len(s.data) for s in wide)
    from schism import verify
    assert verify.compare(module) == []


# -- reverse ------------------------------------------------------------------------------------
def test_a_reversed_sample_swells_into_its_end_and_does_not_loop():
    patch = notation_hook.starter().get("cymbal").compiled.patch
    plain = patch_mod.make_sample(patch.family, patch.params, 1, 120)
    made = patch_mod.make_sample(patch.family, {**patch.params, "rev": True},
                                 1, 120)
    assert made.loop is None and len(made.data) == len(plain.data)
    peak_plain = max(range(len(plain.data)), key=lambda i: abs(plain.data[i]))
    peak_made = max(range(len(made.data)), key=lambda i: abs(made.data[i]))
    assert peak_plain < len(plain.data) // 4
    assert peak_made > 3 * len(made.data) // 4
    # it is the same samples the other way round (but for the fade at the end)
    n = len(plain.data)
    assert all(abs(plain.data[n - 1 - i] - made.data[i]) < 400
               for i in range(100, n - 100, 997))


def test_reversing_a_loop_plays_it_out_first_and_does_not_loop():
    patch = notation_hook.starter().get("pad").compiled.patch
    low, high = patch_mod.groups(patch.bands)[1]
    made = patch_mod.make_sample("tone", {**patch.params, "rev": True},
                                 low, high)
    assert made.loop is None
    seconds = len(made.data) / _rate(made, (low + high) // 2)
    assert 1.4 < seconds < 1.6


# -- the instrument around an edit keeps its loudness --------------------------------------------
def test_an_edit_is_made_up_in_the_gain_where_the_gain_has_room():
    patch = notation_hook.starter().get("pad").compiled.patch.clone()
    patch.inst["gain"] = 60
    edited, short = patch_mod.with_edits(patch, {"chord": {
        "notes": "min7", "root": 0}})
    assert edited.params["chord"]["notes"] == "min7"
    assert patch.params.get("chord") is None            # the original is left
    assert edited.inst["gain"] > 60 and short == 0.0
    # and when there is no room it says how far it fell short
    patch.inst["gain"] = 128
    _, short = patch_mod.with_edits(patch, {"chord": {"notes": "min7",
                                                      "root": 0}})
    assert short > 0.0


# -- banks and scores ---------------------------------------------------------------------------------
def test_a_stack_is_a_bank_entry_that_comes_back_the_same():
    genome, compiled = _stack(("clap", "click"), ("snare", "body"))
    entry = bank_mod.Entry("crack", "layer", genome, compiled, {}, 1.0, "drum",
                           "test")
    bank = bank_mod.Bank("t", [entry])
    with tempfile.TemporaryDirectory() as folder:
        path = bank.save(os.path.join(folder, "t.bank.json"))
        with open(path, encoding="utf-8") as handle:
            json.load(handle)
        back = bank_mod.Bank.load(path).get("crack")
    a = patch_mod.make_sample("layer", compiled.patch.params, 1, 120)
    b = patch_mod.make_sample("layer", back.compiled.patch.params, 1, 120)
    assert list(a.data) == list(b.data) and a.c5speed == b.c5speed
    # and the genome compiles to the same stack again
    again = get_family("layer").compile(back.genome)
    assert again.patch.params["parts"] == compiled.patch.params["parts"]


def _score(*lines, rows=32):
    body = ["C-4 01 v64 ..."] + ["... .. ... ..."] * (rows - 1)
    return ("title Edits\ntempo 125\nchannels 1\nmv 48\n" + "\n".join(lines)
            + f"\npattern a rows {rows}\n" + "\n".join(body)
            + "\nend\norder a\n")


def test_a_score_can_stack_edit_and_reverse():
    text = _score(
        "inst 1 name=Crack forge=layer:clap@click+snare@body",
        "inst 2 name=Stab forge=epiano chord=min9 root=-3 oct=3-4",
        "smp crash forge=cymbal rev=on",
        "inst 3 name=Rev kit=C-5:crash")
    module, report = notation.compile_text(text)
    assert report.errors == [] if hasattr(report, "errors") else True
    names = [i.name for i in module.instruments[:3]]
    assert names[0] == "Crack" and names[1] == "Stab"
    stab = module.forged[2][1]
    assert stab.patch.params["chord"]["notes"] == "min9"
    crack = module.forged[1][1]
    assert crack.family == "layer"
    crash = [s for s in module.samples if s.name == "crash"][0]
    assert crash.loop is None
    peak = max(range(len(crash.data)), key=lambda i: abs(crash.data[i]))
    assert peak > 3 * len(crash.data) // 4
    back = it_read.read(it_write.build(module))
    assert len(back.samples) == len(module.samples)


def test_the_score_says_what_is_wrong_with_a_layer_or_an_edit():
    cases = (("inst 1 forge=layer:snare", "at least two"),
             ("inst 1 forge=layer:snare@wobble+clap", "role"),
             ("inst 1 forge=layer:snare@body+nothing_called_this@click",
              "no archetype"),
             ("inst 1 forge=layer:acid_bass@body+pad@tail", "one loop"),
             ("inst 1 forge=epiano root=3", "root"),
             ("inst 1 forge=epiano chord=minor_eleventy", "chord"),
             ("inst 1 forge=layer:snare@body+clap@click dna=brightness:0.5",
              "one sound"),
             ("inst 1 wave=saw chord=min", "not"))
    for line, word in cases:
        try:
            notation.compile_text(_score(line))
        except notation.NotationError as error:
            assert word in str(error), (line, str(error))
        else:
            report = notation.compile_text(_score(line))[1]
            problems = " ".join(str(p) for p in getattr(report, "problems", []))
            assert word in problems, (line, problems)


def test_a_stack_that_loops_cannot_be_a_one_shot_sample():
    try:
        notation.compile_text(_score(
            "smp thing forge=layer:kick_hard@click+acid_bass@body",
            "inst 1 kit=C-5:thing"))
    except notation.NotationError as error:
        assert "loop" in str(error)
    else:
        raise AssertionError("a loop was made a one-shot")


# -- mixing and the command line (these play) -----------------------------------------------------------
def test_unlike_sounds_are_stacked_not_averaged_by_the_mixer():
    _need()
    entry = mixing.mix(["kick_hard", "acid_bass"], name="kb")
    assert entry.family == "layer" and entry.character.startswith("layer:")
    assert "kick_hard as click" in entry.character
    # asked for by name, the dials are still averaged
    dial = mixing.mix(["kick_hard", "acid_bass"], mode="dna", name="kb2")
    assert dial.family != "layer" and "mixed in dial space" in dial.character
    # two of a kind are still morphed
    same = mixing.mix(["saw_lead", "supersaw"], name="ss")
    assert same.family == "tone"


def test_a_dial_blend_of_a_snare_and_a_clap_has_no_clap_in_it():
    _need()
    clap = _floats(_drum("clap"))
    first = int(0.015 * SR)
    blended = mixing.mix(["snare", "clap"], mode="dna", name="x")
    made = patch_mod.make_sample(blended.compiled.patch.family,
                                 blended.compiled.patch.params, 1, 120)
    limited = splice.convert(clap, SR, SR, spectral.BAND_LIMIT_HZ)
    assert abs(_corr(_floats(made)[:first], limited[:first])) < 0.6
    stacked = mixing.layer([{"from": "clap", "role": "click"},
                            {"from": "snare", "role": "body"}], name="y")
    made = patch_mod.make_sample("layer", stacked.compiled.patch.params, 1, 120)
    assert _corr(_floats(made)[:first], limited[:first]) > 0.95


def test_a_layer_recipe_takes_nested_parts_and_edits():
    _need()
    entry = mixing.layer(
        [{"from": "clap", "role": "click", "ms": 25},
         {"mix": [{"from": "snare"}, {"from": "tom"}], "role": "body"}],
        name="nested", edits={"stereo": 6})
    assert entry.family == "layer"
    made = patch_mod.make_sample("layer", entry.compiled.patch.params, 1, 120)
    assert made.right is not None


def test_the_command_line_stacks_by_role_and_by_position():
    _need()
    import contextlib
    import io

    def run(*argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(list(argv))
        return code, out.getvalue()

    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "l.bank.json")
        code, text = run("-q", "layer", "clap@click", "snare@body", "--name",
                         "crack", "--out", path)
        assert code == 0 and "layer:" in text and "clap as click" in text
        assert bank_mod.Bank.load(path).get("crack").family == "layer"
        # Grok's form: parts joined by +, roles by position
        code, text = run("-q", "layer", "snare+clap", "--roles", "click,body",
                         "--name", "other")
        assert code == 0 and "snare as click" in text and "clap as body" in text
        # the wrong number of roles is refused
        code, _ = run("-q", "layer", "snare+clap", "--roles", "click")
        assert code == 2
        code, _ = run("-q", "layer", "snare")
        assert code == 2


def test_a_stack_plays_the_pitch_and_the_level_of_its_body():
    _need()
    from schism.forge import probe
    _, compiled = _stack(("kick_hard", "click"), ("acid_bass", "body"))
    body = notation_hook.starter().get("acid_bass").compiled.patch
    note = 37
    left, right, hold, rate = probe.render_note(compiled.patch, note, 1.2, 1.0)
    mono = [(a + b) / 2 for a, b in zip(left, right)]
    left2, right2, _, _ = probe.render_note(body, note, 1.2, 1.0)
    ref = [(a + b) / 2 for a, b in zip(left2, right2)]
    hz = probe.note_hz(note)
    assert abs(_cents(dsp.peak_frequency(mono, hz, rate, 32768, int(0.4 * rate)),
                      hz)) < 3.0

    def rms(x, a, b):
        part = x[int(a * rate):int(b * rate)]
        return math.sqrt(sum(v * v for v in part) / len(part))
    # it holds as long as the key is down, and as loud as the body alone
    for a, b in ((0.4, 0.5), (0.9, 1.0)):
        assert abs(20 * math.log10(rms(mono, a, b) / rms(ref, a, b))) < 1.0
    # and lets go when it is let go
    assert rms(mono, 1.7, 1.8) < 0.05 * rms(mono, 0.9, 1.0)


def test_a_stacked_module_sounds_the_same_in_both_players():
    _need()
    from schism import openmpt, schismtracker
    if not schismtracker.available():
        support.skip("Schism Tracker is not installed (apt install schism)")
    text = ("tempo 120\nchannels 2\nmv 48\n"
            "inst 1 name=Crack forge=layer:clap@click+snare@body\n"
            "inst 2 name=KB forge=layer:kick_hard@click+acid_bass@body "
            "oct=2-3\n"
            "pattern a rows 32\n"
            "C-5 01 v64 ... | C-3 02 v64 ...\n"
            "rest 7\n"
            "C-5 01 v64 ... | === .. ... ...\n"
            "rest 23\nend\norder a\n")
    module, _ = notation.compile_text(text)
    blob = it_write.build(module)
    with openmpt.Song(blob) as song:
        ours = song.render(44100)
    theirs = schismtracker.render(blob, 44100)
    n = min(len(ours), len(theirs))

    def rms(x):
        return math.sqrt(sum(v * v for v in x) / max(1, len(x)))
    db = 20 * math.log10(rms(theirs[:n]) / rms(ours[:n]))
    assert abs(db) < 1.5, db
