"""The file format: writer, reader, and libopenmpt's opinion of both."""

import math
import random
import struct
from array import array

import support
from schism import it_read, it_write, model as M, verify


def _sine(n=64, amp=12000):
    return array("h", (int(amp * math.sin(2 * math.pi * i / n))
                       for i in range(n)))


def _module(rows=32, channels=2):
    smp = M.Sample(name="sine", data=_sine(), c5speed=64 * 523, loop=(0, 64))
    ins = M.Instrument(name="Sine")
    ins.use_sample(1)
    mod = M.Module(title="t", samples=[smp], instruments=[ins])
    mod.patterns = [M.Pattern(rows=rows)]
    mod.orders = [0]
    return mod


# -- the pieces ---------------------------------------------------------------
def test_notes_have_the_names_every_tracker_shows():
    assert M.note_name(1) == "C-0" and M.note_name(61) == "C-5"
    assert M.note_name(58) == "A-4" and M.note_name(120) == "B-9"
    for note in range(1, 121):
        assert M.note_number(M.note_name(note)) == note
    assert M.note_number("===") == M.NOTE_OFF
    assert M.note_number("^^^") == M.NOTE_CUT
    assert M.note_number("~~~") == M.NOTE_FADE
    assert abs(M.note_frequency(58) - 440.0) < 1e-9
    assert abs(M.note_frequency(61) - 523.2511) < 1e-3


def test_every_volume_column_value_round_trips_through_its_text():
    seen = set()
    for value in range(0, 213):
        if 65 <= value < 75 + 0 and False:
            continue
        try:
            text = M.volume_column_text(value)
        except ValueError:
            continue                      # 213..: not a volume-column value
        assert M.volume_column(text) == value, (value, text)
        seen.add(text[0])
    assert seen == set("vpabcdefgh")      # every kind exists in the table
    for bad in ("v65", "p99", "a10", "x01", "v6", "64"):
        try:
            M.volume_column(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} was accepted")


def test_effects_parse_and_print_as_hex_letters():
    assert M.effect_parse("H44") == (8, 0x44)
    assert M.effect_parse("Z7F") == (26, 0x7F)
    assert M.effect_parse("...") == (0, 0)
    assert M.effect_text(10, 0x37) == "J37"
    for bad in ("H4", "H4G", "1A2", "HH1"):
        try:
            M.effect_parse(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} was accepted")


# -- patterns -----------------------------------------------------------------
def _random_pattern(seed, rows=64, channels=9):
    rng = random.Random(seed)
    pattern = M.Pattern(rows=rows)
    note = 0
    for r in range(rows):
        for c in range(channels):
            if rng.random() < 0.35:
                cell = pattern.cell(r, c)
                if rng.random() < 0.7:
                    # repeats of the previous note exercise the repeat flags
                    note = note if rng.random() < 0.3 and note else rng.choice(
                        [rng.randint(1, 120), M.NOTE_OFF, M.NOTE_CUT,
                         M.NOTE_FADE])
                    cell.note = note
                if rng.random() < 0.6:
                    cell.instrument = rng.choice((1, 1, 2, 7, 99))
                if rng.random() < 0.5:
                    cell.volume = rng.choice((0, 32, 64, 64, 130, 160, 200))
                if rng.random() < 0.4:
                    cell.effect = rng.choice((8, 8, 10, 19))
                    cell.param = rng.choice((0x44, 0x44, 0x37, 0xC3))
    return pattern


def test_packed_patterns_come_back_cell_for_cell():
    for seed in range(12):
        pattern = _random_pattern(seed)
        blob = it_write.pack_pattern(pattern, 9)
        again = it_read.unpack_pattern(blob)
        assert again.rows == pattern.rows
        for r in range(pattern.rows):
            for c in range(9):
                got = again.cell(r, c)
                want = pattern.cell(r, c)
                assert got == want, (seed, r, c, got, want)


def test_an_empty_pattern_costs_one_byte_a_row():
    blob = it_write.pack_pattern(M.Pattern(rows=64), 4)
    assert len(blob) == 8 + 64


