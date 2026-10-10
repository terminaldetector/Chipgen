"""Instrument programs (synthesis/program.py), the phase readings
(synthesis/phases.py) and the bounded improve loop (synthesis/improve.py).

The critical cases only: what a program asks for against what the chip is
told (and every loss said out loud), the state a note leaves behind for the
next one, loop / release / retrigger and legato, a program surviving the bank
file, the backend's limits refused by name, the rate a render is written at,
and a run that stops on its budget and can be undone exactly.
"""

import copy
import json
import math
import os
import tempfile

import support  # noqa: F401  (puts python/ on the path)


class _Isolated:
    """Whatever a test installs into the engine's banks is gone afterwards."""

    def __enter__(self):
        import instruments
        from synthesis import nes_driver, placement, registry
        self.saved = [(instruments.BANK, dict(instruments.BANK)),
                      (instruments.CHARACTER, dict(instruments.CHARACTER)),
                      (nes_driver.BANK, dict(nes_driver.BANK)),
                      (registry.FORGE, dict(registry.FORGE))]
        self.placement = placement
        return self

    def __exit__(self, *exc):
        for target, snapshot in self.saved:
            target.clear()
            target.update(snapshot)
        self.placement.LAST = self.placement.Report()
        return False


_STARTER = None


def _starter():
    global _STARTER
    from synthesis import bank as B
    if _STARTER is None:
        _STARTER = B.Bank.load(os.path.join(
            support.ROOT, "python", "synthesis", "banks", "starter.bank.json"))
    return _STARTER


def _install(name, base, program):
    from synthesis import program as P
    from synthesis.backends import get_backend
    entry = _starter().get(base)
    compiled = P.attach(copy.deepcopy(entry.compiled), program)
    compiled.name = name
    get_backend(entry.backend).install(P.prepare(compiled))
    return compiled


def _timeline(events):
    import events as E
    now, out = 0, []
    for ev in events:
        if isinstance(ev, E.Wait):
            now += ev.ticks
        else:
            out.append((now, ev))
    return out


# -- the program itself ------------------------------------------------------
def test_a_program_names_the_field_it_cannot_use():
    from synthesis import program as P
    for data, wanted in (
            ({"engine": "ym2612", "tracks": [{"target": "warmth",
                                              "points": [[0, 1]]}]},
             "mod.tl"),
            ({"engine": "ym2612", "tracks": [{"target": "mod.tl",
                                              "points": [[10, 1], [5, 2]]}]},
             "rise"),
            ({"engine": "ym2612", "tracks": [{"target": "fb",
                                              "points": [[0, 9]]}]},
             "0..7"),
            ({"engine": "nes", "articulation": {"mode": "legato"}},
             "not available on the NES"),
            ({"engine": "ym2612", "settings": {"car.rr": 40}}, "0..15"),
            ({"engine": "ym2612", "articulation": {"glide_ms": 30},
              "tracks": [{"target": "pitch", "points": [[0, 10]]}]},
             "keep one"),
            ({"engine": "opl2"}, "ym2612"),
            ({"engine": "ym2612", "warmth": 1}, "unknown field")):
        try:
            P.parse(data)
        except P.ProgramError as error:
            assert wanted in str(error), (data, str(error))
        else:
            raise AssertionError(f"{data} was accepted")


def test_loop_release_and_hold_follow_the_tracker_rules():
    from synthesis import program as P
    # as in an IT envelope, the last held point is where the loop ends: the
    # loop runs from point 1 (100 ms) to point 3 (300 ms) and jumps back
    track = P.Track("mod.tl", [(0, 0), (100, 10), (200, 20), (300, 20),
                               (400, 30), (500, 0)], "ms", "step", loop=1,
                    release=4)
    held = [P.value_at(track, t / 1000.0, None) for t in (0, 150, 250, 350,
                                                         450, 550)]
    assert held == [0, 10, 20, 10, 20, 10], held
    # key-up at 0.5 s: from the release point, timed from the key-up
    after = [P.value_at(track, t, 0.5) for t in (0.5, 0.55, 0.61)]
    assert after == [30, 30, 0], after
    # no release point: the track runs on through the release as if held
    plain = P.Track("mod.tl", [(0, 0), (100, 10)], "ms", "linear")
    assert P.value_at(plain, 0.05, 0.02) == 5.0
    assert P.value_at(plain, 5.0, 0.02) == 10


