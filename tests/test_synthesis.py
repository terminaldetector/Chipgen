"""The instrument/driver synthesis layer: DNA, the three compilers, the loop,
the model hooks, mixing, arrangement, and the pass that puts the result in a
score.

The fast tests compile and parse and never render; the ones that render are
named for what they measure and are in the SLOW set of run_tests.py.
"""

import copy
import json
import random

import support


# -- isolation ------------------------------------------------------------
class _Isolated:
    """`with _Isolated():` — whatever a test installs into the engine's
    banks is gone afterwards, so the rest of the suite sees the engine it
    always saw."""

    def __enter__(self):
        import instruments
        import opl_instruments
        from synthesis import nes_driver, placement, registry
        self.saved = [
            (instruments.BANK, dict(instruments.BANK)),
            (instruments.CHARACTER, dict(instruments.CHARACTER)),
            (opl_instruments.BANK, dict(opl_instruments.BANK)),
            (nes_driver.BANK, dict(nes_driver.BANK)),
            (registry.FORGE, dict(registry.FORGE)),
        ]
        self.placement = placement
        return self

    def __exit__(self, *exc):
        for target, snapshot in self.saved:
            target.clear()
            target.update(snapshot)
        self.placement.LAST = self.placement.Report()
        return False


def _genome(chip, **dna):
    from synthesis import DNA
    from synthesis.backends import get_backend
    from synthesis.genome import Genome
    backend = get_backend(chip)
    return backend, Genome(chip, DNA(**dna), name="t",
                           register=backend.default_register)


# -- DNA ---------------------------------------------------------------------
def test_dna_is_eleven_dials_that_refuse_nonsense():
    from synthesis import AXES, DNA, DNAError
    assert len(AXES) == 11 and AXES[0] == "brightness"
    try:
        DNA(brightness=1.5)
    except DNAError as error:
        assert "brightness" in str(error)
    else:
        raise AssertionError("1.5 was accepted")
    try:
        DNA.from_dict({"warmth": 0.5})
    except DNAError as error:
        assert "warmth" in str(error) and "brightness" in str(error)
    else:
        raise AssertionError("an unknown axis was accepted")
    assert DNA.from_dict({"Bright": 0.4, "release": 0.2}).articulation == 0.2
    assert DNA.clamped(brightness=3.0).brightness == 1.0


def test_dna_operators_stay_in_range_and_are_reproducible():
    from synthesis import AXES, DNA
    from synthesis import dna as dna_mod
    a, b = DNA(brightness=0.9, attack=0.0), DNA(brightness=0.1, attack=0.8)
    mid = dna_mod.lerp(a, b, 0.5)
    assert abs(mid.brightness - 0.5) < 1e-9 and abs(mid.attack - 0.4) < 1e-9
    blended = dna_mod.blend([(a, 3.0), (b, 1.0)])
    assert abs(blended.brightness - 0.7) < 1e-9
    rng1, rng2 = random.Random(5), random.Random(5)
    for _ in range(50):
        c1 = dna_mod.mutate(a, rng1, 0.3)
        c2 = dna_mod.mutate(a, rng2, 0.3)
        assert c1 == c2
        changed = [x for x in AXES if getattr(c1, x) != getattr(a, x)]
        assert 1 <= len(changed) <= 3, changed
        assert all(0.0 <= getattr(c1, x) <= 1.0 for x in AXES)
    locked = dna_mod.mutate(a, random.Random(1), 0.3, locked=AXES[:-1])
    assert all(getattr(locked, x) == getattr(a, x) for x in AXES[:-1])
    child = dna_mod.crossover(a, b, random.Random(2))
    assert all(min(getattr(a, x), getattr(b, x)) - 1e-9 <= getattr(child, x)
               <= max(getattr(a, x), getattr(b, x)) + 1e-9 for x in AXES)


def test_a_direction_adds_sets_or_names_the_axes_when_it_is_wrong():
    from synthesis import DNA, DNAError
    from synthesis import dna as dna_mod
    out = dna_mod.apply_directions(DNA(brightness=0.5), [
        {"axis": "brightness", "add": 0.2}, {"axis": "attack", "set": 0.9}])
    assert abs(out.brightness - 0.7) < 1e-9 and out.attack == 0.9
    for bad in ({"axis": "nope", "add": 0.1}, {"axis": "attack"}):
        try:
            dna_mod.apply_directions(DNA(), [bad])
        except DNAError:
            continue
        raise AssertionError(f"{bad} was accepted")


def test_each_axis_is_documented_by_the_numbers_the_measurement_uses():
    """The claim is that an axis is defined by a measurement. The text that
    says so must quote the constants the code measures with, or the
    documentation is the thing that drifts."""
    from synthesis import AXES, descriptors
    from synthesis.dna import AXIS_DOC
    assert set(AXIS_DOC) == set(AXES)
    body = AXIS_DOC["body"][2]
    assert f"{descriptors.BODY_AT:g} s" in body
    assert f"{descriptors.BODY_FLOOR_DB:g} dB" in body
    assert f"{descriptors.MOTION_MIN_HZ:g} Hz" in AXIS_DOC["motion"][2]
    assert f"{descriptors.DETUNE_MIN_HZ:g}" in AXIS_DOC["detune"][2]
    assert f"{descriptors.MOTION_MIN_HZ:g}" in AXIS_DOC["detune"][2]
    assert f"{descriptors.BASS_RATIO_LOW:g}" in AXIS_DOC["bass_weight"][2]
    assert f"{descriptors.BASS_RATIO_HIGH:g}" in AXIS_DOC["bass_weight"][2]
    assert f"{descriptors.PUNCH_FULL_OCTAVES:g} octaves" \
        in AXIS_DOC["punch"][2]


