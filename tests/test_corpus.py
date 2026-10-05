"""The corpus reader: what a module stores against what it plays, episodes
with the state they start in, and libopenmpt's effect numbers against the
letters a tracker shows.

The modules are built here, small, with the jumps, breaks and loops that
make an order list a poor guide to what is heard.
"""

import array
import hashlib
import json
import os
import tempfile

import support
from schism import corpus, it_write, model as M


def _cell(pattern, row, channel, note=0, inst=0, letter=None, param=0):
    c = pattern.cell(row, channel)
    if note:
        c.note, c.instrument = note, inst
    if letter:
        c.effect = M.EFFECT_LETTERS.index(letter)
        c.param = param


def _module():
    """orders 0 1 2 3. Pattern 0 sets speed 4 and a vibrato of 44; pattern 1
    jumps to order 3 from its last row, so pattern 2 is listed and never
    plays; pattern 3 loops its first four rows three times and has a
    vibrato with no parameter (it uses the memory); pattern 4 is stored and
    not listed."""
    smp = M.Sample(name="s", data=array.array("h", [0, 9000, 0, -9000] * 16),
                   c5speed=8363, loop=(0, 64))
    ins = M.Instrument(name="Tone")
    ins.use_sample(1)
    mod = M.Module(title="flow", samples=[smp], instruments=[ins])
    pats = [M.Pattern(rows=32) for _ in range(5)]
    _cell(pats[0], 0, 0, 61, 1, "A", 0x04)
    _cell(pats[0], 0, 1, 65, 1, "H", 0x44)
    _cell(pats[1], 0, 0, 63, 1)
    _cell(pats[1], 31, 0, letter="B", param=3)
    _cell(pats[2], 0, 0, 70, 1)
    _cell(pats[3], 0, 0, 61, 1, "S", 0xB0)
    _cell(pats[3], 0, 1, letter="H", param=0x00)
    _cell(pats[3], 3, 0, letter="S", param=0xB2)
    _cell(pats[4], 0, 0, 72, 1)
    mod.patterns = pats
    mod.orders = [0, 1, 2, 3]
    return mod


def _file(folder, module, name="flow.it"):
    path = os.path.join(folder, name)
    with open(path, "wb") as handle:
        handle.write(it_write.build(module))
    return path


def test_libopenmpts_effect_numbers_are_not_the_letters():
    support.need_openmpt()
    mod = _module()
    pat = M.Pattern(rows=32)
    letters = M.EFFECT_LETTERS.strip()
    for row, letter in enumerate(letters):
        _cell(pat, row, 0, 61, 1, letter, 0x12)
    mod.patterns = [pat]
    mod.orders = [0]
    with tempfile.TemporaryDirectory() as folder:
        with corpus.Module(_file(folder, mod)) as m:
            for row, letter in enumerate(letters):
                number = m.normalized(0, row, 0)["effect"]
                assert corpus.IT_LETTER[number] == letter, (letter, number)
                assert m.native(0, row, 0).endswith(f"{letter}12")
    # libopenmpt prints the instrument and the volume in hex: what reads
    # `14` and `v40` in an episode is instrument 20 at volume 64
    pat = M.Pattern(rows=32)
    _cell(pat, 0, 0, 61, 20)
    pat.cell(0, 0).volume = 64
    mod.instruments = mod.instruments * 20
    mod.patterns = [pat]
    with tempfile.TemporaryDirectory() as folder:
        with corpus.Module(_file(folder, mod)) as m:
            assert m.native(0, 0, 0) == "C-5 14v40 ...", m.native(0, 0, 0)
            n = m.normalized(0, 0, 0)
            assert (n["instrument"], n["volume"]) == (20, 64), n
    # the map is one to one, and every number has a name
    assert len(set(corpus.IT_LETTER.values())) == 26
    assert all(n in corpus.EFFECT_NAMES for n in corpus.IT_LETTER)


