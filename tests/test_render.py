"""The whole path: events in, audio out, on whichever cores are available."""

import math
import os
import subprocess
import sys

import audio
import chipgen
import support
import tracker
import wavio
from sequencer import Sequencer

SCORE = """\
bpm 160
lpb 4
inst fm0 bass
inst fm1 square_lead
cols fm0 fm1 psg0 noise dac

A-2  ...  A-4    w1   kick
...  A-4  C-5:4  ...  hat
C-3  ...  E-5    w1   snare
...  ===  ===    ===  hat
"""


def test_a_score_becomes_audible_audio():
    result = chipgen.compose(SCORE)
    assert len(result.audio) > 1000
    assert 0.05 < result.peak <= 1.0, f"peak {result.peak} is silence or clipping"
    assert audio.rms(result.audio) > 0.01


def test_render_length_matches_the_score():
    events, meta = tracker.loads(SCORE)
    import events as events_mod
    expected = events_mod.duration_seconds(events, meta.ticks_per_second)
    buf = Sequencer(ticks_per_second=meta.ticks_per_second).render(events)
    assert abs(len(buf) / 44100.0 - expected) < 0.02


def test_tempo_changes_take_effect_mid_score():
    import events as E
    slow = [E.Wait(ticks=192), E.End()]
    fast = [E.Tempo(ticks_per_second=384.0), E.Wait(ticks=192), E.End()]
    seq = Sequencer(ticks_per_second=192.0)
    assert abs(len(seq.render(slow)) / 44100.0 - 1.0) < 0.02
    assert abs(len(seq.render(fast)) / 44100.0 - 0.5) < 0.02


def test_dac_tail_is_not_cut_off():
    # A drum triggered on the last row still has 200 ms of sample left; the
    # render has to run past the end of the score to let it finish.
    import events as E
    import samples
    events = [E.DACSample(name="kick"), E.Wait(ticks=1), E.End()]
    buf = Sequencer(ticks_per_second=192.0).render(events)
    seconds = len(buf) / 44100.0
    assert seconds >= samples.KIT["kick"].duration * 0.9, \
        f"kick is {samples.KIT['kick'].duration:.2f}s but only {seconds:.2f}s rendered"


def _pan_energy(chip_type):
    import events as E
    events = [E.FMInstrumentSelect(channel=0, instrument="organ"),
              E.FMPan(channel=0, left=True, right=False),
              E.FMNoteOn(channel=0, note="A", octave=3),
              E.Wait(ticks=96), E.FMNoteOff(channel=0), E.End()]
    buf = Sequencer(chip_type=chip_type).render(events)
    return (sum(abs(buf[i][0]) for i in range(0, len(buf), 17)),
            sum(abs(buf[i][1]) for i in range(0, len(buf), 17)))


def test_pan_is_silent_on_the_muted_side_of_an_asic_genesis():
    left, right = _pan_energy("ym3438")
    assert left > 1.0, "hard-left pan produced no left channel"
    assert right == 0.0, f"the YM3438 has no ladder; muting must be exact (R={right})"


def test_pan_leaks_the_dac_ladder_on_a_discrete_genesis():
    # Not a bug to be fixed: the discrete YM2612's time-shared DAC emits a
    # small fixed level whenever it is not carrying a channel, so a muted
    # output still carries a quiet square at the signal's zero crossings.
    # This test pins the behaviour so that "chipgen went clean" would show
    # up as a failure rather than as a subtly different sound.
    import core_loader
    if core_loader.backend_for("libopn2") != "native":
        support.skip("the pure-Python core models no ladder, so it is "
                     "always the clean revision")
    left, right = _pan_energy("ym2612")
    assert left > 1.0
    assert 0.0 < right < left * 0.5,         f"ladder bleed outside the expected range (L={left:.2f} R={right:.2f})"


def test_chip_type_does_not_change_the_overall_level():
    loud = _pan_energy("ym2612")[0]
    clean = _pan_energy("ym3438")[0]
    assert support.db_between(loud, clean) < 1.5,         "switching chip revision should change the character, not the volume"


def test_channel_volume_attenuates():
    import events as E

    def render(volume):
        events = [E.FMInstrumentSelect(channel=0, instrument="organ"),
                  E.FMVolume(channel=0, volume=volume),
                  E.FMNoteOn(channel=0, note="A", octave=3),
                  E.Wait(ticks=96), E.FMNoteOff(channel=0), E.End()]
        return audio.rms(Sequencer().render(events))

    loud, quiet = render(127), render(48)
    assert quiet < loud * 0.7, f"volume 48 ({quiet:.4f}) is not quieter than 127 ({loud:.4f})"
    assert quiet > 0, "volume 48 should not be silence"