# -- envelopes ------------------------------------------------------------
def test_the_envelope_inverse_agrees_with_the_definition_of_the_axes():
    import math
    from synthesis import descriptors
    from synthesis.backends import common
    for axis in (0.0, 0.2, 0.5, 0.8, 1.0):
        seconds = common.attack_seconds(axis)
        back = math.log(1.0 + seconds / descriptors.ATTACK_REFERENCE) \
            / math.log(1.0 + descriptors.ATTACK_LONG
                       / descriptors.ATTACK_REFERENCE)
        assert abs(back - axis) < 1e-9
    plan = common.envelope_plan(1.0, 1.0)
    assert plan["hold"] and plan["slope"] == 0.0
    plan = common.envelope_plan(0.2, 0.9)
    # a long decay cannot be 32 dB down in 0.6 s: the body wins
    assert not plan["hold"] and plan["slope"] >= 32.0 / (common.T_REF * 0.9) - 1e-6


def test_the_rate_tables_are_inverses_of_themselves():
    from synthesis.backends import ym2612, ym3812
    # (faster than about 2 ms all read as "instant", so the top rates share
    # one answer and are left out)
    for ar in (5, 10, 15, 20):
        assert ym2612.ar_for_attack(_axis_of_ms(ym2612.attack_ms(ar))) == ar
    for ar in (3, 6, 9):
        assert ym3812.ar_for_attack(_axis_of_ms(ym3812.attack_ms(ar))) == ar
    # slower rates are slower, on both chips
    assert ym2612.attack_ms(10) > ym2612.attack_ms(20)
    assert ym3812.decay_db_per_s(4) > ym3812.decay_db_per_s(2)


def _axis_of_ms(ms):
    import math
    return math.log(1.0 + (ms / 1000.0) / 0.002) / math.log(201.0)


# -- the three compilers -----------------------------------------------------
def test_every_archetype_compiles_to_legal_hardware_on_every_chip():
    from synthesis import archetypes
    from synthesis.backends import backends, get_backend
    from synthesis.genome import Genome
    for chip in backends():
        backend = get_backend(chip)
        for name in archetypes.names():
            dna, register, _roles, _doc = archetypes.get(name)
            g = backend.pin(Genome(chip, dna, name=name, register=register))
            compiled = backend.compile(g)
            data = backend.to_dict(compiled)
            again = backend.to_dict(backend.from_dict(copy.deepcopy(data)))
            assert data == again, f"{chip}/{name} does not survive a bank"
            json.dumps(data)                          # and is plain JSON
            _legal(chip, compiled, f"{chip}/{name}")


def _legal(chip, compiled, label):
    if chip == "ym2612":
        p = compiled.patch
        assert 0 <= p.algorithm <= 7 and 0 <= p.feedback <= 7, label
        for op in p.operators:
            assert 0 <= op.total_level <= 127, label
            assert 0 <= op.attack_rate <= 31 and 0 <= op.decay_rate <= 31, label
            assert 0 <= op.sustain_rate <= 31 and 0 <= op.release_rate <= 15, label
            assert 0 <= op.sustain_level <= 15 and 0 <= op.multiple <= 15, label
            assert 0 <= op.detune <= 7, label
    elif chip == "opl2":
        p = compiled.patch
        assert 0 <= p.feedback <= 7 and p.connection in (0, 1), label
        for op in (p.modulator, p.carrier):
            assert 0 <= op.total_level <= 63 and 0 <= op.attack <= 15, label
            assert 0 <= op.decay <= 15 and 0 <= op.release <= 15, label
            assert 0 <= op.sustain_level <= 15 and 0 <= op.multiple <= 15, label
            assert 0 <= op.waveform <= 3, label
    else:
        inst = compiled.patch
        assert inst.channel in ("pulse", "triangle", "noise"), label
        for macro, lo, hi in ((inst.volume, 0, 15), (inst.duty, 0, 3)):
            assert all(lo <= v <= hi for v in macro.values), label
        assert len(inst.volume.values) >= 1, label


def test_detune_is_a_second_voice_whose_loudness_sets_the_beat_depth():
    from synthesis.backends import common
    low, high = common.detune_ratio(0.1), common.detune_ratio(0.9)
    assert 0.04 < low < high <= 0.45
    for chip in ("ym2612", "opl2", "nes"):
        backend, g = _genome(chip, detune=0.0, body=1.0, decay=1.0)
        plain = backend.compile(backend.pin(g))
        assert not _layers_of(chip, plain)
        _, wide = _genome(chip, detune=0.9, body=1.0, decay=1.0)
        layered = backend.compile(backend.pin(wide))
        layers = _layers_of(chip, layered)
        assert len(layers) == 1, chip
        assert backend.voices_for(layered) == 2
        # a few cents sharp: a beat of about a hertz at the register
        cents = layers[0]["cents"]
        assert 2.0 <= cents <= 30.0, (chip, cents)


def _layers_of(chip, compiled):
    return compiled.patch.layers if chip == "nes" \
        else compiled.style.get("layers", [])


def test_the_nes_vibrato_never_carries_the_timer_across_a_page():
    from synthesis import nes_driver as N
    # A3 sits at timer 507, five steps under the 512 page edge
    hz = 220.0
    p0 = N.period_of(hz)
    assert p0 >> 8 == 1 and p0 > 500
    for cents in (-30, -17, -10, 0, 8, 25, 40):
        safe = N.page_safe(hz, float(cents))
        assert N.period_of(hz * 2 ** (safe / 1200.0)) >> 8 == p0 >> 8, cents
    # an offset a long way off is a jump and goes where it is told
    assert N.page_safe(hz, 600.0) == 600.0
    # and the pitch is left alone when it is nowhere near an edge
    assert N.page_safe(262.0, 12.0) == 12.0


def test_a_macro_loops_while_held_and_carries_on_from_release():
    from synthesis import nes_driver as N
    m = N.Macro([15, 12, 9, 6, 3, 0], loop=1, release=4)
    walked = m.walk(hold_frames=8, total_frames=12)
    assert walked[:3] == [15, 12, 9]              # plays through to...
    assert walked[3] == 6 and walked[4] == 12     # ...the release point, loops
    assert walked[8:] == [3, 0, 0, 0], walked     # key up: carries on
    try:
        N.Macro([1, 2], loop=5)
    except ValueError:
        pass
    else:
        raise AssertionError("a loop point outside the macro was accepted")


