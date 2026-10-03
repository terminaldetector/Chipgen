"""verify.py — does libopenmpt parse the module as it was written?

`compare(module)` writes the module, hands the bytes to libopenmpt, and
walks every cell, name and count, returning what differs. An empty list is
the claim "an independent reader sees exactly what was meant"; it is the
test every writer change has to pass, and the check the CLI runs before it
reports a build as done.

It does not say the module *sounds* right. Sound is `tests/test_audio.py`
and the demos' own measurements: pitch, timing, envelopes.
"""

from . import it_write, model as M, openmpt

_VOLUME_KIND = {"v": 1, "p": 2}


def expected_cell(cell: M.Cell):
    """The six strings libopenmpt should give for a cell."""
    note = M.note_name(cell.note)
    ins = f"{cell.instrument:02X}" if cell.instrument else ".."
    if cell.volume < 0:
        letter, amount = " ", ".."
    else:
        text = M.volume_column_text(cell.volume)
        letter, amount = text[0], f"{int(text[1:]):02X}" \
            if text[0] in "vp" else text[1:]
    if cell.effect or cell.param:
        fx = (M.EFFECT_LETTERS[cell.effect], f"{cell.param:02X}")
    else:
        fx = (".", "..")
    return (note, ins, letter, amount, fx[0], fx[1])


def seen_cell(song, pattern: int, row: int, channel: int):
    lib = openmpt._lib
    parts = []
    for command in (openmpt.COMMAND_NOTE, openmpt.COMMAND_INSTRUMENT,
                    openmpt.COMMAND_VOLUMEEFFECT, openmpt.COMMAND_VOLUME,
                    openmpt.COMMAND_EFFECT, openmpt.COMMAND_PARAMETER):
        parts.append(openmpt._string(
            lib.openmpt_module_format_pattern_row_channel_command(
                song._handle, pattern, row, channel, command)))
    return tuple(parts)


def compare(module: M.Module, blob: bytes = None, limit: int = 40):
    """Differences between `module` and what libopenmpt parsed from it.

    Strings, at most `limit` of them, each naming where it differs.
    """
    blob = blob if blob is not None else it_write.build(module)
    problems = []
    with openmpt.Song(blob) as song:
        def check(what, ours, theirs):
            if ours != theirs and len(problems) < limit:
                problems.append(f"{what}: wrote {ours!r}, libopenmpt read "
                                f"{theirs!r}")

        check("pattern count", len(module.patterns), song.patterns)
        check("instrument count", len(module.instruments), song.instruments)
        check("sample count", len(module.samples), song.samples)
        check("channels", max(1, module.channels_used()), song.channels)
        for i, ins in enumerate(module.instruments):
            check(f"instrument {i + 1} name", ins.name, song.instrument_name(i))
        for i, smp in enumerate(module.samples):
            check(f"sample {i + 1} name", smp.name, song.sample_name(i))
        for p, pattern in enumerate(module.patterns):
            check(f"pattern {p} rows", pattern.rows, song.pattern_rows(p))
            for r in range(pattern.rows):
                row = pattern.cells[r]
                # only channels the file has: an all-empty trailing channel
                # is not written, and libopenmpt rightly does not count it
                for c in range(max(1, module.channels_used())):
                    cell = row[c] if c < len(row) else M.Cell()
                    check(f"pattern {p} row {r} channel {c + 1}",
                          expected_cell(cell), seen_cell(song, p, r, c))
                    if len(problems) >= limit:
                        return problems
        check("title", module.title, song.metadata("title"))
        if module.message:
            check("message", module.message.replace("\r\n", "\n").strip(),
                  song.metadata("message_raw").replace("\r", "\n").strip())
    return problems
