"""effects.py: the things that happen BETWEEN notes.

The engine is deliberately chip-agnostic, so most of this tests it
directly in physical units — cents and Hz and seconds — without an
emulator in the way. The last few close the loop through the sequencer,
because an effect that computes correctly and never reaches a register is
not an effect.
"""

import math

import effects
import events as E
import tracker


def _run(engine, seconds, rate=None):
    """Advance the engine in its own tick steps."""
    step = 1.0 / (rate or engine.rate)
    elapsed = 0.0
    while elapsed < seconds - 1e-9:
        engine.advance(min(step, seconds - elapsed))
        elapsed += step


# -- portamento --------------------------------------------------------------
def test_a_slide_reaches_its_target_and_stops_there():
    engine = effects.EffectEngine()
    engine.portamento("fm0", 600.0, to_cents=200.0)
    _run(engine, 0.2)
    assert 100 < engine.state("fm0")[0] < 130, engine.state("fm0")
    _run(engine, 0.5)                       # long past arrival
    assert abs(engine.state("fm0")[0] - 200.0) < 1e-6
    # And it must not sail past: the rate is cleared on arrival.
    _run(engine, 2.0)
    assert abs(engine.state("fm0")[0] - 200.0) < 1e-6


def test_a_slide_downward_arrives_too():
    engine = effects.EffectEngine()
    engine.portamento("fm1", -1200.0, to_cents=-300.0)
    _run(engine, 1.0)
    assert abs(engine.state("fm1")[0] + 300.0) < 1e-6


def test_a_slide_back_to_zero_is_how_a_bend_returns():
    engine = effects.EffectEngine()
    engine.portamento("fm0", 1200.0, to_cents=400.0)
    _run(engine, 1.0)
    engine.portamento("fm0", -1200.0, to_cents=0.0)
    _run(engine, 1.0)
    assert abs(engine.state("fm0")[0]) < 1e-6


def test_a_slide_can_be_stopped_where_it_is():
    engine = effects.EffectEngine()
    engine.portamento("fm0", 600.0, to_cents=1200.0)
    _run(engine, 0.5)
    here = engine.state("fm0")[0]
    assert 250 < here < 350, here
    engine.portamento("fm0", 0.0)
    _run(engine, 1.0)
    assert abs(engine.state("fm0")[0] - here) < 1e-6


# -- vibrato -----------------------------------------------------------------
def test_vibrato_swings_around_the_note_not_away_from_it():
    engine = effects.EffectEngine(rate=240)
    engine.vibrato("fm0", 60.0, 5.0)
    samples = []
    for _ in range(240):                    # one second
        engine.advance(1 / 240.0)
        samples.append(engine.state("fm0")[0])
    assert abs(sum(samples) / len(samples)) < 3.0, "vibrato is off-centre"
    assert 55 < max(samples) <= 60.5, max(samples)
    assert -60.5 <= min(samples) < -55, min(samples)


def test_vibrato_runs_at_the_speed_it_was_given():
    engine = effects.EffectEngine(rate=480)
    engine.vibrato("fm0", 50.0, 4.0)
    previous, crossings = 0.0, 0
    for _ in range(480):                    # one second
        engine.advance(1 / 480.0)
        value = engine.state("fm0")[0]
        if previous <= 0 < value:
            crossings += 1
        previous = value
    assert crossings == 4, f"{crossings} cycles in a second, wanted 4"


def test_a_delay_holds_the_vibrato_off():
    engine = effects.EffectEngine(rate=240)
    engine.vibrato("fm0", 80.0, 6.0, delay=0.25)
    _run(engine, 0.2)
    assert engine.state("fm0")[0] == 0.0, "vibrato started inside its delay"
    _run(engine, 0.3)
    assert abs(engine.state("fm0")[0]) > 1.0, "vibrato never started"


def test_a_note_on_restarts_the_swing_but_not_the_slide():
    # Carrying vibrato phase across a note-on starts the next note
    # mid-swing, which reads as the note being out of tune. A portamento
    # that reset on every note would be the opposite of what it is for.
    engine = effects.EffectEngine(rate=240)
    engine.vibrato("fm0", 80.0, 6.0)
    engine.portamento("fm0", 300.0, to_cents=600.0)
    _run(engine, 0.3)
    slid = engine.voices["fm0"].portamento_cents
    assert slid > 50

    engine.note_on("fm0")
    assert engine.voices["fm0"].vibrato_phase == 0.0
    assert abs(engine.voices["fm0"].portamento_cents - slid) < 1e-9


