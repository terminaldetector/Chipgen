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


def test_fast_lfo_drifts_between_direct_render_and_vgm_replay():
    # Documents a real, understood limitation rather than hiding it: VGM
    # waits are quantised to 44100 Hz samples (the format spec fixes that
    # rate), but the engine's own event clock runs at ticks_per_second
    # against a native chip rate near 53267 Hz — neither commensurate with
    # 44100. Round-tripping a Wait through 44100 Hz and back can land the
    # replay one native sample off from the direct render's own count for
    # that gap, even though the SUM of all gaps still adds up to the same
    # total duration. LFO is clocked from native chip samples, not from
    # elapsed wall-clock time, so it has no way to "catch up" — a handful
    # of these one-sample offsets permanently shifts its phase, and each
    # one that lands during the LFO-active portion compounds. A found
    # track (fast LFO enabled for the second half) showed this as a
    # difference that grew steadily worse from the moment the LFO turned
    # on. This test pins that shape of behaviour so it reads as "known and
    # understood" rather than "regression" if it is ever encountered again
    # — and so a future fix has a concrete case to check itself against.
    import numpy as np

    import audio
    from events import (End, FMInstrumentSelect, FMLFO, FMNoteOff, FMNoteOn,
                        FMPan, Wait)
    from sequencer import Sequencer

    events = [FMInstrumentSelect(channel=0, instrument="strings"),
              FMPan(channel=0, left=True, right=True, ams=3, pms=7),
              FMLFO(enable=True, freq=7),      # the fastest rate
              FMNoteOn(channel=0, note="A", octave=3),
              Wait(ticks=192 * 3),
              FMNoteOff(channel=0),
              End()]
    seq = Sequencer()

    with support.TempDir() as directory:
        path = os.path.join(directory, "lfo.vgm")
        direct = np.asarray(seq.render(events, vgm_path=path))
        replayed = np.asarray(vgm_player.render(path))

    n = min(len(direct), len(replayed))
    diff = np.abs(direct[:n] - replayed[:n])

    early = diff[:int(n * 0.2)]
    late = diff[int(n * 0.6):int(n * 0.9)]   # before the note-off transient
    assert late.mean() > early.mean(), \
        "drift should grow over the note's duration, not stay flat"
    # A ceiling, not a target: if this ever drops near zero, the timing
    # round-trip got fixed and this test (and its docstring) should be
    # revisited rather than quietly loosened.
    assert diff.mean() < 0.05, \
        f"drift grew to {diff.mean():.4f} average — much worse than observed"


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


# --------------------------------------------------------------------------
# Instrument extraction
# --------------------------------------------------------------------------
def _probe_vgm(directory, names):
    """Export a VGM that plays each named patch a few times."""
    import events as E
    events = [E.FMInstrumentSelect(channel=i, instrument=n)
              for i, n in enumerate(names)]
    for _ in range(3):
        for channel in range(len(names)):
            events += [E.FMNoteOn(channel=channel, note="A", octave=3),
                       E.Wait(ticks=24), E.FMNoteOff(channel=channel)]
    events.append(E.End())
    path = os.path.join(directory, "probe.vgm")
    Sequencer().export_vgm(events, path)
    return path


def _signature(instrument):
    return (instrument.algorithm, instrument.feedback,
            tuple((o.detune, o.multiple, o.total_level, o.attack_rate,
                   o.decay_rate, o.sustain_rate, o.release_rate,
                   o.sustain_level, o.rate_scaling, o.am_enable, o.ssg_eg)
                  for o in instrument.operators))


def test_extracted_patches_match_the_originals_register_for_register():
    import instruments
    import vgm_import

    names = ["bass", "brass", "e_piano", "organ", "metal_stab"]
    with support.TempDir() as directory:
        patches = vgm_import.extract(_probe_vgm(directory, names), prefix="probe")

    assert len(patches) == len(names), f"expected {len(names)} patches, got {len(patches)}"

    expected = {}
    for name in names:
        source = instruments.BANK[name]
        # The chip receives total_level plus the patch's calibration trim, so
        # that is what a VGM records and what comes back out.
        applied = source.copy()
        for i in source.carrier_indices():
            applied.operators[i].total_level = max(
                0, min(127, applied.operators[i].total_level + source.trim))
        expected[_signature(applied)] = name

    for patch in patches:
        assert _signature(patch.instrument) in expected, \
            f"{patch.instrument.name} does not match any source patch"


def test_repeated_notes_collapse_into_one_patch_with_a_use_count():
    import vgm_import
    with support.TempDir() as directory:
        patches = vgm_import.extract(_probe_vgm(directory, ["bass", "organ"]))
    assert len(patches) == 2, "the same patch keyed repeatedly must dedupe"
    assert all(p.uses == 3 for p in patches), [p.uses for p in patches]
    assert all(len(p.channels) == 1 for p in patches)


def test_silent_channels_are_not_imported():
    # Drivers park unused channels with every carrier fully attenuated. A
    # bank full of those looks like choice and delivers nothing.
    import events as E
    import instruments
    import opn2
    import vgm_import

    silent = opn2.FMInstrument(
        7, 0, [opn2.Operator(total_level=127) for _ in range(4)], "silent_probe")
    instruments.BANK["silent_probe"] = silent
    try:
        with support.TempDir() as directory:
            events = [E.FMInstrumentSelect(channel=0, instrument="silent_probe"),
                      E.FMNoteOn(channel=0, note="A", octave=3), E.Wait(ticks=24),
                      E.FMInstrumentSelect(channel=1, instrument="organ"),
                      E.FMNoteOn(channel=1, note="A", octave=3), E.Wait(ticks=24),
                      E.End()]
            path = os.path.join(directory, "silent.vgm")
            Sequencer().export_vgm(events, path)
            patches = vgm_import.extract(path)
    finally:
        instruments.BANK.pop("silent_probe", None)

    assert len(patches) == 1, "the muted channel should not have produced a patch"


