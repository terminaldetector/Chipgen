"""The forge, played: every sound it makes is rendered by libopenmpt and
read back by the same dials it was asked in. Skipped without libopenmpt;
the transform, the loops, the dials and the files are tested without it in
test_forge_core.py."""

import json
import math
import os
import random
import tempfile

import support
from schism import it_read, it_write, model as M, notation
from schism.forge import (arrangement, bank as bank_mod, cli, director,
                          dna, dsp, evolve, genome, kits, mixing,
                          notation_hook, patch as patch_mod, probe,
                          validate)
from schism.forge.family import get_family

SR = 44100


def _need():
    support.need_openmpt()


def _genome(family, **axes):
    style = axes.pop("style", {})
    register = axes.pop("register", None)
    fam = get_family(family)
    return genome.Genome(family, dna.DNA(**axes), style=style, name="t",
                         register=register or fam.default_register)


def _mono(left, right):
    return [(a + b) * 0.5 for a, b in zip(left, right)]


# -- pitch ------------------------------------------------------------------------
def test_every_pitched_family_plays_the_note_it_was_asked_across_the_keyboard():
    _need()
    cases = [
        ("tone", dict(brightness=0.6, body=0.9, decay=1.0, articulation=0.4)),
        ("tone", dict(brightness=0.5, body=0.9, decay=1.0, detune=0.5,
                      articulation=0.4)),            # a long loop with unison
        ("tone", dict(brightness=0.3, body=0.9, decay=1.0, metallicity=0.5,
                      articulation=0.4)),            # inharmonic partials
        ("modal", dict(brightness=0.5, decay=0.5, articulation=0.4,
                       style={"set": "string"})),
    ]
    worst = 0.0
    for family, axes in cases:
        g = _genome(family, **axes)
        compiled = get_family(family).compile(g)
        wide = compiled.patch.clone()
        wide.bands = (1, 120, wide.bands[2])
        # unison voices sit a bin (1.35 Hz) apart and share the main lobe of
        # the analysis below A2, which pulls the peak a few cents
        unison = axes.get("detune", 0) > 0
        for octave in ((2, 3, 4, 5) if unison else (1, 2, 3, 4, 5)):
            note = probe.note_of("A", octave)
            hz = M.note_frequency(note)
            left, right, _, _ = probe.render_note(wide, note, 1.2, 0.1)
            x = _mono(left, right)
            got = dsp.peak_frequency(x, hz, SR, 32768, 4410)
            cents = abs(1200 * math.log2(got / hz))
            worst = max(worst, cents)
            assert cents < (6.0 if unison else 3.0), \
                (family, axes, octave, hz, got, cents)
    assert worst < 6.0


def test_a_drum_plays_its_recorded_pitch_at_its_register_note():
    c = get_family("drum").compile(_genome("drum", decay=0.3,
                                           style={"kind": "tom"},
                                           register=("A", 2)))
    built = patch_mod.make_sample("drum", c.patch.params, 1, 120)
    note = c.patch.params["reg_note"]
    rate = built.c5speed * 2.0 ** ((note - 61) / 12.0)
    assert abs(rate - 44100) < 1.0


# -- the closed loop --------------------------------------------------------------
def _rms_error(g, axes, supports):
    weights = evolve.score_mod.DEFAULT_WEIGHTS
    errs = {a: abs(getattr(g.dna, a) - v) for a, v in axes.items()
            if v is not None and a in supports and a in evolve.CONTROLLED}
    norm = sum(weights[a] for a in errs)
    return math.sqrt(sum(weights[a] * e * e for a, e in errs.items()) / norm)


def test_realisation_brings_each_family_close_to_the_dials_it_was_given():
    _need()
    from schism.forge import archetypes
    bounds = {"saw_lead": 0.12, "pad": 0.14, "pluck": 0.16, "kick": 0.16,
              "hat": 0.16, "zap": 0.2}
    for name, bound in bounds.items():
        a = archetypes.get(name)
        fam = get_family(a.family)
        g = genome.Genome(a.family, a.dna, style=dict(a.style),
                          register=a.register, name=name)
        g2, c, r = evolve.realise(fam, g, probes=3,
                                  full=a.dna.detune > 0.05 or a.dna.motion > 0.05)
        assert r.ok, (name, [str(i) for i in r.issues])
        error = _rms_error(g, r.axes, fam.supports)
        assert error < bound, (name, error, r.axes)


