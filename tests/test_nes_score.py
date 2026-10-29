"""Writing a NES score, not just reading one.

nes_apu.py and nes_transcribe.py both predate this: the APU could be
driven register by register and a NES VGM could be read INTO a score, but
there was no way to write one — no events, no tracker columns, no
sequencer path. These tests cover the layer that closes that, and every
one of them exists because the thing it checks failed silently first.
"""

import math


def _mono(result):
    import analysis
    return [float(v) for v in analysis.to_mono(
        result.audio if hasattr(result, "audio") else result)]


def _rms(samples):
    if not samples:
        return 0.0
    return math.sqrt(sum(v * v for v in samples) / len(samples))


def _render(score):
    import chipgen
    return chipgen.compose(score)


def _held(columns, row, rows=4):
    """A score holding one row, then resting — the shape most of these want."""
    hold = "  ".join("..." for _ in columns.split())
    return (f"bpm 150\nlpb 4\ncols {columns}\n{row}\n"
            + "\n".join(hold for _ in range(rows)) + "\nend\n")


def _register_trace(score, watch):
    """Every value written to the watched APU registers while a score runs."""
    import nes_apu
    import sequencer
    import tracker

    events, meta = tracker.loads(score)
    seen = []
    original = nes_apu.NESAPU.write

    def spy(self, address, value):
        if address in watch:
            seen.append((address, value))
        return original(self, address, value)

    nes_apu.NESAPU.write = spy
    try:
        sequencer.Sequencer(ticks_per_second=meta.ticks_per_second).render(
            events)
    finally:
        nes_apu.NESAPU.write = original
    return seen


def test_the_low_pulse_octave_is_audible_at_all():
    """The sweep unit mutes a pulse channel below about 110 Hz.

    Its target-period overflow check runs whether or not the sweep is
    enabled, and with a shift count of zero the target is twice the
    period — so every period above 1023 silences the channel. Measured
    with $4001 left at zero: A-1, C-2, E-2 and G-2 all render 0.0000 RMS,
    exact silence, while A-2 at period 1016 plays fine. chipgen writes
    the negate bit at init, which makes the target negative instead, and
    the octave comes back. Real NES drivers do the same thing.

    A whole octave of the pulse range vanishing with no error is the
    reason this is a test and not a comment.
    """
    for note in ("A-1", "C-2", "E-2", "G-2", "A-2", "C-3"):
        result = _render(_held("nes0", f"{note}:110"))
        assert _rms(_mono(result)) > 1e-3, (
            f"{note} on a NES pulse channel rendered silence — the sweep "
            f"unit's overflow mute is back")


def test_the_pulse_and_triangle_floors_are_where_the_timer_runs_out():
    # 11 bits, so the pulses bottom out at 54.6 Hz (A-1) and the triangle
    # an octave below that at 27.3 Hz (A-0) because it divides by 32.
    # Below the floor the period clamps and the note sounds SHARP, not
    # low — C-1 on a pulse measures +888 cents. That is the hardware, and
    # what matters is that it is a known edge rather than a surprise.
    import analysis
    import nes_apu

    apu = nes_apu.NESAPU()
    assert apu.pulse_period(55.0) < 0x7FF, "A-1 should fit the pulse timer"
    assert apu.pulse_period(32.7) >= 0x7FF, "C-1 should clamp the pulse timer"
    assert apu.triangle_period(27.5) < 0x7FF, \
        "A-0 should fit the triangle timer"
    assert apu.triangle_period(16.35) >= 0x7FF, \
        "C-0 should clamp the triangle timer"

    # And the clamp really does read as an octave-up error.
    lowest = apu.pulse_frequency(0x7FF)
    assert abs(analysis.cents(lowest, 32.7) - 888) < 20, (
        f"a clamped C-1 should land about 888 cents sharp, got "
        f"{analysis.cents(lowest, 32.7):.0f}")


