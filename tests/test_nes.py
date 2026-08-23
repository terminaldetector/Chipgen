"""The NES APU: the four things about it that change how you write for it."""

import struct


def _apu(**kwargs):
    import nes_apu
    chip = nes_apu.NESAPU(**kwargs)
    chip.write(0x4015, 0x0F)              # enable pulse 1, pulse 2, triangle, noise
    return chip


def _pulse(chip, period, duty=2, volume=15, halt=True):
    chip.write(0x4000, (duty << 6) | (0x20 if halt else 0) | 0x10 | volume)
    chip.write(0x4002, period & 0xFF)
    chip.write(0x4003, ((period >> 8) & 7) | (0x1F << 3))


def test_pitch_lands_where_the_timer_says_it_should():
    import analysis
    import nes_apu

    chip = _apu()
    for hertz in (110.0, 220.0, 440.0, 880.0):
        period = chip.pulse_period(hertz)
        exact = chip.pulse_frequency(period)
        fresh = _apu()
        _pulse(fresh, period)
        buffer = fresh.render(int(fresh.native_rate * 0.4))
        fresh.close()
        measured = analysis.fundamental(analysis.to_mono(buffer),
                                        fresh.native_rate)
        assert measured is not None, f"no pitch at {hertz} Hz"
        assert abs(analysis.cents(measured, exact)) < 2.0, \
            f"{hertz} Hz: timer says {exact:.2f}, chip produced {measured:.2f}"
    chip.close()


def test_the_triangle_sounds_an_octave_below_a_pulse_on_the_same_timer():
    # Its timer divides by 32 where the pulses divide by 16. Copying a
    # timer value across transposes down an octave, which is the single
    # easiest way to write a bass line a twelfth away from where it was
    # meant to be.
    import analysis

    period = 253
    pulse = _apu()
    _pulse(pulse, period)
    a = pulse.render(int(pulse.native_rate * 0.4))
    rate = pulse.native_rate
    pulse.close()

    triangle = _apu()
    triangle.write(0x4008, 0xFF)          # halt linear counter, full reload
    triangle.write(0x400A, period & 0xFF)
    triangle.write(0x400B, ((period >> 8) & 7) | (0x1F << 3))
    b = triangle.render(int(triangle.native_rate * 0.4))
    triangle.close()

    high = analysis.fundamental(analysis.to_mono(a), rate)
    low = analysis.fundamental(analysis.to_mono(b), rate)
    assert high and low
    semitones = analysis.cents(high, low) / 100.0
    assert abs(semitones - 12.0) < 0.1, \
        f"same timer gave {semitones:.2f} semitones apart, expected 12"


def test_the_triangle_has_no_volume_control():
    # Four bits of output, fixed. There is no register that attenuates it.
    # This is why the triangle is the bass on essentially every NES
    # soundtrack: a voice you cannot fade is one you can only give a part
    # that never needs to fade.
    import audio
    import nes_apu

    levels = []
    for attempt in (0x00, 0x40, 0x7F, 0xFF):
        chip = _apu()
        chip.write(0x4008, attempt if attempt & 0x80 else 0x80 | attempt)
        chip.write(0x400A, 253)
        chip.write(0x400B, (0x1F << 3))
        buffer = chip.render(int(chip.native_rate * 0.3))
        chip.close()
        levels.append(audio.peak(buffer))
    assert max(levels) > 0.0, "the triangle never sounded at all"
    assert max(levels) - min(levels) < 1e-6, \
        f"something attenuated the triangle: {levels}"


def test_channels_do_not_mix_linearly():
    # Two pulses at full are not twice one pulse. Anything that reasons
    # about levels by adding them is wrong on this chip in a way it is not
    # wrong on the YM2612.
    import nes_apu

    one = nes_apu.mix(15, 0, 0, 0, 0)
    two = nes_apu.mix(15, 15, 0, 0, 0)
    assert one > 0.0
    assert two < 2.0 * one, "the pulse mix is linear, which the hardware is not"
    assert two > one, "a second voice has to add something"
    assert nes_apu.mix(0, 0, 0, 0, 0) == 0.0


def test_duty_cycle_changes_the_waveform_not_the_pitch():
    import analysis

    period, shapes = 253, {}
    for duty in range(4):
        chip = _apu()
        _pulse(chip, period, duty=duty)
        buffer = chip.render(int(chip.native_rate * 0.4))
        mono = list(analysis.to_mono(buffer))
        rate = chip.native_rate
        chip.close()
        high = sum(1 for v in mono if v > 0) / len(mono)
        shapes[duty] = (high, analysis.fundamental(mono, rate))
    expected = (0.125, 0.25, 0.5, 0.75)
    for duty, want in enumerate(expected):
        high, pitch = shapes[duty]
        assert abs(high - want) < 0.02, \
            f"duty {duty} is high {high:.3f} of the time, expected {want}"
        assert pitch is not None, f"duty {duty} produced no measurable pitch"
    pitches = [shapes[d][1] for d in range(4)]
    assert max(pitches) - min(pitches) < 1.0, \
        f"duty changed the pitch: {pitches}"