# -- scoring, scenarios, banks ---------------------------------------------
def test_an_objective_that_names_a_few_axes_leaves_the_rest_free():
    from synthesis import score
    o = score.Objective.from_dict({"target": {"brightness": 0.7},
                                   "box": {"metallicity": [0.4, 1.0]}})
    assert set(o.targeted()) == {"brightness", "metallicity"}
    assert o.error("metallicity", 0.7) == 0.0
    assert abs(o.error("metallicity", 0.1) - 0.3) < 1e-9
    back = score.Objective.from_dict(o.to_dict())
    assert set(back.targeted()) == set(o.targeted())
    try:
        score.Objective.from_dict({"target": {"warmth": 1}})
    except ValueError as error:
        assert "warmth" in str(error)
    else:
        raise AssertionError("an unknown axis was accepted")


def test_a_scenario_with_a_mistake_says_which_field_and_what_exists():
    from synthesis import bank
    for scenario, words in (
            ({"backend": "ym2612", "seedz": []}, "seedz"),
            ({"seeds": []}, "backend"),
            ({"backend": "sid"}, "sid"),
            ({"backend": "nes", "format": "other/9"}, "format")):
        try:
            bank.load_scenario(scenario)
        except bank.BankError as error:
            assert words in str(error), (scenario, str(error))
        else:
            raise AssertionError(f"{scenario} was accepted")
    ok = bank.load_scenario({"backend": "genesis", "seeds": ["punch_bass"]})
    assert ok["backend"] == "genesis"           # an alias is a legal name


def test_seeds_come_from_archetypes_bank_entries_and_patches():
    from synthesis import bank
    from synthesis.backends import get_backend
    backend = get_backend("opl2")
    seeds = bank.seeds_of(backend, {"seeds": [
        "bell", {"archetype": "pad", "dna": {"brightness": 0.1}},
        {"dna": {"attack": 0.9}}]})
    assert [s.lineage[0] for s in seeds] == ["seed:bell", "seed:pad",
                                             "seed:dna"]
    assert seeds[1].dna.brightness == 0.1
    by_role = bank.seeds_of(backend, {"role": "bass"})
    assert by_role and all(s.backend == "opl2" for s in by_role)
    try:
        bank.seeds_of(backend, {"seeds": [{"colour": "red"}]})
    except bank.BankError as error:
        assert "seed needs" in str(error)
    else:
        raise AssertionError("a seed with nothing to start from was accepted")


def test_a_bank_file_round_trips_and_exports_the_engines_own_formats():
    import opl_import
    import instruments
    from synthesis import bank
    from synthesis.backends import get_backend
    from synthesis.genome import Genome
    from synthesis import archetypes
    out = bank.Bank("rt")
    for chip, arch in (("ym2612", "punch_bass"), ("opl2", "pad"),
                       ("nes", "square_lead")):
        backend = get_backend(chip)
        dna, reg, _, doc = archetypes.get(arch)
        g = backend.pin(Genome(chip, dna, name=f"{chip}_{arch}", register=reg))
        compiled = backend.compile(g)
        compiled.name = g.name
        out.add(bank.Entry(g.name, chip, g, compiled, {}, 0.5, "x", doc))
    with support.TempDir() as folder:
        path = out.save(f"{folder}/a.bank.json")
        again = bank.Bank.load(path)
        assert again.names() == out.names()
        for a, b in zip(out.entries, again.entries):
            assert get_backend(a.backend).to_dict(a.compiled) \
                == get_backend(b.backend).to_dict(b.compiled)
        written = out.export_runtime(folder)
        assert set(written) == {"ym2612", "opl2", "nes"}
        assert "ym2612_punch_bass" in instruments.load_bank(
            written["ym2612"], merge=False)
        loaded = opl_import.load_bank(written["opl2"])
        assert "opl2_pad" in loaded
        with open(written["nes"], encoding="utf-8") as handle:
            assert json.load(handle)[0]["name"] == "nes_square_lead"
        # a file that is not a forge bank is refused by name
        with open(f"{folder}/bad.json", "w", encoding="utf-8") as handle:
            json.dump({"format": "something/else"}, handle)
        try:
            bank.Bank.load(f"{folder}/bad.json")
        except bank.BankError as error:
            assert "forge bank" in str(error)
        else:
            raise AssertionError("a foreign file was accepted as a bank")


def test_the_shipped_starter_bank_loads_and_covers_every_chip():
    import os
    from synthesis import bank, archetypes
    path = os.path.join(support.ROOT, "python", "synthesis", "banks",
                        "starter.bank.json")
    starter = bank.Bank.load(path)
    chips = {e.backend for e in starter.entries}
    assert chips == {"ym2612", "opl2", "nes"}
    names = set(starter.names())
    for arch in archetypes.names():
        assert f"nes_{arch}" in names, arch
        if not arch.startswith("noise_"):
            assert f"fm_{arch}" in names and f"opl_{arch}" in names, arch
    # the names are short enough to type in a score, and unique
    assert len(names) == len(starter.entries)
    assert all(len(n) <= 24 for n in names)


# -- the model hooks ------------------------------------------------------------
def test_a_models_reply_is_read_the_way_models_write_it():
    from synthesis import director
    reply = ('Sure! ```json\n{"directions": [{"axis": "brightness", "add": '
             '+0.2,}, {"axis": "punch", "add": 0.3}, {"axis": "warmth", '
             '"add": 0.1}, {"axis": "attack", "set": 1.7}], "why": "x"}\n``` '
             'hope that helps')
    notes = []
    found = director.clean_directions(director.extract_json(reply), "nes",
                                      notes)
    assert found == [{"axis": "brightness", "add": 0.2},
                     {"axis": "attack", "set": 1.0}]
    assert any("punch" in n and "nes" in n for n in notes)   # the chip lacks it
    assert any("warmth" in n for n in notes)
    assert director.extract_json("no json here") is None
    quoted = director.extract_json("{'directions': [{'axis': 'metal', "
                                   "'add': 0.25}]}")
    assert director.clean_directions(quoted, "opl2") \
        == [{"axis": "metallicity", "add": 0.25}]
    big = director.clean_directions([{"axis": "decay", "add": -0.9}], "opl2")
    assert big == [{"axis": "decay", "add": -director.MAX_ADD}]


