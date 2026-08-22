"""opl2.py + opl_import.py: the YM3812 and the patch formats that feed it.

The chip is checked against facts that can be looked up rather than
against another emulator: the ROM tables' published first entries, the
F-Number every OPL note table lists for A-4, the 0.75 dB Total Level step,
and the shape of each of the four waveforms.
"""

import math
import os
import struct

import opl2
import opl_import
import opl_instruments
import support


def _render(instrument, note="A", octave=4, seconds=0.25):
    chip = opl2.YM3812()
    chip.set_instrument(0, instrument)
    chip.note_on(0, note, octave)
    buf = chip.render(int(chip.native_rate * seconds))
    chip.close()
    return [float(v) for v in buf], chip.native_rate


def _rms(values):
    return math.sqrt(sum(v * v for v in values) / max(1, len(values)))


def _sine_patch(total_level=0, waveform=0):
    """Modulator fully attenuated, so the carrier is a bare oscillator."""
    return opl2.OPLInstrument(
        modulator=opl2.OPLOperator(attack=15, decay=0, sustain_level=0,
                                   release=6, total_level=63, multiple=1),
        carrier=opl2.OPLOperator(attack=15, decay=0, sustain_level=0,
                                 release=6, total_level=total_level,
                                 multiple=1, waveform=waveform))


# -- the chip's own ROM tables ----------------------------------------------
def test_rom_tables_match_the_hardware():
    # These two numbers are the first entries of the YM3812's log-sine and
    # exponent ROMs. Getting either wrong changes the timbre of every note
    # in a way that still sounds like FM, which is why they are pinned to
    # the published values rather than to "it made a sound".
    assert opl2._LOGSIN[0] == 0x859
    assert opl2._LOGSIN[255] == 0
    # Full scale out of one operator is 4084, not 4095: the exponent table
    # stops one step short and the extra bit is ORed back in.
    assert opl2._exp_out(0) == 4084
    # 256 units of attenuation is exactly one octave of amplitude.
    assert opl2._exp_out(256) == 2042


def test_a_note_lands_on_the_published_f_number():
    # A-4 at block 4 is F-Number 580 in every OPL note table ever printed.
    fnum, block = opl2.freq_to_fnum_block(440.0)
    assert (fnum, block) == (580, 4), (fnum, block)
    assert abs(opl2.fnum_block_to_freq(580, 4) - 440.0) < 0.05


def test_pitch_comes_out_where_it_was_asked_for():
    for note, octave, want in (("A", 4, 440.0), ("C", 5, 523.25),
                               ("E", 6, 1318.51)):
        samples, rate = _render(_sine_patch(), note, octave, 0.35)
        # Zero-crossing rate is enough for a single partial and needs no FFT.
        body = samples[2000:]
        crossings = sum(1 for i in range(1, len(body))
                        if body[i - 1] <= 0 < body[i])
        got = crossings * rate / len(body)
        cents = 1200 * math.log2(got / want)
        assert abs(cents) < 25, f"{note}-{octave}: {got:.1f} Hz ({cents:+.0f} cents)"


def test_total_level_steps_by_three_quarters_of_a_decibel():
    base = _rms(_render(_sine_patch(0), seconds=0.15)[0][2000:])
    for steps in (4, 8, 16, 32):
        level = _rms(_render(_sine_patch(steps), seconds=0.15)[0][2000:])
        want = -0.75 * steps
        got = 20 * math.log10(level / base)
        assert abs(got - want) < 0.4, f"TL {steps}: {got:.2f} dB, want {want:.2f}"


def test_key_scale_level_is_six_decibels_per_octave():
    # KSL setting 3 is documented as 6 dB/octave, and the table has to
    # produce that exactly or high notes sit wrong in every patch.
    previous = None
    for block in (2, 3, 4, 5):
        raw = (opl2._KSL_ROM[512 >> 6] << 2) - ((8 - block) << 5)
        value = max(0, raw) * opl2.ENVELOPE_DB
        if previous is not None:
            assert abs((value - previous) - 6.0) < 0.01, (block, value, previous)
        previous = value