def test_correction_beats_the_open_loop():
    _need()
    g = _genome("tone", brightness=0.8, attack=0.0, body=0.1, decay=0.15,
                articulation=0.05, punch=0.8, bass_weight=0.1)
    fam = get_family("tone")
    open_loop = probe.probe(fam.compile(fam.pin(g)), g)
    g2, c, closed = evolve.realise(fam, g, probes=4)
    e_open = _rms_error(g, open_loop.axes, fam.supports)
    e_closed = _rms_error(g, closed.axes, fam.supports)
    assert e_closed < 0.7 * e_open, (e_open, e_closed)


def test_auto_level_puts_instruments_at_one_loudness():
    _need()
    levels = []
    for family, axes in (("tone", dict(brightness=0.6, body=0.9, decay=1.0)),
                         ("tone", dict(brightness=0.2, body=0.9, decay=1.0,
                                       bass_weight=0.9)),
                         ("drum", dict(brightness=0.2, decay=0.3,
                                       bass_weight=0.9,
                                       style={"kind": "kick"}))):
        fam = get_family(family)
        g, c, r = evolve.realise(fam, _genome(family, **axes), probes=2)
        again = probe.probe(c, g, use_cache=False)
        levels.append(again.level)
        assert c.patch.inst["gain"] <= 128
    ref = get_family("tone").reference_level()
    for level in levels:
        assert 0.6 * ref < level < 1.7 * ref, (levels, ref)


def test_the_probe_is_deterministic_and_cached():
    _need()
    fam = get_family("tone")
    g = _genome("tone", brightness=0.5, detune=0.4, body=0.9, decay=1.0)
    c = fam.compile(fam.pin(g))
    probe.clear_cache()
    patch_mod.clear_cache()
    first = probe.probe(c, g, full=True)
    again = probe.probe(c, g, full=True)
    assert again.cached and not first.cached
    probe.clear_cache()
    patch_mod.clear_cache()
    third = probe.probe(c, g, full=True)
    assert first.axes == third.axes and first.level == third.level
    a = it_write.build(probe.probe_module(c.patch, 46, 7, 14))
    patch_mod.clear_cache()
    b = it_write.build(probe.probe_module(c.patch, 46, 7, 14))
    assert a == b


def test_a_detuned_instrument_is_read_for_detune_from_the_beating_voice_only():
    _need()
    fam = get_family("tone")
    g = _genome("tone", brightness=0.3, body=0.95, decay=1.0, detune=0.6,
                articulation=0.7, bass_weight=0.4)
    c = fam.compile(fam.pin(g))
    r = probe.probe(c, g, full=True)
    assert r.axes["detune"] is not None and r.axes["detune"] > 0.2
    plain = probe.probe(fam.plain(c), g, full=True)
    assert plain.axes["detune"] is not None and plain.axes["detune"] < 0.08
    # the envelope dials do not follow the beat
    assert abs(r.axes["body"] - plain.axes["body"]) < 1e-9


# -- searching ---------------------------------------------------------------------
def _scenario(**kw):
    base = {"family": "tone", "seeds": ["saw_lead"],
            "objective": {"target": {"brightness": 0.9, "detune": 0.6}},
            "generations": 2, "offspring": 4, "keep": 3, "seed": 3,
            "name": "t", "realise_probes": 2}
    base.update(kw)
    return base


def test_a_scenario_with_a_seed_makes_the_same_bank_every_time():
    _need()
    a = bank_mod.run_scenario(_scenario())
    probe.clear_cache()
    b = bank_mod.run_scenario(_scenario())
    assert a.names() == b.names()
    assert [round(e.score, 9) for e in a.entries] == \
        [round(e.score, 9) for e in b.entries]
    assert [e.compiled.patch.to_dict() for e in a.entries] == \
        [e.compiled.patch.to_dict() for e in b.entries]