def test_what_is_stored_is_not_what_plays():
    support.need_openmpt()
    with tempfile.TemporaryDirectory() as folder:
        path = _file(folder, _module())
        with corpus.Module(path) as m:
            stored = corpus.static(m)
            played = corpus.trace(m)
        assert stored["listed"] == [0, 1, 2, 3]
        assert stored["unlisted_nonempty"] == [4]
        assert played["reached"] == [0, 1, 3], played["reached"]
        assert played["stopped"] == "song end"
        # the song ends on the last row of pattern 3, not back at the start
        assert played["rows"][-1][:3] == [3, 3, 31], played["rows"][-1]
        # the loop: rows 0-3 of pattern 3 play three times, the rest once
        visits = {}
        for order, pattern, row, speed, tempo, at in played["rows"]:
            visits[(pattern, row)] = visits.get((pattern, row), 0) + 1
            assert speed == 4 or (pattern, row) == (0, 0)
        assert [visits[(3, r)] for r in range(6)] == [3, 3, 3, 3, 1, 1]
        assert (2, 0) not in visits
        # each row says when it starts: 32 rows of 4 ticks of 20 ms a pattern
        times = [r[5] for r in played["rows"]]
        assert times == sorted(times) and times[0] == 0.0
        assert abs(corpus.first_time(played["rows"], 1, 0) - 2.56) <= 0.004
        assert abs(corpus.first_time(played["rows"], 3, 0) - 5.12) <= 0.004
        assert corpus.first_time(played["rows"], 2, 0) is None
        entry = corpus.index_module(path, {"author": "test"})
        assert entry["played"]["listed_never_played"] == [2]
        assert entry["stored"]["unlisted_nonempty"] == [4]
        assert entry["source"] == {"author": "test"}
        assert entry["sha256"] == hashlib.sha256(
            open(path, "rb").read()).hexdigest()
        assert all(c["pattern"] in (0, 1, 3) for c in entry["candidates"])


def test_an_episode_starts_in_the_state_the_song_is_in():
    """An H00 in the episode means "the vibrato I had": the episode must
    say what that was (44, from pattern 0), and the speed the rows play at
    (4, set in pattern 0) — neither is in the episode's own rows."""
    support.need_openmpt()
    with tempfile.TemporaryDirectory() as folder:
        path = _file(folder, _module())
        ep = corpus.episode(path, order=3, row=0, rows=8, context=1)
        state = ep["entry_state"]
        assert state["speed"] == 4
        assert state["channels"]["2"]["memory"] == {"vibrato": "44"}
        assert state["channels"]["1"]["instrument"] == 1
        first = [line for line in ep["lines"] if not line["context"]][0]
        assert (first["pattern"], first["row"]) == (3, 0)
        cell = [c for c in first["cells"] if c["channel"] == 2][0]
        assert cell["native"].endswith("H00")
        assert cell["normalized"]["effect"] == 5
        assert cell["normalized"]["effect_name"] == "vibrato"
        # the line before it is context, from the pattern that jumped here
        assert ep["lines"][0]["context"] and ep["lines"][0]["pattern"] == 1
        assert ep["source"]["sha256"] and ep["it_instruments"]["1"]["name"] \
            == "Tone"
        json.dumps(ep)
        # a row the song never plays is refused, with what it does play
        try:
            corpus.episode(path, order=2, row=0)
        except corpus.CorpusError as error:
            assert "never plays" in str(error) and "[0, 1, 3]" in str(error)
        else:
            raise AssertionError("an unplayed row made an episode")


def test_a_song_that_loops_forever_stops_at_the_limit():
    support.need_openmpt()
    mod = _module()
    # libopenmpt ends a song when it comes back to where it has been, so a
    # jump backwards is a song end, not a hang; the limit is for the songs
    # that are simply longer than anyone wants to trace (or that loop in
    # ways the end detection misses): here, a long one cut at one second
    mod.orders = [0, 1, 2, 3] * 20
    with tempfile.TemporaryDirectory() as folder:
        with corpus.Module(_file(folder, mod)) as m:
            played = corpus.trace(m, limit_s=1.0)
        assert played["stopped"] == "limit" and played["seconds"] <= 1.01


def test_the_originals_are_read_and_never_written():
    support.need_openmpt()
    with tempfile.TemporaryDirectory() as folder:
        path = _file(folder, _module())
        before = open(path, "rb").read()
        stamp = os.stat(path).st_mtime_ns
        corpus.index([path])
        corpus.episode(path, 3, 0)
        assert open(path, "rb").read() == before
        assert os.stat(path).st_mtime_ns == stamp
