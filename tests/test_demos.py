"""The shipped demos: they compile clean, libopenmpt reads them as written,
every channel is audible, and nothing clips."""

import glob
import os

import support
from schism import it_write, levels, notation, openmpt, render, verify

DEMOS = os.path.join(support.ROOT, "demos")


def _scores():
    paths = sorted(glob.glob(os.path.join(DEMOS, "*.sch")))
    assert paths, "no demos found"
    return paths


def test_every_demo_compiles_with_no_warnings():
    for path in _scores():
        module, report = notation.compile_file(path)
        assert report.warnings == (), (path, report.warnings)
        assert report.seconds > 30 and report.channels >= 8


def test_every_demo_is_synthetic_and_says_so():
    for path in _scores():
        module, _ = notation.compile_file(path)
        assert "Synthetic demo" in module.message, path
        assert all(not s.compressed for s in module.samples)


def test_libopenmpt_reads_every_demo_exactly_as_written():
    support.need_openmpt()
    for path in _scores():
        module, report = notation.compile_file(path)
        blob = it_write.build(module)
        assert verify.compare(module, blob) == [], path
        with openmpt.Song(blob) as song:
            # libopenmpt rounds a tick to whole samples (as Impulse Tracker
            # does), so its length is off from the arithmetic by up to
            # tempo/120000 of the song: 0.1% at the 118 BPM of Glass Engine
            assert abs(song.duration - report.seconds) <= \
                0.002 * report.seconds, (path, song.duration, report.seconds)
            assert song.channels == report.channels


def test_every_demo_is_loud_enough_to_enjoy_and_not_so_loud_it_clips():
    support.need_openmpt()
    for path in _scores():
        module, _ = notation.compile_file(path)
        with openmpt.Song(it_write.build(module)) as song:
            frames = song.render(44100)       # the rate the MP3s are made at
        peak = openmpt.peak(frames)
        assert 0.8 < peak < 0.95, (path, peak)


def test_every_channel_of_every_demo_is_audible():
    support.need_openmpt()
    for path in _scores():
        module, _ = notation.compile_file(path)
        quiet = [r["channel"] for r in levels.voice_levels(module)
                 if not r["audible"]]
        assert quiet == [], (path, quiet)