def test_imported_bank_saves_loads_and_is_playable():
    import chipgen
    import instruments
    import vgm_import

    with support.TempDir() as directory:
        source = _probe_vgm(directory, ["bass", "brass", "organ"])
        patches = vgm_import.extract(source, prefix="probe")
        bank_path = os.path.join(directory, "bank.json")
        vgm_import.save_bank(patches, bank_path)

        before = set(instruments.BANK)
        loaded = instruments.load_bank(bank_path)
        try:
            assert set(loaded) == {p.instrument.name for p in patches}
            name = sorted(loaded)[0]
            result = chipgen.compose(
                f"bpm 150\nlpb 4\ninst fm0 {name}\ncols fm0\n\nA-3\n...\n===\n")
            assert result.peak > 0.01, "an imported patch rendered silence"
        finally:
            for extra in set(instruments.BANK) - before:
                instruments.BANK.pop(extra)


def test_imported_patches_are_levelled_against_the_built_in_bank():
    import calibrate_bank
    import instruments
    import vgm_import
    from sequencer import Sequencer as Seq

    with support.TempDir() as directory:
        source = _probe_vgm(directory, ["bass", "square_lead", "orch_hit"])
        patches = vgm_import.extract(source, prefix="probe")
        bank = vgm_import.to_bank(patches, calibrate=True)

        seq = Seq()
        reference = calibrate_bank.measure("organ", seq)
        before = set(instruments.BANK)
        instruments.BANK.update(bank)
        try:
            for name in bank:
                level = calibrate_bank.measure(name, seq)
                assert support.db_between(level, reference) < 4.0, \
                    f"{name} came in {support.db_between(level, reference):.1f} dB off"
        finally:
            for extra in set(instruments.BANK) - before:
                instruments.BANK.pop(extra)


def test_player_and_sequencer_share_one_mixer():
    # These drifted once already: the sequencer's PSG gain and DC blocking
    # changed and a replayed export came out 3.5 dB away from its render.
    import inspect
    import mixer
    import vgm_player
    from sequencer import Sequencer as Seq

    assert Seq().psg_gain == mixer.DEFAULT_PSG_GAIN
    defaults = inspect.signature(vgm_player.render).parameters
    assert defaults["psg_gain"].default == mixer.DEFAULT_PSG_GAIN
    assert defaults["dc_block"].default is True


def test_replay_alignment_is_bounded_and_does_not_accumulate():
    # The companion to the LFO-drift test above, pinning the property that
    # makes that drift tolerable rather than fatal.
    #
    # Measured on a real export: the FIRST note-on lands sample-exact in
    # the replay, and the second lands 2 samples early — the 44100 Hz wait
    # quantisation, once there is a wait in front of a write. The question
    # that decides whether a .vgm is usable is not whether that offset
    # exists but whether it GROWS: 2 samples is 45us and inaudible, 2
    # samples per event over a 60s track would be seconds of skew.
    #
    # It does not grow, because _flush_wait carries the sub-sample
    # remainder instead of dropping it. This test asserts that directly:
    # the alignment at the end of a long track must be no worse than at
    # the beginning. It fails loudly if anyone makes wait encoding lossy.
    import math

    from events import (End, FMInstrumentSelect, FMNoteOn, FMPan, Wait)
    from sequencer import Sequencer

    events = [FMInstrumentSelect(channel=0, instrument="square_lead"),
              FMPan(channel=0, left=True, right=True)]
    for _ in range(160):                      # 160 note-ons over ~10s
        events += [FMNoteOn(channel=0, note="A", octave=4), Wait(ticks=12)]
    events.append(End())

    seq = Sequencer()
    with support.TempDir() as directory:
        path = os.path.join(directory, "align.vgm")
        direct = seq.render(events, vgm_path=path)
        replayed = vgm_player.render(path)

    def best_lag(start, stop):
        x = [direct[i][0] + direct[i][1] for i in range(start, stop)]
        dx = math.sqrt(sum(v * v for v in x))
        best = (-2.0, 0)
        for lag in range(-64, 65):
            y = [replayed[i + lag][0] + replayed[i + lag][1]
                 for i in range(start, stop)]
            dy = math.sqrt(sum(v * v for v in y))
            if dx == 0 or dy == 0:
                continue
            c = sum(x[i] * y[i] for i in range(len(x))) / (dx * dy)
            if c > best[0]:
                best = (c, lag)
        return best

    rate = seq.target_rate
    early_corr, early_lag = best_lag(rate // 2, rate)          # ~0.5-1.0s
    late_corr, late_lag = best_lag(rate * 8, rate * 8 + rate // 2)  # ~8-8.5s

    assert early_corr > 0.95, f"replay diverges early: {early_corr:.3f}"
    assert late_corr > 0.95, f"replay diverges late: {late_corr:.3f}"
    # The whole point: eight seconds and 160 register writes later, the
    # replay is still aligned to within a couple of samples of where it
    # started. Accumulating error would put `late_lag` far from `early_lag`.
    assert abs(late_lag - early_lag) <= 4, \
        f"alignment drifted from {early_lag} to {late_lag} samples"