# -- volume ------------------------------------------------------------------
def test_a_fade_ramps_and_stops_at_the_floor():
    engine = effects.EffectEngine()
    engine.volume_slide("fm0", -127.0)      # a full fade in one second
    _run(engine, 0.5)
    middle = engine.state("fm0")[1]
    assert 0.4 < middle < 0.6, middle
    _run(engine, 2.0)
    assert engine.state("fm0")[1] == 0.0
    # And it does not go negative and come back.
    _run(engine, 1.0)
    assert engine.state("fm0")[1] == 0.0


def test_a_fade_respects_a_floor_above_silence():
    engine = effects.EffectEngine()
    engine.volume_slide("fm0", -200.0, floor=64.0)
    _run(engine, 3.0)
    assert abs(engine.state("fm0")[1] - 64.0 / 127.0) < 0.01


def test_tremolo_dips_the_level_without_raising_it_above_the_note():
    engine = effects.EffectEngine(rate=240)
    engine.tremolo("fm0", 40.0, 5.0)
    scales = []
    for _ in range(240):
        engine.advance(1 / 240.0)
        scales.append(engine.state("fm0")[1])
    assert max(scales) <= 1.0 + 1e-9, max(scales)
    assert min(scales) < 0.75, min(scales)


def test_effects_on_one_voice_leave_the_others_alone():
    engine = effects.EffectEngine()
    engine.vibrato("fm0", 100.0, 6.0)
    _run(engine, 0.5)
    for other in ("fm1", "psg0", "opl3"):
        assert engine.state(other) == (0.0, 1.0), other


def test_an_unknown_voice_lists_the_real_ones():
    engine = effects.EffectEngine()
    try:
        engine.vibrato("fm9", 10, 5)
    except KeyError as exc:
        assert "fm0" in str(exc) and "opl8" in str(exc), str(exc)
    else:
        raise AssertionError("fm9 should not have been accepted")


def test_nothing_active_means_nothing_to_do():
    # The sequencer only pays for the tick loop when something is running.
    engine = effects.EffectEngine()
    assert not engine.any_active()
    engine.vibrato("fm0", 50, 6)
    assert engine.any_active()
    engine.vibrato("fm0", 0, 0)
    engine.clear("fm0")
    assert not engine.any_active()


# -- notation ----------------------------------------------------------------
def test_the_directives_parse_and_round_trip():
    text = ("ticks 240\nbpm 150\nlpb 4\ninst fm0 square_lead\ncols fm0\n"
            "A-4\nvib fm0 60 6 0.05\n...\nporta fm0 -400 -200\n"
            "...\nfade fm0 -50\ntrem fm0 20 4\n===\n")
    events, meta = tracker.loads(text)
    kinds = [type(e).__name__ for e in events]
    for wanted in ("Vibrato", "Portamento", "VolumeSlide", "Tremolo"):
        assert wanted in kinds, kinds

    vibrato = next(e for e in events if isinstance(e, E.Vibrato))
    assert (vibrato.target, vibrato.depth_cents, vibrato.speed_hz,
            vibrato.delay) == ("fm0", 60.0, 6.0, 0.05)

    again, _ = tracker.loads(tracker.dumps(events, meta))
    assert [type(e).__name__ for e in again] == kinds


def test_off_stops_each_effect():
    text = ("cols fm0\ninst fm0 bass\nA-2\nvib fm0 off\nporta fm0 off\n"
            "fade fm0 off\ntrem fm0 off\n===\n")
    events, _meta = tracker.loads(text)
    vibrato = next(e for e in events if isinstance(e, E.Vibrato))
    assert vibrato.depth_cents == 0.0 and vibrato.speed_hz == 0.0
    slide = next(e for e in events if isinstance(e, E.Portamento))
    assert slide.cents_per_second == 0.0


def test_a_directive_with_nonsense_says_which_line():
    try:
        tracker.loads("cols fm0\nvib fm0 wobbly\n")
    except tracker.TrackerError as exc:
        assert "line 2" in str(exc), str(exc)
    else:
        raise AssertionError("`vib fm0 wobbly` should not have parsed")


