"""it_export.py: the .it has to be a real module, not just bytes that parse.

Everything here reads the file back with an independent parser written
from the format rather than from the writer — a writer checked against
itself proves nothing.
"""

import os
import struct

import events as E
import it_export
import support
import tracker


# -- an independent reader ---------------------------------------------------
def _read_it(raw: bytes):
    (magic, songname, _hmin, _hmaj, nord, nins, nsmp, npat, _cwtv, _cmwt,
     flags, special, _gv, _mv, speed, tempo, _sep, _pwd, msglen, msgoff,
     _reserved) = struct.unpack_from("<4s26sBBHHHHHHHHBBBBBBHII", raw, 0)
    assert magic == b"IMPM"
    pos = 64
    chnpan = list(raw[pos:pos + 64]); pos += 64
    pos += 64                                    # channel volumes
    assert pos == 0xC0
    orders = list(raw[pos:pos + nord]); pos += nord
    pos += 4 * nins
    para_smp = list(struct.unpack_from(f"<{nsmp}I", raw, pos)); pos += 4 * nsmp
    para_pat = list(struct.unpack_from(f"<{npat}I", raw, pos))

    samples = []
    for offset in para_smp:
        f = struct.unpack_from("<4s12sBBBB26sBBIIIIIIIBBBB", raw, offset)
        assert f[0] == b"IMPS", f[0]
        samples.append({"name": f[6].split(b"\0")[0].decode("ascii", "replace"),
                        "flags": f[4], "length": f[9], "loopbegin": f[10],
                        "loopend": f[11], "c5speed": f[12], "pointer": f[15]})

    patterns = [_unpack(raw, offset) for offset in para_pat]
    message = (raw[msgoff:msgoff + msglen].split(b"\0")[0].decode("ascii", "replace")
               if msglen else "")
    return {"title": songname.split(b"\0")[0].decode("ascii", "replace"),
            "orders": orders, "samples": samples, "patterns": patterns,
            "speed": speed, "tempo": tempo, "flags": flags, "special": special,
            "chnpan": chnpan, "message": message, "size": len(raw)}


def _unpack(raw: bytes, offset: int):
    length, nrows, _ = struct.unpack_from("<HHI", raw, offset)
    data = raw[offset + 8:offset + 8 + length]
    rows = [{} for _ in range(nrows)]
    last_mask, last_value = {}, {}
    pos = row = 0
    while row < nrows and pos < len(data):
        head = data[pos]; pos += 1
        if head == 0:
            row += 1
            continue
        channel = (head & 0x7F) - 1
        if head & 0x80:
            last_mask[channel] = data[pos]; pos += 1
        mask = last_mask.get(channel, 0)
        previous = last_value.setdefault(channel, {})
        if mask & 1:
            previous["note"] = data[pos]; pos += 1
        if mask & 2:
            previous["ins"] = data[pos]; pos += 1
        if mask & 4:
            previous["vol"] = data[pos]; pos += 1
        if mask & 8:
            previous["fx"], previous["param"] = data[pos], data[pos + 1]; pos += 2
        cell = {}
        if mask & 0x11: cell["note"] = previous.get("note")
        if mask & 0x22: cell["ins"] = previous.get("ins")
        if mask & 0x44: cell["vol"] = previous.get("vol")
        if mask & 0x88:
            cell["fx"] = previous.get("fx")
            cell["param"] = previous.get("param")
        if cell:
            rows[row][channel] = cell
    return rows


SCORE = """
title Test Module
ticks 240
bpm 150
lpb 4
inst fm0 bass
inst fm1 square_lead
pan fm0 C
pan fm1 R
cols fm0 fm1 psg0 noise dac

A-2  A-4  E-5  w1   kick
...  ...  ...  ===  ...
C-3  C-5  ...  w1   snare
...  ===  ===  ===  ...
E-3  E-4  A-5  w1   kick
...  ...  ...  ===  hat
G-3  ...  ...  ...  ...
===  ...  ===  ...  clap
"""


def _export(text=SCORE):
    events, meta = tracker.loads(text)
    data, report = it_export.build(events, meta=meta, title=meta.title or "t",
                                   message="hello")
    return events, meta, data, report, _read_it(data)


# -- the format itself -------------------------------------------------------
def test_the_file_is_a_parseable_it_module():
    _events, _meta, data, _report, module = _export()
    assert data[:4] == b"IMPM"
    assert module["title"] == "Test Module"
    assert module["message"] == "hello"
    assert module["orders"][-1] == 255, "IT order lists end with 0xFF"
    assert module["patterns"], "no patterns written"
    assert module["flags"] & 1, "stereo flag not set"
    assert not (module["flags"] & 4), \
        "instrument-mode flag set, but this writes sample mode"