def test_repeats_are_cheaper_than_new_values():
    steady = M.Pattern(rows=32)
    for r in range(32):
        cell = steady.cell(r, 0)
        cell.note, cell.instrument, cell.volume = 61, 1, 48
    varied = M.Pattern(rows=32)
    for r in range(32):
        cell = varied.cell(r, 0)
        cell.note, cell.instrument, cell.volume = 1 + r, 1 + r % 7, r
    assert len(it_write.pack_pattern(steady, 1)) < \
        len(it_write.pack_pattern(varied, 1)) // 2


# -- the whole file -----------------------------------------------------------
def test_the_header_and_parapointers_sit_where_ittech_puts_them():
    mod = _module()
    blob = it_write.build(mod)
    assert blob[:4] == b"IMPM"
    (n_ord, n_ins, n_smp, n_pat) = struct.unpack_from("<HHHH", blob, 0x20)
    assert (n_ord, n_ins, n_smp, n_pat) == (2, 1, 1, 1)       # orders + 255
    assert blob[0xC0:0xC2] == bytes([0, 255])
    ins_at, = struct.unpack_from("<I", blob, 0xC2)
    smp_at, = struct.unpack_from("<I", blob, 0xC6)
    pat_at, = struct.unpack_from("<I", blob, 0xCA)
    assert blob[ins_at:ins_at + 4] == b"IMPI"
    assert smp_at == ins_at + 554 and blob[smp_at:smp_at + 4] == b"IMPS"
    assert pat_at == smp_at + 80
    data_at, = struct.unpack_from("<I", blob, smp_at + 0x48)
    assert data_at == pat_at + len(it_write.pack_pattern(mod.patterns[0], 1))
    assert len(blob) == data_at + 2 * 64


def test_a_module_reads_back_equal():
    mod = _module()
    mod.title = "round trip"
    mod.message = "line one\nline two"
    mod.samples[0].loop_pingpong = True
    mod.samples[0].sustain_loop = (8, 40)
    mod.samples[0].vibrato_speed, mod.samples[0].vibrato_depth = 20, 7
    ins = mod.instruments[0]
    ins.nna, ins.dct, ins.dca, ins.fadeout = 3, 1, 2, 120
    ins.cutoff, ins.resonance, ins.default_pan = 70, 33, 10
    ins.volume_envelope = M.Envelope(
        nodes=[(0, 64), (10, 50), (30, 50), (80, 0)], sustain=(1, 2))
    ins.pan_envelope = M.Envelope(nodes=[(0, -20), (40, 20)], loop=(0, 1))
    ins.pitch_envelope = M.Envelope(nodes=[(0, 0), (20, 64)], filter=True)
    mod.channel_pan[1], mod.channel_mute[2] = 10, True
    pat = mod.patterns[0]
    c = pat.cell(0, 0)
    c.note, c.instrument, c.volume, c.effect, c.param = 61, 1, 48, 8, 0x44
    again = it_read.read(it_write.build(mod))
    assert again.title == "round trip" and again.message == mod.message
    assert again.samples[0] == mod.samples[0]
    got = again.instruments[0]
    for field in ("name", "nna", "dct", "dca", "fadeout", "cutoff",
                  "resonance", "default_pan", "note_map", "volume_envelope",
                  "pan_envelope", "pitch_envelope"):
        assert getattr(got, field) == getattr(ins, field), field
    assert again.channel_pan[1] == 10 and again.channel_mute[2]
    assert again.patterns[0].cell(0, 0) == c


def test_samples_that_share_data_share_its_bytes():
    mod = _module()
    mod.samples.append(M.Sample(name="alias", data=mod.samples[0].data,
                                c5speed=22050, loop=(0, 64)))
    shared = len(it_write.build(mod))
    mod.samples[1].data = array("h", mod.samples[0].data)    # an equal copy
    assert len(it_write.build(mod)) == shared + 128
    a, b = it_read.read(it_write.build(mod)).samples
    assert a.data == b.data