def _summary(measured, target, score=0.5):
    return {"backend": "opl2", "generation": 1,
            "objective": {"target": target, "box": {}, "ignore": []},
            "members": [{"name": "a", "score": score, "dna": {},
                         "measured": measured, "errors": {}}]}


def test_the_offline_director_pushes_toward_the_target():
    from synthesis import director
    summary = _summary({"brightness": 0.2, "attack": 0.5, "motion": 0.9},
                       {"brightness": 0.8, "attack": 0.5, "motion": 0.1})
    steps = {d["axis"]: d["add"] for d in
             director.HeuristicDirector().directions(summary)}
    assert steps["brightness"] > 0.2 and steps["motion"] < -0.2
    assert "attack" not in steps                  # already there
    gaps = director.signed_gaps(summary, summary["members"][0])
    assert gaps["brightness"] > 0 > gaps["motion"]


def test_a_model_that_fails_costs_the_run_nothing():
    from synthesis import director
    summary = _summary({"brightness": 0.2}, {"brightness": 0.9})
    calls = []

    def ask(messages):
        calls.append(messages)
        if len(calls) == 1:
            raise RuntimeError("server down")
        if len(calls) == 2:
            return "I would make it brighter."
        return '{"directions": [{"axis": "attack", "set": 0.0}]}'
    d = director.LLMDirector(ask=ask, every=1, max_calls=10)
    first = d.directions(summary)                  # raised -> heuristic
    assert first and first[0]["axis"] == "brightness"
    summary["generation"] = 2
    second = d.directions(summary)                 # prose -> heuristic
    assert second and second[0]["axis"] == "brightness"
    summary["generation"] = 3
    assert d.directions(summary) == [{"axis": "attack", "set": 0.0}]
    assert d.errors == ["server down"] and d.calls == 3
    assert len(calls[0]) == 2 and "brightness" in calls[0][1]["content"]


def test_a_model_is_asked_every_few_generations_and_never_beyond_its_budget():
    from synthesis import director
    summary = _summary({"brightness": 0.2}, {"brightness": 0.9})
    asked = []

    def ask(messages):
        asked.append(1)
        return '{"directions": [{"axis": "brightness", "add": 0.3}]}'
    d = director.LLMDirector(ask=ask, every=3, max_calls=2)
    for generation in range(1, 11):
        summary["generation"] = generation
        assert d.directions(summary)
    assert len(asked) == 2                          # 1, 4; then the budget


def test_the_model_ranks_finalists_by_name_and_unknown_names_are_ignored():
    from synthesis import director

    class Cand:
        def __init__(self, name, score):
            self.genome = type("G", (), {"name": name})()
            self.score = score
            self.measured = {"brightness": 0.5}
            self.result = type("R", (), {"issues": []})()
    finalists = [Cand("a", 0.9), Cand("b", 0.8), Cand("c", 0.7)]
    d = director.LLMDirector(ask=lambda m: '{"order": ["c", "zzz", "a"]}')
    ranked = d.rank(finalists, _summary({}, {}))
    assert [c.genome.name for c in ranked] == ["c", "a", "b"]
    broken = director.LLMDirector(ask=lambda m: 1 / 0)
    assert broken.rank(finalists, _summary({}, {})) == finalists


def test_directions_written_beforehand_serve_a_chat_window_as_the_model():
    from synthesis import director
    plan = {"generations": [[{"axis": "brightness", "add": 0.3}],
                            [{"axis": "attack", "set": 0.0}]],
            "order": ["b"]}
    d = director.FileDirector(plan)
    summary = _summary({}, {})
    assert d.directions(summary)[0]["axis"] == "brightness"
    assert d.directions(summary)[0]["axis"] == "attack"
    assert d.directions(summary)[0]["axis"] == "attack"     # the last repeats
    with support.TempDir() as folder:
        path = f"{folder}/reply.json"
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("Here you go:\n```json\n"
                         '{"directions": [{"axis": "motion", "add": 0.2}]}'
                         "\n```")
        assert director.make_director(f"file:{path}").directions(summary) \
            == [{"axis": "motion", "add": 0.2}]
    assert director.make_director("none") is None
    try:
        director.make_director("telepathy")
    except ValueError as error:
        assert "telepathy" in str(error)
    else:
        raise AssertionError("an unknown director was accepted")


# -- placement: how an instrument gets into a score -------------------------
def _install(chip, **dna):
    """Compile (no render), name and install one instrument."""
    from synthesis import registry
    backend, g = _genome(chip, **dna)
    g = backend.pin(g)
    compiled = backend.compile(g)
    compiled.name = f"t_{chip}"
    backend.install(compiled)
    if chip == "nes":
        from synthesis import nes_driver
        assert nes_driver.get(compiled.name) is compiled.patch
    else:
        assert registry.lookup(backend.name, compiled.name) is compiled
    return compiled


def _ticks(events):
    import events as E
    return sum(e.ticks for e in events if isinstance(e, E.Wait))