def test_every_parapointer_lands_on_real_data():
    _events, _meta, data, _report, module = _export()
    for sample in module["samples"]:
        end = sample["pointer"] + sample["length"] * 2   # 16-bit
        assert end <= len(data), (sample["name"], end, len(data))
        if sample["flags"] & 16:
            assert 0 <= sample["loopbegin"] < sample["loopend"] <= sample["length"], \
                sample


def test_every_note_survives_the_round_trip():
    # The one that matters: the arrangement is the deliverable.
    events, meta, _data, _report, module = _export()
    ticks_per_row = meta.ticks_per_row()

    expected, tick = set(), 0
    for event in events:
        if isinstance(event, E.Wait):
            tick += event.ticks
            continue
        if isinstance(event, E.End):
            break
        row = tick // ticks_per_row
        if isinstance(event, E.FMNoteOn):
            expected.add((row, it_export.CHANNEL_MAP[("fm", event.channel)],
                          event.octave * 12 + E.NOTE_NAMES.index(event.note)))
        elif isinstance(event, E.PSGToneOn):
            expected.add((row, it_export.CHANNEL_MAP[("psg", event.channel)],
                          event.octave * 12 + E.NOTE_NAMES.index(event.note)))

    got = set()
    for index, pattern in enumerate(module["patterns"]):
        for row_index, row in enumerate(pattern):
            for channel, cell in row.items():
                note = cell.get("note")
                if note is not None and note < 120 and channel < 9:
                    got.add((index * it_export.IT_ROWS_PER_PATTERN + row_index,
                             channel, note))
    assert expected, "the fixture plays no notes"
    assert got == expected, (sorted(expected - got), sorted(got - expected))


def test_note_offs_are_written_as_255():
    _events, _meta, _data, _report, module = _export()
    offs = [cell for pattern in module["patterns"] for row in pattern
            for cell in row.values() if cell.get("note") == 255]
    assert offs, "the fixture has `===` rows but none reached the file"


def test_panning_reaches_the_channel_table():
    _events, _meta, _data, _report, module = _export()
    assert module["chnpan"][0] == 32, "pan fm0 C should be centre"
    assert module["chnpan"][1] == 64, "pan fm1 R should be hard right"


# -- timing ------------------------------------------------------------------
def test_timing_is_exact_for_ordinary_tempos_and_prefers_speed_six():
    for bpm, lpb in ((150, 4), (120, 4), (128, 4), (174, 4)):
        row = 60.0 / (bpm * lpb)
        speed, tempo, error = it_export._it_timing(row)
        assert error < 1e-9, (bpm, lpb, error)
        # speed 6 makes `tempo` equal the real BPM, which is what someone
        # opening the file in a tracker expects to see.
        assert speed == 6 and tempo == bpm, (bpm, lpb, speed, tempo)


def test_an_unrepresentable_row_is_refused_not_rounded():
    try:
        it_export._it_timing(0.0005)          # 0.5ms, far below IT's floor
    except it_export.ITExportError as exc:
        assert "floor" in str(exc)
    else:
        raise AssertionError("a 0.5ms row should not have been accepted")


# -- samples -----------------------------------------------------------------
def test_loops_are_whole_periods_and_wrap_without_a_click():
    import instruments

    sample = it_export.render_fm_sample(instruments.get("square_lead"))
    start, end = sample["loop"]
    assert (end - start) % it_export.FRAMES_PER_PERIOD == 0, \
        "loop is not a whole number of periods"

    values = struct.unpack(f"<{len(sample['data']) // 2}h", sample["data"])
    body = values[start:end]
    slopes = sorted(abs(body[i + 1] - body[i]) for i in range(len(body) - 1))
    typical = slopes[int(len(slopes) * 0.95)] or 1
    # The wrap from the last frame back to the first must not jump further
    # than the waveform normally moves in one frame, or it clicks once per
    # loop — an audible buzz at the loop rate.
    assert abs(body[0] - body[-1]) <= typical * 1.5, \
        f"loop seam jumps {abs(body[0] - body[-1])} against a typical {typical}"


def test_noise_is_a_one_shot_because_looping_it_would_pitch_it():
    sample = it_export.render_psg_noise_sample()
    assert sample["loop"] is None


def test_only_the_voices_actually_played_get_rendered():
    events, _meta = tracker.loads(SCORE)
    samples, index = it_export.collect_samples(events)
    names = [s["name"] for s in samples]
    assert "bass" in names and "square_lead" in names
    assert "DAC kick" in names and "DAC snare" in names and "DAC clap" in names
    assert "DAC tom" not in names, "rendered a kit piece the score never plays"
    assert all(number >= 1 for number in index.values()), \
        "IT sample numbers are one-based"


