"""What the module sounds like, measured: every claim in the docs about
pitch, envelopes and effects, checked against libopenmpt's own playback.

The numbers in the assertions are not guesses. Each was measured first
(tools/probe notes in docs/COVERAGE.md), and the tests keep them from
drifting if the writer, the synth or libopenmpt changes.
"""

import audio_probe as P
import support
from schism import it_write, levels, model as M, notation, openmpt

TICK, ROW = 0.02, 0.12          # tempo 125, speed 6


def _need():
    P.need()


def _play(body, **kw):
    frames, module = P.render(P.score(body, **kw))
    return frames, P.mono(frames), module


# -- tuning -------------------------------------------------------------------
def test_every_pitched_recipe_plays_at_the_pitch_it_was_asked():
    _need()
    cases = [
        ("inst 1 wave=sine oct=1-6", ["A-1", "A-4"], "zc"),
        ("inst 1 wave=saw oct=1-6", ["A-1", "A-2", "A-3", "A-4", "A-5"], "zc"),
        ("inst 1 wave=square oct=1-6", ["C-2", "C-4", "C-5"], "zc"),
        ("inst 1 wave=triangle oct=1-6", ["C-3", "C-5"], "zc"),
        ("inst 1 wave=pulse duty=0.25 oct=1-6", ["E-3", "E-5"], "zc"),
        ("inst 1 wave=fm ratio=2 index=3 oct=1-6", ["A-2", "A-4"], "acf"),
        ("inst 1 wave=pluck root=C-5 len=2.5", ["A-3", "A-4", "A-5"], "acf"),
        ("inst 1 wave=pluck root=C-3 len=2.5", ["A-3", "A-5"], "acf"),
        ("inst 1 wave=pad voices=1 detune=0 secs=2 base=C-4",
         ["C-4", "A-3", "E-4"], "acf"),
    ]
    worst = 0.0
    for inst, notes, method in cases:
        for note in notes:
            _, x, _ = _play(f"{note} 01 v64 ...\n", inst=inst, tempo=60)
            part = P.window(x, 0.2, 1.4 if method == "zc" else 0.6)
            want = P.hz(note)
            got = (P.frequency(part) if method == "zc"
                   else P.frequency_acf(part, want * 0.9, want * 1.1))
            off = abs(P.cents(got, want))
            worst = max(worst, off)
            assert off < P.TUNING_CENTS, (inst, note, want, got, off)
    assert worst < P.TUNING_CENTS


# -- envelopes ----------------------------------------------------------------
def test_a_volume_envelope_runs_in_ticks_and_in_straight_lines():
    _need()
    inst = "inst 1 wave=sine oct=1-7 venv=0:64,100:0"        # 100 ticks = 2 s
    _, x, _ = _play("A-4 01 v64 ...\n", inst=inst)
    starts = (0.0, 0.5, 1.0, 1.5)
    levels_ = [P.rms(P.window(x, t, t + 0.2)) for t in starts]
    # the line falls from 1 to 0 over 2 s; a 0.2 s window sees its middle
    line = [1.0 - (t + 0.1) / 2.0 for t in starts]
    for got, want in zip(levels_, line):
        assert abs(got / levels_[0] - want / line[0]) < 0.04, (levels_, line)
    assert P.rms(P.window(x, 2.2, 2.6)) < 0.001


def test_a_pan_envelope_goes_from_left_to_right():
    _need()
    inst = "inst 1 wave=sine oct=1-7 penv=0:-32,50:32 fade=200"
    frames, _, _ = _play("A-4 01 v64 ...\n", inst=inst)
    first = (P.rms(P.window(P.left(frames), 0.0, 0.1)),
             P.rms(P.window(P.right(frames), 0.0, 0.1)))
    last = (P.rms(P.window(P.left(frames), 1.2, 1.5)),
            P.rms(P.window(P.right(frames), 1.2, 1.5)))
    assert first[0] > 10 * first[1] and last[1] > 10 * last[0]


def test_a_pitch_envelope_counts_half_semitones():
    _need()
    inst = "inst 1 wave=sine oct=1-7 ienv=0:0,50:32 fade=200"
    _, x, _ = _play("A-4 01 v64 ...\n", inst=inst)
    start = P.frequency(P.window(x, 0.0, 0.1))
    end = P.frequency(P.window(x, 1.3, 1.6))
    # 32 half-semitones = 16 semitones
    assert abs(P.cents(end, 440.0) - 1600) < 10, P.cents(end, 440.0)
    assert abs(P.cents(start, 440.0)) < 80