def test_what_the_chip_is_told_and_what_was_lost_on_the_way():
    from synthesis import program as P
    patch = _starter().get("fm_punch_bass").compiled.patch
    program = P.parse({"engine": "ym2612", "tracks": [
        {"target": "mod.tl", "points": [[0, -10], [50, 0], [250, 14]]}]})
    # on 240 ticks a second a 60 Hz frame is four ticks: no clock loss
    exact = P.fm_note(program, patch, 0, 240.0, 120, 240)
    kinds = {loss["kind"] for loss in exact.losses}
    assert "clock" not in kinds, exact.losses
    assert "steps" in kinds                    # 16.67-ms ramps are rounded
    # on 192 a frame lands between ticks, and that is said with its size
    jittery = P.fm_note(program, patch, 0, 192.0, 96, 192)
    clock = [loss for loss in jittery.losses if loss["kind"] == "clock"]
    assert clock and 0 < clock[0]["size"] <= 1000.0 / 192 / 2 + 1e-6
    # an offset past the register's end is clamped and said to be
    deep = P.parse({"engine": "ym2612", "tracks": [
        {"target": "mod.tl", "points": [[0, 120]]}]})
    clamped = P.fm_note(deep, patch, 0, 240.0, 60, 120)
    assert any(loss["kind"] == "clamp" for loss in clamped.losses)
    assert all(v <= 127 for reg, writes in clamped.writes.items()
               for _, v in writes)
    # a clock slower than the frame merges writes, and says so
    slow = P.fm_note(program, patch, 0, 30.0, 15, 30)
    assert any(loss["kind"] == "merged" for loss in slow.losses)
    # a carrier is the note's level, not its colour: refused with the reason
    carrier = P.parse({"engine": "ym2612", "tracks": [
        {"target": "op4.tl", "points": [[0, 5]]}]})
    problems = P.check_fm(carrier, patch)
    assert problems and "carrier" in problems[0] and "mod.tl" in problems[0]


def test_every_keyed_note_writes_its_start_again():
    """A key-on does not reload the patch: a modulator level written in one
    note is still there at the next. So a program writes its starting value
    at every keyed note, even when that value is the patch's own."""
    import chipgen
    import events as E
    from synthesis import program as P
    with _Isolated():
        _install("dark_tail", "fm_punch_bass", P.parse({
            "engine": "ym2612", "tracks": [
                {"target": "mod.tl", "points": [[0, 0], [100, 20]]}]}))
        events, _, _ = chipgen.to_events(
            "bpm 120\nticks 240\ninst fm0 dark_tail\ncols fm0\nA-2\n...\n...\n...\n"
            "===\nA-2\n...\n...\n...\n===\n")
        writes = [(t, ev.value) for t, ev in _timeline(events)
                  if isinstance(ev, E.FMOperator) and ev.operator == 1]
        ons = [t for t, ev in _timeline(events) if isinstance(ev, E.FMNoteOn)]
        base = P._fm_operator(_starter().get("fm_punch_bass").compiled.patch,
                              1).total_level
        assert len(ons) == 2
        for on in ons:
            assert (on, base) in writes, (on, writes[:6])