def test_wav_round_trips_through_the_stdlib_writer():
    result = chipgen.compose(SCORE)
    with support.TempDir() as directory:
        path = os.path.join(directory, "t.wav")
        wavio.write(path, result.audio, 44100)
        restored, rate = wavio.read(path)
    assert rate == 44100
    assert len(restored) == len(result.audio)
    assert support.db_between(audio.rms(restored), audio.rms(result.audio)) < 0.1


def test_compose_accepts_all_three_input_shapes():
    import events as E
    import json

    events = [E.FMInstrumentSelect(channel=0, instrument="bass"),
              E.FMNoteOn(channel=0, note="A", octave=2),
              E.Wait(ticks=48), E.FMNoteOff(channel=0), E.End()]
    as_json = json.dumps([e.to_dict() for e in events])

    from_objects = chipgen.compose(events)
    from_json = chipgen.compose(as_json)
    from_tracker = chipgen.compose("bpm 150\nlpb 4\ninst fm0 bass\ncols fm0\n\nA-2\n===\n")

    assert from_objects.source_format == "events"
    assert from_json.source_format == "json"
    assert from_tracker.source_format == "tracker"
    for result in (from_objects, from_json, from_tracker):
        assert len(result.audio) > 0 and result.peak > 0.01


def test_compose_repairs_instead_of_refusing():
    result = chipgen.compose('[{"type":"noteon","ch":0,"note":"a","octave":2},'
                             ' {"type":"wait","ticks":48}]')
    assert result.warnings, "repairs should be reported, not silent"
    assert len(result.audio) > 0


def test_resampling_agrees_between_backends():
    # audio.resample has a scipy path, a numpy path and a pure-Python path.
    # They must not disagree about how long the result is, or the two
    # chips would fall out of sync on machines with different packages.
    for rate_in, rate_out, frames in ((53267.0, 44100, 5000), (44100.0, 22050, 3000)):
        buf = audio.zeros(frames, 2)
        out = audio.resample(buf, rate_in, rate_out)
        expected = max(1, round(frames * rate_out / rate_in))
        assert len(out) == expected


def test_engine_runs_with_no_numpy_and_no_scipy():
    # The bridge's whole promise. Run it in a subprocess with the imports
    # blocked, because there is no way back once numpy is loaded.
    blocker = (
        "import sys\n"
        "class Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in ('numpy', 'scipy'):\n"
        "            raise ImportError(name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, Block())\n"
        f"sys.path.insert(0, {os.path.join(support.ROOT, 'python')!r})\n"
        "import audio, chipgen\n"
        "assert not audio.HAVE_NUMPY and not audio.HAVE_SCIPY\n"
        "r = chipgen.compose(chipgen.EXAMPLE)\n"
        "assert len(r.audio) > 1000 and r.peak > 0.05\n"
        "print('OK', len(r.audio), round(r.peak, 3))\n"
    )
    proc = subprocess.run([sys.executable, "-c", blocker],
                          capture_output=True, text=True)
    assert proc.returncode == 0, f"bare-python run failed:\n{proc.stderr}"
    assert proc.stdout.startswith("OK")


def test_pure_python_cores_agree_with_the_native_ones():
    import core_loader
    if core_loader.backend_for("libopn2") != "native":
        support.skip("no native core built, nothing to compare against")

    script = (
        f"import sys; sys.path.insert(0, {os.path.join(support.ROOT, 'python')!r})\n"
        "import audio, chipgen\n"
        "r = chipgen.compose(chipgen.EXAMPLE)\n"
        "print(len(r.audio), audio.rms(r.audio))\n"
    )
    environment = dict(os.environ, CHIPGEN_BACKEND="fallback")
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, env=environment)
    assert proc.returncode == 0, f"fallback cores failed:\n{proc.stderr}"
    frames, rms = proc.stdout.split()

    native = chipgen.compose(chipgen.EXAMPLE)
    assert abs(int(frames) - len(native.audio)) <= 2, "fallback length differs"
    difference = support.db_between(float(rms), audio.rms(native.audio))
    assert difference < 3.0, \
        f"fallback is {difference:.1f} dB away from the native cores"