def test_a_filter_envelope_opens_the_filter_and_leaves_the_pitch_alone():
    _need()
    inst = ("inst 1 wave=saw oct=1-7 cutoff=127 res=0 fenv=0:6,100:64 "
            "fade=200")
    _, x, _ = _play("A-3 01 v64 ...\n", inst=inst, rows=48)

    def brightness(part):             # high-frequency share: |difference|/|signal|
        d = [b - a for a, b in zip(part, part[1:])]
        return P.rms(d) / (P.rms(part) + 1e-9)
    early = brightness(P.window(x, 0.1, 0.3))
    late = brightness(P.window(x, 2.0, 2.2))
    assert late > 2.5 * early, (early, late)
    # The test that stood here measured only brightness, and passed for a
    # year of the project's life while the envelope was being written as a
    # *pitch* envelope (a rising pitch is brighter too). Pitch is checked.
    for t in (0.2, 1.0, 2.1):
        f = P.frequency_acf(P.window(x, t, t + 0.2), 200.0, 240.0)
        assert abs(P.cents(f, P.hz("A-3"))) < 15, (t, f)


def test_a_constant_filter_envelope_scales_the_cutoff():
    """The envelope's value is the share of `cutoff` that is used: 64 is the
    cutoff as set, 32 is half of it. Measured as the high-frequency share of
    looped noise (|difference| / |signal|), which a filter changes and a
    pitch change does not."""
    _need()

    def brightness(inst):
        _, x, _ = _play("C-5 01 v64 ...\n", inst=inst, rows=32)
        part = P.window(x, 0.4, 1.4)
        d = [b - a for a, b in zip(part, part[1:])]
        return P.rms(d) / P.rms(part)

    half = brightness("inst 1 wave=noise cutoff=64 res=0 fenv=0:32,100:32")
    set_to_half = brightness("inst 1 wave=noise cutoff=32 res=0")
    full = brightness("inst 1 wave=noise cutoff=64 res=0 fenv=0:64,100:64")
    plain = brightness("inst 1 wave=noise cutoff=64 res=0")
    assert abs(half / set_to_half - 1) < 0.12, (half, set_to_half)
    assert abs(full / plain - 1) < 0.05, (full, plain)
    assert plain > 1.5 * half, (plain, half)


# -- the effect column --------------------------------------------------------
def test_arpeggio_steps_once_a_tick():
    _need()
    _, x, _ = _play("A-4 01 v64 J47\n... .. ... J00\n")
    got = [P.frequency(P.window(x, t * TICK + 0.003, (t + 1) * TICK - 0.002))
           for t in range(6)]
    for f, want in zip(got, (440.0, 554.37, 659.26) * 2):
        assert abs(P.cents(f, want)) < 12, (got,)


def test_vibrato_depth_is_measured_not_assumed():
    _need()
    _, x, _ = _play("A-4 01 v64 H48\n" + "... .. ... H00\n" * 8)
    fs = [P.frequency(P.window(x, t * TICK + 0.001, (t + 1) * TICK))
          for t in range(1, 40)]
    low, high = P.cents(min(fs), 440.0), P.cents(max(fs), 440.0)
    assert -60 < low < -40 and 40 < high < 60, (low, high)    # H48: about 50


def test_pitch_slides_are_a_semitone_a_tick_at_x10():
    _need()
    for fx, sign in (("F", 1), ("E", -1)):
        _, x, _ = _play(f"A-4 01 v64 {fx}10\n" + f"... .. ... {fx}00\n" * 2)
        ends = [P.cents(P.frequency(P.window(x, r * ROW + 0.09, r * ROW + 0.115)),
                        440.0) for r in range(3)]
        steps = [b - a for a, b in zip(ends, ends[1:])]
        assert all(sign * s > 450 for s in steps), (fx, ends)   # 5 slides a row
        assert all(abs(abs(s) - 500) < 40 for s in steps), (fx, steps)


def test_tone_portamento_arrives_and_stays():
    _need()
    _, x, _ = _play("A-4 01 v64 ...\n... .. ... ...\nC-5 01 v64 G10\n"
                    + "... .. ... G00\n" * 4)
    for r in range(3, 8):
        got = P.frequency(P.window(x, r * ROW + 0.09, r * ROW + 0.115))
        assert abs(P.cents(got, 523.25)) < 5, (r, got)


def test_volume_slide_note_cut_and_note_delay_are_timed_in_ticks():
    _need()
    _, x, _ = _play("A-4 01 v64 D04\n" + "... .. ... D04\n" * 8)
    rows = [P.rms(P.window(x, r * ROW, (r + 1) * ROW)) for r in range(5)]
    assert rows[0] > rows[1] > rows[2] > rows[3] and rows[4] < 0.002

    _, x, _ = _play("A-4 01 v64 SC3\n")
    ticks = [P.rms(P.window(x, t * TICK, (t + 1) * TICK)) for t in range(6)]
    assert min(ticks[:3]) > 0.1 and max(ticks[4:]) < 0.002

    _, x, _ = _play("A-4 01 v64 SD3\n")
    ticks = [P.rms(P.window(x, t * TICK, (t + 1) * TICK)) for t in range(6)]
    assert max(ticks[:3]) < 0.002 and min(ticks[3:]) > 0.1