def test_the_search_improves_on_its_seed_and_keeps_distinct_instruments():
    _need()
    fam = get_family("tone")
    from schism.forge import score as score_mod
    objective = score_mod.Objective({"brightness": 0.95, "bass_weight": 0.0})
    forge = evolve.Forge(fam, objective, seed=5, archive_size=6,
                         min_distance=0.08, realise_probes=2)
    seed = genome.Genome("tone", dna.DNA(brightness=0.3, bass_weight=0.6,
                                         body=0.9, decay=1.0), name="s",
                         register=("A", 3))
    first = forge.seed([seed])[0]
    forge.run(generations=3, offspring=5)
    members = forge.archive.members
    assert members[0].score > first.score + 0.3, (members[0].score, first.score)
    assert members[0].measured["brightness"] > 0.8
    assert members[0].measured["bass_weight"] < 0.3
    for i, a in enumerate(members):
        for b in members[i + 1:]:
            assert score_mod.distance(a.measured, b.measured) >= 0.08 - 1e-9
    assert all(m.ok for m in members)
    summary = forge.summary()
    assert summary["family"] == "tone" and summary["members"]


def test_a_heuristic_director_steers_a_run_and_a_broken_one_costs_nothing():
    _need()
    steered = bank_mod.run_scenario(_scenario(), director=director.HeuristicDirector())
    assert steered.entries

    class Broken(director.Director):
        def directions(self, summary):
            raise RuntimeError("nope")

        def rank(self, finalists, summary):
            raise RuntimeError("nope")
    plain = bank_mod.run_scenario(_scenario(), director=Broken())
    assert plain.entries


def test_a_bank_written_and_read_back_is_the_same_instruments():
    _need()
    b = bank_mod.run_scenario(_scenario(generations=1, offspring=3, keep=2))
    with tempfile.TemporaryDirectory() as tmp:
        path = b.save(os.path.join(tmp, "x.bank.json"))
        again = bank_mod.Bank.load(path)
    assert again.names() == b.names()
    for x, y in zip(b.entries, again.entries):
        assert x.compiled.patch.to_dict() == y.compiled.patch.to_dict()
        assert x.genome.key() == y.genome.key()


def test_a_bank_with_an_illegal_patch_is_refused_with_the_field():
    _need()
    b = bank_mod.run_scenario(_scenario(generations=0, offspring=1, keep=1))
    data = b.to_dict()
    data["instruments"][0]["compiled"]["patch"]["amp"] = \
        [[0.1 * k, 0.5] for k in range(40)]
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "bad.json")
        with open(path, "w") as handle:
            json.dump(data, handle)
        try:
            bank_mod.Bank.load(path)
        except bank_mod.BankError as error:
            assert "not a legal instrument" in str(error) and "amp" in str(error)
        else:
            raise AssertionError("an envelope of 40 nodes was loaded")


# -- mixing ------------------------------------------------------------------------------
def test_mixing_two_tones_by_recipe_keeps_both_and_stays_legal():
    _need()
    entry = mixing.mix(["archetype:saw_lead", "archetype:brass"], [2, 1],
                       name="lead_brass")
    assert entry.family == "tone" and "morphed" in entry.character
    assert entry.compiled.patch.problems() == []
    a = mixing.evaluate({"from": "archetype:saw_lead"}).target
    b = mixing.evaluate({"from": "archetype:brass"}).target
    assert abs(entry.genome.dna.brightness - dna.blend([(a, 2), (b, 1)]).brightness) < 0.2


def test_mixing_across_families_stacks_the_samples_and_blends_dials_on_request():
    _need()
    # different kinds of sound keep what they are: a stack, not an average
    entry = mixing.mix(["archetype:bell", "archetype:pad"], [1, 1], name="x")
    assert entry.family == "layer" and entry.character.startswith("layer:")
    assert "bell" in entry.character and "pad" in entry.character
    # and the old blend is still there when it is asked for by name
    dial = mixing.mix(["archetype:bell", "archetype:pad"], [1, 1],
                      mode="dna", name="y")
    assert "mixed in dial space" in dial.character
    assert dial.family in ("modal", "tone")


def test_mixing_a_recipe_from_a_score_line_with_a_forged_instrument():
    _need()
    entry = mixing.mix(["recipe:wave=saw oct=3-3 fade=60", "archetype:epiano"],
                       name="saw_ep", family="tone")
    assert entry.family == "tone" and entry.measured["brightness"] is not None


