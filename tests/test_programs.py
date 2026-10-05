"""The account of what an instrument does over a note (forge/program.py),
the phase readings (forge/phases.py), and the worked example of a
technique from the corpus (examples/echo): A and B differ in one mechanism,
and the readings show that mechanism doing what it is for."""

import importlib.util
import math
import os

import support
from schism import notation
from schism.forge import patch as patch_mod, phases, program

ECHO = os.path.join(support.ROOT, "examples", "echo")


def _echo():
    spec = importlib.util.spec_from_file_location(
        "echo_make", os.path.join(ECHO, "make.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_patch_written_in_seconds_says_where_its_nodes_landed():
    patch = patch_mod.Patch(family="tone",
                            amp=[[0.0, 1.0], [0.004, 0.5], [0.009, 0.2],
                                 [0.5, 0.0]])
    report = program.explain_patch(patch, tempo=125)
    kinds = {loss["kind"]: loss for loss in report["losses"]}
    # at 20 ms a tick, nodes 4 and 9 ms in land on ticks 0 and 0: merged
    assert kinds["merged"]["size"] == 2, report["losses"]
    assert kinds["clock"]["size"] > 0
    ticks = [n["tick"] for n in report["envelopes"]["volume"]["file"]]
    assert ticks == sorted(set(ticks)) and ticks[0] == 0
    # the same envelope at a faster tempo moves less
    fast = program.explain_patch(patch, tempo=255)
    assert fast["tick_ms"] < report["tick_ms"]


def test_an_instrument_says_what_a_key_off_and_the_next_note_do():
    module, _ = notation.compile_file(os.path.join(ECHO, "echo_on_note.sch"))
    info = program.explain_instrument(module, 1)
    assert info["tick_ms"] == 20.0 and info["nna"] == "note off"
    assert info["volume"]["nodes"][3] == {"tick": 12, "ms": 240.0,
                                          "value": 34}
    assert info["pitch_or_filter"]["slot"] == "filter"
    text = " ".join(info["notes"])
    assert "no sustain loop" in text and "does not cut it" in text
    cut, _ = notation.compile_file(os.path.join(ECHO,
                                                "echo_nna_cut_motif.sch"))
    assert any("ends this one" in n
               for n in program.explain_instrument(cut, 1)["notes"])
    caps = program.capabilities()
    names = [t["target"] for t in caps["targets"]]
    assert any(n.startswith("venv") for n in names)
    assert any("legato" in limit for limit in caps["limits"])


_CARRY = """title carry
tempo 125
speed 6
channels 1
inst 1 name=Tone wave=saw oct=4-6 nna=cut venv=0:64,40:0{carry}
inst 2 name=Other wave=saw oct=4-6 nna=cut venv=0:64,40:0{carry}
pattern a rows 32
C-5 01 ... ...
rest 1
{between}
rest 1
C-5 0{second} ... ...
rest 3
=== .. ... ...
rest 23
end
order a
"""


def _second_note_db(text, engine):
    from schism import it_write, render as R
    module, _ = notation.compile_text(text)
    frames = R.render_frames(it_write.build(module), 44100, 0.8, engine)
    mono = [(a + b) * 0.5 for a, b in zip(frames[0::2], frames[1::2])]
    env = phases.envelope(phases.centred(mono, 44100), 44100)
    def at(ms):
        return 20.0 * math.log10(max(env[ms + 3:ms + 15]) + 1e-12)
    return at(480) - at(0), module


def test_carry_picks_the_envelope_up_where_the_last_note_left_it():
    """IT's carry flag (bit 3), measured: the second note, 24 ticks into a
    64-to-0 fall over 40 ticks, starts at 25.6/64 (-8 dB) of the first when
    the envelope carries, at the first's level when it does not, and a note
    of another instrument starts its own envelope either way, and so does a
    note after a key-off (Manwe's New Wind, read in the corpus, keys its
    carried filter off between some notes and not others). Schism Tracker
    plays it the same (0.2 dB), so a card can lean on it."""
    support.need_openmpt()
    from schism import it_read, it_write, schismtracker
    engines = ["openmpt"] + (["schism"] if schismtracker.available() else [])
    for engine in engines:
        held = "... .. ... ..."
        drop, module = _second_note_db(
            _CARRY.format(carry=",carry", second=1, between=held), engine)
        assert -8.6 <= drop <= -7.4, (engine, drop)
        for released in ("=== .. ... ...", "^^^ .. ... ..."):
            drop, _ = _second_note_db(
                _CARRY.format(carry=",carry", second=1, between=released),
                engine)
            assert abs(drop) <= 0.5, (engine, released, drop)
        back = it_read.read(it_write.build(module), load_data=False)
        assert back.instruments[0].volume_envelope.carry
        info = program.explain_instrument(back, 1)
        assert info["volume"]["carry"] is True
        assert any("carry on the volume envelope" in n for n in info["notes"])
        drop, module = _second_note_db(
            _CARRY.format(carry="", second=1, between=held), engine)
        assert abs(drop) <= 0.5, (engine, drop)
        assert not module.instruments[0].volume_envelope.carry
        drop, _ = _second_note_db(
            _CARRY.format(carry=",carry", second=2, between=held), engine)
        assert abs(drop) <= 0.5, (engine, drop)


def test_a_note_map_that_names_a_missing_sample_is_said_not_fatal():
    """Real modules carry note maps that point past their last sample (an
    instrument edited after its samples were deleted); explaining one must
    say so, not fail."""
    module, _ = notation.compile_file(os.path.join(ECHO, "echo_on_note.sch"))
    ins = module.instruments[0]
    ins.note_map = [(n, 9 if n == 60 else s) for n, s in ins.note_map]
    info = program.explain_instrument(module, 1)
    assert any("sample 9" in n and "play nothing" in n for n in info["notes"])
    assert all(row["sample"] != 9 for row in info["samples"])


def test_phases_read_the_attack_on_a_millisecond_grid_and_name_the_rest():
    rate = 44100.0
    x = []
    for i in range(int(1.2 * rate)):
        t = i / rate
        env = min(1.0, t / 0.005) if t < 0.8 else math.exp(-(t - 0.8) / 0.05)
        x.append(0.5 * env * math.sin(2 * math.pi * 220 * t))
    r = phases.note(x, rate, 220.0, 0.8)
    assert r["attack"]["rise_ms"] <= 6.0
    assert abs(r["phases"]["body"]["pitch_cents"]) < 2.0
    assert 90 <= r["release"]["ms_to_minus20"] <= 140
    assert r["loop_seam"] is None and "no loop" in r["loop_seam_why"]
    clean = phases.loop_seam([math.sin(2 * math.pi * i / 64) * 1000
                              for i in range(64)], 0, 64)
    click = phases.loop_seam([math.sin(2 * math.pi * i / 60) * 1000
                              for i in range(64)], 0, 64)
    assert clean["step"] < 2.0 < click["step"]


def test_the_echo_pair_differs_in_the_volume_envelope_and_nothing_else():
    a, _ = notation.compile_file(os.path.join(ECHO, "echo_on_motif.sch"))
    b, _ = notation.compile_file(os.path.join(ECHO, "echo_off_motif.sch"))
    ia, ib = a.instruments[0], b.instruments[0]
    assert ia.volume_envelope.nodes != ib.volume_envelope.nodes
    for attr in ("pan_envelope", "pitch_envelope"):
        assert getattr(ia, attr).nodes == getattr(ib, attr).nodes
    for attr in ("nna", "dct", "dca", "fadeout", "cutoff", "resonance"):
        assert getattr(ia, attr) == getattr(ib, attr), attr
    notes = [[(c.note, c.instrument) for c in row] for p in a.patterns
             for row in p.cells]
    assert notes == [[(c.note, c.instrument) for c in row]
                     for p in b.patterns for row in p.cells]


def test_the_echo_is_there_where_it_should_be_and_the_hit_is_untouched():
    support.need_openmpt()
    make = _echo()
    _, on = make.build("echo_on_note.sch")
    _, off = make.build("echo_off_note.sch")
    result = make.check(make.hits(*make.play(on, seconds=1.4)),
                        make.hits(*make.play(off, seconds=1.4)))
    first = result.pop("first_hit_unchanged_db")
    assert all(result.values()), result
    assert abs(first) < 0.05