def test_the_hardware_keeps_a_mid_note_write_through_the_next_key_on():
    """The measurement behind the rule above, on the chip itself."""
    import analysis
    import instruments
    import opn2
    from synthesis import descriptors as D
    patch = instruments.get("bass")
    chip = opn2.YM2612(chip_type="ym3438")
    chip.set_instrument(0, patch)
    rate = chip.native_rate
    carriers = set(patch.carrier_indices())

    def centroid():
        mono = list(analysis.to_mono(chip.render(int(rate * 0.3))))
        mags, binw = D._spectrum(mono, int(0.1 * rate), 4096, rate)
        return sum(m * i * binw for i, m in enumerate(mags)) / (sum(mags)
                                                              or 1.0)
    chip.note_on(0, "A", 2)
    first = centroid()
    for slot in range(4):
        if slot not in carriers:
            chip.set_operator(0, (1, 3, 2, 4)[slot], "tl", 127)
    chip.note_off(0)
    chip.render(int(rate * 0.2))
    chip.note_on(0, "A", 2)
    second = centroid()
    chip.close()
    assert second < 0.8 * first, (first, second)


def test_legato_keys_once_per_run_and_resets_before_the_next():
    import chipgen
    import events as E
    from synthesis import placement, program as P
    with _Isolated():
        _install("smooth", "fm_saw_lead", P.parse({
            "engine": "ym2612", "vibrato": {"depth_cents": 20,
                                            "speed_hz": 5.5,
                                            "delay_ms": 250},
            "articulation": {"mode": "legato", "glide_ms": 30,
                             "gate": 0.9}}))
        events, _, _ = chipgen.to_events(
            "bpm 120\nticks 240\ninst fm0 smooth\ncols fm0\nA-4\nB-4\nC#5\n...\n===\n"
            "...\nE-5\n...\n...\n===\n...\n")
        line = _timeline(events)
        ons = [(t, ev.note) for t, ev in line
               if isinstance(ev, E.FMNoteOn) and ev.channel == 0]
        assert ons == [(0, "A"), (180, "E")], ons
        slides = [(t, ev.to_cents) for t, ev in line
                  if isinstance(ev, E.Portamento) and ev.target == "fm0"]
        assert (30, 200.0) in slides and (60, 400.0) in slides
        assert (180, 0.0) in slides          # back to the written pitch
        # the reset comes before the key-on it is for
        order = [type(ev).__name__ for t, ev in line if t == 180]
        assert order.index("Portamento") < order.index("FMNoteOn"), order
        # the detune layer moves with the voice and is keyed only twice
        layer_ons = [t for t, ev in line
                     if isinstance(ev, E.FMNoteOn) and ev.channel == 1]
        assert layer_ons == [0, 180], layer_ons
        assert any(t == 30 and ev.target == "fm1" and abs(ev.to_cents
                                                          - 204.7) < 0.01
                   for t, ev in line if isinstance(ev, E.Portamento))
        # the gate takes the key up 90 % into the run's last written note
        offs = [t for t, ev in line
                if isinstance(ev, E.FMNoteOff) and ev.channel == 0]
        assert 114 in offs and 261 in offs, offs
        vib = [t for t, ev in line if isinstance(ev, E.Vibrato)
               and ev.target == "fm0"]
        assert vib == [0, 180], vib
        assert placement.LAST.legato_notes == 2


def test_the_score_owns_a_channel_whose_pitch_it_moves():
    import chipgen
    import events as E
    from synthesis import placement, program as P
    with _Isolated():
        _install("smooth2", "fm_saw_lead", P.parse({
            "engine": "ym2612",
            "articulation": {"mode": "legato", "glide_ms": 0}}))
        events, _, _ = chipgen.to_events(
            "bpm 120\nticks 240\ninst fm0 smooth2\nvib fm0 30 6\ncols fm0\nA-4\nB-4\n"
            "===\n")
        ons = [t for t, ev in _timeline(events)
               if isinstance(ev, E.FMNoteOn) and ev.channel == 0]
        assert ons == [0, 30], ons            # no legato on this channel
        assert any("left out" in w for w in placement.LAST.warnings)


