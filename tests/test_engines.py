"""Two players, one module: libopenmpt and Schism Tracker itself.

The writer and the reader in this project were written together, so they
can agree and both be wrong; libopenmpt is the first outside witness and
Schism Tracker, the program the project is named for, is the second. These
tests play every demo through both and hold them to the same length, the
same loudness through the song, and the same stereo balance.

They skip unless both are installed (apt install libopenmpt0 schism).
`SCHISM_ENGINE=schism python3 tests/run_tests.py` runs the pitch, envelope
and effect measurements in test_audio.py against Schism instead.
"""

import glob
import math
import os

import support
from schism import it_write, notation, openmpt, schismtracker

DEMOS = os.path.join(support.ROOT, "demos")
SLICES = 8


def _need():
    support.need_openmpt()
    if not schismtracker.available():
        support.skip("Schism Tracker is not installed (apt install schism)")


def _both(path):
    module, report = notation.compile_file(path)
    blob = it_write.build(module)
    with openmpt.Song(blob) as song:
        theirs = song.render(44100)
    return theirs, schismtracker.render(blob, 44100), report


def _rms(values):
    return math.sqrt(sum(v * v for v in values) / max(1, len(values)))


def _db(a, b):
    return 20.0 * math.log10(max(a, 1e-9) / max(b, 1e-9))


def test_both_engines_play_every_demo_for_the_length_the_score_adds_up_to():
    _need()
    for path in sorted(glob.glob(os.path.join(DEMOS, "*.sch"))):
        mpt, sch, report = _both(path)
        schism_seconds = len(sch) / 2 / 44100
        mpt_seconds = len(mpt) / 2 / 44100
        # Schism stops on the last row; libopenmpt lets the last note ring
        assert abs(schism_seconds - report.seconds) < 0.002 * report.seconds, \
            (path, schism_seconds, report.seconds)
        assert 0 <= mpt_seconds - schism_seconds < 0.5, \
            (path, mpt_seconds, schism_seconds)


def test_both_engines_play_every_demo_at_the_same_loudness_all_the_way_through():
    _need()
    for path in sorted(glob.glob(os.path.join(DEMOS, "*.sch"))):
        mpt, sch, _ = _both(path)
        n = min(len(mpt), len(sch)) // 2 * 2
        step = 5
        size = n // SLICES // 2 * 2
        for s in range(SLICES):
            a = mpt[s * size:(s + 1) * size:step]
            b = sch[s * size:(s + 1) * size:step]
            assert abs(_db(_rms(a), _rms(b))) < 1.0, (path, s)
        assert abs(_db(openmpt.peak(mpt), openmpt.peak(sch))) < 1.5, path
        left = _db(_rms(sch[0:n:10]), _rms(mpt[0:n:10]))
        right = _db(_rms(sch[1:n:10]), _rms(mpt[1:n:10]))
        assert abs(left - right) < 0.6, (path, left, right)   # same balance


def test_schism_renders_at_the_rate_it_is_asked_for():
    """The disk writer's rate is its own config section; with only the
    audio one it wrote 44100 Hz whatever was asked, and a caller that read
    the frames as 48000 heard the note 1.5 semitones sharp and short."""
    _need()
    import audio_probe as P
    text = P.score("A-4 01 v64 ...\n", tempo=60,
                   inst="inst 1 wave=pulse duty=0.25 oct=1-7 fade=200")
    module, _ = notation.compile_text(text)
    blob = it_write.build(module)
    lengths = {}
    for rate in (22050, 48000):
        mono = P.mono(schismtracker.render(blob, rate))
        lengths[rate] = len(mono) / rate
        sustain = mono[int(0.5 * rate):int(2.5 * rate)]
        crossings = [i for i in range(1, len(sustain))
                     if sustain[i - 1] < 0.0 <= sustain[i]]
        hz = (len(crossings) - 1) * rate / (crossings[-1] - crossings[0])
        assert abs(P.cents(hz, 440.0)) < 3.0, (rate, hz)
    # the same song is the same length at either rate
    assert abs(lengths[22050] - lengths[48000]) < 0.05, lengths


def test_a_sustained_note_is_the_same_pitch_in_both_engines():
    _need()
    import audio_probe as P
    text = P.score("A-4 01 v64 ...\n", tempo=60,
                   inst="inst 1 wave=pulse duty=0.25 oct=1-7 fade=200")
    module, _ = notation.compile_text(text)
    blob = it_write.build(module)
    with openmpt.Song(blob) as song:
        a = P.mono(song.render(P.SR, 3.0))
    b = P.mono(schismtracker.render(blob, P.SR)[:P.SR * 6])
    fa = P.frequency(P.window(a, 0.5, 2.5))
    fb = P.frequency(P.window(b, 0.5, 2.5))
    assert abs(P.cents(fa, fb)) < 0.5, (fa, fb)       # half a cent
    assert abs(P.cents(fa, 440.0)) < 1.5
