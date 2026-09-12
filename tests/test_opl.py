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


# -- VGM ---------------------------------------------------------------------
def test_every_api_call_becomes_register_writes():
    # The whole reason the chip has a register interface: a .vgm stores
    # register writes, so a chip driven by setting Python attributes has
    # nothing to record.
    log = []
    chip = opl2.YM3812(logger=lambda a, d: log.append((a, d)))
    chip.set_instrument(0, opl_instruments.get("opl_bass"))
    chip.note_on(0, "A", 2)
    chip.note_off(0)
    chip.close()

    addresses = [a for a, _ in log]
    for base in (opl2.REG_AM_VIB_EGT_KSR_MULT, opl2.REG_KSL_TL,
                 opl2.REG_AR_DR, opl2.REG_SL_RR, opl2.REG_WAVEFORM):
        assert base + 0 in addresses, f"no modulator write at 0x{base:02X}"
        assert base + 3 in addresses, f"no carrier write at 0x{base + 3:02X}"
    assert opl2.REG_FEEDBACK_CONNECTION in addresses
    assert opl2.REG_FNUM_LOW in addresses

    keyed = [d for a, d in log if a == opl2.REG_KEYON_BLOCK_FNUM]
    assert any(d & 0x20 for d in keyed), "nothing ever keyed on"
    assert keyed[-1] & 0x20 == 0, "the note was never keyed off"


def test_the_note_register_pair_carries_the_published_f_number():
    log = []
    chip = opl2.YM3812(logger=lambda a, d: log.append((a, d)))
    chip.set_instrument(0, opl_instruments.get("opl_bass"))
    chip.note_on(0, "A", 4)
    chip.close()
    low = [d for a, d in log if a == opl2.REG_FNUM_LOW][-1]
    high = [d for a, d in log if a == opl2.REG_KEYON_BLOCK_FNUM][-1]
    fnum = low | ((high & 3) << 8)
    block = (high >> 2) & 7
    assert (fnum, block) == (580, 4), (fnum, block)


def test_redundant_writes_are_dropped():
    # Without a shadow register file, every note re-sends the same Total
    # Level and the .vgm carries it forever.
    log = []
    chip = opl2.YM3812(logger=lambda a, d: log.append((a, d)))
    chip.write(0x40, 0x10)
    first = len(log)
    chip.write(0x40, 0x10)
    chip.write(0x40, 0x10)
    assert len(log) == first, "an unchanged write still reached the log"
    chip.write(0x40, 0x11)
    assert len(log) == first + 1
    chip.close()


def test_writing_registers_reproduces_what_the_api_produced():
    # Replay the log into a second chip and compare the audio. If the
    # decoder and the encoder disagree anywhere, this is where it shows.
    log = []
    source = opl2.YM3812(logger=lambda a, d: log.append((a, d)))
    source.set_instrument(0, opl_instruments.get("opl_saw_lead"))
    source.note_on(0, "C", 5)
    direct = [float(v) for v in source.render(int(source.native_rate * 0.25))]
    source.close()

    replay = opl2.YM3812()
    for address, data in log:
        replay.write(address, data)
    again = [float(v) for v in replay.render(int(replay.native_rate * 0.25))]
    replay.close()

    assert len(direct) == len(again)
    worst = max(abs(a - b) for a, b in zip(direct, again))
    assert worst < 1e-9, f"register replay diverged by {worst}"


def test_a_score_with_opl_exports_and_replays_as_vgm():
    import os

    import chipgen
    import vgm as vgm_mod
    import vgm_player

    score = ("ticks 240\nbpm 150\nlpb 4\n"
             "inst opl0 opl_square_lead\ninst opl1 opl_organ\n"
             "cols opl0 opl1\n"
             "A-4  A-3\n...  ...\nC-5  C-4\n...  ...\n"
             "E-5  E-4\n...  ...\n===  ===\n...  ...\n")
    with support.TempDir() as directory:
        path = os.path.join(directory, "opl.vgm")
        result = chipgen.compose(score, vgm=path)
        header = vgm_mod.read_header(path)
        assert header["opl_clock"] == int(round(opl2.NTSC_CLOCK)), header
        replayed = vgm_player.render(path)

    import audio
    assert audio.rms(replayed) > 1e-3, "the replayed VGM is silent"
    # Same performance, so the same length and a comparable level.
    assert abs(len(replayed) - len(result.audio)) <= 4
    difference = abs(20 * math.log10(max(1e-9, audio.rms(replayed))
                                     / max(1e-9, audio.rms(result.audio))))
    assert difference < 1.5, f"replay is {difference:.2f} dB away"


