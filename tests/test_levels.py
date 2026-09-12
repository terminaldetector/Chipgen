"""Per-voice levels: why a voice is inaudible, answered by measurement.

Every test here exists because the same diagnosis was reached by hand:
the drums own the master, and an uncalibrated bank sits well below the
one it was dropped into. Neither shows up in a render that "worked" —
the file is at full scale, the channels are all writing, and the
instruments are still not there.
"""

import json
import os

import support


def _bank(tmp, tweak=None):
    """A small imported bank on disk, optionally made deliberately quiet."""
    import instruments

    data = []
    for name, source in (("q_bass", "deep_bass"), ("q_lead", "saw_lead")):
        patch = instruments.BANK[source].copy()
        patch.name = name
        patch.trim = 0
        if tweak:
            tweak(patch)
        data.append(instruments.instrument_to_dict(patch))
    path = os.path.join(tmp, "bank.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    return path


def _quieten(patch):
    """Push every carrier well down, the way an uncalibrated import is."""
    for index in patch.carrier_indices():
        patch.operators[index].total_level = 32


SCORE = """bpm 140
lpb 4
inst fm0 {bass}
inst fm1 {lead}
{fader}
cols fm0 fm1 dac
B-2:110  B-3:110  kick
...      ...      hat
E-3:110  E-4:110  snare
...      ...      hat
end
"""


def _measure(score_text):
    import levels
    import tracker
    from sequencer import Sequencer

    events, meta = tracker.loads(score_text)
    seq = Sequencer(ticks_per_second=meta.ticks_per_second)
    return levels.measure(events, seq)


def test_a_voice_is_measured_alone_not_guessed_from_the_mix():
    got = _measure(SCORE.format(bass="deep_bass", lead="saw_lead", fader=""))
    voices = {v.voice for v in got}
    assert voices == {"fm0", "fm1", "dac"}, \
        f"expected the three sounding voices, got {voices}"
    for level in got:
        assert not level.silent, f"{level.voice} measured silent alone"
    # Sorted loudest peak first, because peak is what sets the master.
    peaks = [v.peak for v in got]
    assert peaks == sorted(peaks, reverse=True), \
        "the report should lead with the voice that owns the peak"


def test_the_drums_own_the_peak_even_with_a_calibrated_bank():
    """The finding that the original diagnosis missed.

    Patch calibration was blamed for the drums dominating, and it is
    genuinely part of it — but the built-in bank is fully calibrated and
    the DAC still leads it by several dB on peak while sitting BELOW it
    on RMS. A drum is nearly all transient; mastering normalises peak.
    Calibration narrows this and cannot close it.
    """
    got = {v.voice: v for v in
           _measure(SCORE.format(bass="deep_bass", lead="saw_lead",
                                 fader=""))}
    dac, fm = got["dac"], got["fm0"]
    assert dac.peak_db > fm.peak_db + 3.0, (
        f"the DAC should lead on peak; dac {dac.peak_db:.1f} vs fm0 "
        f"{fm.peak_db:.1f} dBFS")
    assert dac.rms_db < fm.rms_db, (
        f"and sit below it on RMS; dac {dac.rms_db:.1f} vs fm0 "
        f"{fm.rms_db:.1f} dBFS")
    assert dac.crest_db > fm.crest_db + 5.0, (
        f"the whole reason is crest factor: dac {dac.crest_db:.1f} dB vs "
        f"fm0 {fm.crest_db:.1f}")


def test_the_dac_fader_closes_the_gap():
    # There was no channel volume for the DAC at all, so the only way to
    # bring the drums down was per-hit `:level` on every cell.
    loud = {v.voice: v for v in
            _measure(SCORE.format(bass="deep_bass", lead="saw_lead",
                                  fader=""))}
    tamed = {v.voice: v for v in
             _measure(SCORE.format(bass="deep_bass", lead="saw_lead",
                                   fader="vol dac 55"))}
    assert tamed["dac"].peak_db < loud["dac"].peak_db - 4.0, (
        f"`vol dac 55` should bring the drums down; "
        f"{loud['dac'].peak_db:.1f} -> {tamed['dac'].peak_db:.1f} dBFS")
    # And leave the FM alone.
    assert abs(tamed["fm0"].peak_db - loud["fm0"].peak_db) < 0.5, \
        "the DAC fader moved an FM voice"


def test_the_dominance_warning_fires_and_clears():
    import levels

    loud = levels.warnings(
        _measure(SCORE.format(bass="deep_bass", lead="saw_lead", fader="")))
    assert any("vol dac" in w for w in loud), (
        f"the drums dominating should be reported with its remedy; got "
        f"{loud}")

    tamed = levels.warnings(
        _measure(SCORE.format(bass="deep_bass", lead="saw_lead",
                              fader="vol dac 55")))
    assert not any("peaks" in w for w in tamed), \
        f"the dominance warning should clear once the fader is down: {tamed}"


def test_an_uncalibrated_bank_is_reported_against_the_bank_reference():
    """And not against whatever else is in the score.

    Two heuristics were tried and both were wrong. Unused headroom alone
    flags the built-in bank, whose patches carry 4.5-9 dB on purpose.
    Headroom plus "quiet relative to the mix" then MISSES the real case:
    an uncalibrated bank holds every patch back by a similar amount, so
    turning the drums down makes the mix look level while the bank is
    still far under where it should be. Measuring each patch against the
    bank's own reference — the measurement calibration itself makes — is
    the only test that does not depend on the arrangement.
    """
    import instruments
    import levels

    original = dict(instruments.BANK)
    with support.TempDir() as tmp:
        try:
            path = _bank(tmp, tweak=_quieten)
            instruments.load_bank(path)
            score = SCORE.format(bass="q_bass", lead="q_lead",
                                 fader="vol dac 55")   # drums already tamed
            found = levels.warnings(_measure(score))
            assert any("reference level" in w for w in found), (
                f"an uncalibrated bank should be reported even with the "
                f"drums under control; got {found}")
            assert any("--recalibrate" in w for w in found), \
                "the warning should name the remedy"
        finally:
            instruments.BANK.clear()
            instruments.BANK.update(original)
            levels._REFERENCE_CACHE.clear()


def test_the_built_in_bank_reports_no_calibration_problem():
    # The check has to be quiet on a bank that is already levelled, or it
    # is noise. The built-in patches hold 4.5-9 dB of headroom by design.
    import levels

    found = levels.warnings(
        _measure(SCORE.format(bass="deep_bass", lead="saw_lead",
                              fader="vol dac 55")))
    assert not found, \
        f"a calibrated bank with tamed drums should be clean: {found}"


def test_recalibrating_a_bank_on_disk_matches_calibrating_at_import():
    """The route that did not exist: a bank saved with --no-calibrate.

    Calibration only ever ran during import, so the only way back was
    re-importing from the original .vgm — which whoever holds the .json
    may not have.
    """
    import instruments
    import vgm_import

    original = dict(instruments.BANK)
    with support.TempDir() as tmp:
        try:
            path = _bank(tmp, tweak=_quieten)
            before = {d["name"]: d.get("trim", 0)
                      for d in json.load(open(path, encoding="utf-8"))}
            assert set(before.values()) == {0}, \
                "the fixture should start untrimmed"

            vgm_import.recalibrate_file(path)
            after = {d["name"]: d.get("trim", 0)
                     for d in json.load(open(path, encoding="utf-8"))}
            for name, trim in after.items():
                assert trim < 0, (
                    f"{name} measures quiet and should have been trimmed "
                    f"louder; trim is {trim}")
        finally:
            instruments.BANK.clear()
            instruments.BANK.update(original)
            levels_cache_clear()


def levels_cache_clear():
    import levels
    levels._REFERENCE_CACHE.clear()


def test_headroom_accounts_for_the_trim_already_applied():
    # headroom() deliberately ignores trim — calibration uses it as the
    # clamp bound while trim is still zero — so the report has to take
    # the trim off itself. Reporting the raw number kept the warning
    # firing on a bank that had just been recalibrated.
    import instruments
    import levels

    original = dict(instruments.BANK)
    try:
        patch = instruments.BANK["deep_bass"].copy()
        patch.name = "trimmed"
        for index in patch.carrier_indices():
            patch.operators[index].total_level = 24
        patch.trim = 0
        instruments.BANK["trimmed"] = patch

        events = [__import__("events").FMInstrumentSelect(
            channel=0, instrument="trimmed")]
        raw, _ = levels._headroom_db("fm0", events)
        assert abs(raw - 24 * 0.75) < 0.01, \
            f"untrimmed headroom should be 24 steps, got {raw}"

        patch.trim = -10
        boosted, _ = levels._headroom_db("fm0", events)
        assert abs(boosted - 14 * 0.75) < 0.01, (
            f"with trim -10 the remaining headroom is 14 steps, got "
            f"{boosted}")
    finally:
        instruments.BANK.clear()
        instruments.BANK.update(original)


def test_isolation_keeps_a_voices_own_setup_and_the_timing():
    # A level measured without the voice's instrument select, volume or
    # effects is not the level that voice contributes; and dropping the
    # Waits would change the timing of what is left.
    import levels
    import tracker

    events, _ = tracker.loads(
        "bpm 140\nlpb 4\ninst fm0 deep_bass\ninst fm1 saw_lead\n"
        "vol fm0 90\npan fm0 L\ncols fm0 fm1 dac\n"
        "B-2:110  B-3:110  kick\n...      ...      hat\nend\n")
    alone = levels.isolate(events, "fm0")
    kinds = [type(e).__name__ for e in alone]

    assert "FMInstrumentSelect" in kinds and "FMVolume" in kinds \
        and "FMPan" in kinds, f"fm0 lost its own setup: {kinds}"
    assert kinds.count("Wait") == [type(e).__name__
                                   for e in events].count("Wait"), \
        "isolation dropped Waits, which changes the timing"
    assert not any(getattr(e, "channel", None) == 1 for e in alone
                   if type(e).__name__.startswith("FM")), \
        "fm1's events survived fm0's isolation"
    assert not any(type(e).__name__ == "DACSample" for e in alone), \
        "the DAC survived fm0's isolation"