# -- arpeggios ---------------------------------------------------------------
def test_an_arpeggio_becomes_its_native_it_effect():
    # chipgen spells an arpeggio as FMPitch events inside a row; IT spells
    # it as Jxy. Dropping them would lose the part a listener notices.
    text = ("ticks 240\nbpm 150\nlpb 4\ninst fm0 square_lead\ncols fm0\n"
            "arp fm0 0 3 7\nA-4\n...\n...\n===\n")
    events, meta = tracker.loads(text)
    _data, report = it_export.build(events, meta=meta)
    grid, skipped = it_export.build_grid(
        events, meta.ticks_per_row(),
        it_export.collect_samples(events)[1])
    effects = [(cell.effect, cell.param) for row in grid.rows for cell in row
               if cell.effect]
    assert effects, "no arpeggio effect emitted"
    assert all(fx == it_export._FX_ARPEGGIO for fx, _ in effects), effects
    assert all(param == 0x37 for _, param in effects), \
        f"expected J37 for `arp 0 3 7`, got {effects}"
    assert "arpeggio with more than three steps, truncated" not in skipped


def test_a_four_step_arpeggio_is_truncated_and_says_so():
    # IT's J holds a base plus two offsets. A four-step arp cannot fit, and
    # silently dropping the fourth would be the wrong kind of quiet.
    text = ("ticks 240\nbpm 150\nlpb 4\ninst fm0 square_lead\ncols fm0\n"
            "arp fm0 0 4 7 12\nA-4\n...\n===\n")
    events, meta = tracker.loads(text)
    _grid, skipped = it_export.build_grid(
        events, meta.ticks_per_row(),
        it_export.collect_samples(events)[1])
    assert "arpeggio with more than three steps, truncated" in skipped


def test_a_real_detune_is_not_mistaken_for_an_arpeggio():
    text = ("ticks 240\nbpm 150\nlpb 4\ninst fm0 square_lead\ncols fm0\n"
            "pitch fm0 -35\nA-4\n...\n===\n")
    events, meta = tracker.loads(text)
    found = it_export._arpeggios(events, meta.ticks_per_row())
    assert not found, f"-35 cents is a detune, not an arpeggio: {found}"


# -- end to end --------------------------------------------------------------
def test_export_writes_a_file_and_reports_what_it_could_not_carry():
    events, meta = tracker.loads(SCORE)
    with support.TempDir() as directory:
        path = os.path.join(directory, "t.it")
        report = it_export.export(events, path, meta=meta)
        assert os.path.getsize(path) == report["bytes"]
        with open(path, "rb") as handle:
            assert handle.read(4) == b"IMPM"
    assert report["speed"] == 6 and report["tempo"] == 150
    assert report["timing_error_ms"] < 1e-6
    assert isinstance(report["skipped"], dict)


def test_a_silent_event_list_is_refused_rather_than_written_empty():
    try:
        it_export.build([E.Wait(ticks=192), E.End()])
    except it_export.ITExportError as exc:
        assert "no notes" in str(exc)
    else:
        raise AssertionError("an empty module should not have been written")


def test_the_opl_reaches_the_module_too():
    # The .it is the only export that carries every chip, and the CLI says
    # so when a score plays the OPL2 and asks for a .vgm. That claim has to
    # be true: an OPL part silently missing here would make the advice
    # actively wrong.
    text = ("ticks 240\nbpm 150\nlpb 4\n"
            "inst opl0 opl_bass\ninst opl3 opl_bell\ninst fm0 bass\n"
            "cols opl0 opl3 fm0 dac\n"
            "A-2  C-5  A-2  kick\n...  ...  ...  ...\n"
            "C-3  E-5  C-3  snare\n===  ===  ===  ...\n")
    events, meta = tracker.loads(text)
    data, report = it_export.build(events, meta=meta)
    module = _read_it(data)

    names = [name for name, *_ in report["samples"]]
    assert any(n.startswith("OPL ") for n in names), names
    assert "bass" in names, names

    notes = {(channel, cell["note"])
             for pattern in module["patterns"] for row in pattern
             for channel, cell in row.items()
             if cell.get("note") is not None and cell["note"] < 120}
    for opl_channel in (0, 3):
        assert any(ch == it_export.CHANNEL_MAP[("opl", opl_channel)]
                   for ch, _ in notes), f"opl{opl_channel} is missing"
    assert any(ch == it_export.CHANNEL_MAP[("fm", 0)] for ch, _ in notes)
    # OPLDepth has no IT equivalent and must be reported, not dropped quietly.
    assert not [k for k in report["skipped"] if k.startswith("OPLNote")], \
        report["skipped"]


def test_opl_channels_do_not_collide_with_the_others():
    seen = {}
    for key, channel in it_export.CHANNEL_MAP.items():
        assert channel not in seen, f"{key} collides with {seen[channel]}"
        seen[channel] = key
    assert max(seen) < it_export.IT_CHANNELS_USED