def test_a_score_without_opl_leaves_the_vgm_header_clock_at_zero():
    # Zero is how a player knows there is no OPL2 in the file at all.
    import os

    import chipgen
    import vgm as vgm_mod

    with support.TempDir() as directory:
        path = os.path.join(directory, "plain.vgm")
        chipgen.compose("inst fm0 bass\ncols fm0\nA-2\n...\n===\n", vgm=path)
        assert vgm_mod.read_header(path)["opl_clock"] == 0


def _opl_register_trace(cell, watch, patch="opl_organ"):
    """Every value the OPL2 is given at `watch` while a one-note score runs.

    Registers, not audio: the claim under test is that the tracker's effect
    codes reach this chip at all, and a register trace answers that without
    a pitch detector's octave errors in the way.
    """
    import opl2
    import sequencer
    import tracker

    events, _ = tracker.loads(
        f"bpm 120\nlpb 4\ninst opl0 {patch}\ncols opl0\n{cell}\n"
        "...\n...\n...\nend\n")
    seen = []
    original = opl2.YM3812.__init__

    def patched(self, *args, **kwargs):
        original(self, *args, **kwargs)
        inner = self.write

        def spy(addr, value):
            if addr in watch:
                seen.append(value & 0x3F if addr == 0x43 else value)
            return inner(addr, value)

        self.write = spy

    opl2.YM3812.__init__ = patched
    try:
        sequencer.Sequencer().render(events)
    finally:
        opl2.YM3812.__init__ = original
    return seen


def test_the_effect_column_reaches_the_opl2():
    """Pitch effects on an `opl` cell must move the F-Number.

    This was silently broken: effects.py has carried opl0-8 voices and the
    sequencer's _write_effect has pushed them to set_pitch_offset all
    along, but the tracker's OPL branch parsed a cell's effect codes and
    then dropped them. Nothing errored — the note simply played straight,
    which reads as "this chip has no vibrato" rather than as a missing
    wire. One F-Number write means no effect ran.
    """
    plain = _opl_register_trace("A-4:100", {0xA0})
    assert len(plain) == 1, \
        f"a plain note should set the F-Number once, got {len(plain)} writes"

    rising = _opl_register_trace("A-4:100/1F0", {0xA0})
    assert len(rising) > 20, \
        f"a pitch slide should rewrite the F-Number per tick, got {len(rising)}"
    assert len(set(rising)) > 20, (
        f"the slide wrote {len(rising)} times but only "
        f"{len(set(rising))} distinct values")


def test_vibrato_depth_scales_on_the_opl2():
    # 4xy is speed x, depth y — the ProTracker order, and the order
    # fx.vocabulary() advertises. Reading it as depth-then-speed is an
    # easy mistake: every 4A6/456/416 then looks like the same depth.
    swings = {}
    for depth, code in ((2, "452"), (6, "456"), (10, "45A")):
        seen = _opl_register_trace(f"A-4:100/{code}", {0xA0})
        swings[depth] = (max(seen) - min(seen)) / 2.0

    assert swings[2] < swings[6] < swings[10], \
        f"vibrato depth did not scale the F-Number swing: {swings}"
    # Linear in cents: 8 cents per depth unit, so the ratio of swings
    # should track the ratio of depths within the F-Number's quantisation.
    ratio = swings[10] / swings[2]
    assert 3.5 < ratio < 7.0, \
        f"depth 10 swung {ratio:.1f}x depth 2; expected about 5x"


def test_volume_effects_reach_the_opl2_carrier_level():
    # The OPL2 has no channel volume register, so the only route down is
    # the carrier's Total Level. Higher is quieter.
    plain = _opl_register_trace("A-4:100", {0x43})
    fading = _opl_register_trace("A-4:100/A0F", {0x43})
    tremolo = _opl_register_trace("A-4:100/756", {0x43})

    assert max(fading) > max(plain) + 10, (
        f"a steep volume slide should attenuate the carrier; plain reached "
        f"TL {max(plain)}, sliding reached {max(fading)}")
    assert len(set(tremolo)) > 2, \
        f"tremolo should move the level repeatedly, saw {sorted(set(tremolo))}"