# -- through the engine ------------------------------------------------------
def _crossing_spread(audio_buffer, rate=44100, window=0.05):
    """Zero crossings per window — pitch, without needing an FFT."""
    counts = []
    size = int(rate * window)
    for start in range(0, len(audio_buffer) - size, size):
        segment = [audio_buffer[i][0] + audio_buffer[i][1]
                   for i in range(start, start + size)]
        counts.append(sum(1 for i in range(1, len(segment))
                          if segment[i - 1] <= 0 < segment[i]))
    mean = sum(counts) / len(counts)
    spread = math.sqrt(sum((c - mean) ** 2 for c in counts) / len(counts))
    return mean, spread


def _render(extra):
    import chipgen

    events = [E.FMInstrumentSelect(channel=0, instrument="square_lead"),
              E.FMPan(channel=0, left=True, right=True),
              E.FMNoteOn(channel=0, note="A", octave=4)]
    events += extra
    events += [E.Wait(ticks=192 * 2), E.End()]
    return chipgen.compose(events).audio


def test_vibrato_actually_moves_the_pitch_in_the_render():
    plain_mean, plain_spread = _crossing_spread(_render([]))
    _mean, wobbled = _crossing_spread(
        _render([E.Vibrato(target="fm0", depth_cents=100.0, speed_hz=5.0)]))
    assert wobbled > plain_spread * 1.5, (plain_spread, wobbled)


def _fundamental(audio_buffer, at_seconds, rate=44100, size=4096):
    """Pitch by autocorrelation.

    Counting zero crossings is cheaper but wrong for these patches: a rich
    FM waveform crosses zero several times per period, so its crossing
    rate is not proportional to the fundamental and a real octave of slide
    reads as a 1.26x change.
    """
    start = int(at_seconds * rate)
    values = [audio_buffer[i][0] + audio_buffer[i][1]
              for i in range(start, min(start + size, len(audio_buffer)))]
    best, best_lag = 0.0, 0
    for lag in range(20, 400):
        top = sum(values[i] * values[i + lag]
                  for i in range(0, len(values) - lag, 3))
        left = sum(v * v for v in values[:len(values) - lag:3])
        right = sum(v * v for v in values[lag::3])
        scale = math.sqrt(left * right)
        if scale and top / scale > best:
            best, best_lag = top / scale, lag
    return rate / best_lag if best_lag else 0.0


def test_a_slide_reaches_exactly_where_it_was_aimed_in_the_render():
    # 600 cents per second toward +1200 arrives after two seconds and
    # holds. Checked in cents against the note it started from, because
    # "the pitch went up" is not the claim — "it went up by an octave and
    # then stopped" is.
    import chipgen

    events = [E.FMInstrumentSelect(channel=0, instrument="square_lead"),
              E.FMPan(channel=0, left=True, right=True),
              E.FMNoteOn(channel=0, note="A", octave=4),
              E.Portamento(target="fm0", cents_per_second=600.0,
                           to_cents=1200.0),
              E.Wait(ticks=192 * 3), E.End()]
    audio_buffer = chipgen.compose(events).audio

    def cents_at(seconds):
        frequency = _fundamental(audio_buffer, seconds)
        return 1200 * math.log2(frequency / 440.0) if frequency else None

    assert abs(cents_at(1.0) - 600) < 60, cents_at(1.0)
    assert abs(cents_at(2.0) - 1200) < 60, cents_at(2.0)
    # Arrived, and stayed: a slide that kept going would be well past.
    assert abs(cents_at(2.5) - 1200) < 60, cents_at(2.5)


def test_a_fade_actually_lowers_the_level_in_the_render():
    import audio

    audio_buffer = _render([E.VolumeSlide(target="fm0", per_second=-80.0)])
    rate = 44100
    early = audio.rms(audio_buffer[:rate // 2])
    late = audio.rms(audio_buffer[rate:rate + rate // 2])
    assert late < early * 0.6, (early, late)


def test_a_score_without_effects_renders_identically_to_before():
    # The tick loop must not exist when nothing is running: subdividing
    # every wait would change where register writes land and quietly
    # rewrite every .vgm this project has ever produced.
    import audio
    import chipgen

    text = ("ticks 240\nbpm 150\nlpb 4\ninst fm0 bass\ncols fm0\n"
            "A-2\n...\nC-3\n...\nE-3\n===\n")
    first = chipgen.compose(text).audio
    second = chipgen.compose(text).audio
    assert len(first) == len(second)
    assert abs(audio.rms(first) - audio.rms(second)) < 1e-12
