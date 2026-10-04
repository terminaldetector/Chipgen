"""audition.py — hear what the forge made.

Measuring is not listening. This builds a module in which every instrument
plays a short phrase that suits what it is (a run of the chord for a lead, a
pattern for a bass, long notes for a pad, a groove for a drum), one after
another, so that a bank, or one genome, can be rendered to an MP3 and
judged by ear — or opened in Schism Tracker, which is what it is for.
"""

from typing import List, Tuple

from .. import model as M
from . import patch as patch_mod
from .family import Compiled, get_family
from .probe import note_of

ROW_S = 0.125               # 120 BPM, four rows a beat
TEMPO, SPEED = 120, 6       # a tick of 20.8 ms


def _phrase_pitched(root: int, role: str, rows: int):
    """-> [(row, note, length rows)]; note 0 ends the note before it."""
    if role == "bass":
        pattern = [(0, 0, 3), (4, 0, 3), (6, 7, 2), (8, 0, 3), (12, 5, 3),
                   (14, 7, 2), (16, 0, 3), (20, 0, 3), (22, 12, 2),
                   (24, 7, 3), (28, 5, 3), (30, 3, 1)]
        return [(r, root + k, ln) for r, k, ln in pattern if r < rows]
    if role in ("pad", "harmony"):
        return [(0, root, 15), (16, root + 3, 15), (32, root + 7, 15),
                (48, root + 5, 15)]
    steps = [0, 4, 7, 12, 7, 4, 0, 7, 12, 16, 12, 7, 4, 0, -5, 0]
    return [(i * 4, root + k, 3 if i % 4 != 3 else 5)
            for i, k in enumerate(steps) if i * 4 < rows]


def _phrase_drum(kind: str, rows: int):
    if kind == "kick":
        hits = [0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52, 56, 60]
    elif kind in ("snare", "clap"):
        hits = [4, 12, 20, 28, 36, 44, 52, 60]
    elif kind == "hat":
        hits = list(range(0, 64, 2))
    elif kind in ("tom",):
        hits = [0, 4, 8, 12, 32, 36, 40, 44]
    elif kind in ("rim", "cowbell", "shaker"):
        hits = list(range(0, 64, 4))
    else:                                   # cymbal and the like
        hits = [0, 32]
    return [(r, 0, 2) for r in hits if r < rows]


def pattern_for(compiled: Compiled, role: str, register, rows: int = 64):
    """-> a Pattern with the phrase for one instrument on channel 1."""
    family = get_family(compiled.family)
    pattern = M.Pattern(rows=rows)
    if family.pitched:
        root = note_of(*register)
        notes = _phrase_pitched(root, role, rows)
        for row, note, length in notes:
            if not 1 <= note <= 120:
                continue
            cell = pattern.cell(row, 0)
            cell.note, cell.instrument, cell.volume = note, 1, 64
            if row + length < rows:
                pattern.cell(row + length, 0).note = M.NOTE_OFF
        return pattern
    kind = compiled.style.get("kind", "")
    note = note_of(*register)
    for row, _, _ in _phrase_drum(kind, rows):
        cell = pattern.cell(row, 0)
        cell.note, cell.instrument, cell.volume = note, 1, 64
    return pattern


def module_for(items: List[Tuple[str, Compiled, tuple, str]],
               title: str = "Forge audition") -> M.Module:
    """One module, one instrument per item, each in its own pattern.

    `items` is [(name, compiled, register, role)]."""
    from ..notation import solve_timing
    speed, tempo, _ = solve_timing(120.0, 4)
    module = M.Module(title=title[:25], speed=speed, tempo=tempo,
                      mix_volume=64)
    tick = 2.5 / tempo
    for number, (name, compiled, register, role) in enumerate(items, 1):
        # each instrument is number 1 of its own (tiny) pattern's world, so
        # the pattern refers to instrument `number`
        patch_mod.install(compiled.patch, module, number, tick, name)
        pattern = pattern_for(compiled, role, register)
        for row in pattern.cells:
            for cell in row:
                if cell.instrument:
                    cell.instrument = number
        module.patterns.append(pattern)
        module.orders.append(len(module.patterns) - 1)
    return module


def render(items, path: str, title: str = "Forge audition",
           seconds: float = None, loud: bool = True):
    """Module -> MP3 (or WAV, by the extension). -> (seconds, peak).

    `loud` raises the whole until the loudest note peaks near 0.89, as the
    demos do: the mix volume first, then, because instruments are trimmed to
    a quiet reference and a mix volume tops out at 128, the rendered samples.
    The instruments keep their levels against each other."""
    import math
    from .. import it_write, render as render_mod
    module = module_for(items, title)
    boost = {}
    if loud:
        peak = render_mod.calibrate_mix_volume(module)
        if peak < 0.85:
            boost["headroom"] = -20.0 * math.log10(0.89 / peak)
    blob = it_write.build(module)
    if path.lower().endswith(".wav"):
        return render_mod.to_wav(blob, path, seconds=seconds, **boost)
    seconds_, peak, _ = render_mod.render_mp3(blob, path, title=title,
                                              artist="schism forge",
                                              seconds=seconds, **boost)
    return seconds_, peak
