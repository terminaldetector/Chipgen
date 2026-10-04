"""One voice up the ladder: NES, Mega Drive, Schism.

The failure the ladder exists for: a module whose sounds were made with
`wave=square` and `wave=pulse` is heard as an NES expansion chip however its
notes are arranged, and mixing archetypes did not cure it. The tests hold the
three rungs to what each is: the NES rung has the buzz of a pulse (its
harmonics follow the duty cycle), the Mega Drive rung is an FM voice in the
chip's registers (or a DAC one-shot for a drum), and the Schism rung, built
from the FM render and not from the NES cycle, has none of that: no duty
buzz, an organ with a key click, a snare with a noise crack and a body.

Most of it reads samples, not renders, so it runs without a player; the few
that listen skip without libopenmpt.
"""

import contextlib
import io
import json
import math
import os
import tempfile

import support
from schism import it_read, it_write, notation
from schism.forge import (bank as bank_mod, cli, dsp, fam_layer, fm, ladder,
                          notation_hook, patch as patch_mod, spectral, splice)
from schism.forge.family import get_family

SR = 44100
_CACHE = {}


def _need():
    support.need_openmpt()


def _body(kind):
    entry = notation_hook.starter().get(kind)
    family = get_family(entry.family)
    return fam_layer.Source(kind + "_body", family.from_dict(
        family.to_dict(entry.compiled)), entry.genome, "body")


def rung(role, **overrides):
    """-> (NesVoice, SegaVoice, sega Compiled, schism Compiled), built once."""
    key = (role, tuple(sorted(overrides.items())))
    if key not in _CACHE:
        nes = ladder.nes_voice(role, **overrides)
        sega = ladder.lift_to_sega(nes)
        genome = ladder.sega_genome(sega, role + "_sega")
        compiled = get_family(genome.family).compile(genome)
        _, schism, _ = ladder.lift_to_schism(sega, role + "_schism", _body)
        _CACHE[key] = (nes, sega, compiled, schism)
    return _CACHE[key]


def sample(compiled, zone=1):
    """One band of the instrument, as the file would hold it."""
    low, high = patch_mod.groups(compiled.patch.bands)[zone]
    built = patch_mod.make_sample(compiled.patch.family, compiled.patch.params,
                                  low, high)
    return built, (low + high) // 2


def played(built, base, pitched=True):
    """(floats, frames a second at the base note)"""
    rate = splice.native_rate(built, base, pitched)
    return splice.floats(built.data), rate


def lines(x, rate, f0, count=24, size=32768, start=0):
    seg = x[start:start + size]
    n = dsp.next_pow2(len(seg))
    seg = seg + [0.0] * (n - len(seg))
    mags, w = dsp.spectrum(seg, 0, n, rate)
    out = []
    for h in range(1, count + 1):
        k = int(round(h * f0 / w))
        out.append(max(mags[max(0, k - 3):k + 4]))
    return out


def pulse_lines(duty, count=24):
    return [abs(math.sin(math.pi * n * duty)) / n for n in range(1, count + 1)]


def agreement(measured, ideal):
    """Pearson correlation of the log amplitudes of two harmonic series."""
    a = [math.log(max(v, 1e-9)) for v in measured]
    b = [math.log(max(v, 1e-9)) for v in ideal]
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a)
                    * sum((y - mb) ** 2 for y in b)) or 1.0
    return num / den


def band(x, rate, a, b, lo, hi):
    seg = x[int(a * rate):int(b * rate)]
    n = dsp.next_pow2(len(seg))
    mags, w = dsp.spectrum(seg + [0.0] * (n - len(seg)), 0, n, rate)
    return (sum(m * m for m in mags[int(lo / w):int(hi / w) + 1]) ** 0.5
            / math.sqrt(len(seg)))


