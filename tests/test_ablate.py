"""Taking one mechanism out of a copy of a module (ablate.py) and measuring
A against B where the song plays it (abtest.py).

The modules are built here: an IT from the notation, and an S3M by hand
(this project writes no S3M; the builder below is the format's own layout,
small). Every change must touch only the bytes it reports, refuse what it
cannot do in place, and leave the original bytes alone.
"""

import hashlib
import os
import struct
import tempfile

import support
from schism import ablate, abtest, corpus, it_read, it_write, notation

_IT = """title ablate
tempo 125
speed 6
channels 2
inst 1 name=Tone wave=saw oct=4-6 nna=cut cutoff=90 res=40 venv=0:64,10:40,30:20s,60:0 penv=0:0,20:16 fenv=0:64,40:20,carry
inst 2 name=Lift wave=saw oct=4-6 ienv=0:0,6:12l,12:0l
pattern a rows 32
C-5 01 ... ... | C-5 02 ... ...
rest 3
D-5 01 ... ... | ... .. ... ...
rest 27
end
order a
"""


def _it():
    module, _ = notation.compile_text(_IT)
    return it_write.build(module)


def _changed(before: bytes, after: bytes):
    return [i for i, (a, b) in enumerate(zip(before, after)) if a != b]


def test_an_envelope_is_taken_out_by_its_flag_bits_and_nothing_else():
    data = _it()
    original = bytes(data)
    p = ablate.it_envelope(data, 1, "volume", enabled=False)
    assert _changed(data, p.bytes()) == [c["offset"] for c in p.record()]
    back = it_read.read(p.bytes(), load_data=False).instruments[0]
    assert back.volume_envelope.enabled is False
    assert back.volume_envelope.nodes == it_read.read(
        data, load_data=False).instruments[0].volume_envelope.nodes
    # a filter envelope switched off loses its filter mark too: Schism
    # Tracker would still lower the filter for a disabled one that keeps it
    p = ablate.it_envelope(data, 1, "filter", enabled=False)
    slot = p.bytes()[ablate.it_instrument_offset(data, 1) + 0x1D4]
    assert slot & (ablate.ENV_ON | ablate.ENV_FILTER) == 0
    assert slot & ablate.ENV_CARRY
    # carry, written by the notation's `carry`, comes out alone
    p = ablate.it_envelope(data, 1, "filter", carry=False)
    env = it_read.read(p.bytes(), load_data=False).instruments[0].pitch_envelope
    assert env.enabled and env.filter and not env.carry
    # the first nodes kept: the node count, one byte
    p = ablate.it_envelope(data, 1, "volume", nodes=3)
    assert len(p.record()) == 1 and p.record()[0]["new"] == 3
    assert data == original


def test_what_cannot_be_done_in_place_is_refused_with_the_reason():
    data = _it()
    for call, words in (
            (lambda: ablate.it_envelope(data, 1, "pitch", enabled=False),
             "filter envelope, not a pitch"),
            (lambda: ablate.it_envelope(data, 2, "filter", enabled=False),
             "pitch envelope, not a filter"),
            (lambda: ablate.it_envelope(data, 1, "volume", nodes=2,
                                        sustain=None) and
             ablate.it_envelope(data, 1, "volume", nodes=1), "sustain loop"),
            (lambda: ablate.it_envelope(data, 1, "volume", carry=False),
             "not set"),
            (lambda: ablate.it_envelope(data, 1, "volume", enabled=True),
             "only False"),
            (lambda: ablate.it_envelope(data, 9, "volume", enabled=False),
             "the module has 2"),
            (lambda: ablate.apply(data, ["inst 1 venv sideways"]), "fenv"),
            (lambda: ablate.solo(b"\0" * 64, [1]), "IT and S3M")):
        try:
            call()
        except ablate.AblateError as error:
            assert words in str(error), (words, str(error))
        else:
            raise AssertionError(f"not refused: {words}")


def test_solo_and_end_after_are_header_bytes():
    data = _it()
    p = ablate.solo(data, [2])
    assert p.bytes()[0x40] & 0x80 and not p.bytes()[0x41] & 0x80
    assert all(c["offset"] in range(0x40, 0x80) for c in p.record())
    q = ablate.end_after(data, 0)
    # one order: nothing after it to end
    assert q.record() == []


