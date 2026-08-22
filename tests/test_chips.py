"""Register-level maths on both chips, checked against published tables."""

import opn2
import sn76489
import instruments
import samples


#: The block-4 F-numbers every Genesis note table prints. If these drift,
#: the whole engine is playing in the wrong key or the wrong octave, and
#: nothing downstream will notice because it will drift consistently.
BLOCK4_FNUMS = {"C": 644, "C#": 681, "D": 722, "D#": 765, "E": 810, "F": 858,
                "F#": 910, "G": 964, "G#": 1021, "A": 1082, "A#": 1146,
                "B": 1214}


def test_fnum_matches_the_published_note_table():
    for note, expected in BLOCK4_FNUMS.items():
        frequency = opn2.note_to_freq(note, 4)
        fnum, block = opn2.freq_to_fnum_block(frequency)
        # The picker may choose a lower block with a doubled F-number —
        # same pitch, more room for bends — so compare pitch, not digits.
        played = opn2.fnum_block_to_freq(fnum, block)
        cents = 1200.0 * (played / frequency - 1.0) / 0.0005777
        assert abs(played - frequency) / frequency < 0.001, \
            f"{note}4: wanted {frequency:.2f} Hz, chip plays {played:.2f} Hz"
        reference = opn2.fnum_block_to_freq(expected, 4)
        assert abs(played - reference) / reference < 0.002, \
            f"{note}4 disagrees with the published table ({expected} @ block 4)"


def test_fnum_block_round_trips():
    for octave in range(1, 8):
        for note in ("C", "E", "A#"):
            frequency = opn2.note_to_freq(note, octave)
            fnum, block = opn2.freq_to_fnum_block(frequency)
            assert 0 <= fnum <= 2047 and 0 <= block <= 7
            back = opn2.fnum_block_to_freq(fnum, block)
            assert abs(back - frequency) / frequency < 0.002


def test_carrier_sets_match_the_algorithm_diagrams():
    expected_operator_numbers = {0: {4}, 1: {4}, 2: {4}, 3: {4},
                                 4: {2, 4}, 5: {2, 3, 4}, 6: {2, 3, 4},
                                 7: {1, 2, 3, 4}}
    for algorithm, operators in expected_operator_numbers.items():
        patch = opn2.FMInstrument(algorithm, 0, [opn2.Operator() for _ in range(4)])
        indices = patch.carrier_indices()
        assert {opn2._LIST_TO_OP[i] for i in indices} == operators, \
            f"algorithm {algorithm}: wrong carriers"


def test_volume_fader_is_linear_in_amplitude():
    # Halving the fader should cost about 6 dB, not 47.
    full = opn2.level_to_attenuation(127)
    half = opn2.level_to_attenuation(64)
    assert full == 0
    assert 7 <= half <= 9, f"volume 64 should be ~6 dB down, got {half * 0.75:.1f} dB"
    assert opn2.level_to_attenuation(0) == 127


def test_psg_tone_register_matches_the_frequency_formula():
    for note, octave in (("A", 4), ("C", 3), ("E", 5)):
        wanted = sn76489.note_to_freq(note, octave)
        n = sn76489.freq_to_tone_n(wanted)
        played = sn76489.tone_n_to_freq(n)
        assert abs(played - wanted) / wanted < 0.01, f"{note}{octave} mistuned"


def test_psg_register_saturates_rather_than_wrapping():
    assert sn76489.freq_to_tone_n(20.0) == 1023      # too low to represent
    assert sn76489.freq_to_tone_n(200000.0) == 1     # too high


def test_instrument_bank_is_self_consistent():
    for name, patch in instruments.BANK.items():
        assert patch.name == name, f"{name}: patch.name disagrees with its bank key"
        assert len(patch.operators) == 4
        assert 0 <= patch.algorithm <= 7 and 0 <= patch.feedback <= 7
        assert name in instruments.CHARACTER, f"{name} has no description"
        for op in patch.operators:
            assert 0 <= op.total_level <= 127
            assert 0 <= op.attack_rate <= 31 and 0 <= op.release_rate <= 15


def test_instrument_serialisation_round_trips_both_orderings():
    for name in ("bass", "brass", "organ"):
        encoded = instruments.instrument_to_dict(instruments.BANK[name])
        assert instruments.instrument_to_dict(
            instruments.instrument_from_dict(encoded)) == encoded

    # Declaring chip order must actually reorder, or a patch imported from
    # a normal tracker silently gets two operators swapped.
    chip_order = dict(encoded, operator_order="chip")
    reordered = instruments.instrument_from_dict(chip_order)
    original = instruments.instrument_from_dict(encoded)
    assert reordered.operators[1].multiple == original.operators[2].multiple


def test_dac_kit_is_deterministic_and_in_range():
    first = {name: bytes(samples.KIT[name].data) for name in samples.names()}
    assert set(first) >= {"kick", "snare", "hat", "clap", "rim", "tom"}
    for name, data in first.items():
        assert len(data) > 100, f"{name} is suspiciously short"
        assert min(data) >= 0 and max(data) <= 255
        assert max(data) - min(data) > 60, f"{name} is nearly silent"
    second = {name: bytes(samples.KIT[name].data) for name in samples.names()}
    assert first == second, "the kit must render identically every time"