def test_mastering_is_opt_in_and_hits_its_target():
    import mixer
    plain = chipgen.compose(SCORE)
    mastered = chipgen.compose(SCORE, normalize=mixer.DEFAULT_MASTER_PEAK)
    assert abs(mastered.peak - mixer.DEFAULT_MASTER_PEAK) < 0.005
    assert plain.peak != mastered.peak, "the library default must not master"
    # Scaling only — the shape of the music has to survive it.
    ratio = mastered.peak / plain.peak
    assert support.db_between(audio.rms(mastered.audio),
                              audio.rms(plain.audio) * ratio) < 0.05


def test_psg_no_longer_buries_the_fm_chip():
    # One square plus a hat used to match five FM voices in RMS. Compare a
    # PSG-only render against an FM-only one from the same score.
    import events as E
    from demo_generator import generate_pattern

    events = generate_pattern(bars=2)

    def only(prefix):
        return [e for e in events
                if isinstance(e, (E.Wait, E.End))
                or type(e).__name__.startswith(prefix)]

    fm = audio.rms(Sequencer().render(only("FM")))
    psg = audio.rms(Sequencer().render(only("PSG")))
    assert psg < fm, f"PSG ({psg:.4f}) should sit under five FM voices ({fm:.4f})"


def test_noise_hits_do_not_repeat_themselves():
    # Writing the PSG noise register resets its shift register, so gating a
    # hat with a register write makes every hit the identical waveform.
    import sn76489

    def hits(restart):
        chip = sn76489.SN76489()
        chip.noise_on(True, 1, 0, restart=True)
        out = []
        for _ in range(6):
            out.append([float(v) for v in chip.render(3000)])
            chip.noise_off()
            chip.render(600)
            chip.noise_on(True, 1, 0, restart=restart)
        return out

    def correlation(a, b):
        n = len(a)
        ma, mb = sum(a) / n, sum(b) / n
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        va = sum((x - ma) ** 2 for x in a) ** 0.5
        vb = sum((y - mb) ** 2 for y in b) ** 0.5
        return cov / (va * vb) if va and vb else 0.0

    repeated = hits(restart=True)
    assert max(correlation(repeated[0], repeated[i])
               for i in range(1, len(repeated))) > 0.99, \
        "restart=True is supposed to make hits identical; it did not"

    varied = hits(restart=False)
    assert max(abs(correlation(varied[0], varied[i]))
               for i in range(1, len(varied))) < 0.5, \
        "hats are still replaying the same LFSR states"


def test_the_render_is_centred():
    result = chipgen.compose(SCORE)
    frames = len(result.audio)
    total = 0.0
    for i in range(frames):
        frame = result.audio[i]
        total += sum(float(v) for v in frame) / len(frame)
    assert abs(total / frames) < 1e-4, "DC offset survived the mixer"


# -- the acceptance checks a handoff spec asked for --------------------------
# Four properties that a YM2612 renderer can get wrong while still producing
# plausible audio. Written after reviewing an external renderer contract that
# had three of the four backwards; they are as much a guard on this engine as
# they were a correction to that one.

def test_the_same_score_renders_to_the_same_bytes():
    # Determinism is the cheapest acceptance criterion there is, and the
    # only one that catches an emulator carrying state between runs.
    import hashlib
    import os

    import chipgen

    score = ("ticks 240\nbpm 150\nlpb 4\ninst fm0 bass\n"
             "inst fm1 square_lead\ncols fm0 fm1 psg0 noise dac\n"
             "A-2  A-4  E-5  w1:8  kick\n...  ...  ...  ===   ...\n"
             "C-3  C-5  ...  w1:8  snare\n===  ===  ===  ===   ...\n")
    with support.TempDir() as directory:
        digests = []
        for index in range(3):
            wav = os.path.join(directory, f"r{index}.wav")
            vgm = os.path.join(directory, f"r{index}.vgm")
            chipgen.compose(score, wav=wav, vgm=vgm)
            with open(wav, "rb") as handle:
                audio_digest = hashlib.sha256(handle.read()).hexdigest()
            with open(vgm, "rb") as handle:
                vgm_digest = hashlib.sha256(handle.read()).hexdigest()
            digests.append((audio_digest, vgm_digest))
    assert len(set(digests)) == 1, "the same score rendered differently"