def test_a_mix_that_nests_too_deep_or_has_one_part_is_refused():
    _need()
    deep = {"from": "archetype:pluck"}
    for _ in range(8):
        deep = {"mix": [deep, {"from": "archetype:harp"}]}
    for recipe, word in ((deep, "nests"), ({"mix": [{"from": "archetype:pluck"}]},
                                            "at least two")):
        try:
            mixing.evaluate(recipe)
        except mixing.MixError as error:
            assert word in str(error)
        else:
            raise AssertionError(word)
    try:
        mixing.evaluate({"mix": [{"from": "archetype:pluck", "weight": 0},
                                 {"from": "archetype:harp"}]})
    except mixing.MixError as error:
        assert "positive" in str(error)


def test_lifting_a_recipe_finds_its_family_and_its_dials():
    _need()
    g = mixing.lift_recipe("wave=kick")
    assert g.family == "drum" and g.style["kind"] == "kick"
    assert g.dna.bass_weight > 0.8 and g.dna.attack < 0.2
    g = mixing.lift_recipe("wave=saw oct=3-3 fade=60")
    assert g.family == "tone" and g.dna.brightness > 0.5
    g = mixing.lift_recipe("wave=sine oct=3-3 fade=60")
    assert g.dna.brightness < 0.1


# -- validation ---------------------------------------------------------------------------------
def test_validation_finds_loops_that_click_and_samples_that_end_loud():
    from array import array
    from schism.forge import spectral
    n = 400
    ramp = array("h", [int(-20000 + 40000 * i / n) for i in range(n)])
    built = spectral.Built(ramp, 44100, (0, n), "ramp")
    codes = {i.code for i in validate.check_patch(patch_mod.Patch("tone"),
                                                  [(1, 120, built)])}
    assert "loop_click" in codes
    loud_end = spectral.Built(array("h", [20000] * 100), 44100, None, "end")
    codes = {i.code for i in validate.check_patch(patch_mod.Patch("tone"),
                                                  [(1, 120, loud_end)])}
    assert "end_click" in codes and "dc" in codes
    clean = spectral.Built(spectral.to_int16(spectral.make_loop(
        spectral.LoopSpec([(1.0, 1.0)], 512, 1))), 44100, (0, 512), "ok")
    assert validate.check_patch(patch_mod.Patch("tone"),
                                [(1, 120, clean)]) == []


def test_a_silent_note_is_an_error_and_a_loud_one_a_warning():
    _need()
    fam = get_family("tone")
    g = _genome("tone", brightness=0.5, body=0.9, decay=1.0)
    c = fam.compile(fam.pin(g))
    c.patch.inst["gain"] = 128
    mute = c.patch.clone(amp=[[0.0, 0.0]], amp_sustain=[0, 0])
    r = probe.probe(type(c)(c.family, "q", mute, {}, dict(c.style), []), g)
    assert not r.ok and any(i.code == "silent" for i in r.issues)
    soft = c.patch.clone()
    soft.inst["gain"] = 2                          # 1/64 of the level
    r = probe.probe(type(c)(c.family, "q", soft, {}, dict(c.style), []), g)
    assert not r.ok and any(i.code == "quiet" for i in r.issues)
    loud = c.patch.clone()
    loud.inst["gain"] = 128
    r = probe.probe(type(c)(c.family, "q", loud, {}, dict(c.style), []), g)
    assert r.ok


# -- modules -------------------------------------------------------------------------------------------
def test_the_forged_instrument_is_what_the_file_format_and_the_reader_expect():
    _need()
    fam = get_family("tone")
    g, c, r = evolve.realise(fam, _genome("tone", brightness=0.6, attack=0.4,
                                          body=0.7, decay=0.5, detune=0.4,
                                          articulation=0.6), probes=2)
    module = M.Module(title="x")
    patch_mod.install(c.patch, module, 3, 0.02, "forged")
    pattern = M.Pattern(rows=32)
    pattern.cell(0, 0).note, pattern.cell(0, 0).instrument = 46, 3
    module.patterns.append(pattern)
    module.orders = [0]
    back = it_read.read(it_write.build(module))
    assert back.instruments[2].name == "forged"
    assert back.instruments[2].volume_envelope.nodes == \
        module.instruments[2].volume_envelope.nodes
    assert len(back.samples) == len(module.samples) >= 1
    from schism import verify
    assert verify.compare(module) == []