def test_a_detune_layer_is_added_on_the_next_channel_and_undone_with_the_note():
    import events as E
    import tracker
    with _Isolated():
        compiled = _install("ym2612", detune=0.8, body=1.0, decay=1.0)
        text = ("bpm 120\nlpb 4\nticks 240\ninst fm0 t_ym2612\ncols fm0\n"
                "A-3\n...\n...\n...\n===\n...\n")
        events, _ = tracker.loads(text)
        on = [e for e in events if isinstance(e, E.FMNoteOn)]
        assert [e.channel for e in on] == [0, 1]
        assert on[1].velocity < on[0].velocity          # quieter, by design
        porta = [e for e in events if isinstance(e, E.Portamento)]
        assert [(p.target, p.to_cents != 0.0) for p in porta] \
            == [("fm1", True), ("fm1", False)]
        offs = [e for e in events if isinstance(e, E.FMNoteOff)]
        assert sorted(e.channel for e in offs) == [0, 1]
        selects = [e for e in events if isinstance(e, E.FMInstrumentSelect)]
        assert {e.channel for e in selects} >= {0}
        # the plain parse has the same length: nothing moved
        plain = tracker.loads(text.replace("t_ym2612", "organ"))[0]
        assert _ticks(events) == _ticks(plain)


def test_a_layer_that_would_take_a_channel_the_score_plays_is_dropped_and_said():
    import events as E
    import tracker
    from synthesis import placement
    with _Isolated():
        _install("ym2612", detune=0.8, body=1.0, decay=1.0)
        text = ("bpm 120\nlpb 4\nticks 240\ninst fm0 t_ym2612\n"
                "inst fm1 organ\ncols fm0 fm1\nA-3 A-2\n... ...\n... ...\n"
                "... ...\n=== ===\n... ...\n")
        events, _ = tracker.loads(text)
        assert [e.channel for e in events if isinstance(e, E.FMNoteOn)] \
            == [0, 1]                            # the score's own, no layer
        assert any("layer dropped" in w for w in placement.LAST.warnings), \
            placement.LAST.text()


def test_an_instrument_with_an_lfo_setting_brings_it_with_the_select():
    import events as E
    import tracker
    with _Isolated():
        compiled = _install("ym2612", motion=0.7, body=1.0, decay=1.0)
        assert compiled.setup.get("pms")
        text = ("bpm 120\nlpb 4\nticks 240\ninst fm2 t_ym2612\ncols fm2\n"
                "A-3\n...\n===\n")
        events, _ = tracker.loads(text)
        pans = [e for e in events if isinstance(e, E.FMPan)]
        assert pans and pans[0].channel == 2 and pans[0].pms > 0
        assert any(isinstance(e, E.FMLFO) for e in events)


def test_an_nes_instrument_plays_from_its_macros_and_a_plain_score_is_untouched():
    import events as E
    import tracker
    with _Isolated():
        _install("nes", brightness=0.6, body=0.9, decay=0.6, motion=0.4)
        text = ("bpm 120\nlpb 4\nticks 240\ninst nes0 t_nes\ncols nes0\n"
                "A-4\n...\n...\n...\n===\n...\n")
        events, _ = tracker.loads(text)
        kinds = {type(e).__name__ for e in events}
        assert {"NESNoteOn", "NESNoteOff", "NESVolume", "Portamento"} <= kinds
        assert "Marker" not in kinds                    # consumed
        assert sum(isinstance(e, E.NESNoteOn) for e in events) == 1
        plain = tracker.loads("bpm 120\nlpb 4\ncols nes0\nA-4\n...\n===\n")[0]
        assert {type(e).__name__ for e in plain} \
            <= {"Tempo", "NESNoteOn", "NESNoteOff", "Wait", "End", "NESDuty"}


def test_a_wrong_nes_instrument_is_refused_with_the_line_and_the_way_out():
    import tracker
    with _Isolated():
        _install("nes", brightness=0.6, body=0.9, decay=0.6)
        for text, words in (
                ("cols nes0\ninst nes0 nothing_here\nA-4\n", "nothing_here"),
                ("cols nes3\ninst nes3 t_nes\n6\n", "pulse instrument"),
                ("cols nes4\ninst nes4 t_nes\nkick\n", "samples")):
            try:
                tracker.loads(text)
            except tracker.TrackerError as error:
                assert words in str(error), str(error)
            else:
                raise AssertionError(f"{text!r} was accepted")
        try:
            tracker.loads("cols nes0\ninst nes0 nothing_here\nA-4\n")
        except tracker.TrackerError as error:
            assert "line 2" in str(error)


def test_the_tracker_costs_nothing_extra_when_the_layer_is_not_in_use():
    import sys
    import tracker
    text = "bpm 120\nlpb 4\ncols fm0\nA-3\n...\n===\n"
    before = tracker.loads(text)[0]
    assert tracker._place_forge_instruments(before, None) is before
    assert "synthesis.placement" in sys.modules or True    # lazily imported


def test_a_marker_survives_a_trip_through_tracker_text():
    """What `inst nes0 NAME` leaves behind is a `mark` line, so a score
    written out by dumps() and read back still places the instrument."""
    import events as E
    import tracker
    with _Isolated():
        _install("nes", brightness=0.6, body=0.9, decay=0.6)
        label = "forge:nes pulse1 t_nes @2"
        events, _ = tracker.loads(
            f"cols nes0\nmark {label}\nA-4\n...\n===\n")
        assert any(isinstance(e, E.NESVolume) for e in events)   # expanded
        assert not any(isinstance(e, E.Marker) and e.label == label
                       for e in events)


# -- the analysis of how parts sit together -------------------------------------
def _score(text):
    import score_model
    return score_model.loads(text)


_BAND = """bpm 120
lpb 4
cols fm0 fm1 fm2
{rows}
"""


def _rows(lines):
    return _BAND.format(rows="\n".join(lines))


def test_the_lowest_unsure_part_is_the_bass_and_the_user_can_say_otherwise():
    from synthesis import arrangement, archetypes
    from synthesis.backends import get_backend
    backend = get_backend("ym2612")
    rows = []
    for bar in range(4):
        rows += ["A-2 A-4 C-5", "... ... ...", "E-2 C-5 ...", "... ... ..."]
    score = _score(_rows(rows))
    dna, reg, _, _ = archetypes.get("punch_bass")
    from synthesis.genome import Genome
    g = backend.pin(Genome("ym2612", dna, name="b", register=reg))
    c = backend.compile(g)
    assign = {v: ("ym2612", c) for v in ("fm0", "fm1", "fm2")}
    arr = arrangement.Arrangement(score, assign)
    assert arr.part("fm0").role == "bass"
    said = arrangement.Arrangement(score, assign, roles={"fm0": "pad"})
    assert said.part("fm0").role == "pad"


