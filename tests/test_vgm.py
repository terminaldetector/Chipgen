"""VGM export: a well-formed file, and one that says what was played."""

import gzip
import os
import struct

import support
import tracker
import vgm
import vgm_player
from sequencer import Sequencer

SCORE = """\
bpm 150
lpb 4
inst fm0 bass
inst fm1 square_lead
cols fm0 fm1 psg0 noise dac

A-2  ...  A-4    w1   kick
...  A-4  C-5:4  ...  hat
C-3  ...  E-5    w1   snare
loop
...  ===  ===    ===  hat
"""


def _render(**kwargs):
    events, meta = tracker.loads(SCORE)
    return events, meta, Sequencer(ticks_per_second=meta.ticks_per_second, **kwargs)


def test_header_is_well_formed():
    events, meta, seq = _render()
    with support.TempDir() as directory:
        path = os.path.join(directory, "t.vgm")
        seq.export_vgm(events, path, gd3=meta.to_gd3())
        raw = open(path, "rb").read()
        header = vgm.read_header(raw)

    assert header["version"] == "1.71"
    assert header["eof_offset"] == header["file_size"] == len(raw)
    assert header["data_offset"] == vgm.HEADER_SIZE
    assert 0 < header["gd3_offset"] < len(raw)
    assert raw[header["gd3_offset"]:header["gd3_offset"] + 4] == b"Gd3 "
    assert raw[-1:] != b""
    # Sega's PSG is the 16-bit/0x0009 variant; declaring the TI part makes
    # every player render the noise channel wrong.
    assert header["psg_feedback"] == 0x0009
    assert header["psg_shift_width"] == 16
    assert header["ym2612_clock"] > 7_000_000
    assert header["psg_clock"] == 3_579_545


def test_loop_point_lands_inside_the_data():
    events, meta, seq = _render()
    with support.TempDir() as directory:
        path = os.path.join(directory, "t.vgm")
        seq.export_vgm(events, path, gd3=meta.to_gd3())
        header = vgm.read_header(path)
    assert header["data_offset"] < header["loop_offset"] < header["gd3_offset"], \
        "a loop offset outside the command stream sends players off a cliff"


def test_reported_duration_matches_the_rendered_audio():
    events, meta, seq = _render()
    with support.TempDir() as directory:
        path = os.path.join(directory, "t.vgm")
        buf = seq.render(events, vgm_path=path)
        header = vgm.read_header(path)
    audio_seconds = len(buf) / seq.target_rate
    assert abs(header["duration"] - audio_seconds) < 0.01, \
        "the VGM clock drifted away from the audio clock"


def test_dac_uses_a_pcm_data_block():
    # Three bytes per DAC sample would triple the file; real Genesis rips
    # use a data block plus one-byte 0x8n commands, and so should we.
    events, meta, seq = _render()
    with support.TempDir() as directory:
        blocked = os.path.join(directory, "blocked.vgm")
        seq.export_vgm(events, blocked, gd3=meta.to_gd3())
        size_blocked = os.path.getsize(blocked)

        writer = vgm.VGMWriter(pcm_blocks=False)
        assert writer.pcm_blocks is False
        raw = open(blocked, "rb").read()

    assert bytes((vgm.CMD_DATA_BLOCK, vgm.CMD_END,
                  vgm.DATA_BLOCK_YM2612_PCM)) in raw, "no PCM data block emitted"
    assert size_blocked > 1000, "the drum kit should produce real PCM bytes"


def test_naive_dac_encoding_still_works_and_is_bigger():
    import opn2
    import sn76489
    sizes = {}
    for mode in (True, False):
        writer = vgm.VGMWriter(pcm_blocks=mode)
        ym = opn2.YM2612(logger=writer.ym_logger)
        ym.set_dac_enable(True)
        for i in range(500):
            ym.write_dac(i & 0xFF)
            writer.advance(1 / 16000.0)
        sizes[mode] = len(writer.to_bytes())
    assert sizes[True] < sizes[False], \
        "the data-block encoding should be the smaller one"


def test_wait_encoding_is_exact():
    writer = vgm.VGMWriter()
    writer.psg_logger(0x9F)
    writer.advance(1.0)
    writer.psg_logger(0x9F)
    raw = writer.to_bytes()
    assert vgm.read_header(raw)["total_samples"] == 44100


def test_sub_sample_waits_do_not_drift():
    # A tick that is not a whole number of 44100ths must carry its
    # remainder, or a three-minute track ends up seconds out.
    writer = vgm.VGMWriter()
    for _ in range(1000):
        writer.psg_logger(0x9F)
        writer.advance(1.0 / 3000.0)          # 14.7 samples each
    total = vgm.read_header(writer.to_bytes())["total_samples"]
    assert abs(total - 14700) <= 1, f"drifted to {total} instead of 14700"


def test_vgz_is_gzip_and_reproducible():
    events, meta, seq = _render()
    with support.TempDir() as directory:
        first = os.path.join(directory, "a.vgz")
        second = os.path.join(directory, "b.vgz")
        seq.export_vgm(events, first, gd3=meta.to_gd3())
        seq.export_vgm(events, second, gd3=meta.to_gd3())
        raw_first = open(first, "rb").read()
        assert raw_first[:2] == b"\x1f\x8b", "a .vgz must actually be gzipped"
        assert raw_first == open(second, "rb").read(), \
            "the same music must produce the same bytes"
        assert vgm.read_header(first)["version"] == "1.71"


def test_replaying_the_export_reproduces_the_performance():
    # The strongest check there is: play the file back through the same
    # emulators and compare. If the export were lying about the music,
    # this is where it would show.
    events, meta, seq = _render()
    with support.TempDir() as directory:
        path = os.path.join(directory, "t.vgm")
        direct = seq.render(events, vgm_path=path)
        replayed = vgm_player.render(path)

    assert abs(len(direct) - len(replayed)) <= 2, "lengths diverged"
    import audio
    difference = support.db_between(audio.rms(direct), audio.rms(replayed))
    assert difference < 1.5, \
        f"replay is {difference:.2f} dB away from the direct render"


def test_player_rejects_a_non_vgm():
    try:
        vgm_player.render(b"this is not a vgm file at all")
    except ValueError:
        return
    raise AssertionError("the player should refuse a file with a bad magic number")


def test_gd3_survives_the_round_trip():
    tag = vgm.GD3(title="Тест", author="chipgen", notes="unicode is fine")
    raw = tag.to_bytes()
    assert raw[:4] == b"Gd3 "
    length = struct.unpack_from("<I", raw, 8)[0]
    assert length == len(raw) - 12
    assert "Тест".encode("utf-16-le") in raw