def test_the_same_forged_module_sounds_the_same_in_both_players():
    _need()
    from schism import openmpt, schismtracker
    if not schismtracker.available():
        support.skip("Schism Tracker is not installed (apt install schism)")
    text = ("tempo 120\nchannels 3\n"
            "inst 1 name=Pad forge=pad\n"
            "inst 2 name=Pluck forge=pluck\n"
            "inst 3 name=Kit forge=kit:tight_kit\n"
            "pattern a rows 32\n"
            "C-3 01 v64 ... | E-4 02 v50 ... | C-2 03 v64 ...\n"
            "rest 7\n"
            "=== .. ... ... | ... .. ... ... | D-2 03 v60 ...\n"
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


# -- the score language -----------------------------------------------------------------------------------
_SCORE = """title Forge test
tempo 120
channels 3
inst 1 name=Bass forge=punch_bass
inst 2 name=Bell forge=bell dna=brightness:0.6 reg=A4 oct=3-6
smp thump forge=kick
inst 3 name=Kit kit=C-2:thump,D-2:@snare,F#2:@hat
pattern a rows 32
C-3 01 v64 ... | E-5 02 v48 ... | C-2 03 v64 ...
... .. ... ... | ... .. ... ... | F#2 03 v40 ...
rest 6
G-2 01 v64 ... | ... .. ... ... | D-2 03 v60 ...
rest 23
end
order a
"""


def test_a_score_can_ask_the_forge_for_instruments_drums_and_kits():
    _need()
    module, report = notation.compile_text(_SCORE)
    assert [i.name for i in module.instruments] == ["Bass", "Bell", "Kit"]
    assert sorted(module.forged) == [1, 2]
    kit = module.instruments[2]
    assert {t for t, s in kit.note_map if s} == {60}
    assert len({s for _, s in kit.note_map if s}) == 3
    from schism import openmpt
    with openmpt.Song(it_write.build(module)) as song:
        frames = song.render(44100)
    assert 0.02 < openmpt.peak(frames) < 1.0


def test_dna_and_register_in_a_score_move_the_sound():
    _need()
    quiet = "tempo 120\nchannels 1\ninst 1 forge=saw_lead{}\npattern a rows 32\n" \
            "A-4 01 v64 ...\nrest 31\nend\norder a\n"
    results = {}
    for label, extra in (("plain", ""), ("dark", " dna=brightness:0.1")):
        module, _ = notation.compile_text(quiet.format(extra))
        genome_, compiled = module.forged[1]
        results[label] = genome_.dna.brightness
    assert results["dark"] == 0.1 and results["plain"] > 0.5
    module, _ = notation.compile_text(quiet.format(" reg=A2"))
    assert module.forged[1][0].register == ("A", 2)


def test_the_envelopes_of_a_forged_instrument_follow_the_scores_tempo():
    _need()
    line = "inst 1 forge=pad\npattern a rows 32\nA-3 01 v64 ...\nrest 31\n" \
           "end\norder a\n"
    slow, _ = notation.compile_text("tempo 60\nchannels 1\n" + line)
    fast, _ = notation.compile_text("tempo 180\nchannels 1\n" + line)
    seconds_slow = slow.instruments[0].volume_envelope.nodes[-1][0] * 2.5 / slow.tempo
    seconds_fast = fast.instruments[0].volume_envelope.nodes[-1][0] * 2.5 / fast.tempo
    assert abs(seconds_slow - seconds_fast) / seconds_slow < 0.05, \
        (seconds_slow, seconds_fast)


def test_a_score_with_a_bad_forge_line_is_told_what_exists():
    _need()
    for line, word in (("inst 1 forge=nothing", "no archetype"),
                       ("inst 1 forge=pad dna=sparkle:1", "not a DNA axis"),
                       ("inst 1 forge=pad dna=brightness:2", "outside"),
                       ("inst 1 forge=pad reg=Z9", "not a note"),
                       ("inst 1 forge=pad frobnicate=3", "not a setting"),
                       ("inst 1 forge=pad wave=saw", "exactly one"),
                       ("inst 1 wave=saw dna=brightness:0.3", "settings of forge"),
                       ("inst 1 forge=kit:nosuch", "no kit")):
        try:
            notation.compile_text("tempo 120\nchannels 1\n" + line + "\n")
        except notation.NotationError as error:
            assert word in str(error), (line, str(error))
        else:
            raise AssertionError(line)
    try:
        notation.compile_text("tempo 120\nchannels 1\ninst 1 forge=nothing\n")
    except notation.NotationError as error:
        assert "pad" in str(error) and "kick" in str(error)


def test_a_bank_directive_finds_the_file_next_to_the_score():
    _need()
    b = bank_mod.run_scenario(_scenario(generations=0, offspring=1, keep=1,
                                        name="mine", prefix="mine"))
    with tempfile.TemporaryDirectory() as tmp:
        b.save(os.path.join(tmp, "mine.bank.json"))
        text = ("tempo 120\nchannels 1\nbank mine.bank.json\n"
                "inst 1 name=Mine forge=mine_01\npattern a rows 32\n"
                "A-4 01 v64 ...\nrest 31\nend\norder a\n")
        path = os.path.join(tmp, "song.sch")
        with open(path, "w") as handle:
            handle.write(text)
        module, _ = notation.compile_file(path)
        assert module.instruments[0].name == "Mine"
        try:
            notation.compile_text(text)           # no base directory: cwd
        except notation.NotationError as error:
            assert "no bank file" in str(error)


def test_a_forged_tone_cannot_be_a_one_shot_sample():
    _need()
    try:
        notation.compile_text("tempo 120\nchannels 1\nsmp t forge=saw_lead\n"
                              "inst 1 sample=t\n")
    except notation.NotationError as error:
        assert "needs one sample per octave" in str(error)
    else:
        raise AssertionError("a tone is a loop")


# -- the arrangement ------------------------------------------------------------------------------------------
def test_the_walk_agrees_with_the_modules_own_clock():
    _need()
    text = ("tempo 120\nchannels 2\ninst 1 name=A wave=saw oct=3-4 fade=40\n"
            "pattern a rows 32\nC-4 01 v64 ... | ... .. ... ...\n"
            "rest 3\n... .. ... A04 | E-4 01 v64 ...\nrest 3\n"
            "... .. ... T90 | ... .. ... ...\nrest 23\nend\n"
            "pattern b rows 32\nC-4 01 v64 ... | ... .. ... ...\nrest 30\n"
            "... .. ... C10 | ... .. ... ...\nend\norder a b a\n")
    module, _ = notation.compile_text(text)
    song = arrangement.walk(module)
    assert abs(song.seconds - module.seconds()) < 0.01, \
        (song.seconds, module.seconds())
    assert len(song.row_t) == song.rows
    assert abs(song.row_t[-1] + song.row_s[-1] - song.seconds) < 1e-6
    # a, b, then a again from row 16 (the C10 breaks there): 32 + 32 + 16
    assert song.rows == 80
    assert [len(n) for _, n in sorted(song.notes.items())] == [2, 1]


_MUDDY = """tempo 120
channels 4
inst 1 name=BassA forge=punch_bass
inst 2 name=BassB forge=sub_bass
inst 3 name=PadA forge=pad
inst 4 name=PadB forge=strings
pattern a rows 64
{rows}
end
order a a
"""


def _muddy():
    rows = []
    for r in range(64):
        a = "C-2 01 v64 ..." if r % 8 == 0 else "... .. ... ..."
        b = "C-2 02 v64 ..." if r % 8 == 0 else "... .. ... ..."
        c = "C-4 03 v50 ..." if r % 32 == 0 else "... .. ... ..."
        d = "C-4 04 v50 ..." if r % 32 == 0 else "... .. ... ..."
        rows.append(f"{a} | {b} | {c} | {d}")
    text = _MUDDY.replace("{rows}", "\n".join(rows))
    module, _ = notation.compile_text(text)
    return module


def test_two_basses_on_one_octave_and_two_pads_in_the_middle_are_named():
    _need()
    arr = arrangement.Arrangement(_muddy(), roles={"ch01": "bass", "ch02": "bass"})
    report = arr.analyse()
    kinds = {c.kind for c in report.conflicts}
    assert "bass_stack" in kinds, report.text()
    assert "masking" in kinds or "pan_stack" in kinds, report.text()
    assert report.cost > 0.5
    assert report.to_dict()["conflicts"]


def test_fitting_an_arrangement_lowers_its_cost_and_changes_the_module():
    _need()
    module = _muddy()
    arr = arrangement.Arrangement(module, roles={"ch01": "bass", "ch02": "bass"})
    before = arr.analyse().cost
    pans = list(module.channel_pan[:4])
    report, applied = arrangement.fit(arr, rounds=4)
    assert applied and report.cost < before - 0.03, (before, report.cost)
    assert module.channel_pan[:4] != pans or any(
        s.get("octave") or s.get("dna") for s in applied)
    # a repaired module is still a module
    from schism import openmpt
    blob = it_write.build(module)
    with openmpt.Song(blob) as song:
        assert song.channels >= 4


def test_dials_can_be_moved_on_an_instrument_nobody_forged():
    _need()
    module, _ = notation.compile_text(
        "tempo 120\nchannels 1\ninst 1 wave=saw oct=3-3 fade=60\n"
        "pattern a rows 32\nA-3 01 v64 ...\nrest 31\nend\norder a\n")
    ins = module.instruments[0]
    assert ins.volume_envelope is None and ins.cutoff is None
    assert arrangement.edit_instrument(ins, {"body": -0.4, "attack": 0.3,
                                             "brightness": -0.3}, 0.02)
    nodes = ins.volume_envelope.nodes
    assert nodes[0] == (0, 0) and max(v for _, v in nodes) == 64
    assert ins.volume_envelope.sustain is not None
    held = nodes[ins.volume_envelope.sustain[0]][1]
    assert held < 32
    assert ins.cutoff is not None and ins.cutoff < 127
    before = probe.probe_instrument(module, 1, 46).axes["brightness"]
    module2, _ = notation.compile_text(
        "tempo 120\nchannels 1\ninst 1 wave=saw oct=3-3 fade=60\n"
        "pattern a rows 32\nA-3 01 v64 ...\nrest 31\nend\norder a\n")
    after = probe.probe_instrument(module2, 1, 46).axes["brightness"]
    assert before < after - 0.1                       # the filter closed


def test_samples_nobody_uses_are_dropped_after_an_instrument_is_replaced():
    _need()
    module, _ = notation.compile_text(
        "tempo 120\nchannels 1\ninst 1 forge=pad\npattern a rows 32\n"
        "A-3 01 v64 ...\nrest 31\nend\norder a\n")
    n = len(module.samples)
    patch_mod.install(module.forged[1][1].patch, module, 1, 0.02, "again")
    assert len(module.samples) == 2 * n
    arrangement.compact_samples(module)
    assert len(module.samples) == n
    assert {s for ins in module.instruments for _, s in ins.note_map if s} \
        <= set(range(1, n + 1))


def test_real_levels_can_stand_in_for_the_estimate():
    _need()
    arr = arrangement.Arrangement(_muddy(), roles={"ch01": "bass"})
    report = arr.analyse(measure=True)
    assert report.levels_db and max(report.levels_db.values()) == 0.0


# -- kits ----------------------------------------------------------------------------------------------------------
def test_a_kit_is_drums_that_leave_room_for_each_other():
    _need()
    b = kits.forge_kit("tight", "tight", seed=1, roles=["kick", "snare", "hat"],
                       generations=1, offspring=3)
    assert [k for k, _ in b.kits[0]["keys"]] == ["C-2", "D-2", "F#2"]
    members = [b.get(n) for _, n in b.kits[0]["keys"]]
    assert members[0].measured["bass_weight"] > members[2].measured["bass_weight"]
    assert members[2].measured["brightness"] > members[0].measured["brightness"]
    with tempfile.TemporaryDirectory() as tmp:
        path = b.save(os.path.join(tmp, "k.bank.json"))
        again = bank_mod.Bank.load(path)
        assert again.kit("tight")["keys"] == b.kits[0]["keys"]
    data = b.to_dict()
    data["kits"][0]["keys"][0][1] = "nobody"
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "k.json")
        with open(path, "w") as handle:
            json.dump(data, handle)
        try:
            bank_mod.Bank.load(path)
        except bank_mod.BankError as error:
            assert "nobody" in str(error)
        else:
            raise AssertionError("a kit named a drum that is not there")