def test_retrigger_restarts_a_one_shot_every_y_ticks():
    _need()
    inst = "inst 1 wave=kick len=0.4 oct=1-7"
    window = 0.005                                    # a quarter of a tick

    def restarts(effect):
        _, x, _ = _play(f"C-5 01 v64 {effect}\n", inst=inst)
        lv = [P.rms(P.window(x, i * window, (i + 1) * window))
              for i in range(40)]
        # a restart plays the sample's first moments again: the next four
        # windows are the first four windows over, to within 3%
        return [i for i in range(2, 36)
                if all(abs(lv[i + k] - lv[k]) < 0.03 * lv[k] for k in range(4))]
    # a row is six ticks: Q02 restarts at ticks 2 and 4, Q03 at tick 3 only,
    # and nothing carries into the next row (tick 6) without a new Qxy
    assert restarts("Q02") == [8, 16]
    assert restarts("Q03") == [12]
    assert restarts("Q01") == [4, 8, 12, 16, 20]


def test_pan_global_and_channel_volume_are_the_documented_scales():
    _need()
    for fx, left_wins in (("X00", True), ("XFF", False)):
        frames, _, _ = _play(f"A-4 01 v64 {fx}\n")
        a, b = P.rms(P.left(frames)[:5000]), P.rms(P.right(frames)[:5000])
        assert (a > 100 * b) if left_wins else (b > 100 * a), (fx, a, b)
    reference = P.rms(P.window(_play("A-4 01 v64 ...\n")[1], 0.01, 0.1))
    for cell in ("A-4 01 v32 ...", "A-4 01 v64 V40", "A-4 01 v64 M20"):
        level = P.rms(P.window(_play(cell + "\n")[1], 0.01, 0.1))
        assert abs(level / reference - 0.5) < 0.03, (cell, level / reference)


def test_tremolo_and_tremor_move_the_level():
    _need()
    _, x, _ = _play("A-4 01 v64 R48\n" + "... .. ... R00\n" * 8)
    levels_ = [P.rms(P.window(x, t * TICK, (t + 1) * TICK)) for t in range(1, 40)]
    assert max(levels_) / min(levels_) > 1.25
    _, x, _ = _play("A-4 01 v64 I13\n" + "... .. ... I00\n" * 2)
    ticks = [P.rms(P.window(x, t * TICK, (t + 1) * TICK)) for t in range(12)]
    quiet = [t for t in ticks if t < 0.02]
    assert len(quiet) >= 6 and max(ticks) > 0.1


def test_a_filter_macro_closes_the_filter_by_hand():
    _need()
    inst = "inst 1 wave=saw oct=1-7 cutoff=127 res=0 fade=200"
    _, open_, _ = _play("A-3 01 v64 ...\n", inst=inst)
    _, shut, _ = _play("A-3 01 v64 Z10\n", inst=inst)

    def brightness(part):
        d = [b - a for a, b in zip(part, part[1:])]
        return P.rms(d) / (P.rms(part) + 1e-9)
    assert brightness(P.window(shut, 0.2, 0.5)) < \
        0.5 * brightness(P.window(open_, 0.2, 0.5))


# -- instruments --------------------------------------------------------------
def test_nna_decides_what_a_new_note_does_to_the_old_one():
    _need()
    rows = "A-3 01 v64 ...\n" + "... .. ... ...\n" * 3 + "E-4 01 v64 ...\n"
    levels_ = {}
    for nna in ("cut", "cont"):
        inst = f"inst 1 wave=sine oct=1-7 nna={nna} fade=200"
        _, x, _ = _play(rows, inst=inst, rows=32)
        levels_[nna] = P.rms(P.window(x, 4 * ROW + 0.1, 4 * ROW + 0.5))
    assert 1.3 < levels_["cont"] / levels_["cut"] < 1.55, levels_


def test_a_note_fade_needs_a_fadeout_and_a_key_off_needs_one_too():
    _need()
    rows = "A-4 01 v64 ...\n" + "... .. ... ...\n" * 3 + "~~~ .. ... ...\n"
    _, faded, _ = _play(rows, inst="inst 1 wave=sine oct=1-7 fade=256")
    _, kept, _ = _play(rows, inst="inst 1 wave=sine oct=1-7 fade=0")
    assert P.rms(P.window(faded, 1.0, 1.4)) < 0.002
    assert P.rms(P.window(kept, 1.0, 1.4)) > 0.1
    rows = rows.replace("~~~", "===")
    _, off, _ = _play(rows, inst="inst 1 wave=sine oct=1-7 fade=0")
    assert P.rms(P.window(off, 1.0, 1.4)) > 0.1       # the warning's claim


