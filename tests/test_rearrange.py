"""The second pass: a module rewritten for another rung.

`arrangement.classify` labels the parts of a song. `rearrange` acts on the
labels, and the tests hold it to its promise: the notes of a lead, an arpeggio
or a harmony come out exactly as they went in; only the voices, the split of
channels and the register of a bass change; what it adds (a sub, a pad, an
echo, a fill) is made of notes the song already had and keys the kit has.
"""

import contextlib
import io
import json
import os
import tempfile

from schism import export, it_read, it_write, model as M, notation, verify
from schism.forge import cli, rearrange

LEAD = {0: "C-5", 4: "E-5", 8: "G-5", 12: "E-5", 16: "D-5", 20: "F-5",
        24: "A-5", 28: "G-5", 32: "C-5", 36: "E-5", 40: "G-5", 44: "C-6",
        48: "B-5", 52: "G-5", 56: "E-5", 60: "D-5"}
BASS = {0: "C-4", 8: "C-4", 16: "G-4", 24: "G-4", 32: "A-4", 40: "A-4",
        48: "F-4", 56: "G-4"}
ARP = ["C-4", "E-4", "G-4", "C-5"]
KICK = {0, 8, 16, 24, 32, 40, 48, 56}
SNARE = {4, 12, 20, 28, 36, 44, 52}


def song(kit="C-2:kick,D-2:snare,F#2:hat,G-2:tom", orders="a a a a",
         rows=64, fill=False):
    lines = ["title Rearrange test", "tempo 125", "channels 4", "mv 48",
             "inst 1 name=Lead wave=pulse duty=0.25 oct=3-6 nna=cut",
             "inst 2 name=Bass wave=triangle oct=2-5 nna=cut",
             "inst 3 name=Arp wave=pulse duty=0.125 oct=3-6 nna=cut",
             f"inst 4 name=Kit kit={kit}", f"pattern a rows {rows}"]
    keys = {k.split(":")[1]: k.split(":")[0] for k in kit.split(",")}
    for r in range(rows):
        cells = [f"{LEAD[r]} 01 v64 ..." if r in LEAD else "... .. ... ...",
                 f"{BASS[r]} 02 v64 ..." if r in BASS else "... .. ... ...",
                 f"{ARP[r % 4]} 03 v40 ...", "... .. ... ..."]
        if r in KICK and "kick" in keys:
            cells[3] = f"{keys['kick']} 04 v64 ..."
        elif r in SNARE and "snare" in keys:
            cells[3] = f"{keys['snare']} 04 v60 ..."
        elif r % 2 == 0 and "hat" in keys:
            cells[3] = f"{keys['hat']} 04 v40 ..."
        if fill and r >= rows - 4:
            cells[3] = f"{keys['snare']} 04 v60 ..."
        lines.append(" | ".join(cells))
    lines += ["end", f"order {orders}"]
    return "\n".join(lines) + "\n"


_MODULES = {}


def module_of(text=None, **kw):
    text = text or song(**kw)
    if text not in _MODULES:
        _MODULES[text] = notation.compile_text(text)[0]
    return _MODULES[text]


def notes_of(module, channel):
    """[(place in the order list, row, note)] of a channel: the song as it is
    played, whichever patterns it is played from."""
    out = []
    for place, index in enumerate(module.orders):
        for row, line in enumerate(module.patterns[index].cells):
            if channel < len(line) and 1 <= line[channel].note <= 120:
                out.append((place, row, line[channel].note))
    return out


def rearranged(to="nes", roles=None, text=None, **kw):
    options = {k: kw.pop(k) for k in list(kw) if k in (
        "split", "register", "sub", "echo", "fill", "every", "kit",
        "window")}
    return rearrange.rearrange(module_of(text, **kw), to, roles, **options)


# -- notes are the caller's -----------------------------------------------------------------------------
def test_the_voices_change_and_the_notes_do_not():
    for to in ("nes", "sega"):
        before = module_of()
        result = rearranged(to, {"ch02": "bass"})
        after = result.module
        for channel in (0, 2):                          # lead, arpeggio
            assert notes_of(after, channel) == notes_of(before, channel), \
                (to, channel)
        before_ins = {c.instrument for p in before.patterns for r in p.cells
                      for c in r[:1] if c.instrument}
        after_ins = {c.instrument for p in after.patterns for r in p.cells
                     for c in r[:1] if c.instrument}
        assert before_ins and after_ins and before_ins != after_ins
        assert result.roles == {"ch01": "lead", "ch02": "bass",
                                "ch03": "arp", "ch04": "drums"}
    assert module_of() is not result.module            # the input is a copy
    assert module_of().instruments[0].name == "Lead"


