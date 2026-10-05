"""The shape of a module (structure.py): form by content, transitions,
channel roles with the rule that guessed them, and the 8-row fingerprints
that keep a split from leaking a fragment."""

import array
import tempfile
import os

import support
from schism import corpus, it_write, model as M, structure


def _cell(pattern, row, channel, note=0, inst=0, letter=None, param=0):
    c = pattern.cell(row, channel)
    if note:
        c.note, c.instrument = note, inst
    if letter:
        c.effect = M.EFFECT_LETTERS.index(letter)
        c.param = param


def _song():
    """Four channels: a kick and a snare at fixed pitches (channel 1), a
    low bass (2), a high line (3), and chords that start with the bass and
    the line (4). Orders 0 1 0 2, pattern 2 a copy of pattern 0; pattern 1
    ends on a jump and changes the tempo."""
    smp = M.Sample(name="s", data=array.array("h", [0, 9000, 0, -9000] * 16),
                   c5speed=8363, loop=(0, 64))
    instruments = []
    for name in ("Kick", "Snare", "Bass", "Lead", "Pad"):
        ins = M.Instrument(name=name)
        ins.use_sample(1)
        instruments.append(ins)
    mod = M.Module(title="shape", samples=[smp], instruments=instruments)

    def verse():
        p = M.Pattern(rows=32)
        for r in range(0, 32, 4):
            _cell(p, r, 0, 37 if r % 8 == 0 else 41, 1 if r % 8 == 0 else 2)
        for r in range(0, 32, 4):
            _cell(p, r, 1, 25 + (r // 4) % 3, 3)
        for r in range(0, 32, 2):
            _cell(p, r, 2, 73 + (r % 5), 4)
        for r in range(0, 32, 4):
            _cell(p, r, 3, 61 + (r // 8) % 2, 5)
        return p
    bridge = M.Pattern(rows=32)
    for r in range(0, 32, 8):
        _cell(bridge, r, 1, 30, 3)
        _cell(bridge, r, 2, 80, 4)
    _cell(bridge, 0, 0, letter="T", param=140)
    _cell(bridge, 31, 0, letter="B", param=2)
    mod.patterns = [verse(), bridge, verse()]
    mod.orders = [0, 1, 0, 2]
    return mod


def _open(folder, mod):
    path = os.path.join(folder, "shape.it")
    with open(path, "wb") as handle:
        handle.write(it_write.build(mod))
    return corpus.Module(path)


def test_the_form_letters_patterns_by_what_they_hold():
    support.need_openmpt()
    with tempfile.TemporaryDirectory() as folder:
        with _open(folder, _song()) as m:
            s = structure.structure(m)
    # pattern 1 jumps to order 2, which plays pattern 0 again, then 2 (= 0)
    assert s["form"] == "A B A A", s["form"]
    assert s["letters"] == 2
    kinds = [t["kinds"] for t in s["transitions"]]
    assert any("jump (Bxx)" in k for k in kinds), kinds
    assert any(any(x.startswith("speed/tempo") for x in k) for k in kinds)
    assert s["tempo_map"][0]["tempo"] == 125
    assert any(t["tempo"] == 140 for t in s["tempo_map"])
    assert s["density"]["notes"] > 0 and s["commands"]["position jump"] >= 1


def test_roles_are_guessed_and_say_by_which_rule():
    support.need_openmpt()
    with tempfile.TemporaryDirectory() as folder:
        with _open(folder, _song()) as m:
            s = structure.structure(m)
    roles = {c["channel"]: c["role_guess"] for c in s["channels"]}
    assert roles[1] == "percussion", roles
    assert roles[2] == "bass", roles
    assert roles[4] == "chords / pad", roles
    assert roles[3] == "melodic", roles
    assert all(c["role_rule"] for c in s["channels"])
    assert any("written register" in c for c in s["caveats"])


def test_fingerprints_fold_transposition_and_splits_keep_groups_whole():
    support.need_openmpt()
    mod = _song()
    # a copy of the verse a fifth up: the same fragments, transposed
    up = M.Pattern(rows=32)
    for r in range(32):
        for ch in range(4):
            c = mod.patterns[0].cell(r, ch)
            if c.note:
                _cell(up, r, ch, c.note + 7, c.instrument)
    mod.patterns.append(up)
    with tempfile.TemporaryDirectory() as folder:
        with _open(folder, mod) as m:
            fp = structure.fingerprints(m, [0, 3])
    assert fp and all(count % 2 == 0 for count in fp.values()), fp
    groups = {"alice": ["a1", "a2"], "bob": ["b1"], "carol": ["c1", "c2"],
              "dave": ["d1"], "erin": ["e1", "e2", "e3"]}
    plan = structure.splits(groups)
    again = structure.splits(dict(reversed(list(groups.items()))))
    assert plan == again
    seen = [g for entry in plan.values() for g in entry["groups"]]
    assert sorted(seen) == sorted(groups) and len(seen) == len(set(seen))
    assert all(plan[s]["items"] for s in ("test", "validation", "train"))
    # a fragment two splits share is found
    test_item = plan["test"]["items"][0]
    train_item = plan["train"]["items"][0]
    shared = next(iter(fp))
    prints = {test_item: {shared: 1}, train_item: {shared: 3}}
    found = structure.leaks(prints, plan)
    assert found["crossing_splits"] == 1
    assert sorted(found["examples"][0]["items"]) == sorted([test_item,
                                                            train_item])