def test_an_installed_program_leaves_a_score_that_does_not_use_it_alone():
    # Once a program is installed the placement pass runs on every score.
    # It must keep the score's own cuts of time: the effect clock starts
    # afresh at each Wait, and merging the Waits of empty rows moved every
    # arpeggio step of a score that never names the instrument.
    import chipgen
    from synthesis import program as P
    score = ("bpm 150\nlpb 4\ninst fm0 square_lead\ncols fm0\nA-4/047\n"
             + "...\n" * 7 + "===\n")
    plain = chipgen.to_events(score)[0]
    with _Isolated():
        _install("elsewhere", "fm_saw_lead", P.parse({
            "engine": "ym2612",
            "vibrato": {"depth_cents": 30, "speed_hz": 6}}))
        placed = chipgen.to_events(score)[0]
    assert placed == plain, (placed, plain)


def test_priority_decides_who_wins_a_register_both_write():
    import chipgen
    import events as E
    from synthesis import program as P
    score = ("bpm 120\nticks 240\ninst fm0 {name}\ncols fm0\nA-2\n...\nop fm0 1 tl 90"
             "\n...\n...\n===\n")
    results = {}
    for priority in ("program", "pattern"):
        with _Isolated():
            _install(f"own_{priority}", "fm_punch_bass", P.parse({
                "engine": "ym2612", "tracks": [{
                    "target": "mod.tl", "points": [[0, 0], [200, 4]],
                    "priority": priority}]}))
            events, _, _ = chipgen.to_events(score.format(
                name=f"own_{priority}"))
            writes = [(t, ev.value) for t, ev in _timeline(events)
                      if isinstance(ev, E.FMOperator) and ev.operator == 1]
            results[priority] = writes
    score_tick = 60
    # program: its value goes back right after the score's 90
    after = [v for t, v in results["program"] if t == score_tick]
    assert after[-1] != 90 and 90 in after, results["program"]
    # pattern: once the score has written, the program leaves op1 alone
    assert all(t <= score_tick for t, v in results["pattern"]
               if v != 90), results["pattern"]


def test_a_program_survives_the_bank_file_and_the_export():
    from synthesis import bank as B, program as P
    program = P.parse({"engine": "ym2612", "tracks": [
        {"target": "mod.tl", "points": [[0, -4], [30, 0], [120, 10]],
         "release": 2}], "settings": {"car.rr": 11},
        "vibrato": {"depth_cents": 15, "speed_hz": 6, "delay_ms": 300},
        "articulation": {"mode": "legato", "glide_ms": 25, "gate": 0.95}})
    entry = copy.deepcopy(_starter().get("fm_punch_bass"))
    entry.compiled = P.attach(entry.compiled, program)
    bank = B.Bank("t", [entry])
    with tempfile.TemporaryDirectory() as folder:
        path = bank.save(os.path.join(folder, "t.bank.json"))
        again = B.Bank.load(path)
        assert P.of(again.get(entry.name).compiled).to_dict() == \
            program.to_dict()
        assert json.load(open(path))["instruments"][0] == \
            again.to_dict()["instruments"][0]
        # the engine's own bank carries the settings (and only them)
        written = again.export_runtime(folder, "t")
        fm = json.load(open(written["ym2612"]))
        text = json.dumps(fm)
        assert '"release_rate": 11' in text, text[:400]
        # a program that does not fit its patch is refused at load
        data = json.load(open(path))
        data["instruments"][0]["compiled"]["style"]["program"]["tracks"] = [
            {"target": "op4.tl", "points": [[0, 3]]}]
        with open(path, "w") as handle:
            json.dump(data, handle)
        try:
            B.Bank.load(path)
        except B.BankError as error:
            assert "carrier" in str(error)
        else:
            raise AssertionError("a carrier track was accepted")