def test_kit_styles_are_named_and_unknown_ones_refused():
    assert {"tight", "808", "dark", "bright", "lofi", "big"} <= \
        set(kits.style_names())
    try:
        kits.forge_kit("x", "quirky")
    except KeyError as error:
        assert "tight" in str(error)


# -- the starter bank ---------------------------------------------------------------------------------------------------
def test_the_starter_bank_loads_and_its_instruments_play():
    _need()
    bank = notation_hook.starter()
    assert len(bank.entries) >= 40 and {"tight_kit", "808_kit", "dark_kit"} \
        <= {k["name"] for k in bank.kits}
    families_seen = {e.family for e in bank.entries}
    assert families_seen == {"tone", "modal", "drum", "fx"}
    rng = random.Random(2)
    for entry in rng.sample(bank.entries, 8):
        g = entry.genome
        r = probe.probe(entry.compiled, g)
        assert r.ok, (entry.name, [str(i) for i in r.issues])
        assert r.level > 0.004, entry.name


def test_every_archetype_has_an_entry_in_the_starter_bank():
    from schism.forge import archetypes
    bank = notation_hook.starter()
    assert set(archetypes.names()) <= set(bank.names())


# -- the command line -----------------------------------------------------------------------------------------------------------
def _run(*argv):
    import contextlib
    import io
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(["-q"] + list(argv))
    return code, out.getvalue(), err.getvalue()