def test_retuning_writes_the_period_high_byte_only_when_it_changes():
    """Otherwise the effect clock becomes an audible tone.

    A write to $4003 resets the pulse's duty phase and restarts its
    envelope. Re-asserting an unchanged high byte at the 60 Hz effect
    rate therefore puts a 60 Hz buzz into the note: measured at -30.5 dB
    with harmonics at -29.7 and -28.2, against -148.8 dB when only the
    low byte is written. 118 dB of difference from one redundant write.
    """
    plain = [v for a, v in _register_trace(_held("nes0", "A-4:110"),
                                           {0x4003})]
    sliding = [v for a, v in _register_trace(_held("nes0", "A-4:110/1F0"),
                                             {0x4003})]
    assert len(plain) == 1, \
        f"a plain note should latch the high byte once, got {len(plain)}"
    # The slide stays inside one 256-block, so it needs no further latch.
    assert len(sliding) <= 2, (
        f"a pitch slide inside one period block rewrote the high byte "
        f"{len(sliding)} times; each one resets the duty phase")


def test_the_dmc_is_enabled_or_a_sample_is_exact_silence():
    """$4015 bit 4 gates the DMC's output to zero.

    Enabling 0x0F — the two pulses, triangle and noise — leaves it clear,
    and then a sample written to $4011 logs every byte and renders
    nothing at all. There is no clue in the register log that the channel
    was switched off rather than the sample empty, which is how this
    survived being written.
    """
    writes = _register_trace(_held("nes4", "kick"), {0x4011, 0x4015})
    enables = [v for a, v in writes if a == 0x4015]
    assert enables, "nothing ever wrote $4015"
    assert all(v & 0x10 for v in enables), (
        f"$4015 was written {enables} — bit 4 clear means the DMC is "
        f"muted and a sample renders silence")

    result = _render(_held("nes4", "kick"))
    assert _rms(_mono(result)) > 1e-4, \
        "a DMC sample rendered silence"


def test_a_dmc_sample_is_fed_at_its_own_rate():
    # 55,930 APU samples per second over a 16,000 Hz sample is 3.496.
    # Truncating that to an integer step feeds the bytes at 18,643 Hz and
    # the sample plays 16.5% sharp. One write per byte, and the byte
    # count is the sample's length.
    import samples

    writes = [v for a, v in _register_trace(_held("nes4", "kick"), {0x4011})]
    expected = len(samples.KIT["kick"].data)
    assert abs(len(writes) - expected) <= 2, (
        f"a {expected}-byte sample produced {len(writes)} writes to $4011")


def test_every_nes_voice_survives_a_vgm_round_trip():
    """Export and replay have to agree, or the .vgm is silent-broken.

    This caught three separate things: the writer had no NES logger at
    all (a NES score exported a 417-byte file claiming Genesis clocks),
    the player skipped command 0xB4, and the DMC bytes were all stamped
    at one instant because render_nes never advanced the VGM clock.
    """
    import os
    import tempfile

    import analysis
    import sequencer
    import tracker
    import vgm_player

    cases = {
        "pulses": ("nes0 nes1", "A-4:110  E-5:90"),
        "triangle": ("nes2", "A-2"),
        "noise": ("nes3", "6:90"),
        "dmc": ("nes4", "kick"),
    }
    directory = tempfile.mkdtemp()
    try:
        for name, (columns, row) in cases.items():
            path = os.path.join(directory, f"{name}.vgm")
            events, meta = tracker.loads(_held(columns, row))
            direct = sequencer.Sequencer(
                ticks_per_second=meta.ticks_per_second).render(
                    events, vgm_path=path)
            replayed = vgm_player.render(path)

            a = [float(v) for v in analysis.to_mono(direct)]
            b = [float(v) for v in analysis.to_mono(replayed)]
            assert _rms(a) > 1e-4, f"{name} rendered silence"
            assert abs(len(a) - len(b)) < 100, (
                f"{name}: direct render is {len(a)} frames and the replay "
                f"{len(b)}")
            # Correlation over the overlap. The Genesis path scores 0.997
            # on the same measure, so anything much below that is a real
            # disagreement rather than sub-sample jitter.
            n = min(len(a), len(b))
            mean_a = sum(a[:n]) / n
            mean_b = sum(b[:n]) / n
            cov = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
            va = math.sqrt(sum((a[i] - mean_a) ** 2 for i in range(n)))
            vb = math.sqrt(sum((b[i] - mean_b) ** 2 for i in range(n)))
            correlation = cov / (va * vb) if va and vb else 0.0
            assert correlation > 0.95, (
                f"{name}: the VGM replays at {correlation:.4f} correlation "
                f"against its own render")
    finally:
        import shutil
        shutil.rmtree(directory, ignore_errors=True)


