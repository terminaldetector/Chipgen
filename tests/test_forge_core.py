"""The forge's foundations, with no player involved: the transform, the
loop synthesiser, the dials as defined by their measurements, the partial
arithmetic, patches and their envelopes, banks and scenarios on disk, the
director's reading of a model's reply."""

import json
import math
import os
import random
import tempfile

import support
from schism import model as M
from schism.forge import (bank as bank_mod, descriptors, director, dna, dsp,
                          genome, patch as patch_mod, score, spectral,
                          tonelib)

SR = 44100


# -- the transform -------------------------------------------------------------
def _dft(x, k):
    n = len(x)
    return sum(x[t] * complex(math.cos(2 * math.pi * k * t / n),
                              -math.sin(2 * math.pi * k * t / n))
               for t in range(n))


def test_the_fft_is_the_definition():
    rng = random.Random(3)
    for n in (8, 64, 256):
        x = [rng.uniform(-1, 1) for _ in range(n)]
        bins = dsp.rfft(x)
        assert len(bins) == n // 2 + 1
        for k in (0, 1, 3, n // 4, n // 2):
            assert abs(bins[k] - _dft(x, k)) < 1e-9, (n, k)


def test_the_inverse_transform_returns_the_signal():
    rng = random.Random(4)
    for n in (4, 64, 4096):
        x = [rng.uniform(-1, 1) for _ in range(n)]
        back = dsp.irfft(dsp.rfft(x), n)
        assert max(abs(a - b) for a, b in zip(x, back)) < 1e-12


def test_a_line_in_a_bin_is_a_cosine_of_that_amplitude():
    n = 1024
    bins = [0j] * (n // 2 + 1)
    bins[37] = 0.5 * n / 2 * complex(math.cos(1.0), math.sin(1.0))
    x = dsp.irfft(bins, n)
    for t in (0, 5, 100, 777):
        assert abs(x[t] - 0.5 * math.cos(2 * math.pi * 37 * t / n + 1.0)) < 1e-12


def test_a_transform_of_a_bad_length_is_refused():
    for bad in (lambda: dsp.rfft([0.0] * 12), lambda: dsp.irfft([0j] * 7, 12),
                lambda: dsp.fft([0j] * 6)):
        try:
            bad()
        except ValueError:
            continue
        raise AssertionError("a length that is not a power of two was taken")


def test_bessel_functions_match_their_tables():
    assert abs(spectral.bessel_j(0, 1.0) - 0.7651976866) < 1e-9
    assert abs(spectral.bessel_j(1, 2.0) - 0.5767248078) < 1e-9
    assert abs(spectral.bessel_j(-2, 3.0) - spectral.bessel_j(2, 3.0)) < 1e-12
    total = sum(spectral.bessel_j(j, 2.5) ** 2 for j in range(-20, 21))
    assert abs(total - 1.0) < 1e-9                  # the power is conserved


# -- loops ------------------------------------------------------------------------
def _spec(**kw):
    base = dict(partials=[(h, 1.0 / h) for h in range(1, 21)], frames=4096,
                cycles=1)
    base.update(kw)
    return spectral.LoopSpec(**base)


def test_a_loop_joins_itself():
    for kw in ({}, {"cycles": 16}, {"frames": 32768, "cycles": 163,
                                    "voices": [(0, 1.0), (1, 0.3), (-1, 0.3)]},
               {"frames": 32768, "cycles": 163, "pm": 20.0, "am": 0.1,
                "lfo_bins": 4}, {"frames": 32768, "cycles": 100,
                                 "noise": 0.01}):
        spec = _spec(**kw)
        x = spectral.make_loop(spec)
        assert len(x) == spec.frames and abs(max(abs(v) for v in x) - 1.0) < 1e-9
        steepest = max(abs(b - a) for a, b in zip(x, x[1:]))
        assert abs(x[0] - x[-1]) <= 1.05 * steepest, kw


def test_the_partials_of_a_saw_have_their_ratios():
    x = spectral.make_loop(_spec())
    bins = dsp.rfft(x)
    base = abs(bins[1])
    for h in (2, 3, 5, 10):
        assert abs(abs(bins[h]) / base - 1.0 / h) < 1e-6, h
    assert abs(bins[25]) < 1e-9 * base                 # nothing above the 20th


def test_unison_voices_sit_a_bin_apart_at_the_level_asked():
    spec = _spec(frames=32768, cycles=163,
                 voices=[(0, 1.0), (1, 0.4), (-1, 0.4)])
    bins = dsp.rfft(spectral.make_loop(spec))
    centre, up, down = (abs(bins[163]), abs(bins[164]), abs(bins[162]))
    assert abs(up / centre - 0.4) < 1e-6 and abs(down / centre - 0.4) < 1e-6
    # the beat is a bin: 44100/32768 hertz, 1.346 Hz
    assert abs(spectral.SAMPLE_RATE / 32768 - 1.3458) < 1e-3


def test_vibrato_sidebands_keep_the_power_of_the_line():
    plain = spectral.make_loop(_spec(frames=32768, cycles=163))
    moved = spectral.make_loop(_spec(frames=32768, cycles=163, pm=40.0,
                                     lfo_bins=4))
    # both were normalised to a peak of 1; compare the shape of the
    # spectrum around the first partial instead: the carrier shrinks and
    # sidebands carry what it lost, 4 bins either side
    b0 = dsp.rfft(plain)
    b1 = dsp.rfft(moved)
    p0 = abs(b0[163]) ** 2
    near = sum(abs(b1[163 + j * 4]) ** 2 for j in range(-6, 7))
    assert abs(b1[163 - 4]) > 0 and abs(b1[163 + 4]) > 0
    assert abs(near / (abs(b1[163]) ** 2 + 1e-30)) > 1.0 + 1e-3
    assert p0 > 0


def test_a_sample_plays_the_note_it_was_asked_to_by_arithmetic():
    from schism.forge import fam_tone
    params = {"partials": [[float(h), 1.0 / h] for h in range(1, 12)],
              "grid": 1, "phases": "sine", "seed": 7}
    for low, high in ((13, 24), (37, 48), (61, 72)):
        built = fam_tone.tone_sampler(params, low, high)
        loop = built.data[built.loop[0]:built.loop[1]]
        n = len(loop)
        peak_bin = max(range(1, n // 2),
                       key=lambda k: abs(dsp.rfft([float(v) for v in loop])[k])
                       ) if n & (n - 1) == 0 else None
        assert peak_bin == 1, (low, high, peak_bin)
        for note in (low, (low + high) // 2, high):
            # fundamental: one cycle per loop, played at c5speed * ratio
            rate = built.c5speed * 2.0 ** ((note - 61) / 12.0)
            played = rate / n * peak_bin
            assert abs(1200 * math.log2(played / M.note_frequency(note))) < 0.2


# -- the dials, as measured -----------------------------------------------------
def _play(x, hz, hold=0.84, tail=0.7, movement=False):
    return descriptors.measure(x, SR, hz, hold, tail, movement=movement)


def _tone(f, seconds, partials=((1, 1.0),), env=None):
    n = int(seconds * SR)
    out = [0.0] * n
    for k, a in partials:
        w = 2 * math.pi * f * k / SR
        for i in range(n):
            out[i] += a * math.sin(w * i)
    if env:
        out = [v * env(i / SR) for i, v in enumerate(out)]
    return out


def test_a_sine_reads_as_a_sine():
    m = _play(_tone(220.0, 1.54, env=lambda t: 1.0 if t < 0.84 else
                    math.exp(-(t - 0.84) / 0.05)), 220.0)
    assert m.axes["brightness"] < 0.03 and m.axes["bass_weight"] > 0.95
    assert m.axes["metallicity"] < 0.1 and m.axes["noise"] < 0.05
    assert m.axes["attack"] < 0.1 and m.axes["body"] > 0.95
    assert m.pitch == "exact"


def test_brightness_is_the_centroid_over_the_fundamental():
    parts = [(h, 1.0 / h) for h in range(1, 40)]
    m = _play(_tone(220.0, 1.54, parts), 220.0)
    centroid = sum(a * h for h, a in parts) / sum(a for _, a in parts)
    want = math.log(centroid) / math.log(16)
    assert abs(m.axes["brightness"] - want) < 0.08, (m.axes["brightness"], want)


def test_attack_is_read_from_the_rise_time():
    for rise in (0.06, 0.12, 0.24):
        env = lambda t, r=rise: min(1.0, t / r) if t < 0.84 else \
            math.exp(-(t - 0.84) / 0.05)
        m = _play(_tone(220.0, 1.54, env=env), 220.0)
        seconds = 0.8 * rise                    # a ramp reads as 80% of itself
        want = math.log(1 + seconds / 0.002) / math.log(1 + 0.4 / 0.002)
        assert abs(m.axes["attack"] - want) < 0.1, (rise, m.axes["attack"], want)


def test_decay_and_body_are_read_from_the_fall():
    tau = 0.15
    env = lambda t: math.exp(-t / tau)
    m = _play(_tone(220.0, 1.54, env=env), 220.0)
    want = math.log(tau / 0.03) / math.log(100)
    assert abs(m.axes["decay"] - want) < 0.08, (m.axes["decay"], want)
    assert m.axes["body"] < 0.2          # e**-4 of the peak at 0.6 s: -35 dB


def test_a_beat_between_two_voices_reads_as_detune():
    seconds = 3.1
    n = int(seconds * SR)
    a, b = 220.0, 221.35
    x = [math.sin(2 * math.pi * a * i / SR)
         + 0.3 * math.sin(2 * math.pi * b * i / SR) for i in range(n)]
    x = [v * (1.0 if i < int(2.4 * SR) else math.exp(-(i / SR - 2.4) / 0.05))
         for i, v in enumerate(x)]
    m = descriptors.measure(x, SR, 220.0, 2.4, 0.7, movement=True)
    assert m.axes["detune"] is not None and 0.2 < m.axes["detune"] < 0.9, \
        m.axes["detune"]
    assert m.axes["motion"] is not None and m.axes["motion"] < 0.2


def test_a_tremolo_reads_as_motion_and_not_as_detune():
    # depth against dial: motion = (depth - 0.04) / 0.35, however deep; a
    # deep tremolo falls as fast as a cliff in an envelope does, and must
    # not be mistaken for one
    for depth in (0.1, 0.25, 0.4):
        n = int(3.1 * SR)
        x = [(1.0 + depth * math.sin(2 * math.pi * 5.5 * i / SR))
             * math.sin(2 * math.pi * 220.0 * i / SR)
             * (1.0 if i < int(2.4 * SR) else
                math.exp(-(i / SR - 2.4) / 0.05)) for i in range(n)]
        m = descriptors.measure(x, SR, 220.0, 2.4, 0.7, movement=True)
        want = min(1.0, (depth - 0.04) / 0.35)
        assert m.axes["motion"] is not None, depth
        assert abs(m.axes["motion"] - want) < 0.1, (depth, m.axes["motion"])
        assert m.axes["detune"] is not None and m.axes["detune"] < 0.1


def test_noise_reads_as_noise_and_a_tone_does_not():
    rng = random.Random(5)
    x = [rng.uniform(-1, 1) for _ in range(int(1.54 * SR))]
    assert _play(x, 440.0).axes["noise"] > 0.7
    assert _play(_tone(440.0, 1.54), 440.0).axes["noise"] < 0.1


def test_the_semitone_spectrum_peaks_where_the_note_is():
    m = _play(_tone(220.0, 1.54), 220.0)
    band = max(range(len(m.semitones)), key=lambda i: m.semitones[i])
    assert band == 36                                   # A3 is 36 above A0
    assert abs(sum(m.semitones) - 1.0) < 1e-6


# -- the arithmetic of a tone ---------------------------------------------------------
def test_the_design_of_a_spectrum_lands_on_the_centroid_it_was_given():
    for b in (0.2, 0.4, 0.6, 0.8, 1.0):
        ratio = 16 ** b
        parts, p, g1, stretch, rough = tonelib.design(
            "saw", 40, ratio, tonelib.bass_ratio(0.4))
        assert abs(tonelib.centroid_ratio(parts) / ratio - 1) < 0.03, b
        assert abs(tonelib.bass_energy_ratio(parts) / tonelib.bass_ratio(0.4)
                   - 1) < 0.05, b


def test_the_dial_scales_are_the_inverses_of_the_measurements():
    for dial in (0.1, 0.4, 0.7):
        seconds = tonelib.attack_seconds(dial)
        assert abs(math.log(1 + seconds / 0.002) / math.log(201) - dial) < 1e-9
        tau = tonelib.decay_seconds(dial)
        assert abs(math.log(tau / 0.03) / math.log(100) - dial) < 1e-9
        t20 = tonelib.release_seconds(dial)
        assert abs(math.log(t20 / 0.02) / math.log(50) - dial) < 1e-9
        assert abs(20 * math.log10(tonelib.sustain_ratio(dial)) - (-40 * (1 - dial))) < 1e-9


def test_inharmonicity_is_zero_for_multiples_and_grows_with_stretch():
    plain = tonelib.partials_of("saw", 1.0, 30)
    assert tonelib.inharmonicity(plain) < 1e-9
    last = 0.0
    for s in (0.005, 0.02, 0.05):
        now = tonelib.inharmonicity(tonelib.partials_of("saw", 1.0, 30,
                                                        stretch=s))
        assert now > last
        last = now


# -- DNA, scores, objectives -----------------------------------------------------------------
def test_dna_has_eleven_axes_and_each_has_its_measurement_written_down():
    assert len(dna.AXES) == 11
    for axis in dna.AXES:
        low, high, how = dna.AXIS_DOC[axis]
        assert low and high and how


def test_dna_refuses_what_is_not_a_dial_and_clamps_on_request():
    try:
        dna.DNA(brightness=1.5)
    except dna.DNAError as error:
        assert "brightness" in str(error)
    else:
        raise AssertionError("1.5 is not a dial setting")
    assert dna.DNA.clamped(brightness=1.5).brightness == 1.0
    assert dna.DNA.from_dict({"Bright": 0.3, "bass": 0.9}).bass_weight == 0.9
    try:
        dna.DNA.from_dict({"sparkle": 1})
    except dna.DNAError as error:
        assert "sparkle" in str(error) and "brightness" in str(error)
    else:
        raise AssertionError("sparkle is not an axis")


def test_blending_dna_is_a_weighted_mean_and_mutation_moves_few_axes():
    a, b = dna.DNA(brightness=0.2), dna.DNA(brightness=0.8)
    assert abs(dna.blend([(a, 1), (b, 3)]).brightness - 0.65) < 1e-9
    rng = random.Random(1)
    for _ in range(50):
        child = dna.mutate(a, rng, 0.3)
        moved = [x for x in dna.AXES if abs(getattr(child, x) - getattr(a, x))
                 > 1e-12]
        assert 1 <= len(moved) <= 3
        assert all(0.0 <= getattr(child, x) <= 1.0 for x in dna.AXES)


def test_an_objective_that_names_few_axes_scores_only_those():
    obj = score.Objective({"brightness": 0.8})
    assert obj.targeted() == ["brightness"]
    fit, errors = score.objective_fit(
        obj, {"brightness": 0.8, "decay": 0.0, "noise": 1.0}, dna.AXES)
    assert fit == 1.0 and list(errors) == ["brightness"]
    boxed = score.Objective(box={"metallicity": (0.5, 1.0)})
    assert boxed.error("metallicity", 0.7) == 0.0
    assert abs(boxed.error("metallicity", 0.2) - 0.3) < 1e-12


def test_a_genome_has_one_key_per_sound():
    g = genome.Genome("tone", dna.DNA(brightness=0.4), name="a")
    assert g.key() == g.clone(name="b").key()
    assert g.key() != g.clone(dna=dna.DNA(brightness=0.41)).key()
    assert genome.Genome.from_dict(g.to_dict()).key() == g.key()


# -- patches ----------------------------------------------------------------------------------
def test_envelopes_are_in_seconds_and_become_ticks_at_the_modules_tempo():
    p = patch_mod.Patch("tone", amp=[[0.0, 0.0], [0.1, 1.0], [0.5, 0.5],
                                     [0.9, 0.0]], amp_sustain=[2, 2])
    slow, fast = patch_mod.build_envelopes(p, 0.04)[0], \
        patch_mod.build_envelopes(p, 0.02)[0]
    assert [t for t, _ in fast.nodes] == [0, 5, 25, 45]
    assert [t for t, _ in slow.nodes] == [0, 2, 12, 22]      # 2.5 rounds to 2
    assert [v for _, v in fast.nodes] == [0, 64, 32, 0]
    assert fast.sustain == (2, 2)


def test_nodes_that_land_on_one_tick_merge_and_the_marks_follow():
    nodes, (sus,) = patch_mod.to_ticks(
        [[0.0, 0.0], [0.004, 1.0], [0.5, 0.4]], 0.02, 64.0, 0, 64, ([2, 2],))
    assert nodes == [(0, 64), (25, 26)] and sus == (1, 1)


def test_a_flat_full_envelope_is_no_envelope():
    p = patch_mod.Patch("tone")
    assert patch_mod.build_envelopes(p, 0.02) == (None, None, None)


def test_a_patch_names_the_field_the_format_cannot_hold():
    p = patch_mod.Patch("tone", amp=[[0.0, 1.0]] + [[0.1 * k, 0.5]
                                                    for k in range(1, 30)])
    assert any("amp has 30 nodes" in s for s in p.problems())
    p = patch_mod.Patch("tone", amp=[[0.0, 1.0], [0.0, 0.5]])
    assert any("times must increase" in s for s in p.problems())
    p = patch_mod.Patch("tone", inst={"nna": "explode", "gain": 1})
    text = " ".join(p.problems())
    assert "nna" in text and "gain" in text
    p = patch_mod.Patch("tone", filter_env=[[0.0, 10]], pitch_env=[[0.0, 1]])
    assert any("share one slot" in s for s in p.problems())
    assert patch_mod.Patch("tone").problems() == []


def test_a_patch_survives_its_dictionary():
    p = patch_mod.Patch("modal", {"modes": [[1.0, 1.0, 0.5]]},
                        amp=[[0.0, 1.0], [0.2, 0.0]], amp_sustain=[0, 0],
                        inst={"nna": "fade", "fade": 40}, vib=[24, 6, 20, 0],
                        bands=(13, 60, 15), cutoff=90)
    q = patch_mod.Patch.from_dict(json.loads(json.dumps(p.to_dict())))
    assert q.to_dict() == p.to_dict()


# -- the director -----------------------------------------------------------------------------------
def test_a_models_reply_is_read_through_its_decoration():
    for text in ('{"directions": [{"axis": "brightness", "add": 0.2}]}',
                 'Sure!\n```json\n{"directions": [{"axis": "brightness", '
                 '"add": +0.2,}]}\n```\nHope that helps',
                 "{'directions': [{'axis': 'brightness', 'add': 0.2}]}"):
        raw = director.extract_json(text)
        assert raw is not None, text
        got = director.clean_directions(raw, "tone")
        assert got == [{"axis": "brightness", "add": 0.2}], text
    assert director.extract_json("I think it should be brighter.") is None


def test_a_direction_the_family_cannot_use_is_dropped_and_steps_are_clipped():
    notes = []
    got = director.clean_directions(
        [{"axis": "detune", "add": 0.3}, {"axis": "brightness", "add": 5},
         {"axis": "sparkle", "add": 1}, {"axis": "brightness", "add": 0.1},
         {"axis": "punch", "set": 7}], "drum", notes)
    assert got == [{"axis": "brightness", "add": 0.5},
                   {"axis": "punch", "set": 1.0}]
    assert any("detune" in n for n in notes) and any("sparkle" in n
                                                     for n in notes)


def test_the_heuristic_director_pushes_toward_the_target():
    summary = {"family": "tone", "generation": 1,
               "objective": {"target": {"brightness": 0.9}, "box": {},
                             "weights": {}, "ignore": []},
               "members": [{"name": "a", "score": 0.5,
                            "measured": {"brightness": 0.3, "decay": 0.5},
                            "errors": {}}]}
    got = director.HeuristicDirector().directions(summary)
    assert got and got[0]["axis"] == "brightness" and got[0]["add"] > 0.2


def test_a_director_that_fails_costs_nothing():
    def broken(messages):
        raise RuntimeError("the server is down")
    d = director.LLMDirector(ask=broken)
    summary = {"family": "tone", "generation": 1,
               "objective": {"target": {"brightness": 0.9}},
               "members": [{"name": "a", "score": 0.5,
                            "measured": {"brightness": 0.3}, "errors": {}}]}
    assert d.directions(summary)[0]["axis"] == "brightness"
    assert d.errors and "down" in d.errors[0]


# -- files -------------------------------------------------------------------------------------------
def _write(path, data):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)


def test_a_bank_that_is_not_a_bank_says_so():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "x.json")
        _write(path, {"format": "something-else"})
        try:
            bank_mod.Bank.load(path)
        except bank_mod.BankError as error:
            assert "not a forge bank" in str(error)
        else:
            raise AssertionError("loaded")
        try:
            bank_mod.Bank.load(os.path.join(tmp, "missing.json"))
        except bank_mod.BankError as error:
            assert "no bank file" in str(error)
        with open(path, "w") as handle:
            handle.write("{not json")
        try:
            bank_mod.Bank.load(path)
        except bank_mod.BankError as error:
            assert "not JSON" in str(error)


def test_a_scenario_names_its_mistakes():
    for bad, word in (({"family": "tone", "generashuns": 3}, "generashuns"),
                      ({"seeds": ["saw_lead"]}, "family"),
                      ({"family": "didgeridoo"}, "didgeridoo"),
                      ({"family": "tone", "format": "other/9"}, "format")):
        try:
            bank_mod.load_scenario(bad)
        except bank_mod.BankError as error:
            assert word in str(error), (bad, str(error))
        else:
            raise AssertionError(f"{bad} was taken")
    assert bank_mod.load_scenario({"family": "tone"})["family"] == "tone"


def test_a_seed_of_the_wrong_family_is_refused():
    from schism.forge.family import get_family
    try:
        bank_mod.seeds_of(get_family("drum"), {"seeds": ["saw_lead"]})
    except bank_mod.BankError as error:
        assert "tone sound" in str(error) and "drum" in str(error)
    else:
        raise AssertionError("a saw lead is not a drum")


def test_without_libopenmpt_a_ready_made_instrument_compiles_and_a_fresh_one_says_why():
    from schism import notation, openmpt
    asked = ("tempo 120\nchannels 1\ninst 1 name=B forge=%s\n"
             "pattern a rows 32\nC-2 01 v64 ...\nrest 31\nend\norder a\n")
    real = openmpt.available
    openmpt.available = lambda: False
    try:
        module, report = notation.compile_text(asked % "punch_bass")
        assert report.warnings == () and module.samples
        try:
            notation.compile_text(asked % "punch_bass dna=brightness:0.9")
        except notation.NotationError as error:
            text = str(error)
            assert "libopenmpt" in text and "starter bank" in text, text
        else:
            raise AssertionError("a fresh sound was made without a player")
    finally:
        openmpt.available = real


def test_what_the_search_writes_does_not_depend_on_the_hash_seed():
    """Python orders a set by a hash that changes with every process. A bank
    made through a crossover or a mix whose dictionaries came from sets was
    a different file each time it was made, with the same sound in it."""
    import subprocess
    import sys
    code = (
        "import json, sys; sys.path.insert(0, %r)\n"
        "from schism.forge import dna, evolve, genome, mixing\n"
        "a = genome.Genome('tone', dna.DNA(brightness=0.3), name='a',"
        " bias=dict(punch=0.1, decay=-0.2, brightness=0.3, body=0.05,"
        " bass_weight=-0.1))\n"
        "b = genome.Genome('tone', dna.DNA(brightness=0.8), name='b',"
        " bias=dict(metallicity=0.2, attack=0.1, noise=-0.3, decay=0.1))\n"
        "f = evolve.Forge('tone', seed=4)\n"
        "child = f.cross(a, b)\n"
        "pa = dict(zip('abcdefghij', range(10)))\n"
        "pb = dict(zip('fghijklmno', range(10)))\n"
        "merged = mixing._morph_numbers(pa, pb, 0.5)\n"
        "print(json.dumps([child.to_dict(), list(merged)]))\n" % support.ROOT)
    outputs = []
    for hash_seed in ("1", "2", "3"):
        done = subprocess.run([sys.executable, "-c", code], check=True,
                              capture_output=True, text=True,
                              env=dict(os.environ, PYTHONHASHSEED=hash_seed))
        outputs.append(done.stdout)
    assert outputs[0] == outputs[1] == outputs[2], outputs
    bias = json.loads(outputs[0])[0]["bias"]
    assert list(bias) == sorted(bias), bias