def test_a_volume_slide_upward_at_full_volume_does_nothing():
    """Axy is x up, y down — and `A40` on a loud note is a no-op.

    Worth pinning because it is how this looked like a bug: `A40` produced
    no level writes at all, which reads as "volume effects are not wired"
    when in fact the note was already against the ceiling.
    """
    plain = _opl_register_trace("A-4:100", {0x43})
    upward = _opl_register_trace("A-4:100/A40", {0x43})
    assert upward == plain, (
        f"sliding up from full volume should write nothing extra; "
        f"plain {plain} vs upward {upward}")


def _seg_db(result, start, end):
    import math

    import analysis

    samples = [float(v) for v in analysis.to_mono(result.audio)]
    rate = result.sample_rate
    chunk = samples[int(start * rate):int(end * rate)]
    if not chunk:
        return -180.0
    power = math.sqrt(sum(v * v for v in chunk) / len(chunk))
    return 20 * math.log10(max(power, 1e-9))


def _partials(result, f0, count=6, seconds=0.5):
    """Each harmonic's level relative to the loudest of them, in dB."""
    import math

    import analysis

    samples = [float(v) for v in analysis.to_mono(result.audio)]
    rate = result.sample_rate
    chunk = samples[:int(seconds * rate)]
    n = len(chunk)

    def amplitude(frequency):
        re = im = 0.0
        for i, value in enumerate(chunk):
            window = 0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1))
            phase = 2 * math.pi * frequency * i / rate
            re += value * window * math.cos(phase)
            im += value * window * math.sin(phase)
        return math.hypot(re, im)

    found = [(k, amplitude(f0 * k)) for k in range(1, count + 1)]
    loudest = max(a for _, a in found)
    return {k: 20 * math.log10(max(a, 1e-12) / loudest) for k, a in found}


def test_one_operator_field_can_be_written_mid_note():
    """`op opl0 2 tl 40` — the job `op fm0 4 tl 12` does on the YM2612.

    Until this existed the OPL2 could only be handed a whole patch, so a
    part that should shape itself while it sounds had to be rebuilt out
    of note-ons.
    """
    import chipgen

    def score(write, pre=""):
        return ("bpm 120\nlpb 4\ninst opl0 opl_organ\n" + pre
                + "cols opl0\nA-4:110\n...\n...\n...\n" + write
                + "...\n...\n...\nend\n")

    # opl_organ is ADDITIVE, so both operators reach the output and
    # attenuating one barely moves the sum. That is the connection bit
    # doing its job, not a missed write — and it is the thing to know
    # before reaching for `op` on this chip.
    quiet = chipgen.compose(score("op opl0 2 tl 40\n"))
    both = chipgen.compose(score("op opl0 1 tl 40\nop opl0 2 tl 40\n"))
    fm = chipgen.compose(score("op opl0 2 tl 40\n", pre="alg opl0 0\n"))

    one_operator = _seg_db(quiet, 0.55, 0.90) - _seg_db(quiet, 0.10, 0.45)
    two_operators = _seg_db(both, 0.55, 0.90) - _seg_db(both, 0.10, 0.45)
    carrier_only = _seg_db(fm, 0.55, 0.90) - _seg_db(fm, 0.10, 0.45)

    assert two_operators < -12.0, (
        f"attenuating both operators of an additive patch should drop it "
        f"audibly; measured {two_operators:.1f} dB")
    assert one_operator > two_operators + 8.0, (
        f"on an additive patch one operator should matter much less than "
        f"two; measured {one_operator:.1f} against {two_operators:.1f} dB")
    assert carrier_only < -12.0, (
        f"in FM mode the carrier is the only thing heard, so attenuating "
        f"it should drop the note; measured {carrier_only:.1f} dB")


def test_the_waveform_select_has_no_ym2612_equivalent_and_shifts_the_octave():
    """The OPL2's four shapes, and the trap in two of them.

    Waves 2 and 3 rectify the sine, which DOUBLES its frequency: at the
    same written note their loudest partial is the second harmonic, an
    octave above where waves 0 and 1 put it. A melody that changes
    waveform mid-phrase changes octave with it, and nothing errors.
    """
    import chipgen

    def shape(wave):
        return chipgen.compose(
            "bpm 60\nlpb 4\ninst opl0 opl_organ\nalg opl0 1\n"
            "op opl0 1 mul 1\nop opl0 2 mul 1\n"
            f"op opl0 1 wave {wave}\nop opl0 2 wave {wave}\n"
            "cols opl0\nA-4:110\n...\n...\n...\nend\n")

    sine = _partials(shape(0), 440.0)
    half = _partials(shape(1), 440.0)
    rectified = _partials(shape(2), 440.0)

    # A sine is a sine: nothing but the fundamental.
    assert sine[1] > -1.0 and sine[2] < -40.0, (
        f"wave 0 should be a pure sine; h1={sine[1]:.0f} h2={sine[2]:.0f} dB")
    # Half-sine keeps the fundamental but adds the even harmonics.
    assert half[1] > -1.0 and half[2] > -20.0, (
        f"wave 1 should add even harmonics; h1={half[1]:.0f} "
        f"h2={half[2]:.0f} dB")
    # Absolute sine loses the fundamental almost entirely.
    assert rectified[2] > -1.0 and rectified[1] < -40.0, (
        f"wave 2 rectifies, so its second harmonic should dominate and the "
        f"fundamental nearly vanish; h1={rectified[1]:.0f} "
        f"h2={rectified[2]:.0f} dB")