def test_the_command_line_describes_the_dials_families_and_archetypes():
    code, out, _ = _run("axes")
    assert code == 0 and "brightness" in out and "cannot express" in out
    code, out, _ = _run("families")
    assert code == 0 and "kind kick" in out and "set: string" in out
    code, out, _ = _run("archetypes", "--family", "drum")
    assert "snare" in out and "saw_lead" not in out


def test_the_command_line_compiles_measures_and_writes_a_bank_and_a_module():
    _need()
    with tempfile.TemporaryDirectory() as tmp:
        bank_path = os.path.join(tmp, "a.bank.json")
        code, out, _ = _run("compile", "bell", "--dna", "brightness=0.7",
                            "--out", bank_path, "--wav",
                            os.path.join(tmp, "bell.wav"))
        assert code == 0 and "asked vs heard" in out
        assert os.path.getsize(os.path.join(tmp, "bell.wav")) > 10000
        code, out, _ = _run("show", bank_path)
        assert "bell_modal" in out
        code, out, _ = _run("mix", "archetype:pluck", "archetype:harp",
                            "--name", "ph", "--out", bank_path)
        assert code == 0 and "ph" in out
        code, out, _ = _run("show", bank_path)
        assert "ph" in out and "bell_modal" in out
        code, out, err = _run("compile", "no_such_thing")
        assert code == 2 and "no_such_thing" in err
        code, out, err = _run("compile", "pad", "--dna", "brightness=2")
        assert code == 2


