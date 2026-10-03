"""The synthesiser: band-limiting, loops, tuning arithmetic, determinism."""

import math

import support
from schism import synth


def _harmonic_energy(cycle, above):
    """Energy in harmonics above `above` of one period (direct DFT)."""
    n = len(cycle)
    total = 0.0
    for h in range(above + 1, n // 2):
        a = sum(cycle[i] * math.cos(2 * math.pi * h * i / n)
                for i in range(n))
        b = sum(cycle[i] * math.sin(2 * math.pi * h * i / n)
                for i in range(n))
        total += (a * a + b * b) * 4.0 / (n * n)
    return total


def test_a_band_limited_cycle_has_nothing_above_its_limit():
    for shape in ("saw", "square", "triangle", "pulse"):
        cycle = synth.cycle(shape, 256, 20, 0.3)
        assert max(abs(v) for v in cycle) <= 1.0 + 1e-9
        assert _harmonic_energy(cycle, 20) < 1e-12, shape


def test_the_classic_shapes_have_their_series():
    saw = synth.cycle("saw", 512, 60)
    assert saw[100] < saw[300]            # a rising ramp...
    assert min(saw) < -0.99 and max(saw) > 0.99
    assert abs(sum(saw)) < 1e-6           # ...and has no DC
    square = synth.cycle("square", 512, 61)
    assert all(abs(square[i] + square[i + 256]) < 1e-6 for i in range(256))
    triangle = synth.cycle("triangle", 512, 61)
    assert max(triangle) > 0.99 and min(triangle) < -0.99


def test_each_octave_sample_is_clean_at_its_top_note():
    groups = synth.multisample(lambda n, h: synth.cycle("saw", n, h), 13, 96)
    assert [(lo, hi) for lo, hi, _, _ in groups][0] == (13, 24)
    assert groups[-1][1] == 96
    for low, high, data, c5speed in groups:
        cycle_frames = len(data)
        # c5speed is the cycle length times C-5, so every note is in tune
        assert c5speed == round(cycle_frames * synth.C5_HZ)
        top = synth.hz_of_note(high)
        # harmonics the cycle carries, at the top note, stay under the limit
        carried = cycle_frames // 2 - 1
        kept = min(carried, int(synth.BAND_LIMIT_HZ / top))
        floats = [v / 32767.0 for v in data]
        assert _harmonic_energy(floats, kept) < 1e-4, (low, high)


def test_a_pad_loop_joins_itself():
    data, c5speed = synth.pad(base_note=49, voices=5, detune_cents=14,
                              seconds=1.0)
    assert len(data) == 44100
    step = max(abs(data[i + 1] - data[i]) for i in range(0, len(data) - 1, 7))
    join = abs(data[0] - data[-1])
    assert join <= step * 1.5, (join, step)    # no bigger than an ordinary step
    # played at its base note it is at its own pitch: C-4 is one octave
    # under C-5, so the data plays at twice the sample rate's ratio
    assert c5speed == round(44100 * synth.C5_HZ / synth.hz_of_note(49))


def test_the_pluck_is_tuned_by_arithmetic_not_hope():
    data, c5speed = synth.pluck(root_note=61)
    size = round(44100 / synth.hz_of_note(61))
    assert c5speed == round((size + 0.5) * synth.C5_HZ)
    # and the sample really rings at 44100 / (N + 0.5)
    x = [v / 32767.0 for v in data[2000:12000]]
    best = max(range(size - 3, size + 4),
               key=lambda lag: sum(a * b for a, b in zip(x, x[lag:])))
    assert best == size


def test_recipes_are_deterministic():
    assert synth.kick() == synth.kick()
    assert synth.hat(seed=3) == synth.hat(seed=3)
    assert synth.hat(seed=3) != synth.hat(seed=4)
    assert synth.noise_loop(0.2, 9) == synth.noise_loop(0.2, 9)


def test_one_shots_end_quietly_and_fit_in_sixteen_bits():
    for make in (synth.kick, synth.snare, synth.hat, synth.clap, synth.tom,
                 synth.rim):
        data = make()
        assert max(abs(v) for v in data) <= 32767
        tail = data[-max(8, len(data) // 50):]
        assert max(abs(v) for v in tail) < 0.02 * 32767, make.__name__
        assert data[-1] == 0, make.__name__          # no step at the end
    for data in (synth.bell()[0], synth.pluck()[0],
                 synth.pluck(root_note=37)[0]):         # a long, slow line
        tail = data[-max(8, len(data) // 50):]
        assert max(abs(v) for v in tail) < 0.02 * 32767
        assert data[-1] == 0


def test_a_noise_loop_has_no_click_at_the_seam():
    data = synth.noise_loop(0.5, 8)
    steps = [abs(data[i + 1] - data[i]) for i in range(len(data) - 1)]
    seam = abs(data[0] - data[-1])
    assert seam <= max(steps)