def test_the_length_counter_stops_the_note():
    # Not a bug when a note ends on its own: the length counter is how the
    # hardware does note-off, and a driver that never sets the halt bit
    # gets a fixed note length whether it wanted one or not.
    import analysis

    chip = _apu()
    _pulse(chip, 253, halt=False)          # length counter running
    buffer = chip.render(int(chip.native_rate * 1.0))
    mono = list(analysis.to_mono(buffer))
    rate = chip.native_rate
    chip.close()
    last = max((i for i, v in enumerate(mono) if v > 0), default=0)
    ends_at = last / rate
    # Load 0x1F is 30 units at 120 Hz.
    assert 0.20 < ends_at < 0.28, f"note ran to {ends_at:.3f}s, expected ~0.25"


def test_noise_is_noise_and_never_settles_to_a_constant():
    import analysis
    import audio

    for mode in (0x00, 0x80):
        chip = _apu()
        chip.write(0x400C, 0x20 | 0x10 | 0x0F)
        chip.write(0x400E, mode | 0x04)
        chip.write(0x400F, 0x1F << 3)
        buffer = chip.render(int(chip.native_rate * 0.3))
        mono = list(analysis.to_mono(buffer))
        chip.close()
        assert audio.peak(buffer) > 0.0, f"noise mode {mode:#04x} was silent"
        assert len(set(mono)) > 1, "the shift register froze"


def test_vgm_reports_every_chip_a_file_declares():
    # A VGM is not one chip. Reading only the first one found silently
    # drops half of a two-chip soundtrack.
    import vgm

    header = bytearray(0x100)
    header[0:4] = b"Vgm "
    struct.pack_into("<I", header, 0x08, 0x161)
    struct.pack_into("<I", header, 0x34, 0x100 - 0x34)   # data starts at 0x100
    struct.pack_into("<I", header, 0x84, 1789773)        # NES APU
    struct.pack_into("<I", header, 0x74, 1789773)        # AY8910
    found = vgm.detect_chips(bytes(header))
    assert found.get("NES APU") == 1789773, found
    assert found.get("AY8910") == 1789773, found
    assert "AY8910" in vgm.unsupported_chips(bytes(header))
    assert "NES APU" not in vgm.unsupported_chips(bytes(header))


def test_an_old_header_does_not_report_chips_it_cannot_hold():
    # The NES and AY clock fields arrived in later header revisions.
    # Reading them unconditionally on a 1.50 file reads the data section
    # as a clock and invents chips that are not there.
    import vgm

    header = bytearray(0x40)
    header[0:4] = b"Vgm "
    struct.pack_into("<I", header, 0x08, 0x150)
    struct.pack_into("<I", header, 0x34, 0x0C)           # data at 0x40
    struct.pack_into("<I", header, 0x2C, 7670454)        # YM2612 only
    found = vgm.detect_chips(bytes(header))
    assert found == {"YM2612": 7670454}, found


def _nes_corpus():
    import score_model
    import os
    root = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "corpus", "nes")
    return score_model.load_all(score_model.corpus_paths(root))


def test_the_nes_corpus_loads_and_holds_only_nes_voices():
    scores = _nes_corpus()
    assert len(scores) > 100, f"only {len(scores)} NES scores"
    names = {name for score in scores for name in score.voices}
    assert names <= {"pulse1", "pulse2", "triangle", "noise"}, \
        f"a non-NES voice got into the NES corpus: {names}"
    notes = sum(len(v) for s in scores for v in s.voices.values())
    assert notes > 40000, notes   # 48,626 at the time of writing


def test_one_nes_voice_never_plays_two_notes_at_once():
    # The transcriber pairs register writes into notes, and a note takes
    # several writes. Reading the channel between them produced two
    # different notes at the same instant, which one channel cannot do.
    for score in _nes_corpus():
        for name, notes in score.voices.items():
            rows = [n.row for n in notes]
            assert len(rows) == len(set(rows)), \
                f"{score.title}/{name} has two notes on one row"
            for a, b in zip(notes, notes[1:]):
                assert a.end <= b.row, f"{score.title}/{name}: {a} overlaps {b}"


def test_the_transcriber_walks_a_log_to_its_declared_end():
    # A jingle whose closing chord rings out writes nothing for the last
    # second. Reporting the last register write as the duration made 24 of
    # 214 tracks come back about a second short.
    import glob
    import os
    import nes_transcribe
    import vgm

    root = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "corpus", "nes", "scores")
    if not glob.glob(os.path.join(root, "*.json")):
        return                                  # corpus not installed
    # Build a tiny log by hand: one write, then a long wait, then end.
    header = bytearray(0x100)
    header[0:4] = b"Vgm "
    struct.pack_into("<I", header, 0x08, 0x161)
    struct.pack_into("<I", header, 0x34, 0x100 - 0x34)
    struct.pack_into("<I", header, 0x84, 1789773)
    struct.pack_into("<I", header, 0x04, 0x100 + 8 - 4)
    body = bytes((vgm.CMD_NES_APU, 0x15, 0x0F)) \
        + bytes((vgm.CMD_WAIT_LONG,)) + struct.pack("<H", 44100) \
        + bytes((vgm.CMD_END,))
    _, info = nes_transcribe.transcribe(bytes(header) + body)
    assert info["duration"] >= 0.99, \
        f"walked {info['duration']:.3f}s, the log is 1.0s long"