def test_the_opl_algorithm_space_is_one_bit_and_says_so():
    # `alg opl0 4` would be a YM2612 habit. This chip has FM or additive
    # and nothing else, so the error names the two rather than clamping
    # to something that renders.
    import tracker

    try:
        tracker.loads("bpm 120\nlpb 4\ninst opl0 opl_organ\ncols opl0\n"
                      "A-4\nalg opl0 4\n...\nend\n")
    except tracker.TrackerError as error:
        assert "one algorithm bit" in str(error), \
            f"the error did not explain the OPL2's algorithm space: {error}"
    else:
        raise AssertionError("the OPL2 accepted algorithm 4")

    for value in (0, 1):
        events, _ = tracker.loads(
            "bpm 120\nlpb 4\ninst opl0 opl_organ\ncols opl0\n"
            f"A-4\nalg opl0 {value}\n...\nend\n")
        assert any(type(e).__name__ == "OPLConnection" for e in events), \
            f"alg opl0 {value} produced no connection event"


def test_a_directive_after_an_opl_op_still_runs():
    """_directive returns True to mean STOP PARSING, which `end` uses.

    The OPL branch first returned True on success, so the score ended at
    the first `op opl0` line and everything after it — every later
    directive, every remaining row — was dropped with no error. Pinned
    because the symptom is a short render, not a failure.
    """
    import tracker

    events, _ = tracker.loads(
        "bpm 120\nlpb 4\ninst opl0 opl_organ\ncols opl0\n"
        "A-4:100\nop opl0 2 tl 20\nop opl0 1 wave 2\nalg opl0 1 5\n"
        "...\nC-5:100\n...\nend\n")
    kinds = [type(e).__name__ for e in events]
    assert kinds.count("OPLOperator") == 2, \
        f"expected both operator writes, got {kinds}"
    assert "OPLConnection" in kinds, "the alg directive after an op was lost"
    assert kinds.count("OPLNoteOn") == 2, \
        f"the second note went missing: {kinds}"


def test_writing_one_operator_field_preserves_the_one_sharing_its_register():
    # Two fields per register on this chip too, so `tl` must not zero
    # `ksl`, `ar` must not zero `dr`, and so on.
    import opl2
    import opl_instruments

    chip = opl2.YM3812()
    try:
        chip.set_instrument(0, opl_instruments.get("opl_organ"))
        chip.note_on(0, "A", 4)
        chip.set_operator(0, 2, "tl", 20)
        merged = chip.set_operator(0, 2, "ksl", 2)
        assert merged & 0x3F == 20, (
            f"writing ksl zeroed tl: register is 0x{merged:02X}")
        assert (merged >> 6) & 3 == 2, \
            f"ksl did not take: register is 0x{merged:02X}"

        chip.set_operator(0, 1, "ar", 12)
        merged = chip.set_operator(0, 1, "dr", 5)
        assert (merged >> 4) & 0xF == 12, \
            f"writing dr zeroed ar: register is 0x{merged:02X}"
    finally:
        chip.close()


def test_a_bad_opl_operator_or_field_is_refused_with_the_options():
    import tracker

    for line, wanted in (("op opl0 3 tl 20", "two operators"),
                         ("op opl0 2 nope 20", "unknown OPL2 operator field"),
                         ("op opl0 2 wave 9", "0-3")):
        try:
            tracker.loads("bpm 120\nlpb 4\ninst opl0 opl_organ\ncols opl0\n"
                          f"A-4\n{line}\n...\nend\n")
        except tracker.TrackerError as error:
            assert wanted in str(error), \
                f"{line!r} was refused without saying why: {error}"
        else:
            raise AssertionError(f"{line!r} was accepted")