def test_the_duty_cycle_changes_the_harmonics_and_75_percent_equals_25():
    """Duty 3 and duty 1 are one waveform and its inverse.

    They measure identically — same harmonic series, differing only in
    phase — so a score that spends its second pulse channel on the
    "other" one gets two voices with one timbre. Duty 2 is the square:
    its even harmonics cancel, measured at -40.9 dB against -5.6 for the
    others.
    """
    import analysis

    def harmonic_db(result, f0, which):
        samples = _mono(result)
        rate = result.sample_rate
        n = len(samples)

        def amplitude(frequency):
            re = im = 0.0
            for i, value in enumerate(samples):
                window = 0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1))
                phase = 2 * math.pi * frequency * i / rate
                re += value * window * math.cos(phase)
                im += value * window * math.sin(phase)
            return math.hypot(re, im)

        base = amplitude(f0)
        return 20 * math.log10(max(amplitude(f0 * which), 1e-12)
                               / max(base, 1e-12))

    shapes = {}
    for duty in (1, 2, 3):
        result = _render("bpm 60\nlpb 4\ncols nes0\n"
                         f"nes duty nes0 {duty}\nA-4:110\n...\n...\n...\n"
                         "end\n")
        shapes[duty] = harmonic_db(result, 440.0, 2)

    assert shapes[2] < shapes[1] - 20, (
        f"the 50% duty should cancel its second harmonic; measured "
        f"{shapes[2]:.1f} dB against {shapes[1]:.1f} for 25%")
    assert abs(shapes[3] - shapes[1]) < 1.0, (
        f"75% and 25% are the same waveform inverted and should measure "
        f"the same; got {shapes[3]:.1f} and {shapes[1]:.1f} dB")


def test_sweep_up_raises_the_pitch():
    """`up` is the negate bit, because period and frequency run opposite.

    Getting this backwards is silent — the note sweeps the wrong way and
    nothing errors. Measured with period 1, shift 3 over half a second:
    negate on takes A-4 up 2,716 cents, negate off down 2,815.
    """
    import analysis

    def drift(directive):
        result = _render("bpm 60\nlpb 4\ncols nes0\n" + directive
                         + "A-4:110\n...\n...\n...\nend\n")
        samples = _mono(result)
        rate = result.sample_rate
        window = int(0.04 * rate)
        track = [analysis.fundamental(samples[i:i + window], rate)
                 for i in range(0, len(samples) - window, window)]
        track = [f for f in track if f]
        assert len(track) > 2, "no pitch to track"
        return analysis.cents(track[-1], track[0])

    assert drift("nes sweep nes0 1 3 up\n") > 500, \
        "an upward sweep did not raise the pitch"
    assert drift("nes sweep nes0 1 3 down\n") < -500, \
        "a downward sweep did not lower the pitch"
    assert abs(drift("")) < 30, "a note with no sweep drifted"


def test_the_triangle_refuses_volume_effects_rather_than_ignoring_them():
    # It has no volume register: its output measures bit-identical at
    # velocity 8 and velocity 127. Accepting a tremolo and writing
    # nothing is the failure fx.py exists to prevent.
    import fx

    for code in ("756", "A0F"):
        try:
            fx.to_events("triangle", code)
        except fx.FXError as error:
            assert "volume register" in str(error), \
                f"the triangle refused {code} without explaining why"
        else:
            raise AssertionError(
                f"the triangle accepted {code}, which it cannot perform")
    # Pitch effects are fine on it.
    assert fx.to_events("triangle", "456")


def test_the_nes_columns_carry_the_effect_column():
    import tracker

    wanted = {"nes0": "Vibrato", "nes1": "Portamento", "nes2": "Vibrato",
              "nes3": "Tremolo", "nes4": "VolumeSlide"}
    codes = {"nes0": "456", "nes1": "1F0", "nes2": "456", "nes3": "756",
             "nes4": "A0F"}
    cells = {"nes0": "A-4:110", "nes1": "E-5:90", "nes2": "A-2",
             "nes3": "6:90", "nes4": "kick"}
    for column, effect in wanted.items():
        score = _held(column, f"{cells[column]}/{codes[column]}")
        events, _ = tracker.loads(score)
        kinds = [type(e).__name__ for e in events]
        assert effect in kinds, (
            f"{column} dropped its {codes[column]} effect code — parsed "
            f"and then discarded, which is silence with no error")