def test_the_writer_refuses_what_the_format_cannot_hold():
    def mutate(change):
        mod = _module()
        change(mod)
        try:
            it_write.build(mod)
        except it_write.ITWriteError as error:
            return str(error)
        raise AssertionError("accepted: " + change.__doc__)

    def short(mod):
        """16 rows"""
        mod.patterns[0] = M.Pattern(rows=16)

    def bad_order(mod):
        """order names pattern 3"""
        mod.orders = [3]

    def backwards(mod):
        """envelope ticks going backwards"""
        mod.instruments[0].volume_envelope = M.Envelope(
            nodes=[(0, 64), (20, 30), (10, 0)])

    def loud(mod):
        """envelope value 90"""
        mod.instruments[0].volume_envelope = M.Envelope(nodes=[(0, 90)])

    def map_length(mod):
        """a note map of 10"""
        mod.instruments[0].note_map = [(0, 1)] * 10

    def tempo(mod):
        """tempo 10"""
        mod.tempo = 10

    def many_samples(mod):
        """236 samples"""
        mod.samples = [mod.samples[0]] * 236

    def many_patterns(mod):
        """241 patterns"""
        mod.patterns = [M.Pattern(rows=32) for _ in range(241)]

    def loop(mod):
        """a loop past the end"""
        mod.samples[0].loop = (0, 999)

    for change, word in ((short, "32-200"), (bad_order, "pattern 3"),
                         (backwards, "increase"), (loud, "0..64"),
                         (map_length, "120"), (tempo, "32..255"),
                         (many_samples, "at most 235"),
                         (many_patterns, "at most 240"),
                         (loop, "loop end")):
        assert word in mutate(change), change.__doc__


def test_a_compressed_sample_keeps_its_header_and_says_so():
    blob = bytearray(it_write.build(_module()))
    smp_at, = struct.unpack_from("<I", blob, 0xC6)
    blob[smp_at + 0x12] |= 8                  # the 'compressed' flag
    sample = it_read.read(bytes(blob)).samples[0]
    assert sample.compressed and sample.data is None
    assert sample.c5speed == 64 * 523 and sample.name == "sine"


def test_the_old_instrument_layout_is_refused_by_name():
    blob = bytearray(it_write.build(_module()))
    struct.pack_into("<H", blob, 0x2A, 0x0100)             # Cmwt: IT 1.x
    try:
        it_read.read(bytes(blob))
    except it_read.ITReadError as error:
        assert "old instrument layout" in str(error)
    else:
        raise AssertionError("an IT 1.x instrument was read as 2.x")


def test_note_fade_is_stored_as_a_byte_both_ittech_and_libopenmpt_accept():
    # measured: 253 reads as no note in libopenmpt, 121..252 as a fade
    assert 120 < it_write.FADE_BYTE < 253


# -- libopenmpt ---------------------------------------------------------------
def test_libopenmpt_reads_every_cell_as_written():
    support.need_openmpt()
    mod = _module(channels=9)
    mod.patterns = [_random_pattern(3), _random_pattern(4, rows=48)]
    mod.orders = [0, 1, 0]
    problems = verify.compare(mod)
    assert problems == [], problems[:3]


def test_libopenmpt_agrees_on_how_long_a_song_lasts():
    support.need_openmpt()
    from schism import openmpt, it_write as w

    def song(*cells):
        mod = _module()
        for (row, text) in cells:
            from schism import notation
            cell = mod.patterns[0].cell(row, 0)
            parsed = notation.parse_cell(text)
            cell.note, cell.instrument = parsed.note, parsed.instrument
            cell.volume, cell.effect = parsed.volume, parsed.effect
            cell.param = parsed.param
        return mod

    cases = {
        "plain": song(),
        "speed 3": song((0, "A-4 01 ... A03")),
        "speed 9 late": song((8, "... .. ... A09")),
        "tempo 150": song((0, "... .. ... T96")),
        "tempo 60": song((4, "... .. ... T3C")),
        "break to row 8": song((0, "... .. ... C08")),
        "jump": song((10, "... .. ... B00"), (2, "... .. ... T80")),
        "pattern delay 3": song((5, "... .. ... SE3")),
        "fine delay 4": song((5, "... .. ... S64")),
    }
    for label, mod in cases.items():
        with openmpt.Song(w.build(mod)) as theirs:
            assert abs(theirs.duration - mod.seconds()) < 0.01, \
                (label, theirs.duration, mod.seconds())