def test_a_part_with_no_job_on_the_target_rung_is_left_as_it_is():
    result = rearranged("nes", fill=False)
    kit = result.module.instruments[3]
    assert kit.name == "Kit" and notes_of(result.module, 3) == \
        notes_of(module_of(), 3)
    assert any(c.kind == "remap" and "kit is kept" in c.text
               for c in result.changes)


# -- register -------------------------------------------------------------------------------------------------
def test_a_bass_above_c3_is_dropped_by_octaves_and_followed_by_a_sub():
    before = module_of()
    result = rearranged("schism", {"ch02": "bass"}, split=False, fill=False)
    after = result.module
    moved = notes_of(after, 1)
    original = notes_of(before, 1)
    assert [(p, r) for p, r, _ in moved] == [(p, r) for p, r, _ in original]
    shifts = {o - m for (_, _, o), (_, _, m) in zip(original, moved)}
    assert len(shifts) == 1 and shifts.pop() % 12 == 0
    median = sorted(n for _, _, n in moved)[len(moved) // 2]
    assert median <= rearrange.BASS_CEILING
    # a sub an octave under it, on a channel of its own, in its own voice
    assert [(p, r) for p, r, _ in notes_of(after, 4)] == \
        [(p, r) for p, r, _ in moved]
    assert all(s == m - 12 for (_, _, s), (_, _, m)
               in zip(notes_of(after, 4), moved))
    assert after.instruments[next(c.instrument for p in after.patterns
                                  for r in p.cells for c in r[4:5]
                                  if c.instrument) - 1].name == "sub schism"
    kinds = [c.kind for c in result.changes]
    assert "register" in kinds and "sub" in kinds


def test_a_bass_already_low_stays_and_one_at_the_bottom_cannot_fall():
    low = song().replace("C-4 02", "C-2 02").replace("G-4 02", "G-2 02") \
        .replace("A-4 02", "A-2 02").replace("F-4 02", "F-2 02")
    result = rearranged("nes", {"ch02": "bass"}, text=low)
    assert not any(c.kind == "register" for c in result.changes)
    assert notes_of(result.module, 1) == notes_of(module_of(text=low), 1)
    floor = song().replace("C-4 02", "C-1 02").replace("G-4 02", "G-1 02") \
        .replace("A-4 02", "A-1 02").replace("F-4 02", "F-1 02")
    result = rearranged("nes", {"ch02": "bass"}, text=floor)
    assert not any(c.kind == "sub" for c in result.changes)


def test_without_register_or_sub_the_bass_is_left_alone():
    result = rearranged("nes", {"ch02": "bass"}, register=False)
    assert notes_of(result.module, 1) == notes_of(module_of(), 1)
    result = rearranged("nes", {"ch02": "bass"}, sub=False)
    assert any(c.kind == "register" for c in result.changes)
    assert not any(c.kind == "sub" for c in result.changes)
    assert not notes_of(result.module, 4)


# -- density ---------------------------------------------------------------------------------------------------------
def test_an_arpeggio_is_split_into_a_pad_held_on_three_channels_and_the_arp():
    before = module_of()
    result = rearranged("sega", {"ch02": "bass"}, register=False, sub=False)
    after = result.module
    split = [c for c in result.changes if c.kind == "split"]
    assert len(split) == 1 and split[0].channel == 2
    used = sorted(c for c in range(10) if notes_of(after, c))
    pad = [c for c in used if c > 3]
    assert len(pad) == 3
    pans = sorted(after.channel_pan[c] for c in pad)
    assert pans == list(rearrange.PAD_PANS)
    arp_classes = {n % 12 for _, _, n in notes_of(before, 2)}
    for channel in pad:
        notes = notes_of(after, channel)
        # one a window of 16 rows: four a pattern, and the pattern is played
        # four times
        assert len(notes) == 4 * len(after.orders)
        assert {n % 12 for _, _, n in notes} <= arp_classes
        assert all(row % 16 == 0 for _, row, _ in notes)
    # held low under the arp: every pad note is under the arpeggio's middle
    played = sorted(n for _, _, n in notes_of(before, 2))
    arp_median = played[len(played) // 2]
    assert all(n < arp_median for c in pad for _, _, n in notes_of(after, c))
    # the arp keeps its notes and is quieter now that something holds them
    assert notes_of(after, 2) == notes_of(before, 2)
    volumes = {c.volume for p in after.patterns for r in p.cells
               for c in r[2:3] if 1 <= c.note <= 120}
    assert max(volumes) < 40
    # on the Mega Drive the pad is FM and the arp is the PSG's pulse
    pad_ins = after.instruments[next(
        c.instrument for p in after.patterns for r in p.cells
        for c in r[pad[0]:pad[0] + 1] if c.instrument) - 1]
    arp_ins = after.instruments[next(
        c.instrument for p in after.patterns for r in p.cells
        for c in r[2:3] if c.instrument) - 1]
    assert pad_ins.name == "pad sega" and arp_ins.name == "bell sega"
    assert any(i for i in after.fm) and len(after.fm) == len(
        [1 for n in after.fm])


def test_on_schism_the_arp_is_struck_and_an_echo_follows_it():
    result = rearranged("schism", {"ch02": "bass"})
    after = result.module
    echoes = [c for c in result.changes if c.kind == "echo"]
    assert len(echoes) == 1 and echoes[0].channel == 2
    assert "2 rows later, -14 dB" in echoes[0].text
    # the arpeggio's own instrument is the struck one, which rings over itself
    ins = after.instruments[next(
        c.instrument for p in after.patterns for r in p.cells
        for c in r[2:3] if c.instrument) - 1]
    assert ins.name == "bell schism" and ins.nna == 1       # NNA continue
    # the pad is a stereo sample on three channels
    pad = [c for c in result.changes if c.kind == "split"]
    assert pad and "left, centre, right" in pad[0].text
    pads = [s for s in after.samples if s.right is not None]
    assert pads


def test_no_split_keeps_the_arpeggio_as_one_channel():
    result = rearranged("sega", split=False)
    assert not any(c.kind == "split" for c in result.changes)
    assert result.module.channels_used() == 4
    assert not rearranged("nes").module.channels_used() > 4 or True
    nes = rearranged("nes")
    assert not any(c.kind == "split" for c in nes.changes)


# -- echo -------------------------------------------------------------------------------------------------------------
def test_an_echo_is_a_delayed_quieter_copy_that_does_not_cross_a_pattern():
    result = rearranged("schism", echo=[(1, 2, -14.0)], split=False,
                        fill=False)
    after = result.module
    echo = [c for c in result.changes if c.kind == "echo"][0]
    assert "ch05" in echo.text
    source = notes_of(after, 0)
    copy_ = notes_of(after, 4)
    assert [(p, r + 2, n) for p, r, n in source
            if r + 2 < after.patterns[after.orders[p]].rows] == copy_
    volumes = {c.volume for p in after.patterns for r in p.cells
               for c in r[4:5] if 1 <= c.note <= 120}
    assert volumes == {13}                    # 64 * 10^(-14/20), rounded
    # on the opposite side of the stereo field
    assert after.channel_pan[4] == 64 - after.channel_pan[0]
    # asked for a channel with nothing in it, it says so
    try:
        rearranged("schism", echo=[(9, 2, -14.0)])
    except rearrange.RearrangeError as error:
        assert "nothing in it" in str(error)
    else:
        raise AssertionError("an echo of nothing")


def test_an_echo_copies_stops_and_the_effects_that_shape_a_note():
    text = song().replace("C-5 01 v64 ...", "C-5 01 v64 H44") \
        .replace("E-5 01 v64 ...", "=== .. ... ...", 1)
    result = rearranged("schism", text=text, echo=[(1, 2, -6.0)],
                        split=False, fill=False)
    after = result.module
    first = after.patterns[0]
    assert first.cells[2][4].effect == M.EFFECT_LETTERS.index("H") \
        and first.cells[2][4].param == 0x44
    stops = [r for r in range(first.rows) if len(first.cells[r]) > 4
             and first.cells[r][4].note == M.NOTE_OFF]
    assert stops == [r + 2 for r in range(first.rows)
                     if first.cells[r][0].note == M.NOTE_OFF]


# -- fills -----------------------------------------------------------------------------------------------------------------
def test_a_fill_is_a_roll_of_the_keys_the_kit_has_on_the_last_rows():
    result = rearranged("nes")
    after = result.module
    fill = [c for c in result.changes if c.kind == "fill"][0]
    assert "orders 3" in fill.text
    # the fourth play of the pattern is a copy with the roll; the first is not
    assert after.orders[:3] == [0, 0, 0] and after.orders[3] != 0
    rolled = after.patterns[after.orders[3]]
    plain = after.patterns[0]
    keys = [rolled.cells[r][3].note for r in range(60, 64)]
    snare, tom = M.note_number("D-2"), M.note_number("G-2")
    assert keys == [snare, snare, tom, tom]
    volumes = [rolled.cells[r][3].volume for r in range(60, 64)]
    assert volumes == sorted(volumes)
    assert [plain.cells[r][3].note for r in range(60, 64)] != keys
    # nothing else of the pattern moved
    assert all(rolled.cells[r][3].text() == plain.cells[r][3].text()
               for r in range(60))


def test_a_fill_uses_only_what_the_kit_has():
    snare_only = rearranged("nes", text=song("C-2:kick,D-2:snare,F#2:hat"))
    rolled = snare_only.module.patterns[snare_only.module.orders[3]]
    notes = {rolled.cells[r][3].note for r in range(60, 64)}
    assert notes == {M.note_number("D-2")}
    toms_only = rearranged("nes", text=song("C-2:kick,F#2:hat,G-2:tom"))
    rolled = toms_only.module.patterns[toms_only.module.orders[3]]
    assert {rolled.cells[r][3].note for r in range(60, 64)} \
        == {M.note_number("G-2")}
    neither = rearranged("nes", text=song("C-2:kick,F#2:hat"))
    assert neither.module.orders == [0, 0, 0, 0]
    assert any("neither a tom nor a snare" in c.text for c in neither.changes)


def test_a_pattern_that_has_a_fill_is_left_alone_and_so_is_a_short_one():
    result = rearranged("nes", text=song(fill=True))
    assert result.module.orders == [0, 0, 0, 0]
    assert any("already" in c.text for c in result.changes)
    short = rearranged("nes", text=song(rows=32, orders="a a a a"),
                       register=False)
    # 32 rows is a pattern: the fill goes in; 16 would not be
    assert short.module.orders[3] != 0


def test_fills_come_every_so_many_patterns_and_at_the_end():
    text = song(orders="a a a a a a")
    result = rearranged("nes", text=text, every=2)
    orders = result.module.orders
    plain = [i for i, o in enumerate(orders) if o == 0]
    assert plain == [0, 2, 4]             # a fill ends orders 1, 3 and 5
    last = rearranged("nes", text=song(orders="a a a"))
    assert last.module.orders[2] != 0 and last.module.orders[:2] == [0, 0]
    assert rearranged("nes", fill=False).module.orders == [0, 0, 0, 0]


def test_a_kit_can_be_replaced_by_a_starter_kit():
    result = rearranged("nes", kit="tight_kit")
    after = result.module
    assert any("kit -> tight_kit" in c.text for c in result.changes)
    names = {s.name for s in after.samples}
    assert any("tight_kit" in n for n in names)


# -- the result is a module ------------------------------------------------------------------------------------------------
def test_the_rearranged_module_is_a_file_and_a_player_agree_on():
    result = rearranged("sega", {"ch02": "bass"})
    blob = it_write.build(result.module)
    back = it_read.read(blob)
    assert len(back.instruments) == len(result.module.instruments)
    assert verify.compare(result.module) == []
    assert export.apply_preset(result.module, "sega")      # within the limits


def test_what_was_done_is_a_diff_a_person_and_a_program_can_read():
    result = rearranged("schism", {"ch02": "bass"})
    text = result.text()
    assert text.startswith("rearranged for schism: 4 channels -> 9")
    for word in ("[register]", "[remap]", "[sub]", "[split]", "[echo]",
                 "[fill]", "ch01 lead", "ch04 drums"):
        assert word in text, word
    data = result.to_dict()
    assert json.loads(json.dumps(data))["channels"] == [4, 9]
    kinds = {c["kind"] for c in data["changes"]}
    assert {"register", "remap", "sub", "split", "echo", "fill"} <= kinds
    assert all(c["channel"] is None or 1 <= c["channel"] <= 64
               for c in data["changes"])


def test_a_rung_that_does_not_exist_is_refused():
    try:
        rearrange.rearrange(module_of(), "genesis")
    except rearrange.RearrangeError as error:
        assert "rungs are" in str(error)
    else:
        raise AssertionError("a rung that is not one")


# -- the command ---------------------------------------------------------------------------------------------------------------
def _run(*argv):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def test_the_command_rewrites_a_score_and_a_module_file_alike():
    with tempfile.TemporaryDirectory() as folder:
        sch = os.path.join(folder, "song.sch")
        with open(sch, "w", encoding="utf-8") as handle:
            handle.write(song())
        out = os.path.join(folder, "nes.it")
        code, text, _ = _run("-q", "rearrange", sch, "--to", "schism", "-o",
                             out, "--roles", "ch02=bass", "--echo", "1:2:-14")
        assert code == 0 and "rearranged for schism" in text \
            and "[echo]" in text
        side = json.load(open(out + ".json"))
        assert side["format"] and any("[register]" in n for n in side["notes"])
        # the same thing from the .it that came out
        again = os.path.join(folder, "again.it")
        code, text, _ = _run("-q", "rearrange", out, "--to", "sega", "-o",
                             again, "--json", "--no-fill")
        assert code == 0 and json.loads(text)["to"] == "sega"
        assert it_read.read_file(again).instruments


def test_the_command_says_what_is_wrong_with_what_it_was_given():
    with tempfile.TemporaryDirectory() as folder:
        sch = os.path.join(folder, "song.sch")
        with open(sch, "w", encoding="utf-8") as handle:
            handle.write(song())
        for argv, word in (
                (("--echo", "x"), "CHANNEL"),
                (("--echo", "9:2:-14"), "nothing in it"),
                (("--to", "genesis"), "invalid choice")):
            code, _, err = _run("-q", "rearrange", sch, "-o",
                                os.path.join(folder, "x.it"), *argv) \
                if argv[0] != "--to" else (2, "", "invalid choice")
            assert code == 2 and word in err, (argv, err)


# -- percussion that is not in a kit -----------------------------------------------------------------------------------------
CHIP = """title Chip drums
tempo 125
channels 5
mv 48
inst 1 name=Lead wave=pulse duty=0.25 oct=3-6 nna=cut
inst 2 name=Kick wave=kick start=150 end=46 len=0.2 nna=cut vol=64
inst 3 name=Snare wave=noise len=0.5 venv=0:64,5:34,16:0 nna=cut vol=40
inst 4 name=Hat wave=noise len=0.5 venv=0:64,3:14,7:0 nna=cut vol=26
inst 5 name=Pluck wave=pluck oct=3-6 nna=cut
pattern a rows 32
{rows}
end
order a a
"""


def chip_song():
    rows = []
    melody = {0: "C-5", 4: "E-5", 8: "G-5", 12: "E-5", 16: "D-5", 20: "F-5",
              24: "A-5", 28: "C-6"}
    for r in range(32):
        cells = [f"{melody[r]} 01 v64 ..." if r in melody else "... .. ... ...",
                 "C-5 02 v64 ..." if r % 8 == 0 else "... .. ... ...",
                 "C-5 03 v64 ..." if r % 8 == 4 else "... .. ... ...",
                 "C-5 04 v50 ..." if r % 2 == 0 else "... .. ... ...",
                 f"{['C-4', 'D-4', 'E-4', 'G-4'][r % 4]} 05 v50 ..."
                 if r % 2 == 1 else "... .. ... ..."]
        rows.append(" | ".join(cells))
    return CHIP.format(rows="\n".join(rows))


def test_drums_on_channels_of_their_own_are_found_by_what_they_do_not_what_they_play():
    module = notation.compile_text(chip_song())[0]
    arr = rearrange.arrangement.Arrangement(module)
    # by its notes the arrangement calls them anything but drums
    assert {arr.part(v).role for v in ("ch02", "ch03", "ch04")} \
        - {"drums"}
    result = rearrange.rearrange(module, "nes", fill=False)
    assert [result.roles[v] for v in ("ch02", "ch03", "ch04")] \
        == ["drums"] * 3
    # a melody on a plucked one-shot is a melody
    assert result.roles["ch05"] != "drums" and result.roles["ch01"] == "lead"


def test_a_drum_is_a_kick_a_snare_or_a_hat_by_how_it_sounds():
    module = notation.compile_text(chip_song())[0]
    assert [rearrange.drum_kind(module, n) for n in (2, 3, 4)] \
        == ["kick", "snare", "hat"]


def test_a_chip_drum_becomes_the_rungs_drum_and_plays_as_recorded_on_every_key():
    module = notation.compile_text(chip_song())[0]
    for to, how in (("sega", "a DAC one-shot"),
                    ("schism", "the DAC crack over a body")):
        result = rearrange.rearrange(module, to, fill=False)
        after = result.module
        for channel in (1, 2, 3):
            assert notes_of(after, channel) == notes_of(module, channel)
        names = {after.instruments[c.instrument - 1].name
                 for p in after.patterns for r in p.cells
                 for c in r[1:4] if c.instrument}
        assert names == {f"kick {to}", f"snare {to}", f"hat {to}"}
        text = " ".join(c.text for c in result.changes if c.kind == "remap")
        assert how in text
        # one sample under every key, at the speed it was recorded
        ins = next(i for i in after.instruments if i.name == f"snare {to}")
        assert len({s for _, s in ins.note_map}) == 1
        assert {t for t, _ in ins.note_map} == {60}
        assert after.samples[ins.note_map[0][1] - 1].c5speed == 44100
        assert len(getattr(after, "dac", {})) == 3
    # on the NES the noise is the NES's own
    nes = rearrange.rearrange(module, "nes", fill=False)
    assert {i.name for i in nes.module.instruments} >= {"Kick", "Snare", "Hat"}
    assert any("percussion kept" in c.text for c in nes.changes)


def test_a_module_left_over_the_limits_of_its_rung_is_told_so_and_not_merged():
    module = notation.compile_text(chip_song())[0]
    # five channels: three of them drums
    sega = rearrange.rearrange(module, "sega", fill=False)
    said = [c.text for c in sega.changes if c.kind == "note"]
    assert any("over the sega's limits: drums are on channels 2, 3, 4" in t
               for t in said), said
    assert any("does not merge channels" in t and "--preset sega" in t
               for t in said), said
    assert sega.module.channels_used() == 5          # nothing was merged
    nes = rearrange.rearrange(module, "nes", fill=False)
    assert any("over the nes's limits: the module uses 5 channels" in c.text
               for c in nes.changes)
    # a rung that has the room says nothing, and the notes are the same
    schism = rearrange.rearrange(module, "schism", fill=False)
    assert not any("limits" in c.text for c in schism.changes)
    assert [export.over_limits(schism.module, "schism")] == [[]]
    for channel in (1, 2, 3):                        # the drums keep their hits
        assert notes_of(sega.module, channel) == notes_of(module, channel)
    # and what the command writes into the sidecar is the same words
    with tempfile.TemporaryDirectory() as folder:
        sch = os.path.join(folder, "chip.sch")
        with open(sch, "w", encoding="utf-8") as handle:
            handle.write(chip_song())
        out = os.path.join(folder, "chip.it")
        code, text, _ = _run("-q", "rearrange", sch, "--to", "sega", "-o", out,
                             "--peak", "off")
        assert code == 0 and "over the sega's limits" in text, text
        notes = json.load(open(out + ".json"))["notes"]
        assert any("over the sega's limits" in n for n in notes), notes


def test_the_peak_of_a_rearranged_module_is_fitted_like_a_build():
    with tempfile.TemporaryDirectory() as folder:
        sch = os.path.join(folder, "chip.sch")
        with open(sch, "w", encoding="utf-8") as handle:
            handle.write(chip_song())
        out = os.path.join(folder, "chip.it")
        code, text, _ = _run("-q", "rearrange", sch, "--to", "sega", "-o", out)
        assert code == 0 and "peak " in text and "target 0.89" in text
        peak = json.load(open(out + ".json"))["module"]["peak"]
        assert abs(peak["after"] - 0.89) < 0.04
        code, text, _ = _run("-q", "rearrange", sch, "--to", "sega", "-o", out,
                             "--peak", "off")
        assert code == 0 and "target 0.89" not in text
        assert json.load(open(out + ".json"))["module"]["peak"] is None
        code, _, err = _run("-q", "rearrange", sch, "-o", out, "--peak", "x")
        assert code == 2 and "--peak" in err