# -- the chip ---------------------------------------------------------------------------------
def test_a_voice_is_the_registers_of_a_chip_and_refuses_what_they_cannot_hold():
    patch = fm.FmPatch(4, 5, [fm.Operator(mul=1, tl=30), fm.Operator(dt=3),
                              fm.Operator(mul=2), fm.Operator(dt=-3)])
    assert patch.problems() == []
    back = fm.FmPatch.from_dict(json.loads(json.dumps(patch.to_dict())))
    assert back.to_dict() == patch.to_dict()
    bad = (({"algorithm": 9}, "algorithm"), ({"feedback": 8}, "feedback"),
           ({"pan": "C"}, "pan"))
    for change, word in bad:
        data = {**patch.to_dict(), **change}
        try:
            fm.FmPatch.from_dict(data)
        except fm.FmError as error:
            assert word in str(error)
        else:
            raise AssertionError(change)
    for field, value in (("tl", 200), ("ar", 32), ("sl", 16), ("mul", 16),
                         ("rr", 16), ("dt", 4)):
        data = patch.to_dict()
        data["operators"][1][field] = value
        try:
            fm.FmPatch.from_dict(data)
        except fm.FmError as error:
            assert field.upper() in str(error)
        else:
            raise AssertionError(field)
    short = patch.to_dict()
    short["operators"] = short["operators"][:3]
    try:
        fm.FmPatch.from_dict(short)
    except fm.FmError as error:
        assert "four" in str(error)
    else:
        raise AssertionError("three operators")


def test_the_additive_algorithm_is_four_sines_and_a_chain_is_not():
    ops = [fm.Operator(mul=m, tl=t) for m, t in ((1, 0), (2, 8), (3, 16),
                                                 (4, 24))]
    organ = fm.FmPatch(7, 0, ops)
    x = fm.render_note(organ, 220.0, 0.6, 44100.0, 46)
    got = lines(x, 44100.0, 220.0, 6, 16384, 8000)
    # four lines, in the levels TL gives (0.75 dB a step: 8 steps is 6 dB)
    assert got[4] < 0.01 * got[0] and got[5] < 0.01 * got[0]
    for k in (1, 2, 3):
        db = 20 * math.log10(got[k] / got[0])
        assert abs(db - (-6.0 * k)) < 1.6, (k, db)
    chain = fm.FmPatch(0, 0, [fm.Operator(mul=1, tl=20), fm.Operator(),
                              fm.Operator(), fm.Operator()])
    y = fm.render_note(chain, 220.0, 0.6, 44100.0, 46)
    more = lines(y, 44100.0, 220.0, 8, 16384, 8000)
    assert sum(more[2:]) > 0.05 * more[0]        # modulation makes overtones


def test_the_envelope_follows_its_registers():
    def seconds_to(env, db):
        t = 0.0
        while t < 20.0 and env(t) > db:
            t += 0.0005
        return t
    attack = {ar: seconds_to(fm.Envelope(fm.Operator(ar=ar), 46, None).att,
                             3.0) for ar in (31, 20, 15)}
    assert attack[31] < 0.003 < attack[20] < 0.05 < attack[15] < 0.4
    # every two steps of a rate double the speed, as on the chip
    def reach(env, db):
        t = 0.02                                 # past the attack
        while t < 120.0 and env(t) < db:
            t += 0.0005
        return t
    slow = fm.Envelope(fm.Operator(ar=31, d1r=10, sl=15), 46, None).att
    fast = fm.Envelope(fm.Operator(ar=31, d1r=12, sl=15), 46, None).att
    assert 1.8 < reach(slow, 40.0) / reach(fast, 40.0) < 2.2
    # SL 4 holds at 12 dB, SL 15 at 93
    hold = fm.Envelope(fm.Operator(ar=31, d1r=20, sl=4), 46, None)
    assert abs(hold.att(2.0) - 12.0) < 0.1
    gone = fm.Envelope(fm.Operator(ar=31, d1r=20, sl=15), 46, None)
    assert gone.att(2.0) > 90.0
    # release: a fast one is fast, and starts from where the key was let go
    quick = fm.Envelope(fm.Operator(ar=31, rr=15), 46, 0.2)
    slowr = fm.Envelope(fm.Operator(ar=31, rr=4), 46, 0.2)
    assert quick.att(0.2) < 0.1 and quick.att(0.4) > 20.0 > slowr.att(0.4)
    # an attack of 0 never begins
    assert fm.Envelope(fm.Operator(ar=0), 46, None).att(5.0) >= 95.0


def test_a_voice_that_holds_is_a_loop_and_one_that_dies_is_a_one_shot():
    _, _, lead, _ = rung("lead")
    _, _, bell, _ = rung("bell")
    held, base = sample(lead)
    assert held.loop is not None and lead.style["held"]
    struck, _ = sample(bell)
    assert struck.loop is None and not bell.style["held"]
    x = splice.floats(held.data)
    start, end = held.loop
    step = max(abs(x[i + 1] - x[i]) for i in range(start, end - 1))
    assert abs(x[end - 1] - x[start]) < 2.0 * step + 0.002
    assert max(abs(v) for v in struck.data[-32:]) < 0.02 * 32767