def test_the_command_line_analyses_a_demo_and_a_module_file():
    _need()
    demo = os.path.join(support.ROOT, "demos", "first_light.sch")
    code, out, _ = _run("analyse", demo)
    assert code == 0 and "role" in out and "conflicts" in out or "no conflicts" in out
    code, out, _ = _run("analyse", demo, "--json")
    data = json.loads(out[:out.rindex("}") + 1])
    assert data["parts"] and "cost" in data
    it_path = os.path.join(support.ROOT, "demos", "first_light.it")
    code, out, _ = _run("analyse", it_path)
    assert code == 0 and "ch01" in out


# -- the manual ----------------------------------------------------------------------------------------------
def _manual_fences(kind):
    import re
    path = os.path.join(support.ROOT, "docs", "FORGE.md")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    found = re.findall(r"^```" + re.escape(kind) + r"\n(.*?)^```$", text,
                       re.S | re.M)
    assert found, f"no ```{kind} example in docs/FORGE.md"
    return found


def test_the_forge_manual_score_examples_compile_clean():
    _need()
    for body in _manual_fences("sch-forge"):
        with tempfile.TemporaryDirectory() as tmp:
            # the example loads a bank of its own; an empty one will do
            bank_mod.Bank("mine").save(os.path.join(tmp, "mine.bank.json"))
            path = os.path.join(tmp, "song.sch")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(body)
            module, report = notation.compile_file(path)
            assert report.warnings == (), report.warnings
            assert module.forged, "the example forged nothing"


def test_the_forge_manual_json_examples_are_what_the_forge_reads():
    _need()
    seen = {"scenario": 0, "mix": 0}
    for body in _manual_fences("json"):
        data = json.loads(body)
        if "family" in data:
            bank_mod.load_scenario(data)
            seen["scenario"] += 1
        elif "mix" in data:
            node = mixing.evaluate(data, bank=notation_hook.starter())
            assert node.compiled.patch is not None
            seen["mix"] += 1
    assert seen["scenario"] >= 1 and seen["mix"] >= 1, seen