def test_sustain_level_zero_holds_and_fifteen_dies():
    # SL is the level at which the envelope hands over from decay to
    # sustain, so SL=0 sustains at full and SL=15 decays to nothing. It
    # reads backwards to anyone expecting "sustain level 15 = sustain
    # loudest", and getting it backwards makes every pad die mid-note and
    # every drum ring until key-off.
    import audio
    import opn2

    def held(sustain_level):
        carrier = opn2.Operator(multiple=1, total_level=0, attack_rate=31,
                                decay_rate=12, sustain_rate=0,
                                release_rate=7, sustain_level=sustain_level)
        mute = opn2.Operator(multiple=1, total_level=127, attack_rate=31)
        chip = opn2.YM2612()
        chip.set_instrument(0, opn2.FMInstrument(
            algorithm=7, feedback=0, operators=[carrier, mute, mute, mute]))
        chip.set_pan(0, True, True)
        chip.note_on(0, "A", 4)
        peak = audio.rms(chip.render(int(chip.native_rate * 0.05)))
        chip.render(int(chip.native_rate * 1.9))
        tail = audio.rms(chip.render(int(chip.native_rate * 0.05)))
        chip.close()
        return 20 * math.log10(max(1e-9, tail / max(1e-9, peak)))

    assert held(0) > -3.0, f"SL=0 should hold; measured {held(0):.1f} dB"
    assert held(15) < -12.0, f"SL=15 should die; measured {held(15):.1f} dB"


def test_detune_registers_bend_the_way_the_hardware_says():
    # DT1's low two bits are magnitude and bit 2 is sign: 0 and 4 are
    # neutral, 1-3 sharpen, 5-7 flatten. A map that sends "one step down"
    # to register 3 detunes upward.
    import opn2

    def frequency(detune, note="A", octave=6):
        operator = opn2.Operator(multiple=1, total_level=0, attack_rate=31,
                                 decay_rate=0, sustain_rate=0,
                                 release_rate=7, sustain_level=0,
                                 detune=detune)
        mute = opn2.Operator(multiple=1, total_level=127, attack_rate=31)
        chip = opn2.YM2612()
        chip.set_instrument(0, opn2.FMInstrument(
            algorithm=7, feedback=0, operators=[operator, mute, mute, mute]))
        chip.set_pan(0, True, True)
        chip.note_on(0, note, octave)
        buffer = chip.render(int(chip.native_rate * 2.0))
        chip.close()
        values = [left + right for left, right in buffer][5000:]
        crossings = sum(1 for i in range(1, len(values))
                        if values[i - 1] <= 0 < values[i])
        return crossings / (len(values) / chip.native_rate)

    neutral = frequency(0)
    assert abs(frequency(4) - neutral) < 0.4, "register 4 should be neutral too"
    assert frequency(3) > neutral + 0.4, "register 3 must sharpen"
    assert frequency(7) < neutral - 0.4, "register 7 must flatten"
    # ...and symmetrically, which is what makes 5/1 a usable +/-1 pair.
    assert abs((frequency(3) - neutral) + (frequency(7) - neutral)) < 0.6


def test_swapping_operators_two_and_three_changes_the_sound():
    # The register offsets ascend op1, op3, op2, op4. A renderer that
    # writes a musically-ordered operator list straight to base+i*4 swaps
    # two of them, and the result is a plausible timbre that is not the
    # one that was written. This asserts the two orderings are audibly
    # different, so the mistake cannot hide.
    import audio
    import opn2

    def voice(multiples):
        operators = [opn2.Operator(multiple=m, total_level=tl, attack_rate=31,
                                   decay_rate=8, sustain_rate=0,
                                   release_rate=7, sustain_level=0)
                     for m, tl in zip(multiples, (0, 24, 24, 0))]
        chip = opn2.YM2612()
        chip.set_instrument(0, opn2.FMInstrument(
            algorithm=4, feedback=0, operators=operators))
        chip.set_pan(0, True, True)
        chip.note_on(0, "A", 3)
        buffer = chip.render(int(chip.native_rate * 0.4))
        chip.close()
        return buffer

    straight = voice((1, 2, 7, 1))
    swapped = voice((1, 7, 2, 1))       # operators 2 and 3 exchanged
    n = min(len(straight), len(swapped))
    difference = audio.rms([(a[0] - b[0], a[1] - b[1])
                            for a, b in zip(straight[:n], swapped[:n])])
    reference = audio.rms(straight[:n])
    assert difference > reference * 0.1, \
        "swapping two operators changed nothing — the ordering is not applied"
