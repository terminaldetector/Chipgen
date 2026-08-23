"""Measurement primitives, and the two bugs that measuring them exposed."""

import math


def _tone(frequency, rate=44100.0, seconds=0.5, harmonics=1):
    n = int(rate * seconds)
    return [sum(math.sin(2 * math.pi * frequency * h * i / rate) / h
                for h in range(1, harmonics + 1)) for i in range(n)]


def test_pitch_is_accurate_across_the_chips_musical_range():
    # The whole point of measuring pitch is to catch a note that landed
    # somewhere other than where the score asked. That is only worth doing
    # if the measurement is tighter than the error it looks for, so this
    # pins it: within a cent over C1..C7, for timbres from a bare sine to
    # sixteen harmonics.
    import analysis

    worst = 0.0
    for harmonics in (1, 3, 10, 16):
        for frequency in (32.70, 65.41, 130.81, 261.63, 440.0, 523.25,
                          1046.5, 2093.0):
            measured = analysis.fundamental(
                _tone(frequency, harmonics=harmonics), 44100.0)
            assert measured is not None, f"no pitch found for {frequency} Hz"
            worst = max(worst, abs(analysis.cents(measured, frequency)))
    assert worst < 1.5, f"pitch measurement is off by {worst:.2f} cents"


def test_pitch_rejects_what_is_not_pitched():
    import random
    import analysis

    random.seed(7)
    noise = [random.uniform(-1.0, 1.0) for _ in range(44100)]
    assert analysis.fundamental(noise, 44100.0) is None
    assert analysis.fundamental([0.0] * 44100, 44100.0) is None


def test_centroid_is_not_dominated_by_the_noise_floor():
    # An FFT has thousands of bins and nearly all of them hold only floor.
    # Weighted by amplitude and summed without a gate, that floor outvotes
    # the harmonics by sheer count: a pure sine measured as if its energy
    # sat many octaves above its own fundamental.
    import analysis

    for frequency in (110.0, 440.0):
        centroid = analysis.spectral_centroid(_tone(frequency), 44100.0)
        ratio = centroid / frequency
        assert ratio < 1.2, \
            f"a {frequency} Hz sine centres at {centroid:.0f} Hz ({ratio:.1f}x)"


def test_a_quiet_note_does_not_end_louder_than_it_played():
    # Key-off starts the release; the note is still sounding. Restoring the
    # patch's full Total Level at that moment made every soft note go out
    # with a click louder than the note itself — measured at velocity 16,
    # a -37.0 dBFS note with a -22.8 dBFS release burst.
    import audio
    import instruments
    import opn2

    for velocity in (16, 48, 96):
        chip = opn2.YM2612(chip_type="ym3438")
        chip.set_instrument(0, instruments.get("deep_bass"))
        chip.set_pan(0, True, True)
        chip.note_on(0, "C", 2, velocity=velocity)
        held = chip.render(int(chip.native_rate * 0.4))
        chip.note_off(0)
        released = chip.render(int(chip.native_rate * 0.4))
        chip.close()
        assert audio.peak(released) <= audio.peak(held), (
            f"velocity {velocity}: release peaks at "
            f"{audio.peak(released):.5f}, above the note's own "
            f"{audio.peak(held):.5f}")


def test_full_velocity_after_a_quiet_note_is_still_full():
    # The other half of the same fix. Carrier levels are restored at the
    # next note_on rather than at note_off, so a velocity-127 note that
    # follows a quiet one must not inherit the quiet one's attenuation.
    import audio
    import instruments
    import opn2

    def play(chip, velocity):
        chip.note_on(0, "C", 3, velocity=velocity)
        buffer = chip.render(int(chip.native_rate * 0.3))
        chip.note_off(0)
        chip.render(int(chip.native_rate * 0.2))
        return audio.peak(buffer)

    chip = opn2.YM2612(chip_type="ym3438")
    chip.set_instrument(0, instruments.get("deep_bass"))
    chip.set_pan(0, True, True)
    alone = play(chip, 127)
    play(chip, 16)
    after_quiet = play(chip, 127)
    chip.close()
    assert abs(20 * math.log10(after_quiet / alone)) < 0.5, \
        f"full velocity gave {alone:.5f} alone but {after_quiet:.5f} after a quiet note"


def test_envelope_times_separate_a_pad_from_a_stab():
    # If attack and release cannot tell strings from metal_stab there is no
    # point measuring them, because that is the easiest pair in the bank.
    import audition

    pad = audition.audition("strings", notes=(("C", 4),), velocities=(127,))
    stab = audition.audition("metal_stab", notes=(("C", 4),), velocities=(127,))
    assert pad["attack"] > stab["attack"], \
        f"strings attack {pad['attack']}, metal_stab {stab['attack']}"
    assert pad["release"] > stab["release"] * 3, \
        f"strings release {pad['release']}, metal_stab {stab['release']}"