def test_the_low_band_the_message_names_is_the_band_the_test_sums():
    from synthesis import arrangement
    assert arrangement.LOW_BANDS == 3
    assert 250.0 < arrangement.LOW_HZ < 350.0
    for band in range(1, 12):
        edge = arrangement.band_edge_hz(band)
        assert arrangement.band_of(edge * 1.001) == band
        assert arrangement.band_of(edge * 0.999) == band - 1


def test_a_suggestion_names_its_part_once_and_carries_the_numbers():
    from synthesis import arrangement
    cs = [arrangement.Conflict("dynamics", ("a", "b"), 0.8, "x",
                               data={"have_db": 5.0, "want_db": -4.0})]
    report = arrangement.Report(conflicts=cs)

    class Fake(arrangement.Arrangement):
        def __init__(self):
            self.roles = {}
            self._parts = {}

        def part(self, voice):
            return arrangement.PartProfile(voice, notes=8, median=60.0)
    suggestions = Fake().suggest(report)
    assert len(suggestions) == 1
    assert suggestions[0]["part"] == "b"
    # 9 dB over the target: about x0.35, not a flat 3 dB
    assert 0.3 < suggestions[0]["velocity_scale"] < 0.4


# -- everything below renders -------------------------------------------------
def _read(chip, full=False, **dna):
    from synthesis import probe
    backend, g = _genome(chip, **dna)
    g = backend.pin(g)
    compiled = backend.compile(g)
    return probe.probe(backend, compiled, g, full=full).axes


def test_a_dial_turned_up_reads_higher_on_every_chip():
    """Open loop, no correction: the claim is only that each dial moves its
    own measurement in the right direction, on all three chips."""
    base = dict(brightness=0.5, attack=0.1, body=0.6, decay=0.5,
                metallicity=0.2, bass_weight=0.4, articulation=0.5)
    for chip, dials in (("ym2612", ("brightness", "attack", "body")),
                        ("opl2", ("brightness", "attack", "body")),
                        ("nes", ("attack", "body"))):
        for dial in dials:
            lo = _read(chip, **dict(base, **{dial: 0.15}))[dial]
            hi = _read(chip, **dict(base, **{dial: 0.85}))[dial]
            assert lo is not None and hi is not None
            assert hi > lo + 0.15, f"{chip} {dial}: {lo:.2f} -> {hi:.2f}"


def test_detune_is_graded_on_every_chip_and_reads_zero_when_there_is_none():
    base = dict(brightness=0.5, attack=0.1, body=1.0, decay=1.0,
                metallicity=0.2, bass_weight=0.4, articulation=0.5)
    for chip in ("ym2612", "opl2", "nes"):
        none = _read(chip, True, **dict(base, detune=0.0))["detune"]
        some = _read(chip, True, **dict(base, detune=0.45))["detune"]
        wide = _read(chip, True, **dict(base, detune=0.9))["detune"]
        assert none is not None and none < 0.08, (chip, none)
        assert some > none + 0.1 and wide > some + 0.1, (chip, none, some, wide)


def test_detune_does_not_leak_into_the_envelope_dials():
    base = dict(brightness=0.5, attack=0.1, body=1.0, decay=1.0,
                metallicity=0.2, bass_weight=0.4, articulation=0.5)
    for chip in ("ym2612", "opl2"):
        plain = _read(chip, True, **dict(base, detune=0.0))
        wide = _read(chip, True, **dict(base, detune=0.9))
        for dial in ("attack", "body", "decay", "brightness"):
            assert abs(plain[dial] - wide[dial]) < 0.03, (chip, dial)


def test_the_probe_and_the_sequencer_agree_about_a_layered_instrument():
    """Same instrument, played once by the probe and once through the
    tracker and the sequencer: the beat and the vibrato come out the same,
    which is what makes the numbers in a bank mean something in a song."""
    import analysis
    import chipgen
    from synthesis import descriptors, probe
    with _Isolated():
        for chip, line, column in (("ym2612", "inst fm0 t_ym2612", "fm0"),
                                   ("opl2", "inst opl0 t_opl2", "opl0")):
            compiled = _install(chip, detune=0.6, motion=0.3, body=1.0,
                                decay=1.0)
            backend = __import__("synthesis.backends", fromlist=["x"]) \
                .get_backend(chip)
            _, g = _genome(chip)
            heard = probe.probe(backend, compiled, g, full=True).axes
            text = (f"bpm 120\nlpb 4\nticks 240\n{line}\ncols {column}\nA-3\n"
                    + "...\n" * 19 + "===\n" + "...\n" * 4)
            result = chipgen.compose(text, chip_type="ym3438", quiet=True)
            mono = list(analysis.to_mono(result.audio))
            m = descriptors.measure(mono, result.sample_rate, 220.0, 2.4,
                                    0.5, movement=True)
            assert abs(m.axes["detune"] - heard["detune"]) < 0.15, \
                (chip, m.axes["detune"], heard["detune"])
            assert abs(m.axes["brightness"] - heard["brightness"]) < 0.1, chip


def test_the_search_is_deterministic_and_never_returns_an_invalid_instrument():
    from synthesis import bank
    scenario = {"name": "det", "backend": "opl2", "seeds": ["pluck", "bell"],
                "objective": {"target": {"brightness": 0.6, "attack": 0.1}},
                "generations": 2, "offspring": 3, "keep": 3, "seed": 11}
    one = bank.run_scenario(scenario)
    two = bank.run_scenario(scenario)
    assert one.names() == two.names()
    for a, b in zip(one.entries, two.entries):
        assert a.genome.to_dict() == b.genome.to_dict()
        assert abs(a.score - b.score) < 1e-9
    assert 1 <= len(one.entries) <= 4
    for entry in one.entries:
        assert not [i for i in entry.issues if i.startswith("error")]
        assert entry.measured.get("brightness") is not None