# -- S3M ---------------------------------------------------------------------
def _s3m(speed_on_channel_2=True):
    """Two channels, one looped 8-bit sample, two patterns. Channel 2 sets
    the speed to 3 on the first row (the only speed command); channel 1
    plays a note, slides it (D04), cuts it at row 8 and strikes again at
    row 32, out of silence."""
    title = b"ablate".ljust(28, b"\0")
    orders = [0, 1, 0xFF, 0xFF]
    n_ins, n_pat = 1, 2
    header = bytearray(0x60)
    header[0:28] = title
    header[0x1C], header[0x1D] = 0x1A, 16
    struct.pack_into("<HHHHHH", header, 0x20, len(orders), n_ins, n_pat, 0,
                     0x1320, 2)
    header[0x2C:0x30] = b"SCRM"
    header[0x30:0x36] = bytes([64, 6, 125, 0xB0, 0, 0])
    header[0x40:0x60] = bytes([0, 8] + [255] * 30)
    table_at = 0x60 + len(orders)
    first = (table_at + 2 * (n_ins + n_pat) + 15) // 16 * 16
    ins_at = first
    smp_at = ins_at + 0x50
    wave = bytes(128 + int(100 * ((i % 32) / 16.0 - 1.0)) for i in range(256))
    pat_at = (smp_at + len(wave) + 15) // 16 * 16

    def pattern(rows):
        out = bytearray()
        for r in range(64):
            for ch, cell in sorted(rows.get(r, {}).items()):
                note, ins, vol, cmd = cell
                what = ch
                body = bytearray()
                if note is not None:
                    what |= 32
                    body += bytes([note, ins])
                if vol is not None:
                    what |= 64
                    body += bytes([vol])
                if cmd is not None:
                    what |= 128
                    body += bytes(cmd)
                out += bytes([what]) + body
            out.append(0)
        return struct.pack("<H", len(out) + 2) + out

    p0 = {0: {0: (0x40, 1, 64, None)}, 1: {0: (None, 0, None, (4, 0x04))},
          8: {0: (254, 0, None, None)}, 32: {0: (0x42, 1, None, None)}}
    if speed_on_channel_2:
        p0[0][1] = (None, 0, None, (1, 3))
    pats = [pattern(p0), pattern({0: {0: (0x44, 1, 48, None)}})]
    blob = bytearray(header) + bytes(orders)
    blob += bytes(first - len(blob)) if False else b""
    blob = bytearray(blob)
    blob += bytes(2 * (n_ins + n_pat))
    blob += bytes(first - len(blob))
    ins = bytearray(0x50)
    ins[0] = 1
    ins[0x0D] = 0
    struct.pack_into("<H", ins, 0x0E, smp_at // 16)
    struct.pack_into("<III", ins, 0x10, len(wave), 0, len(wave))
    ins[0x1C], ins[0x1F] = 64, 1
    struct.pack_into("<I", ins, 0x20, 8363)
    ins[0x4C:0x50] = b"SCRS"
    blob += ins + wave
    blob += bytes(pat_at - len(blob))
    offsets = []
    for pat in pats:
        offsets.append(len(blob) // 16)
        blob += pat
        blob += bytes((16 - len(blob) % 16) % 16)
    struct.pack_into("<H", blob, table_at, ins_at // 16)
    for i, off in enumerate(offsets):
        struct.pack_into("<H", blob, table_at + 2 * (n_ins + i), off)
    return bytes(blob)


def test_an_s3m_cell_is_changed_where_it_is_stored():
    support.need_openmpt()
    data = _s3m()
    cells = ablate.s3m_cells(data)
    assert set(cells) >= {(0, 0, 1), (0, 0, 2), (0, 1, 1), (0, 32, 1),
                          (1, 0, 1)}
    with corpus.Module(data) as m:
        assert m.native(0, 1, 0).endswith("D04")
    p = ablate.apply(data, ["cell 0 1 1 effect none",
                            "cell 0 32 1 instrument 1",
                            "cell 0 0 1 volume 20"])
    assert sorted(_changed(data, p.bytes())) == sorted(
        c["offset"] for c in p.record())
    with corpus.Module(p.bytes()) as m:
        assert m.native(0, 1, 0) == "... .. .. ...", m.native(0, 1, 0)
        assert m.normalized(0, 0, 0)["volume"] == 20
    # K -> D keeps the parameter: one byte
    q = ablate.s3m_cell(data, 0, 1, 1, command="K")
    assert len(q.record()) == 1 and q.record()[0]["new"] == 11
    for call, words in (
            (lambda: ablate.s3m_cell(data, 0, 2, 1, command="none"),
             "stores nothing"),
            (lambda: ablate.s3m_cell(data, 0, 32, 1, command="none"),
             "stores no command"),
            (lambda: ablate.s3m_cell(data, 0, 1, 1, instrument=2),
             "stores no instrument"),
            (lambda: ablate.apply(data, ["cell 0 0 1 volume none"]),
             "cannot be taken away"),
            (lambda: ablate.apply(_it(), ["cell 0 1 1 effect none"]),
             "not an S3M")):
        try:
            call()
        except ablate.AblateError as error:
            assert words in str(error), (words, str(error))
        else:
            raise AssertionError(f"not refused: {words}")


def _onset(left, right, after_s):
    """The first ms after `after_s` where the level jumps 10 dB."""
    import math
    mono = [(a + b) * 0.5 for a, b in zip(left, right)]
    step = abtest.RATE // 1000
    levels = [math.sqrt(sum(v * v for v in mono[i:i + step]) / step) + 1e-9
              for i in range(0, len(mono) - step, step)]
    start = int(after_s * 1000)
    for i in range(start + 5, len(levels)):
        if 20 * math.log10(levels[i] / min(levels[i - 5:i])) >= 10:
            return i / 1000.0
    return None


def test_isolating_an_s3m_channel_keeps_the_other_channels_commands():
    """libopenmpt plays a muted S3M channel as Scream Tracker 3 did: not at
    all, its speed command included, so the song slows down (the second
    strike at row 32 comes at 3.84 s instead of 1.92 s). abtest silences
    the other channels by volume instead, and the strike stays where the
    song puts it; Schism Tracker still plays a disabled channel's commands,
    so its copy with the disabled bit is right as it is."""
    support.need_openmpt()
    from schism import openmpt, schismtracker
    data = _s3m()
    with corpus.Module(data) as m:
        assert abs(corpus.first_time(corpus.trace(m, rate=abtest.RATE)["rows"],
                                     0, 32) - 1.92) < 0.01
    muted = ablate.solo(data, [1]).bytes()
    with corpus.Module(muted) as m:
        late = corpus.first_time(corpus.trace(m, rate=abtest.RATE)["rows"],
                                 0, 32)
    assert abs(late - 3.84) < 0.01, ("libopenmpt now plays a muted S3M "
                                     "channel's commands", late)
    left, right = abtest.render_openmpt(data, 0.0, 2.5, keep=[1])
    assert abs(_onset(left, right, 1.0) - 1.92) < 0.01
    if schismtracker.available():
        frames = schismtracker.render(muted, abtest.RATE, suffix=".s3m")
        left, right = list(frames[0::2]), list(frames[1::2])
        assert abs(_onset(left, right, 1.0) - 1.92) < 0.015


def test_an_ab_run_reports_the_bytes_the_readings_and_an_untouched_original():
    """The carry of instrument 1's filter envelope, taken out: the report
    names the one byte, both copies' hashes, and the original's hash after
    the run; the isolated renders differ, and the file on disk is the
    same."""
    support.need_openmpt()
    data = _it()
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "a.it")
        with open(path, "wb") as handle:
            handle.write(data)
        stamp = os.stat(path).st_mtime_ns
        with open(path, "rb") as handle:
            blob = handle.read()
        report = abtest.run(blob, 0, 0, 1.0, ["inst 1 fenv carry off"], [1],
                            engines=("openmpt",))
        assert os.stat(path).st_mtime_ns == stamp
        assert open(path, "rb").read() == data
    assert report["original_untouched"]
    assert report["hashes"]["original"] == hashlib.sha256(data).hexdigest()
    assert len(report["change"]["bytes"]) == 1
    entry = report["engines"]["openmpt"]
    # B differs (the second note sweeps the filter again), A is not B
    assert entry["null_isolated_db"] > -40.0, entry["null_isolated_db"]
    assert set(entry["summary"]["A"]) >= {"level_db", "strikes",
                                          "centroid_mean_hz"}
    assert report["address"]["tick_ms"] == 20.0
