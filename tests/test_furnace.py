"""furnace_import.py: reading Furnace/DefleMask/TFM instrument files.

Every fixture here is built byte by byte in the test rather than read from
a real library, so the suite stays self-contained — the funeuro checkout
these were developed against is not part of this repository.
"""

import furnace_import as fi
import support


def _tfi_bytes(alg=4, fb=6, dt=3, mult=2, tl=20, rs=1, ar=31, dr=10,
               d2r=5, rr=7, sl=3, ssg=0):
    """One .tfi: alg, fb, then 4 identical operators of 10 bytes."""
    data = bytes([alg, fb])
    for _ in range(4):
        data += bytes([mult, dt, tl, rs, ar, dr, d2r, rr, sl, ssg])
    return data


def _dmp_bytes(alg=2, fb=5, dt=3, mult=1, tl=12, rs=0, ar=31, dr=8,
               d2r=4, rr=9, sl=2, ssg=0, am=0, version=11, system=2):
    """One version-11 Genesis .dmp."""
    data = bytes([version, system, 1, 0, fb, alg, 0])   # mode=1, fms=0, ams=0
    for _ in range(4):
        data += bytes([mult, tl, ar, dr, sl, rr, am, rs, dt, d2r, ssg])
    return data


def test_tfi_maps_every_field_to_its_register_meaning():
    instrument = fi.load_tfi(_tfi_bytes(), "probe")
    assert instrument.algorithm == 4
    assert instrument.feedback == 6
    assert instrument.name == "probe"
    for op in instrument.operators:
        assert op.multiple == 2
        assert op.total_level == 20
        assert op.rate_scaling == 1
        assert op.attack_rate == 31
        assert op.decay_rate == 10
        assert op.sustain_rate == 5      # d2r
        assert op.release_rate == 7
        assert op.sustain_level == 3
        assert op.ssg_eg == 0


def test_detune_goes_through_furnaces_table_not_straight_across():
    # The single easiest thing to get wrong: Furnace stores detune in its
    # own 0-7 space centred on 3 and converts on the way to the chip.
    # Copying the value straight across detunes every imported patch.
    assert fi.DT_TABLE == (7, 6, 5, 0, 1, 2, 3, 4)

    centred = fi.load_tfi(_tfi_bytes(dt=3))
    assert centred.operators[0].detune == 0, \
        "Furnace's 3 is the chip's 0 — no detune"

    for furnace_value, register_value in enumerate(fi.DT_TABLE):
        instrument = fi.load_tfi(_tfi_bytes(dt=furnace_value))
        assert instrument.operators[0].detune == register_value


def test_dmp_reads_a_genesis_instrument():
    instrument = fi.load_dmp(_dmp_bytes(), "probe")
    assert instrument.algorithm == 2
    assert instrument.feedback == 5
    for op in instrument.operators:
        assert op.multiple == 1
        assert op.total_level == 12
        assert op.attack_rate == 31
        assert op.decay_rate == 8
        assert op.sustain_level == 2
        assert op.release_rate == 9
        assert op.sustain_rate == 4
        assert op.detune == 0            # Furnace 3 -> register 0


def test_dmp_am_bit_and_dt2_nibble():
    # DefleMask packs OPM's DT2 into the high nibble of dt. OPN has no DT2,
    # so a file carrying one must not corrupt the detune.
    instrument = fi.load_dmp(_dmp_bytes(dt=0x23, am=1))
    assert instrument.operators[0].detune == fi.DT_TABLE[3], \
        "the high nibble is DT2 and belongs to OPM, not here"
    assert instrument.operators[0].am_enable == 1


def test_dmp_rejects_what_it_cannot_read():
    for data, needle in (
            (_dmp_bytes(system=3), "not an OPN"),        # SMS, a PSG part
            (bytes([12, 2, 1]) + bytes(44), "newer than"),
            (bytes([5]) + bytes(50), "predates"),
            (bytes([11]), "too short"),
    ):
        try:
            fi.load_dmp(data, "probe.dmp")
        except fi.InstrumentFormatError as exc:
            assert needle in str(exc), f"{needle!r} missing from: {exc}"
            assert "probe.dmp" in str(exc), "the error must name the file"
        else:
            raise AssertionError(f"{needle} should have been rejected")


def test_dmp_rejects_a_psg_instrument():
    psg = bytes([11, 2, 0]) + bytes(48)      # mode = 0 -> standard/PSG
    try:
        fi.load_dmp(psg, "psg.dmp")
    except fi.InstrumentFormatError as exc:
        assert "not a 4-operator" in str(exc)
    else:
        raise AssertionError("a PSG instrument is not an FM patch")


def test_tfi_length_is_checked():
    try:
        fi.load_tfi(bytes(20), "short.tfi")
    except fi.InstrumentFormatError as exc:
        assert "42 bytes" in str(exc) and "short.tfi" in str(exc)
    else:
        raise AssertionError("a truncated .tfi should be rejected")


def test_imported_patch_actually_renders():
    import audio
    import instruments
    from events import End, FMInstrumentSelect, FMNoteOff, FMNoteOn, Wait
    from sequencer import Sequencer

    instrument = fi.load_tfi(_tfi_bytes(alg=4, fb=4, tl=8), "furnace_probe")
    before = set(instruments.BANK)
    instruments.BANK["furnace_probe"] = instrument
    try:
        events = [FMInstrumentSelect(channel=0, instrument="furnace_probe"),
                  FMNoteOn(channel=0, note="A", octave=3), Wait(ticks=96),
                  FMNoteOff(channel=0), Wait(ticks=48), End()]
        buf = Sequencer().render(events)
        assert audio.peak(buf) > 0.01, "an imported patch rendered silence"
    finally:
        for extra in set(instruments.BANK) - before:
            instruments.BANK.pop(extra)


def test_directory_walk_names_by_category_and_reports_failures():
    import os

    with support.TempDir() as directory:
        os.makedirs(os.path.join(directory, "bass"))
        os.makedirs(os.path.join(directory, "keys"))
        with open(os.path.join(directory, "bass", "Fat Bass 1.tfi"), "wb") as fh:
            fh.write(_tfi_bytes())
        with open(os.path.join(directory, "keys", "e piano.dmp"), "wb") as fh:
            fh.write(_dmp_bytes())
        with open(os.path.join(directory, "keys", "broken.tfi"), "wb") as fh:
            fh.write(bytes(10))

        bank, failures = fi.load_directory(directory)

    assert "bass_fat_bass_1" in bank, sorted(bank)
    assert "keys_e_piano" in bank, sorted(bank)
    assert len(failures) == 1 and "broken" in failures[0][0]


def test_silent_patches_are_dropped_not_imported():
    import os

    with support.TempDir() as directory:
        with open(os.path.join(directory, "silent.tfi"), "wb") as fh:
            fh.write(_tfi_bytes(alg=7, tl=127))    # every carrier muted
        bank, failures = fi.load_directory(directory)

    assert bank == {}
    assert failures and "silent" in failures[0][1]


def test_name_collisions_get_distinct_keys():
    import os

    with support.TempDir() as directory:
        with open(os.path.join(directory, "bass 1.tfi"), "wb") as fh:
            fh.write(_tfi_bytes())
        with open(os.path.join(directory, "bass_1.tfi"), "wb") as fh:
            fh.write(_tfi_bytes(alg=5))
        bank, _ = fi.load_directory(directory)

    assert len(bank) == 2, f"both files should survive: {sorted(bank)}"