def test_all_four_waveforms_have_their_documented_shape():
    shapes = {}
    for waveform in range(4):
        samples, _ = _render(_sine_patch(waveform=waveform), seconds=0.15)
        body = samples[2000:]
        shapes[waveform] = (
            sum(1 for v in body if v > 1e-6) / len(body),
            sum(1 for v in body if v < -1e-6) / len(body),
        )
    positive, negative = shapes[0]
    assert abs(positive - 0.5) < 0.05 and abs(negative - 0.5) < 0.05, \
        "waveform 0 should be a symmetric sine"
    assert shapes[1][1] < 0.02, "waveform 1 (half-sine) must mute its lower half"
    assert shapes[2][0] > 0.95, "waveform 2 (absolute sine) is all positive"
    assert shapes[3][1] < 0.02 and shapes[3][0] < 0.6, \
        "waveform 3 (pulse-sine) keeps one quarter in two"


def test_the_modulator_controls_brightness():
    # The whole point of FM: attenuating the modulator has to take
    # harmonics away, monotonically.
    centroids = []
    for modulator_level in (63, 30, 10, 0):
        instrument = opl2.OPLInstrument(
            modulator=opl2.OPLOperator(attack=15, decay=0, sustain_level=0,
                                       release=6, total_level=modulator_level,
                                       multiple=1),
            carrier=opl2.OPLOperator(attack=15, decay=0, sustain_level=0,
                                     release=6, total_level=0, multiple=1))
        samples, rate = _render(instrument, seconds=0.2)
        body = samples[2000:]
        # Mean absolute slope stands in for spectral centroid: more
        # harmonics means the waveform moves further per sample.
        slope = sum(abs(body[i] - body[i - 1]) for i in range(1, len(body)))
        centroids.append(slope / max(1e-9, _rms(body)))
    assert centroids == sorted(centroids), \
        f"brightness is not monotonic in modulator level: {centroids}"


def test_an_envelope_actually_decays_and_releases():
    instrument = opl2.OPLInstrument(
        modulator=opl2.OPLOperator(total_level=63),
        carrier=opl2.OPLOperator(attack=15, decay=6, sustain_level=4,
                                 release=8, total_level=0, sustaining=True))
    chip = opl2.YM3812()
    chip.set_instrument(0, instrument)
    chip.note_on(0, "A", 4)
    early = _rms([float(v) for v in chip.render(int(chip.native_rate * 0.02))])
    held = _rms([float(v) for v in chip.render(int(chip.native_rate * 0.30))])
    chip.note_off(0)
    after = _rms([float(v) for v in chip.render(int(chip.native_rate * 0.30))])
    chip.close()
    assert early > 0.001, "nothing came out of the attack"
    assert held < early, "the envelope never decayed toward its sustain level"
    assert after < held * 0.5, "note-off did not release"


def test_faster_attack_rates_reach_full_level_sooner():
    times = []
    for attack in (4, 8, 15):
        instrument = opl2.OPLInstrument(
            modulator=opl2.OPLOperator(total_level=63),
            carrier=opl2.OPLOperator(attack=attack, decay=0, sustain_level=0,
                                     release=6, total_level=0))
        chip = opl2.YM3812()
        chip.set_instrument(0, instrument)
        chip.note_on(0, "A", 4)
        samples = [abs(float(v)) for v in chip.render(int(chip.native_rate * 0.4))]
        chip.close()
        ceiling = max(samples) * 0.7
        times.append(next((i for i, v in enumerate(samples) if v >= ceiling),
                          len(samples)))
    assert times[0] > times[1] > times[2], f"attack rates out of order: {times}"


# -- the patch formats -------------------------------------------------------
def _sbi_bytes(name=b"test patch", registers=None):
    registers = registers or bytes([0x21, 0x21, 0x4F, 0x00, 0xF1, 0xF2,
                                    0x53, 0x74, 0x00, 0x01, 0x06])
    return b"SBI\x1a" + name.ljust(32, b"\0") + registers + b"\0" * 5