def test_an_nes_program_becomes_the_macros_the_driver_plays():
    from synthesis import nes_driver, program as P
    from synthesis.backends.base import Compiled
    inst = nes_driver.NESInstrument("t", "pulse")
    program = P.parse({"engine": "nes", "tracks": [
        {"target": "volume", "points": [[0, 15], [50, 8], [100, 10],
                                        [150, 0]], "interp": "step",
         "loop": 1, "release": 3},
        {"target": "duty", "points": [[0, 1], [33.3, 2]], "interp": "step"}],
        "articulation": {"gate": 0.5}})
    made, losses = P.apply_nes(program, inst)
    vol = made.volume
    assert vol.values[0] == 15 and vol.values[3] == 8 and vol.loop == 3
    assert vol.release == len(vol.values) - 1 and vol.values[-1] == 0
    assert any(loss["kind"] == "clock" for loss in losses)   # 33.3 ms
    prepared = P.prepare(Compiled("nes", "t", inst, {}, {
        "program": program.to_dict()}))
    assert prepared.style["gate"] == 0.5
    triangle = nes_driver.NESInstrument("tri", "triangle")
    try:
        P.apply_nes(program, triangle)
    except P.ProgramError as error:
        assert "duty" in str(error)
    else:
        raise AssertionError("a duty macro on the triangle was accepted")
    # the noise channel's own macro: its period, whole steps, noise only
    noise = nes_driver.NESInstrument("hat", "noise")
    hiss = P.parse({"engine": "nes", "tracks": [
        {"target": "period", "points": [[0, 3], [50, 9.4]],
         "interp": "step"}]})
    made, losses = P.apply_nes(hiss, noise)
    assert made.period.values[0] == 3 and made.period.values[-1] == 9
    assert any(loss["kind"] == "steps" for loss in losses)
    try:
        P.apply_nes(hiss, inst)
    except P.ProgramError as error:
        assert "noise" in str(error)
    else:
        raise AssertionError("a period macro on a pulse was accepted")


# -- the phase readings -------------------------------------------------------
def test_phases_read_a_fast_attack_and_say_what_does_not_apply():
    from synthesis import phases
    rate = 44100.0
    f0 = 220.0
    n = int(1.2 * rate)
    x = []
    for i in range(n):
        t = i / rate
        env = min(1.0, t / 0.005) if t < 0.8 else math.exp(-(t - 0.8) / 0.05)
        x.append(0.5 * env * math.sin(2 * math.pi * f0 * t))
    r = phases.note(x, rate, f0, 0.8)
    assert r["attack"]["rise_ms"] <= 6.0, r["attack"]       # 1 ms grid
    assert abs(r["phases"]["body"]["pitch_cents"]) < 2.0
    assert abs(r["phases"]["body"]["brightness"] - 1.0) < 0.05
    rel = r["release"]["ms_to_minus20"]
    assert rel is not None and 90 <= rel <= 140, rel      # 50 ms tau: 115
    assert r["loop_seam"] is None and "no sample loop" in r["loop_seam_why"]
    # noise has no pitch, and says so instead of reading 0
    import random
    rng = random.Random(1)
    noise = [0.3 * rng.uniform(-1, 1) for _ in range(int(0.6 * rate))]
    r = phases.note(noise, rate, None, 0.5, pitched=False)
    assert r["phases"]["body"]["pitch_cents"] is None
    assert phases.why(r, "phases.body.pitch_cents") == "unpitched"


def test_power_brightness_ignores_a_faint_floor_the_magnitude_one_does_not():
    import random
    from synthesis import phases
    rate = 44100.0
    rng = random.Random(3)
    tone = [0.5 * math.sin(2 * math.pi * 110 * i / rate)
            for i in range(8192)]
    hiss = [v + 0.005 * rng.uniform(-1, 1) for v in tone]     # -40 dB
    (p0, m0), _ = phases._brightness(tone, rate, 110.0)
    (p1, m1), _ = phases._brightness(hiss, rate, 110.0)
    assert abs(p1 - p0) < 0.1 * p0, (p0, p1)
    assert m1 > 2 * m0, (m0, m1)