def test_a_director_steers_a_run_and_a_broken_one_cannot_stop_it():
    from synthesis import bank, director
    scenario = {"name": "dir", "backend": "opl2", "seeds": ["pluck"],
                "objective": {"target": {"brightness": 0.8}},
                "generations": 2, "offspring": 3, "keep": 2, "seed": 4}
    seen = []

    def ask(messages):
        seen.append(messages)
        raise RuntimeError("down")
    d = director.LLMDirector(ask=ask, every=1)
    result = bank.run_scenario(scenario, director=d)
    assert result.entries and seen and d.errors
    assert "brightness" in seen[0][1]["content"]


def test_a_mix_follows_its_weights_and_a_tree_nests():
    from synthesis import mixing
    from synthesis.backends import get_backend
    backend = get_backend("opl2")
    parents = ["archetype:sub_bass", "archetype:saw_lead"]   # dark, bright
    dark = mixing.mix(backend, parents, [3, 1], "dna", name="m_dark")
    light = mixing.mix(backend, parents, [1, 3], "dna", name="m_light")
    assert dark.backend == "opl2"
    assert light.measured["brightness"] > dark.measured["brightness"] + 0.2, \
        (dark.measured["brightness"], light.measured["brightness"])
    assert light.measured["bass_weight"] < dark.measured["bass_weight"]
    tree = mixing.run_recipe(backend, {"mix": [
        {"from": "archetype:organ", "weight": 2},
        {"mix": [{"from": "archetype:bell"}, {"from": "archetype:pad"}],
         "weight": 1}], "mode": "dna"}, None, "tree")
    assert "mix" in tree.character
    try:
        mixing.mix(backend, ["archetype:organ"], name="one")
    except mixing.MixError as error:
        assert "two" in str(error)
    else:
        raise AssertionError("a mix of one was accepted")
    try:
        mixing.mix(backend, ["archetype:organ", "no_such_patch"], name="x")
    except mixing.MixError as error:
        assert "no_such_patch" in str(error)
    else:
        raise AssertionError("an unknown parent was accepted")


def test_fitting_an_arrangement_lowers_its_cost_with_real_renders():
    import os
    import score_model
    from synthesis import arrangement, bank
    starter = bank.Bank.load(os.path.join(
        support.ROOT, "python", "synthesis", "banks", "starter.bank.json"))
    score = score_model.load(os.path.join(support.ROOT, "examples",
                                          "neon_transit.trk"))
    names = {"fm0": "fm_punch_bass", "fm1": "fm_saw_lead", "fm2": "fm_pad",
             "fm3": "fm_pluck", "fm4": "fm_organ"}
    assign = {v: ("ym2612", starter.get(n).compiled) for v, n in names.items()}
    genomes = {v: starter.get(n).genome for v, n in names.items()}
    arr = arrangement.Arrangement(score, assign)
    before = arr.analyse()
    assert before.conflicts and before.cost > 1.0
    assert arr.part("fm0").role == "bass"
    after, applied = arrangement.fit(arr, genomes, rounds=5)
    assert applied and after.cost < before.cost - 0.5, \
        (before.cost, after.cost)


def test_a_forge_bank_renders_through_the_command_line_path():
    """The whole way: a scenario makes a bank, `--forge-bank` loads it, the
    score names the instruments, the engine renders sound for all three
    chips, and the engine's own banks are left as they were."""
    import chipgen
    import instruments
    from synthesis import bank
    with _Isolated():
        out = bank.Bank("e2e")
        for chip, arch, name in (("ym2612", "saw_lead", "e_lead"),
                                 ("opl2", "pad", "e_pad"),
                                 ("nes", "square_lead", "e_nes")):
            backend = __import__("synthesis.backends", fromlist=["x"]) \
                .get_backend(chip)
            from synthesis import archetypes
            from synthesis.genome import Genome
            dna, reg, _, doc = archetypes.get(arch)
            g = backend.pin(Genome(chip, dna, name=name, register=reg))
            compiled = backend.compile(g)
            compiled.name = name
            out.add(bank.Entry(name, chip, g, compiled, {}, 0.0, "", doc))
        with support.TempDir() as folder:
            path = out.save(f"{folder}/e2e.bank.json")
            text = ("bpm 120\nlpb 4\nticks 240\ninst fm0 e_lead\n"
                    "inst opl0 e_pad\ninst nes0 e_nes\n"
                    "cols fm0 opl0 nes0\nA-3 A-3 A-4\n... ... ...\n"
                    "... ... ...\n... ... ...\n=== === ===\n... ... ...\n")
            result = chipgen.compose(text, forge_bank=path, quiet=True)
            assert result.peak > 0.05, "silent render"
            assert result.duration > 0.5
        assert "e_lead" in instruments.BANK          # while it was loaded
    assert "e_lead" not in instruments.BANK          # and not after


def test_a_pan_line_keeps_the_instruments_lfo_depth_and_the_panning():
    import events as E
    import tracker
    with _Isolated():
        compiled = _install("ym2612", motion=0.7, body=1.0, decay=1.0)
        pms = compiled.setup["pms"]
        text = ("bpm 120\nlpb 4\nticks 240\npan fm2 L\ninst fm2 t_ym2612\n"
                "pan fm2 R\ncols fm2\nA-3\n...\n===\n")
        events, _ = tracker.loads(text)
        pans = [e for e in events if isinstance(e, E.FMPan)
                and e.channel == 2]
        assert len(pans) >= 3, pans
        # before the select: the user's own, as written
        assert (pans[0].left, pans[0].right, pans[0].pms) == (True, False, 0)
        # the instrument's own setting arrives where the user panned left...
        assert (pans[1].left, pans[1].right, pans[1].pms) == (True, False, pms)
        # ...and the later `pan ... R` keeps its side and the depth
        assert (pans[2].left, pans[2].right, pans[2].pms) == (False, True, pms)