def test_a_held_voice_plays_the_pitch_of_the_key():
    for role in ("lead", "organ", "bass"):
        _, _, compiled, _ = rung(role)
        for zone in (0, 2):
            built, base = sample(compiled, zone)
            x, rate = played(built, base)
            f0 = spectral.hz_of_note(base)
            at = built.loop[0]
            got = dsp.peak_frequency(x[at:], f0, rate, 32768, 0) \
                if len(x) - at >= 32768 else \
                dsp.peak_frequency(x[at:] * (32768 // (len(x) - at) + 1), f0,
                                   rate, 32768, 0)
            assert abs(1200 * math.log2(got / f0)) < 8.0, (role, zone, got, f0)


def test_a_detune_is_the_whole_bins_of_a_long_loop():
    lead = fm.FmPatch(4, 0, [fm.Operator(mul=1, tl=127), fm.Operator(dt=3),
                             fm.Operator(mul=1, tl=127),
                             fm.Operator(dt=-3)])
    # DT's share of the frequency in bins, rounded: a bin or two at A5, one
    # at A4, none where a bin is a quarter of a semitone
    high = fm.operator_cycles(lead, 650, True)
    assert high[1] - 650 == 2 and high[3] - 650 == -2
    mid = fm.operator_cycles(lead, 330, True)
    assert mid[1] - 330 == 1 and mid[3] - 330 == -1
    assert fm.operator_cycles(lead, 80, True)[1] == 80
    assert fm.beats(lead, 330) and not fm.beats(lead, 80)
    assert fm.operator_cycles(lead, 650, False)[1] == 650  # a short loop has none
    assert fm.loop_cycles(fm.FmPatch(7, 0, [fm.Operator(mul=0)] * 4), 220.0,
                          32768) % 2 == 0      # a half needs an even count


# -- rung 1 to rung 2: the rules -----------------------------------------------------------------
def test_a_pulse_lead_becomes_two_detuned_pairs_with_feedback():
    nes, sega, compiled, _ = rung("lead")
    voice = fm.FmPatch.from_dict(sega.fm)
    assert voice.algorithm in (4, 5) and voice.feedback > 0
    carriers = voice.carriers()
    assert len(carriers) >= 2
    detunes = sorted(voice.ops[k].dt for k in carriers)
    assert detunes[0] == -3 and detunes[-1] == 3
    assert voice.ops[0].d1r > 0                   # the bite is a short decay
    # a narrower duty is a brighter voice: a stronger modulator
    narrow = fm.FmPatch.from_dict(ladder.lift_to_sega(
        ladder.nes_voice("lead", duty=0.125)).fm)
    wide = fm.FmPatch.from_dict(ladder.lift_to_sega(
        ladder.nes_voice("lead", duty=0.5)).fm)
    assert narrow.ops[0].tl < wide.ops[0].tl
    assert any("duty" in line for line in sega.notes)


def test_a_triangle_bass_is_a_chain_of_low_multiples_with_short_decays():
    nes, sega, _, _ = rung("bass")
    voice = fm.FmPatch.from_dict(sega.fm)
    assert nes.channel == "triangle" and voice.algorithm == 0
    assert max(op.mul for op in voice.ops) <= 2
    modulators = [voice.ops[k] for k in (0, 1, 2)]
    assert all(0 < op.d1r and op.sl < 15 for op in modulators)
    assert voice.ops[3].d1r == 0 and voice.ops[3].d2r == 0     # it holds


def test_a_noise_drum_is_a_dac_one_shot_not_an_fm_voice():
    for role in ("snare", "kick", "hat"):
        nes, sega, compiled, _ = rung(role)
        assert nes.channel == "noise" and sega.kind == "dac" and sega.fm is None
        assert sega.dac["bits"] == 8 and sega.dac["rate"] <= 16000
        assert compiled.family == "drum" and compiled.patch.params["dac"]
        built, base = sample(compiled, 0) if len(
            patch_mod.groups(compiled.patch.bands)) > 1 else (
            patch_mod.make_sample("drum", compiled.patch.params, 1, 120), 60)
        assert built.right is None
        # a DAC holds each sample for 44100/rate frames and has 8 bits
        steps = len(set(built.data))
        assert steps <= 256


def test_an_organ_has_drawbar_multiples_and_a_click_operator():
    _, sega, _, _ = rung("organ")
    voice = fm.FmPatch.from_dict(sega.fm)
    assert voice.algorithm == 7
    held = [op for op in voice.ops if op.d1r == 0]
    assert sorted(op.mul for op in held) == [1, 2, 3]
    click = [op for op in voice.ops if op.d1r > 0][0]
    assert click.ar == 31 and click.sl == 15 and click.mul > 3


def test_the_mega_drive_rung_has_no_stereo_sample_but_a_pan_register():
    for role in ("lead", "bass", "pad", "organ", "stab", "bell"):
        _, sega, compiled, _ = rung(role)
        assert fm.FmPatch.from_dict(sega.fm).pan == "LR"
        assert compiled.patch.inst["pan"] == 32
        for zone in range(len(patch_mod.groups(compiled.patch.bands))):
            if role in ("pad", "organ", "lead") and zone != 1:
                continue
            built, _ = sample(compiled, zone)
            assert built.right is None
    left = fm.FmPatch.from_dict(ladder.lift_to_sega(
        ladder.nes_voice("lead")).fm)
    left.pan = "L"
    genome = ladder.sega_genome(ladder.SegaVoice("fm", "lead", left.to_dict()),
                                "l")
    assert get_family("fm").compile(genome).patch.inst["pan"] == 0


def test_a_pad_is_an_arpeggio_on_the_nes_and_a_voice_above_it():
    nes, sega, compiled, _ = rung("pad")
    assert nes.arp == [0, 4, 7]
    rows = ladder.arp_rows(nes, "C-4", 1, 4)
    assert rows[0] == "C-4 01 v40 J47" and rows[1].endswith("J47")
    assert "wave=pulse" in ladder.nes_line(nes)
    assert fm.FmPatch.from_dict(sega.fm).ops[1].ar < 20      # a slow attack
    # nothing of the arpeggio is left in the FM voice: it is one voice
    assert ladder.nes_voice("pad", arp=[0, 3, 7, 10]).arp == [0, 3, 7, 10]


# -- rung 3 -----------------------------------------------------------------------------------------
def test_the_schism_rung_has_zones_loops_stereo_and_nna_by_role():
    expect = {  # role: (loops, stereo, nna)
        "lead": (True, True, "cut"), "bass": (True, False, "cut"),
        "pad": (True, True, "fade"), "organ": (True, True, "fade"),
        "stab": (False, True, "cut"), "bell": (False, True, "cont")}
    for role, (loops, stereo, nna) in expect.items():
        _, _, _, schism = rung(role)
        assert schism.patch.inst["nna"] == nna, role
        assert len(patch_mod.groups(schism.patch.bands)) == 5   # an octave each
        built, _ = sample(schism, 1)
        assert (built.loop is not None) == loops, role
        assert (built.right is not None) == stereo, role
    for role in ("snare", "kick", "hat"):
        _, _, _, schism = rung(role)
        assert schism.family == "layer" and schism.patch.inst["nna"] == "cut"


def test_the_stereo_pair_is_a_little_flat_and_a_little_sharp():
    _, _, _, schism = rung("organ")
    built, base = sample(schism, 1)
    x, rate = played(built, base)
    r = splice.floats(built.right)
    f0 = spectral.hz_of_note(base)
    at = built.loop[0]
    left = 1200 * math.log2(dsp.peak_frequency(x[at:], f0, rate, 32768, 0) / f0)
    right = 1200 * math.log2(dsp.peak_frequency(r[at:], f0, rate, 32768, 0) / f0)
    assert left < -2.0 and right > 2.0 and abs(left + right) < 2.0


def test_only_the_organ_has_a_chorus_and_a_stab_is_a_chord():
    for role in ("lead", "bass", "pad", "stab", "bell"):
        _, _, _, schism = rung(role)
        assert not schism.patch.params["post"].get("chorus"), role
    _, _, _, organ = rung("organ")
    assert organ.patch.params["post"]["chorus"]
    assert organ.patch.params["post"]["drive"] > 0
    assert organ.patch.params["post"]["cabinet"]
    _, _, _, stab = rung("stab")
    notes = splice.chord_notes(stab.patch.params["chord"])
    assert len(notes) >= 4
    built, base = sample(stab, 1)
    x, rate = played(built, base)
    f0 = spectral.hz_of_note(base)
    for semis in notes:
        f = f0 * 2.0 ** (semis / 12.0)
        got = lines(x, rate, f, 1, 8192, 0)[0]
        assert got > 0.02 * max(lines(x, rate, f0, 1, 8192, 0)[0], 1e-9) \
            or semis == 0, semis


def test_a_loop_stays_a_loop_through_the_drive_the_cabinet_and_the_chorus():
    _, _, _, organ = rung("organ")
    built, _ = sample(organ, 1)
    for data in (built.data, built.right):
        x = splice.floats(data)
        start, end = built.loop
        step = max(abs(x[i + 1] - x[i]) for i in range(start, end - 1))
        assert abs(x[end - 1] - x[start]) < 2.0 * step + 0.003


# -- the point of it ------------------------------------------------------------------------------------
def test_the_duty_buzz_of_the_nes_is_gone_by_the_schism_rung():
    from schism import recipes
    for duty in (0.125, 0.25):
        nes, sega, compiled, schism = rung("lead", duty=duty)
        ideal = pulse_lines(duty)
        # the NES rung is a pulse: its harmonics are the duty cycle's
        pulse = recipes.make_sample("pulse", {"duty": duty})
        low, high, wave = next(p for p in pulse
                               if p[0] <= 58 <= p[1])
        base = (low + high) // 2
        x = splice.floats(wave.data)
        rate = wave.c5speed * 2.0 ** ((base - 61) / 12.0)
        f0 = spectral.hz_of_note(base)
        size = len(x) if len(x) & (len(x) - 1) == 0 else 4096
        tiled = (x * (size // len(x) + 1))[:size] if len(x) < size else x[:size]
        nes_lines = lines(tiled * 8, rate, f0, 24, size * 8 if size * 8 <= 65536
                          else size, 0)
        assert agreement(nes_lines, ideal) > 0.9, duty
        for label, c in (("sega", compiled), ("schism", schism)):
            built, b = sample(c, 1)
            y, r = played(built, b)
            at = built.loop[0]
            got = lines(y[at:] * (32768 // max(1, len(y) - at) + 1),
                        r, spectral.hz_of_note(b), 24, 32768, 0)
            assert agreement(got, ideal) < 0.6, (label, duty)


def test_an_organ_has_a_key_click_the_nes_organ_does_not():
    _, _, sega, schism = rung("organ")
    for label, c in (("sega", sega), ("schism", schism)):
        # the click is the fourth operator, six times the pitch, and gone in
        # a few milliseconds: it is in the first of them and not in the
        # tenth part of a second after
        for zone in (2, 3, 4):
            built, base = sample(c, zone)
            x, rate = played(built, base)
            f0 = spectral.hz_of_note(base)
            tail = x[built.loop[0]:built.loop[1]]
            held = x[:built.loop[0]] + tail * (int(0.4 * rate) // len(tail) + 1)
            early = band(held, rate, 0.0, 0.012, 5.3 * f0, 6.7 * f0)
            late = band(held, rate, 0.15, 0.30, 5.3 * f0, 6.7 * f0)
            # the Schism rung's drive puts a little of that pitch into the
            # held notes too (3 + 2 + 1), so its click stands out less
            need = 3.0 if label == "sega" else 2.0
            assert early > need * late, (label, zone, early, late)
    # a NES organ is a pulse held: no more at the front than behind
    from schism import recipes
    pulse = recipes.make_sample("pulse", {"duty": 0.5})
    low, high, wave = next(p for p in pulse if p[0] <= 46 <= p[1])
    rate = wave.c5speed * 2.0 ** (((low + high) // 2 - 61) / 12.0)
    x = splice.floats(wave.data) * 400
    assert band(x, rate, 0.0, 0.015, 1500, 8000) \
        < 1.5 * band(x, rate, 0.15, 0.30, 1500, 8000)


def test_a_snare_has_a_noise_crack_and_a_body_not_the_daccs_nothing():
    _, sega, dac, schism = rung("snare")
    plain = patch_mod.make_sample("drum", dac.patch.params, 1, 120)
    stack = patch_mod.make_sample("layer", schism.patch.params, 1, 120)
    reg = dac.patch.params["reg_note"]
    results = {}
    for label, built in (("dac", plain), ("schism", stack)):
        x = splice.floats(built.data)
        results[label] = {
            "crack": band(x, SR, 0.0, 0.04, 2000, 9000),
            "late_hi": band(x, SR, 0.12, 0.25, 2000, 9000),
            "body": band(x, SR, 0.05, 0.25, 120, 400)}
    s, d = results["schism"], results["dac"]
    assert s["crack"] > 1.5 * s["late_hi"]          # the crack is at the front
    assert s["body"] > 1.5 * d["body"]              # and there is more under it
    assert s["body"] > 0.15 * s["crack"]
    assert reg == schism.patch.params["reg_note"]


def test_the_forge_reads_the_snares_body_and_noise_where_the_dac_has_none():
    _need()
    from schism.forge import probe
    _, _, dac, schism = rung("snare")
    from schism.forge.genome import Genome
    from schism.forge.dna import DNA
    reg = ("G", 4)
    got = {}
    for label, c in (("dac", dac), ("schism", schism)):
        got[label] = probe.probe(c, Genome(c.family, DNA(), register=reg),
                                 full=True).axes
    assert got["dac"]["body"] < 0.03                  # "body 0.00"
    assert got["schism"]["body"] > 0.06
    assert got["schism"]["noise"] > 0.5


def test_heard_in_a_player_the_rungs_are_what_they_were_measured_to_be():
    _need()
    from schism.forge import probe
    nes, _, sega, schism = rung("organ")[0], None, rung("organ")[2], \
        rung("organ")[3]
    note = probe.note_of("A", 3)
    for c in (sega, schism):
        left, right, hold, rate = probe.render_note(c.patch, note, 1.0, 0.5)
        mono = [(a + b) / 2 for a, b in zip(left, right)]
        hz = probe.note_hz(note)
        got = dsp.peak_frequency(mono, hz, rate, 32768, int(0.3 * rate))
        assert abs(1200 * math.log2(got / hz)) < 6.0
        assert band(mono, rate, 0.0, 0.012, 5.3 * hz, 6.7 * hz) \
            > 2.0 * band(mono, rate, 0.15, 0.3, 5.3 * hz, 6.7 * hz)
    del nes


# -- the file and the command -----------------------------------------------------------------------------
def test_a_voice_file_remembers_the_rungs_that_have_been_lifted():
    nes = ladder.nes_voice("lead")
    voice = ladder.Voice("lead", "lead", nes, ladder.lift_to_sega(nes),
                         {"entry": "lead_schism"})
    with tempfile.TemporaryDirectory() as folder:
        path = voice.save(os.path.join(folder, "lead.voice.json"))
        back = ladder.Voice.load(path)
    assert back.to_dict() == voice.to_dict()
    assert back.sega.fm["operators"][1]["dt"] == 3
    for bad in ({"format": "x"}, {**voice.to_dict(), "sega": {"kind": "fm",
                                                              "role": "lead"}}):
        try:
            ladder.Voice.from_dict(bad)
        except ladder.LadderError:
            pass
        else:
            raise AssertionError(bad)


def test_a_voice_is_named_for_the_role_a_console_would_give_it():
    assert ladder.role_of("saw_lead") == "lead"
    assert ladder.role_of("acid_bass") == "bass"
    assert ladder.role_of("organ") == "organ"
    assert ladder.role_of("kick_hard") == "kick"
    assert ladder.role_of("open_hat") == "hat"
    assert ladder.role_of("anything", "pad") == "pad"
    try:
        ladder.role_of("x", "kazoo")
    except ladder.LadderError as error:
        assert "kazoo" in str(error)
    else:
        raise AssertionError("an unknown role")
    for bad in ({"duty": 0.3}, {"noise_mode": "wet"}, {"arp": [0, 99]}):
        try:
            ladder.nes_voice("lead", **bad)
        except ladder.LadderError:
            pass
        else:
            raise AssertionError(bad)


def _run(*argv):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def test_the_command_lifts_a_voice_a_rung_at_a_time_and_says_what_it_did():
    with tempfile.TemporaryDirectory() as folder:
        code, text, _ = _run("-q", "lift-voice", "bass", "--from", "nes",
                             "--to", "sega", "--dir", folder)
        assert code == 0
        assert "ALG 0" in text and "triangle" in text and "carrier" in text
        voice = ladder.Voice.load(os.path.join(folder, "bass.voice.json"))
        assert voice.sega is not None and voice.schism is None
        bank = bank_mod.Bank.load(os.path.join(folder, "bass.bank.json"))
        assert bank.names() == ["bass_sega"]
        # the next rung needs the one below
        code, text, _ = _run("-q", "lift-voice", "bass", "--from", "sega",
                             "--to", "schism", "--dir", folder,
                             "--demo", os.path.join(folder, "bass.sch"))
        assert code == 0 and "built from the Sega voice" in text
        bank = bank_mod.Bank.load(os.path.join(folder, "bass.bank.json"))
        assert bank.names() == ["bass_sega", "bass_schism"]
        module, report = notation.compile_text(
            open(os.path.join(folder, "bass.sch")).read(), folder)
        assert report.warnings == () or not report.warnings
        assert len(module.patterns) == 3          # one section a rung
        # lifting again replaces, it does not pile up
        _run("-q", "lift-voice", "bass", "--from", "nes", "--to", "schism",
             "--dir", folder)
        assert len(bank_mod.Bank.load(
            os.path.join(folder, "bass.bank.json")).entries) == 2


def test_the_command_refuses_to_go_down_or_to_start_from_nothing():
    with tempfile.TemporaryDirectory() as folder:
        for argv, word in (
                (("lift-voice", "lead", "--from", "sega", "--to", "nes"),
                 "up the ladder"),
                (("lift-voice", "lead", "--from", "sega", "--to", "schism"),
                 "no voice file"),
                (("lift-voice", "lead", "--duty", "0.3"), "duty"),
                (("lift-voice", "lead", "--role", "kazoo"), "kazoo")):
            code, _, err = _run("-q", *argv, "--dir", folder)
            assert code == 2 and word in err, (argv, err)


def test_a_chord_voice_prints_its_arpeggio_pattern():
    with tempfile.TemporaryDirectory() as folder:
        code, text, _ = _run("-q", "lift-voice", "pad", "--dir", folder)
        assert code == 0 and "J47" in text and "arpeggio" in text
        code, text, _ = _run("-q", "lift-voice", "pad", "--dir", folder,
                             "--arp", "0,3,7")
        assert code == 0 and "J37" in text


def test_the_lifted_voices_carry_their_operators_into_the_sidecar():
    from schism import export
    with tempfile.TemporaryDirectory() as folder:
        _run("-q", "lift-voice", "lead", "--to", "schism", "--dir", folder,
             "--demo", os.path.join(folder, "lead.sch"))
        _run("-q", "lift-voice", "snare", "--to", "schism", "--dir", folder,
             "--demo", os.path.join(folder, "snare.sch"))
        lead, _ = notation.compile_text(
            open(os.path.join(folder, "lead.sch")).read(), folder)
        data = export.sidecar(lead)
        by_name = {i["name"]: i for i in data["instruments"]}
        assert "operators" not in by_name["NES"]
        for rung_name in ("Sega", "Schism"):
            ops = by_name[rung_name]["operators"]
            assert ops["algorithm"] in (4, 5) and len(ops["operators"]) == 4
        assert any("operator dumps" in n for n in export.apply_preset(
            lead, "sega"))
        drums, _ = notation.compile_text(
            open(os.path.join(folder, "snare.sch")).read(), folder)
        data = export.sidecar(drums)
        by_name = {i["name"]: i for i in data["instruments"]}
        assert by_name["Sega"]["dac"]["bits"] == 8
        assert by_name["Schism"]["dac"]["rate"] == by_name["Sega"]["dac"]["rate"]
        assert any("DAC" in n for n in export.apply_preset(drums, "sega"))


def test_the_ladder_demo_goes_through_the_file_and_sounds_the_same_in_both_players():
    _need()
    from schism import openmpt, schismtracker
    if not schismtracker.available():
        support.skip("Schism Tracker is not installed (apt install schism)")
    with tempfile.TemporaryDirectory() as folder:
        _run("-q", "lift-voice", "organ", "--to", "schism", "--dir", folder,
             "--demo", os.path.join(folder, "organ.sch"))
        module, _ = notation.compile_text(
            open(os.path.join(folder, "organ.sch")).read(), folder)
    blob = it_write.build(module)
    back = it_read.read(blob)
    assert any(s.right is not None for s in back.samples)
    with openmpt.Song(blob) as song:
        ours = song.render(44100)
    theirs = schismtracker.render(blob, 44100)
    n = min(len(ours), len(theirs))

    def rms(x):
        return math.sqrt(sum(v * v for v in x) / max(1, len(x)))
    db = 20 * math.log10(rms(theirs[:n]) / rms(ours[:n]))
    assert abs(db) < 1.5, db