def _vibrato_trace(setting, ticks=range(0, 150)):
    inst = f"inst 1 wave=sine oct=1-7 fade=200 vib={setting}"
    _, x, _ = _play("A-4 01 v64 ...\n", inst=inst, rows=32)
    return [P.cents(P.frequency(P.window(x, t * TICK + 0.001,
                                         (t + 1) * TICK)), 440.0)
            for t in ticks]


def test_sample_auto_vibrato_fades_in_at_its_rate_and_reaches_its_depth():
    _need()
    # depth 12 is +-18.75 cents (1.5625 a unit; 64 would be a semitone); the
    # depth grows by `rate` 256ths a tick, so rate 64 takes 48 ticks to get there
    fast = _vibrato_trace("30/12/64")
    assert max(abs(c) for c in fast[:6]) < 6
    assert 15 < max(fast[60:]) < 22 and -22 < min(fast[60:]) < -15, \
        (max(fast[60:]), min(fast[60:]))
    slow = _vibrato_trace("30/12/8")                  # 384 ticks to get there
    # by tick 150 it is 150/384 of the way: about 7 cents of 18.75
    assert 3 < max(slow[100:]) < 10 and max(slow[100:]) < 0.55 * max(fast)
    # and rate 0 never starts it: the trap the compiler warns about
    still = _vibrato_trace("30/12/0")
    # a steady note, so only the estimator's jitter (a crude interpolator
    # makes a few cents of it) -- against 18.75 for a vibrato that runs
    assert max(abs(c) for c in still) < 3.0


def test_a_vibrato_that_cannot_start_and_a_gain_that_cannot_be_heard_warn():
    for setting, word in (("vib=30/12/0", "never fades the vibrato in"),
                          ("gain=1", "plays silent"),
                          ("gain=0", "plays silent")):
        text = P.score("A-4 01 v64 ...\n",
                       inst=f"inst 1 wave=sine oct=1-7 fade=200 {setting}")
        _, report = notation.compile_text(text)
        assert any(word in w for w in report.warnings), (setting,
                                                        report.warnings)
    ok = P.score("A-4 01 v64 ...\n",
                 inst="inst 1 wave=sine oct=1-7 fade=200 gain=2 vib=30/12/1")
    assert notation.compile_text(ok)[1].warnings == ()


def test_the_instrument_gain_is_applied_in_steps_of_two():
    _need()
    peaks = {}
    for gain in (1, 2, 3, 4, 8, 128):
        frames, _ = P.render(P.score(
            "A-4 01 v64 ...\n",
            inst=f"inst 1 wave=sine oct=1-7 fade=200 gain={gain}"), 0.5)
        peaks[gain] = P.peak(frames)
    assert peaks[1] == 0.0 and peaks[2] > 0.0           # the trap
    assert peaks[2] == peaks[3]
    assert abs(peaks[4] / peaks[2] - 2.0) < 0.04      # 16-bit output rounds
    assert abs(peaks[128] / peaks[8] - 16.0) < 0.3


def test_a_kit_key_with_no_sample_is_silent_and_the_others_are_not():
    _need()
    inst = "smp thump wave=kick len=0.2\ninst 1 kit=C-2:thump"
    for key, loud in (("C-2", True), ("E-2", False)):
        frames, module = P.render(P.score(f"{key} 01 v64 ...\n", inst=inst))
        assert (P.peak(frames) > 0.05) == loud, key


# -- the tools that look ------------------------------------------------------
def test_a_muted_channel_is_silent_and_levels_names_the_quiet_one():
    support.need_openmpt()          # the level report is libopenmpt's
    text = P.score("A-4 01 v64 ... | C-5 02 v08 ...\n", channels=2,
                   inst="inst 1 wave=sine oct=1-7 fade=200\n"
                        "inst 2 wave=saw oct=1-7 fade=200 gain=8")
    module, _ = notation.compile_text(text)
    rows = levels.voice_levels(module)
    by_channel = {r["channel"]: r for r in rows}
    assert by_channel[1]["audible"] and not by_channel[2]["audible"]
    assert by_channel[1]["rms_db"] > by_channel[2]["rms_db"] + 40
    blob = it_write.build(module)
    muted = levels._muted_but(blob, 1)               # keep only channel 2
    with openmpt.Song(muted) as song:
        only_two = song.render(P.SR, 1.0)
    with openmpt.Song(levels._muted_but(blob, 5)) as song:
        none = song.render(P.SR, 1.0)
    assert P.peak(none) == 0.0 and P.peak(only_two) > 0.0