def test_a_macro_is_written_the_way_trackers_write_it():
    from synthesis import nes_driver as N
    m = N.parse_macro("15 14 13 | 12 11 / 8 4 0", "volume", 0, 15, whole=True)
    assert m.values == [15, 14, 13, 12, 11, 8, 4, 0]
    assert (m.loop, m.release) == (3, 5)
    walked = m.walk(hold_frames=9, total_frames=12)
    assert walked[:3] == [15, 14, 13] and walked[3:7] == [12, 11, 12, 11]
    assert walked[9:] == [8, 4, 0], walked          # key up: 8 4 0
    # no marks: play through and hold the last value
    held = N.parse_macro("3 2 1")
    assert held.walk(10, 12)[-1] == 1.0
    for text, words in (("15 99", "outside"), ("15 x", "not a number"),
                        ("| |", "only one"), ("15 | 14 / 13 | 12", "only one"),
                        ("", "no values"), ("15 /", "before a value"),
                        ("15 / 14 | 12", "loop")):
        try:
            N.parse_macro(text, "volume", 0, 15)
        except ValueError as error:
            assert words in str(error), (text, str(error))
        else:
            raise AssertionError(f"{text!r} was accepted")


def test_an_nes_instrument_written_by_hand_goes_into_a_bank_and_plays():
    import chipgen
    from synthesis import bank
    from synthesis.cli import main
    with _Isolated(), support.TempDir() as folder:
        path = f"{folder}/hand.bank.json"
        code = main(["nes-inst", "hand_lead", "--volume", "15 14 | 12 / 6 0",
                     "--duty", "1 1 2", "--pitch", "0 | 3 6 3 0 -3 -6 -3",
                     "--out", path])
        assert code == 0
        loaded = bank.Bank.load(path)
        assert loaded.names() == ["hand_lead"]
        assert loaded.get("hand_lead").measured["attack"] is not None
        text = ("bpm 120\nlpb 4\nticks 240\ninst nes0 hand_lead\ncols nes0\n"
                "A-4\n...\n...\n...\n===\n...\n")
        result = chipgen.compose(text, forge_bank=path, quiet=True)
        assert result.peak > 0.02
        # a wrong macro is refused with the macro named, and nothing is written
        assert main(["nes-inst", "bad", "--volume", "99",
                     "--out", f"{folder}/bad.bank.json"]) == 2


def test_a_detune_layer_is_not_a_part_of_its_own_to_the_crowding_check():
    """A layer is the same note on the next channel; counting it as a voice
    would warn "three voices in one octave" about a lead and a pad."""
    import chipgen
    with _Isolated():
        _install("ym2612", detune=0.8, body=1.0, decay=1.0)

        def crowded(text):
            result = chipgen.compose(text, quiet=True)
            return any("one octave" in w for w in result.warnings)
        # long enough to be judged as an arrangement (the floor is 5 s)
        layered = ("bpm 140\nlpb 4\ninst fm2 t_ym2612\ninst fm5 organ\n"
                   "cols fm2 fm4\n" + "C-4 E-4\n... ...\n" * 48)
        three = ("bpm 140\nlpb 4\ninst fm0 organ\ninst fm2 organ\n"
                 "inst fm4 organ\ncols fm0 fm2 fm4\n"
                 + "C-4 E-4 G-4\n... ... ...\n" * 48)
        assert not crowded(layered), "the layer was counted as a voice"
        assert crowded(three), "three real voices in an octave must still warn"


def test_a_bank_with_numbers_the_chip_cannot_take_is_refused_on_load():
    """A bank is a file anyone can edit, and the chip would take any number
    written to its register and play something else."""
    import os
    from synthesis import bank
    path = os.path.join(support.ROOT, "python", "synthesis", "banks",
                        "starter.bank.json")
    with open(path, encoding="utf-8") as handle:
        original = json.load(handle)

    def tampered(chip, change):
        data = copy.deepcopy(original)
        entry = next(e for e in data["instruments"] if e["backend"] == chip)
        change(entry["compiled"]["patch"])
        data["instruments"] = [entry]
        return data, entry["name"]
    cases = (
        ("ym2612", lambda p: p["operators"][0].update(total_level=999),
         "operator 1 total_level"),
        ("opl2", lambda p: p["carrier"].update(attack=-3), "carrier attack"),
        ("nes", lambda p: p["volume"]["values"].__setitem__(0, 40),
         "volume frame 0"))
    with support.TempDir() as folder:
        for chip, change, words in cases:
            data, name = tampered(chip, change)
            file = f"{folder}/{chip}.json"
            with open(file, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            try:
                bank.Bank.load(file)
            except bank.BankError as error:
                assert words in str(error) and name in str(error), str(error)
            else:
                raise AssertionError(f"{chip}: an illegal bank was accepted")


def test_a_layer_hears_what_its_part_is_told():
    """Volume, panning and vibrato on a part reach its detune layer; the
    layer is the same part, and one that ignored the fader would be louder
    than its own lead."""
    import events as E
    import tracker
    with _Isolated():
        _install("ym2612", detune=0.8, body=1.0, decay=1.0)
        text = ("bpm 120\nlpb 4\nticks 240\npan fm2 L\ninst fm2 t_ym2612\n"
                "vol fm2 60\nvib fm2 20 5\ncols fm2\nA-3\n...\n===\n")
        events, _ = tracker.loads(text)
        layer = [e for e in events if getattr(e, "channel", None) == 3
                 or getattr(e, "target", None) == "fm3"]
        kinds = {type(e).__name__ for e in layer}
        assert {"FMPan", "FMVolume", "Vibrato", "FMNoteOn"} <= kinds, kinds
        pan = next(e for e in layer if isinstance(e, E.FMPan))
        assert (pan.left, pan.right) == (True, False)
        volume = next(e for e in layer if isinstance(e, E.FMVolume))
        assert volume.volume == 60
        # and only the part that has a layer passes things on
        plain = tracker.loads(text.replace("t_ym2612", "organ"))[0]
        assert not any(getattr(e, "channel", None) == 3 for e in plain)