# -- the rate a render is written at -------------------------------------------
def test_a_render_is_written_at_the_rate_asked_for_and_in_tune():
    import array
    import wave
    import chipgen
    score = "inst fm0 organ\ncols fm0\nA-4\n...\n...\n...\n...\n...\n===\n"
    with tempfile.TemporaryDirectory() as folder:
        for rate in (22050, 48000):
            path = os.path.join(folder, f"r{rate}.wav")
            chipgen.compose(score, wav=path, target_rate=rate)
            with wave.open(path) as handle:
                assert handle.getframerate() == rate
                pcm = array.array("h")
                pcm.frombytes(handle.readframes(handle.getnframes()))
                channels = handle.getnchannels()
            from synthesis import phases
            left = [v / 32768.0 for v in
                    pcm[0::channels][int(0.1 * rate):int(0.4 * rate)]]
            cents, _ = phases._pitch(left, rate, 440.0)
            assert cents is not None and abs(cents) < 10.0, (rate, cents)


# -- the loop -------------------------------------------------------------------
def test_an_unknown_intent_or_chip_stops_before_any_render():
    from synthesis import improve as I
    entry = _starter().get("fm_punch_bass")
    for intent, chip_entry in (("warmer", entry),
                               ("legato", _starter().get("nes_pad"))):
        try:
            I.improve(chip_entry, intent)
        except I.ImproveError as error:
            assert ("warmer" in str(error) and "legato" in str(error)) or \
                "YM2612" in str(error), str(error)
        else:
            raise AssertionError(f"{intent} ran")


def test_a_run_keeps_its_budget_and_can_be_undone_exactly():
    from synthesis import bank as B, improve as I
    with _Isolated():
        entry = copy.deepcopy(_starter().get("fm_punch_bass"))
        bank = B.Bank("t", [copy.deepcopy(entry)])
        before = bank.to_dict()
        record = I.improve(entry, "shorter release", I.Budget(2, 1))
        assert record["stop"]["reason"] in ("budget", "no improvement")
        tried = [c for r in record["rounds"] for c in r["candidates"]]
        assert len(tried) <= 2 and len(record["rounds"]) == 1
        assert record["costs"]["renders"] <= 3 * (1 + 2) + 1
        assert record["costs"]["model_tokens"] is None
        assert "not done" in record["listening"]
        assert record["hypothesis"]["parameter"].startswith("car.rr")
        kept = I.commit(bank, record)
        if record["best"]["id"] != "baseline":
            assert kept is not None and bank.to_dict() != before
            got = bank.get(entry.name).compiled.style["program"]
            assert got["settings"]["car.rr"] > 9
        I.rollback(bank, record)
        assert bank.to_dict() == before
        # a candidate that did not pass is not a choice
        rejected = [c["id"] for c in tried if not c.get("accepted")]
        for cid in rejected:
            try:
                I.commit(bank, record, cid)
            except I.ImproveError:
                continue
            raise AssertionError(f"{cid} was committed")


def test_a_round_of_repeats_stops_the_loop():
    from synthesis import improve as I
    with _Isolated():
        entry = copy.deepcopy(_starter().get("fm_punch_bass"))
        intent = I.INTENTS["shorter release"]
        saved = intent.amounts
        try:
            # every amount proposes the same program: one is tried, the
            # rest are repeats, and the second round has nothing new
            I.INTENTS["shorter release"] = I.Intent(**{
                **intent.__dict__, "amounts": (2, 2, 2)})
            record = I.improve(entry, "shorter release", I.Budget(3, 2))
        finally:
            I.INTENTS["shorter release"] = intent
            intent.amounts = saved
        first = record["rounds"][0]["candidates"]
        assert sum(1 for c in first if c.get("repeat")) == 2
        assert record["stop"]["reason"] in ("repeated state", "budget",
                                            "no improvement")