def test_sbi_maps_every_register_field():
    with support.TempDir() as directory:
        path = os.path.join(directory, "t.sbi")
        with open(path, "wb") as handle:
            handle.write(_sbi_bytes())
        instrument = opl_import.load_sbi(path)

    # 0x21 = AM off, VIB off, EG-type on, KSR off, MULT 1
    assert instrument.modulator.sustaining is True
    assert instrument.modulator.multiple == 1
    assert instrument.modulator.tremolo == 0
    # 0x4F = KSL 1, TL 15
    assert instrument.modulator.key_scale_level == 1
    assert instrument.modulator.total_level == 15
    # 0xF1 = AR 15, DR 1
    assert instrument.modulator.attack == 15
    assert instrument.modulator.decay == 1
    # 0x53 = SL 5, RR 3
    assert instrument.modulator.sustain_level == 5
    assert instrument.modulator.release == 3
    # waveform bytes, then 0x06 = feedback 3, connection 0
    assert instrument.modulator.waveform == 0
    assert instrument.carrier.waveform == 1
    assert instrument.feedback == 3
    assert instrument.connection == 0
    assert instrument.name == "test_patch"


def test_a_file_that_is_not_an_sbi_is_refused():
    with support.TempDir() as directory:
        path = os.path.join(directory, "no.sbi")
        with open(path, "wb") as handle:
            handle.write(b"nope" + b"\0" * 60)
        try:
            opl_import.load_sbi(path)
        except opl_import.InstrumentFormatError as exc:
            assert "SBI" in str(exc)
        else:
            raise AssertionError("garbage should not have parsed")


def test_a_bank_round_trips_through_json():
    bank = {name: opl_instruments.BANK[name]
            for name in list(opl_instruments.names())[:4]}
    with support.TempDir() as directory:
        path = os.path.join(directory, "bank.json")
        opl_import.save_bank(bank, path, calibrate=False)
        restored = opl_import.load_bank(path)
    assert set(restored) == set(bank)
    for name, instrument in bank.items():
        other = restored[name]
        assert other.feedback == instrument.feedback
        assert other.connection == instrument.connection
        for field in opl2.OPLOperator.__slots__:
            assert getattr(other.carrier, field) == \
                getattr(instrument.carrier, field), (name, field)


def test_every_built_in_patch_makes_a_sound():
    for name in opl_instruments.names():
        samples, _ = _render(opl_instruments.get(name), "C", 5, 0.2)
        assert _rms(samples) > 1e-3, f"{name} is silent"


def test_an_unknown_patch_name_lists_the_real_ones():
    try:
        opl_instruments.get("no_such_patch")
    except KeyError as exc:
        assert "opl_bass" in str(exc), str(exc)
    else:
        raise AssertionError("an unknown name should have raised")


def test_calibration_pulls_a_bank_together():
    import copy

    bank = {name: copy.deepcopy(opl_instruments.BANK[name])
            for name in ("opl_organ", "opl_bell", "opl_pad")}
    for instrument in bank.values():
        instrument.trim = 0
    before = [opl_import.measure_loudness(i) for i in bank.values()]
    opl_import.calibrate_bank(bank)
    after = [opl_import.measure_loudness(i) for i in bank.values()]

    spread = lambda values: max(values) / max(1e-9, min(values))
    assert spread(after) <= spread(before) + 1e-6, \
        f"calibration widened the spread: {before} -> {after}"


# -- end to end --------------------------------------------------------------
def test_a_score_can_drive_the_opl_and_the_ym2612_at_once():
    import chipgen

    score = ("ticks 240\nbpm 150\nlpb 4\n"
             "inst opl0 opl_bass\ninst fm0 bass\n"
             "cols opl0 fm0 dac\n"
             "A-2  A-2  kick\n...  ...  ...\n"
             "C-3  C-3  snare\n===  ===  ...\n")
    result = chipgen.compose(score)
    kinds = {type(e).__name__ for e in result.events}
    assert "OPLNoteOn" in kinds and "FMNoteOn" in kinds
    import audio
    assert audio.rms(result.audio) > 1e-3, "the mix is silent"


def test_a_score_without_opl_does_not_build_the_chip():
    # The OPL2 core is pure Python, so instantiating one for a score that
    # never plays it would be the most expensive thing in the render.
    import sequencer
    from events import End, FMNoteOn, Wait

    assert not sequencer._uses_opl([FMNoteOn(channel=0, note="A", octave=3),
                                    Wait(ticks=10), End()])
    from events import OPLNoteOn
    assert sequencer._uses_opl([OPLNoteOn(channel=0, note="A", octave=3), End()])
