"""Importing real samples, and playing them at a pitch."""

import math
import os

SCRATCH = os.environ.get("CLAUDE_SCRATCH",
                         "/tmp/claude-0/-home-user/"
                         "8c6bfdd3-4d2c-5d24-810b-de8b23ba9c0d/scratchpad")


def _write_tone(path, hertz=220.0, rate=22050, seconds=0.3):
    """A decaying stereo tone, written the way wavio expects."""
    import audio
    import wavio

    n = int(rate * seconds)
    flat = []
    for i in range(n):
        value = 0.7 * math.exp(-4.0 * i / n) * math.sin(2 * math.pi * hertz * i / rate)
        flat += [value, value]
    wavio.write(path, audio.from_floats(flat, channels=2), rate)
    return path


def _fixture(name="fx_tone.wav"):
    os.makedirs(SCRATCH, exist_ok=True)
    return _write_tone(os.path.join(SCRATCH, name))


def test_the_dac_has_a_rate_ceiling_and_we_know_where_it_is():
    # Not from a datasheet: feed a tone through register 0x2A faster and
    # faster and read the pitch back. Above the ceiling the chip drops
    # bytes, so the DURATION stays right and only the pitch falls — which
    # is exactly why it reads as broken repitching instead of a limit.
    import analysis
    import audio
    import opn2
    import samples

    def pitch_at(feed_rate, tone=220.0, seconds=0.2):
        count = int(feed_rate * seconds)
        data = [int(round(128 + 110 * math.sin(2 * math.pi * tone * i / feed_rate)))
                for i in range(count)]
        chip = opn2.YM2612(chip_type="ym3438")
        chip.set_dac_enable(True)
        step = chip.native_rate / feed_rate
        frames = []
        accumulated = 0.0
        for byte in data:
            chip.write_dac(byte)
            accumulated += step
            take = int(accumulated)
            accumulated -= take
            if take:
                frames.extend(list(chip.render(take)))
        rate = chip.native_rate
        chip.close()
        buffer = audio.from_floats([v for f in frames for v in f], channels=2)
        return analysis.fundamental(list(analysis.to_mono(buffer)), rate)

    under = pitch_at(11025)
    assert abs(analysis.cents(under, 220.0)) < 15, \
        f"a feed under the ceiling should be in tune, got {under:.1f} Hz"
    over = pitch_at(44100)
    assert over < 220.0 * 0.6, \
        f"a feed far over the ceiling should read flat, got {over:.1f} Hz"
    # And the ceiling we act on has to be in the same place we measured.
    effective = 44100 * over / 220.0
    assert abs(effective - samples.DAC_RATE_CEILING) < 600, \
        f"measured ceiling {effective:.0f} vs declared " \
        f"{samples.DAC_RATE_CEILING}"


def test_a_wav_is_mono_ised_on_import():
    # wavio hands back a numpy (frames, channels) array or a flat Buffer.
    # The import used to test isinstance(frame, (tuple, list)) — false for
    # both — so it never averaged and a stereo file came out twice as long
    # with the channels interleaved as consecutive samples.
    import samples

    path = _fixture("fx_mono.wav")
    sample = samples.load_wav("fx_mono", path)
    assert 0 < len(sample) < int(22050 * 0.3) + 16, \
        f"{len(sample)} bytes for a 0.3 s file — channels were not merged"


def test_an_imported_sample_is_stored_at_a_rate_the_dac_can_play():
    import samples

    sample = samples.load_wav("fx_rate", _fixture("fx_rate.wav"))
    assert sample.rate <= samples.DAC_RATE_CEILING, \
        f"stored at {sample.rate} Hz, above the ceiling"


def test_a_sample_plays_at_the_pitch_it_is_asked_for():
    # The whole point. Across four octaves, including above the base note
    # where the data has to be thinned rather than the feed raised.
    import analysis
    import chipgen

    path = _fixture("fx_pitch.wav")
    score = (f"bpm 60\nlpb 4\nsample tone {path} A-3\ncols dac\n"
             f"CELL\n...\n...\n...\nend\n")
    wanted = {"tone": 220.0, "tone@A-1": 55.0, "tone@A-2": 110.0,
              "tone@C-4": 261.63, "tone@A-4": 440.0, "tone@A-5": 880.0}
    for cell, hertz in wanted.items():
        result = chipgen.compose(score.replace("CELL", cell))
        mono = list(analysis.to_mono(result.audio))
        found = analysis.fundamental(mono[:len(mono) // 3], result.sample_rate)
        assert found, f"{cell} produced no pitch"
        assert abs(analysis.cents(found, hertz)) < 25, \
            f"{cell}: wanted {hertz:.1f} Hz, got {found:.1f}"


def test_thinning_takes_a_factor_not_a_rate():
    # Passing a rate looked natural and was wrong: thinning to the ceiling
    # from a sample already AT the ceiling is a factor of one, so pitching
    # up did nothing and every note came out at the base pitch.
    import samples

    sample = samples.load_wav("fx_thin", _fixture("fx_thin.wav"))
    half = samples.thin(sample, 2.0, sample.rate, "fx_thin_half")
    assert abs(len(half) - len(sample) // 2) <= 1, \
        f"factor 2 gave {len(half)} bytes from {len(sample)}"


def test_pitching_down_makes_the_sample_longer():
    # Repitching on a chip is playing faster or slower; there is no
    # formant correction, and the drum getting fatter is the reason the
    # trick is used.
    import samples

    kick = samples.KIT["kick"]
    down = samples.rate_for_note(kick, "C", 3)
    up = samples.rate_for_note(kick, "C", 5)
    assert abs(down - kick.rate / 2) <= 1
    assert abs(up - kick.rate * 2) <= 1


def test_an_unknown_sample_or_pitch_is_refused_by_name():
    import tracker

    for score, wanted in (
            ("cols dac\nnosuch@C-3\nend\n", "unknown sample"),
            ("cols dac\nkick@zz\nend\n", "not a note"),
    ):
        try:
            tracker.loads(score)
        except tracker.TrackerError as error:
            assert wanted in str(error), f"{score!r}: {error}"
        else:
            raise AssertionError(f"{score!r} was accepted")


def test_a_missing_wav_says_where_paths_are_resolved():
    import tracker

    try:
        tracker.loads("cols dac\nsample t /definitely/not/here.wav\nkick\nend\n")
    except tracker.TrackerError as error:
        assert "no such file" in str(error)
        assert "relative to" in str(error)
    else:
        raise AssertionError("a missing WAV was accepted")
