"""notation.py — the text a model writes, and the Module it becomes.

    title Glass Engine
    tempo 118
    channels 4
    smp thump wave=kick len=0.30
    inst 1 name=Bass wave=saw oct=1-4 nna=cut cutoff=60 venv=0:64,40:40,160:0
    inst 2 name=Kit  kit=C-2:thump,D-2:snare,F#2:hat

    pattern a rows 32
    C-2 01 v64 ... | C-2 02 ... ... | ... .. ... ... | ... .. ... ...
    ...
    end
    order a a b

A cell is four fields — note, instrument, volume column, effect — and a
row is the cells of every channel between `|`. That is the shape every
tracker prints, so it is the shape a model has read the most of.

The compiler returns *every* problem in one pass, each with its line, the
line's text and the fix, rather than stopping at the first. A model that
is told one mistake per round takes ten rounds to fix a score with ten.
"""

import re
from typing import List, NamedTuple, Tuple

from . import export
from . import model as M
from . import recipes

NAME = re.compile(r"^[A-Za-z0-9_\-]+$")
#: `rest 24` — twenty-four empty rows, so a pattern with little in it does
#: not have to be written out row by row
_REST = re.compile(r"^rest\s+(\S+)$", re.I)
_COMMENT = re.compile(r"(?:^|\s)[#;].*$|;.*$")

#: tempo solutions: IT's row length is speed * 2.5 / tempo seconds, so a
#: beat of R rows lasts R * speed * 2.5 / tempo and BPM = 24 * tempo /
#: (speed * R). Speed 6 first (IT's default, and 6 ticks leave room for an
#: arpeggio's three steps), then neighbours.
_SPEEDS = (6, 5, 4, 3, 8, 7, 9, 10, 12, 2, 16, 11, 14, 15, 18, 20, 24)


class Diagnostic(NamedTuple):
    line: int
    source: str
    message: str
    fix: str = ""

    def text(self) -> str:
        out = [f"line {self.line}: {self.message}"]
        if self.source:
            out.append(f"  {self.source.strip()}")
        if self.fix:
            out.append(f"  fix: {self.fix}")
        return "\n".join(out)


class NotationError(ValueError):
    """Every problem found, in `.diagnostics`."""

    def __init__(self, diagnostics: List[Diagnostic]):
        self.diagnostics = list(diagnostics)
        count = len(self.diagnostics)
        head = f"{count} problem{'s' if count != 1 else ''} in the score"
        super().__init__(head + ":\n" + "\n".join(d.text()
                                                  for d in self.diagnostics))


class Report(NamedTuple):
    title: str
    bpm_asked: float
    bpm_actual: float
    speed: int
    tempo: int
    channels: int
    patterns: int
    orders: int
    rows: int
    seconds: float
    instruments: int
    samples: int
    sample_bytes: int
    warnings: Tuple[str, ...]
    #: did the score say `mv`? (then a build leaves the mix volume alone)
    mv_set: bool = False
    #: one line per kit: which key plays which drum
    keymaps: Tuple[str, ...] = ()

    def text(self) -> str:
        lines = [f"{self.title or 'untitled'}: {self.seconds:.1f} s, "
                 f"{self.channels} channels, {self.patterns} patterns "
                 f"({self.rows} rows played), {self.instruments} "
                 f"instruments over {self.samples} samples "
                 f"({self.sample_bytes / 1024:.0f} KB)",
                 f"tempo {self.bpm_actual:.2f} BPM "
                 f"(speed {self.speed}, tempo {self.tempo})"
                 + ("" if abs(self.bpm_actual - self.bpm_asked) < 0.01
                    else f", asked {self.bpm_asked:g}")]
        lines += [f"keys: {k}" for k in self.keymaps]
        lines += [f"warning: {w}" for w in self.warnings]
        return "\n".join(lines)


def solve_timing(bpm: float, rows_per_beat: int):
    """-> (speed, tempo, actual_bpm) closest to the asked tempo."""
    best = None
    for rank, speed in enumerate(_SPEEDS):
        tempo = round(bpm * speed * rows_per_beat / 24.0)
        if not 32 <= tempo <= 255:
            continue
        actual = 24.0 * tempo / (speed * rows_per_beat)
        error = abs(actual - bpm) / bpm
        # a small penalty for leaving speed 6, so ties stay at 6
        score = error + rank * 1e-5
        if best is None or score < best[0]:
            best = (score, speed, tempo, actual)
    if best is None:
        raise ValueError(f"no speed/tempo pair gives {bpm:g} BPM at "
                         f"{rows_per_beat} rows per beat (tempo must be "
                         f"32..255)")
    return best[1], best[2], best[3]


# -- cells -------------------------------------------------------------------
_FLATS = {"DB": "C#", "EB": "D#", "GB": "F#", "AB": "G#", "BB": "A#"}


def _note(token: str) -> int:
    token = token.strip()
    if len(token) == 3 and token[1] in "bB" and token[0].upper() in "DEGAB":
        sharp = _FLATS.get(token[:2].upper())
        if sharp:
            token = sharp + token[2]
    if len(token) == 3:
        token = token[0].upper() + token[1:]
    return M.note_number(token)


def parse_cell(text: str) -> M.Cell:
    """`C-5 01 v64 H44` -> Cell. A lone `...` (or `.`) is an empty cell."""
    parts = text.split()
    if len(parts) == 1 and parts[0] in ("...", ".", "-"):
        return M.Cell()
    if len(parts) != 4:
        raise ValueError(f"a cell is four fields (note, instrument, volume "
                         f"column, effect), got {len(parts)}: {text.strip()!r}")
    note_t, ins_t, vol_t, fx_t = parts
    cell = M.Cell()
    cell.note = _note(note_t)
    if ins_t not in ("..", "...", "."):
        if not ins_t.isdigit() or not 1 <= int(ins_t) <= M.IT_MAX_INSTRUMENTS:
            raise ValueError(f"instrument {ins_t!r} is not 01..99 or ..")
        cell.instrument = int(ins_t)
    cell.volume = M.volume_column("..." if vol_t in ("..", ".") else vol_t)
    cell.effect, cell.param = M.effect_parse("..." if fx_t in ("..", ".") else fx_t)
    return cell


def parse_row(text: str) -> List[M.Cell]:
    pieces = text.split("|")
    if pieces and not pieces[0].strip():
        pieces = pieces[1:]
    if pieces and not pieces[-1].strip():
        pieces = pieces[:-1]
    return [parse_cell(piece) for piece in pieces]


# -- the compiler ------------------------------------------------------------
class _Compiler:
    def __init__(self, text: str, base_dir: str = ""):
        self.lines = text.splitlines()
        self.diagnostics: List[Diagnostic] = []
        self.warnings: List[str] = []
        self.module = M.Module()
        self.library = recipes.Library()
        self.library.base_dir = base_dir
        self.bpm = 125.0
        self.mv_set = False
        self.speed_set = None
        self.channels = None
        self.patterns = {}               # name -> (index, line)
        self.pattern_order = []          # names, in definition order
        self.order_lines = []            # (lineno, source, [names])
        self.inst_lines = {}             # number -> lineno
        self.failed_inst = set()         # numbers whose line had an error
        self.author = ""
        self._message = ""
        self.cell_refs = []              # (lineno, source, instrument number)

    def error(self, lineno, message, fix=""):
        source = self.lines[lineno - 1] if 0 < lineno <= len(self.lines) else ""
        self.diagnostics.append(Diagnostic(lineno, source, message, fix))

    # -- driving ---------------------------------------------------------
    def run(self):
        in_pattern = None
        for number, raw in enumerate(self.lines, start=1):
            line = _COMMENT.sub("", raw).strip()
            if not line:
                continue
            if in_pattern is not None:
                if line.split()[0].lower() == "end":
                    self._end_pattern(in_pattern, number)
                    in_pattern = None
                else:
                    self._row(in_pattern, number, line)
                continue
            head, _, rest = line.partition(" ")
            head = head.lower()
            handler = getattr(self, "d_" + head, None)
            if handler is None:
                known = sorted(n[2:] for n in dir(self) if n.startswith("d_"))
                self.error(number, f"unknown directive {head!r}",
                           "directives: " + " ".join(known))
                continue
            if head == "pattern":
                in_pattern = self.d_pattern(number, rest.split())
            else:
                try:
                    handler(number, rest.strip())
                except (ValueError, recipes.RecipeError) as exc:
                    self.error(number, str(exc), getattr(exc, "fix", ""))
        if in_pattern is not None:
            self.error(in_pattern["line"], f"pattern {in_pattern['name']!r} "
                       f"is never closed", "end the pattern with `end`")
        return self._finish()

    # -- header directives -----------------------------------------------
    def d_title(self, n, rest):
        if len(rest) > 25:
            self.warnings.append(f"title is {len(rest)} characters; Impulse "
                                 f"Tracker keeps 25")
        self.module.title = rest

    def d_author(self, n, rest):
        self.author = rest

    def d_msg(self, n, rest):
        self._message += rest + "\n"

    d_message = d_msg

    def d_tempo(self, n, rest):
        try:
            self.bpm = float(rest)
        except ValueError:
            raise ValueError(f"tempo wants beats per minute, got {rest!r}") \
                from None
        if not 20 <= self.bpm <= 400:
            raise ValueError(f"tempo {self.bpm:g} is outside 20..400 BPM")

    d_bpm = d_tempo

    def d_rpb(self, n, rest):
        value = self._number(rest, "rpb (rows per beat)")
        if not 1 <= value <= 32:
            raise ValueError("rows per beat is 1..32")
        self.module.rows_per_beat = value
        self.module.rows_per_measure = value * 4

    @staticmethod
    def _number(text, what):
        try:
            return int(text)
        except ValueError:
            raise ValueError(f"{what} wants a whole number, got "
                             f"{text!r}") from None

    def d_speed(self, n, rest):
        if rest.lower() != "auto":
            value = self._number(rest, "speed")
            if not 1 <= value <= 255:
                raise ValueError("speed is 1..255 ticks per row")
            self.speed_set = value

    def d_channels(self, n, rest):
        value = self._number(rest, "channels")
        if not 1 <= value <= M.MAX_CHANNELS:
            raise ValueError(f"channels is 1..{M.MAX_CHANNELS}")
        self.channels = value

    def d_chan(self, n, rest):
        parts = rest.split()
        if not parts or not parts[0].isdigit() or \
                not 1 <= int(parts[0]) <= M.MAX_CHANNELS:
            raise ValueError("`chan N pan=0..64 vol=0..64 [mute]` — N is "
                             "the channel, 1..64")
        index = int(parts[0]) - 1
        for part in parts[1:]:
            if part.lower() == "mute":
                self.module.channel_mute[index] = True
                continue
            key, _, value = part.partition("=")
            if key == "pan":
                self.module.channel_pan[index] = 100 if value == "surround" \
                    else recipes._int(0, 64)(value)
            elif key == "vol":
                self.module.channel_volume[index] = recipes._int(0, 64)(value)
            else:
                raise ValueError(f"{part!r}: chan takes pan=, vol= and mute")

    def d_gv(self, n, rest):
        self.module.global_volume = recipes._int(0, 128)(rest)

    def d_mv(self, n, rest):
        self.module.mix_volume = recipes._int(0, 128)(rest)
        self.mv_set = True

    def d_sep(self, n, rest):
        self.module.pan_separation = recipes._int(0, 128)(rest)

    def d_flags(self, n, rest):
        for word in rest.lower().split():
            if word == "linear":
                self.module.linear_slides = True
            elif word == "amiga":
                self.module.linear_slides = False
            elif word == "oldfx":
                self.module.old_effects = True
            elif word == "gxxlink":
                self.module.gxx_link = True
            else:
                raise ValueError(f"flag {word!r}: linear amiga oldfx gxxlink")

    # -- sounds ----------------------------------------------------------
    def d_bank(self, n, rest):
        """`bank PATH` — a forge bank (python3 -m schism forge run) whose
        instruments `forge=NAME` and `@NAME` may then name."""
        from .forge import notation_hook
        notation_hook.load_bank(self.library, rest.strip())

    def d_smp(self, n, rest):
        parts = rest.split()
        if not parts or not NAME.match(parts[0]):
            raise ValueError("`smp NAME wave=... settings` — NAME is letters, "
                             "digits, _ and -")
        options = recipes.split_options(parts[1:])
        if "forge" in options:
            self._timing_for_forge()
            self.library.define_forged(parts[0], options)
            return
        if "wave" not in options:
            raise recipes.RecipeError(
                f"smp {parts[0]} needs wave=",
                "waves: " + " ".join(recipes.WAVES))
        kind = recipes._choice(*recipes.WAVES)(options.pop("wave"))
        self.library.define(parts[0], kind, options)

    def d_inst(self, n, rest):
        parts = rest.split()
        if not parts or not parts[0].isdigit() or \
                not 1 <= int(parts[0]) <= M.IT_MAX_INSTRUMENTS:
            raise ValueError("`inst N key=value ...` — N is 1..99")
        number = int(parts[0])
        if number in self.inst_lines:
            raise ValueError(f"instrument {number} is already defined on "
                             f"line {self.inst_lines[number]}")
        self.failed_inst.add(number)      # until it compiles
        options = recipes.split_options(parts[1:])
        self._timing_for_forge()
        recipes.compile_instrument(number, options, self.library, self.module,
                                   warn=self.warnings.append)
        self.failed_inst.discard(number)
        self.inst_lines[number] = n

    def _timing_for_forge(self):
        """The tick the score will have, as far as the header has said:
        what a forged instrument's envelopes are timed in. `_finish` times
        them again against the final tempo."""
        try:
            _, tempo, _ = solve_timing(self.bpm, self.module.rows_per_beat)
        except ValueError:
            tempo = 125
        self.library.tick = 2.5 / tempo

    # -- patterns --------------------------------------------------------
    def d_pattern(self, n, parts):
        name = parts[0] if parts else ""
        rows = 0
        if len(parts) == 3 and parts[1].lower() == "rows" and parts[2].isdigit():
            rows = int(parts[2])
        if not NAME.match(name) or not rows:
            self.error(n, "`pattern NAME rows N`",
                       "e.g. pattern verse rows 64")
            return {"name": name or "?", "line": n, "rows": 0, "cells": [],
                    "bad": True}
        if name in self.patterns:
            self.error(n, f"pattern {name!r} is already defined (line "
                       f"{self.patterns[name][1]})")
        if not M.MIN_ROWS <= rows <= M.MAX_ROWS:
            self.error(n, f"pattern {name!r} has {rows} rows; Impulse "
                       f"Tracker patterns have {M.MIN_ROWS}-{M.MAX_ROWS}",
                       "use 32, 48, 64 ... or pad with empty rows")
        return {"name": name, "line": n, "rows": rows, "cells": [],
                "bad": False, "width": None}

    def _row(self, pattern, number, line):
        rest = _REST.match(line)
        if rest:
            text = rest.group(1)
            if not text.isdigit() or not 1 <= int(text) <= M.MAX_ROWS:
                self.error(number, f"`rest {text}` — rest wants a count of "
                           f"1-{M.MAX_ROWS} empty rows",
                           "e.g. `rest 24`")
                pattern["cells"].append(None)
                return
            pattern["cells"].extend([[] for _ in range(int(text))])
            return
        try:
            cells = parse_row(line)
        except ValueError as exc:
            self.error(number, str(exc), "write each cell as NOTE INS VOL FX, "
                       "e.g. `C-5 01 v64 ...`, and put | between channels; "
                       "an empty cell is `...`")
            pattern["cells"].append(None)
            return
        width = self.channels or pattern.get("width")
        if width is None:
            width = pattern["width"] = len(cells)
        if len(cells) != width:
            self.error(number, f"{len(cells)} cells but this score has "
                       f"{width} channels",
                       f"every row needs {width} cells separated by |; an "
                       f"empty cell is `...`")
            pattern["cells"].append(None)
            return
        if self.channels is None:
            self.channels = width
        pattern["width"] = width
        for cell in cells:
            if cell.instrument:
                self.cell_refs.append((number, self.lines[number - 1],
                                       cell.instrument))
        pattern["cells"].append(cells)

    def _end_pattern(self, pattern, number):
        if pattern["bad"]:
            return
        got = len(pattern["cells"])
        if got != pattern["rows"]:
            self.error(pattern["line"], f"pattern {pattern['name']!r} says "
                       f"{pattern['rows']} rows and has {got}",
                       f"write exactly {pattern['rows']} row lines, or "
                       f"change the number to {got}")
            return
        built = M.Pattern(rows=pattern["rows"], name=pattern["name"])
        for r, cells in enumerate(pattern["cells"]):
            built.cells[r] = list(cells) if cells else []
        self.module.patterns.append(built)
        self.patterns[pattern["name"]] = (len(self.module.patterns) - 1,
                                          pattern["line"])
        self.pattern_order.append(pattern["name"])

    def d_end(self, n, rest):
        raise ValueError("`end` closes a pattern, and none is open")

    def d_order(self, n, rest):
        names = []
        for token in rest.split():
            count = 1
            if "*" in token:
                token, _, times = token.partition("*")
                count = self._number(times, f"{token}*")
                if not 1 <= count <= 64:
                    raise ValueError(f"{token}*{times}: repeat 1..64 times")
            names += [token] * count
        if not names:
            raise ValueError("`order a b c` — list the patterns in play order, "
                             "name*3 repeats one")
        self.order_lines.append((n, self.lines[n - 1], names))

    # -- finishing -------------------------------------------------------
    def _finish(self):
        m = self.module
        used_names = set()
        for lineno, source, names in self.order_lines:
            for name in names:
                if name not in self.patterns:
                    self.diagnostics.append(Diagnostic(
                        lineno, source, f"order names pattern {name!r}, "
                        f"which is not defined",
                        "defined: " + (", ".join(self.pattern_order) or
                                       "none")))
                else:
                    used_names.add(name)
                    m.orders.append(self.patterns[name][0])
        if not self.order_lines and self.pattern_order:
            self.diagnostics.append(Diagnostic(
                0, "", "the score defines patterns but has no `order` line",
                "add `order " + " ".join(self.pattern_order[:3]) + "`"))
        for name in self.pattern_order:
            if self.order_lines and name not in used_names:
                self.diagnostics.append(Diagnostic(
                    self.patterns[name][1], "", f"pattern {name!r} is defined "
                    f"but not in any `order` line, so it would not be played",
                    f"add it to the order, or delete it"))
        defined = sorted(self.inst_lines)
        for lineno, source, number in self.cell_refs:
            if number not in self.inst_lines and number not in self.failed_inst:
                self.diagnostics.append(Diagnostic(
                    lineno, source, f"instrument {number:02d} is not defined",
                    "defined: " + (", ".join(f"{d:02d}" for d in defined) or
                                   "none") + " — add an `inst` line"))
        if len(m.orders) > 255:
            self.diagnostics.append(Diagnostic(
                0, "", f"the order list has {len(m.orders)} entries; the "
                f"format holds 255"))
        if len(m.samples) > M.SCHISM_MAX_SAMPLES:
            self.diagnostics.append(Diagnostic(
                0, "", f"the score makes {len(m.samples)} samples, and "
                f"Schism Tracker plays at most {M.SCHISM_MAX_SAMPLES}",
                "a pitched instrument costs one sample per octave it covers: "
                "narrow oct= (oct=3-5 is three samples), share instruments, "
                "or use a one-shot kit"))
        if len(m.patterns) > M.SCHISM_MAX_PATTERNS:
            self.diagnostics.append(Diagnostic(
                0, "", f"the score defines {len(m.patterns)} patterns; "
                f"Schism Tracker and OpenMPT load at most "
                f"{M.SCHISM_MAX_PATTERNS}",
                "repeat a pattern in the `order` line (a*4) rather than "
                "writing it out again"))
        if self.diagnostics:
            raise NotationError(sorted(self.diagnostics, key=lambda d: d.line))

        if self.speed_set:
            speed = self.speed_set
            tempo = round(self.bpm * speed * m.rows_per_beat / 24.0)
            if not 32 <= tempo <= 255:
                raise NotationError([Diagnostic(
                    0, "", f"speed {speed} cannot reach {self.bpm:g} BPM "
                    f"(tempo would be {tempo}, the format holds 32..255)",
                    "use `speed auto`")])
            actual = 24.0 * tempo / (speed * m.rows_per_beat)
        else:
            speed, tempo, actual = solve_timing(self.bpm, m.rows_per_beat)
        m.speed, m.tempo = speed, tempo
        m.message = (f"by {self.author}\n" if self.author else "") \
            + self._message
        if self.library.forged:
            from .forge import notation_hook
            notation_hook.retime(m, self.library, 2.5 / tempo)
            m.forged = notation_hook.forged_of(self.library)
            m.fm = notation_hook.forged_fm(self.library)
            m.dac = notation_hook.forged_dac(self.library)
        m.recipes = dict(self.library.recipes)

        if len(m.samples) > M.IT_MAX_SAMPLES:
            self.warnings.append(
                f"{len(m.samples)} samples: Schism Tracker and OpenMPT play "
                f"them, but Impulse Tracker 2.14 itself loads only "
                f"{M.IT_MAX_SAMPLES}. Narrow oct= if the module has to open "
                f"there")
        if len(m.patterns) > M.IT_MAX_PATTERNS:
            self.warnings.append(
                f"{len(m.patterns)} patterns: Schism Tracker and OpenMPT "
                f"load them, but Impulse Tracker 2.14 itself holds only "
                f"{M.IT_MAX_PATTERNS}")
        self._silence_warnings()
        rows = sum(m.patterns[i].rows for i in m.orders)
        report = Report(
            title=m.title, bpm_asked=self.bpm, bpm_actual=actual,
            speed=speed, tempo=tempo, channels=m.channels_used(),
            patterns=len(m.patterns), orders=len(m.orders), rows=rows,
            seconds=m.seconds(),
            instruments=sum(1 for i in m.instruments if i.name or any(
                s for _, s in i.note_map)),
            samples=len(m.samples),
            sample_bytes=sum(len(s.data) * (2 if s.bits == 16 else 1)
                             * s.channels
                             for s in {(id(s.data), id(s.right)): s
                                       for s in m.samples}.values()),
            warnings=tuple(self.warnings), mv_set=self.mv_set,
            keymaps=export.kit_lines(m))
        return m, report

    def _silence_warnings(self):
        """The silent failures of this format, walked in play order.

        A note sounds nothing when (1) no instrument has ever been set on
        its channel, or (2) the instrument's note map sends that note to no
        sample. Both render without a word, which is what makes them worth
        a warning each — one per channel and pattern, not one per note.
        """
        m = self.module
        current = {}                      # channel -> instrument number
        warned = set()
        for order in m.orders:
            pattern = m.patterns[order]
            for r, row in enumerate(pattern.cells):
                for c, cell in enumerate(row):
                    if cell.instrument:
                        current[c] = cell.instrument
                    if cell.note == M.NOTE_OFF and c in current \
                            and current[c] <= len(m.instruments):
                        ins = m.instruments[current[c] - 1]
                        if not _releases(ins) and (c, current[c], "off") \
                                not in warned:
                            warned.add((c, current[c], "off"))
                            self.warnings.append(
                                f"=== on channel {c + 1} (pattern "
                                f"{pattern.name!r} row {r}) does nothing: "
                                f"instrument {current[c]:02d} has no fade and "
                                f"its volume envelope never reaches 0 after "
                                f"a key-off, so the note rings on. Give it "
                                f"fade=, or end its venv at 0, or use ^^^")
                    if not 1 <= cell.note <= 120:
                        continue
                    key = (c, pattern.name)
                    if c not in current:
                        if key not in warned:
                            warned.add(key)
                            self.warnings.append(
                                f"channel {c + 1} plays {M.note_name(cell.note)}"
                                f" in pattern {pattern.name!r} row {r} before "
                                f"any instrument was set on it — Impulse "
                                f"Tracker plays nothing for that note")
                        continue
                    number = current[c]
                    if number > len(m.instruments):
                        continue                     # reported as undefined
                    if m.instruments[number - 1].note_map[cell.note - 1][1] == 0:
                        if (c, pattern.name, number) not in warned:
                            warned.add((c, pattern.name, number))
                            self.warnings.append(
                                f"instrument {number:02d} has no sample for "
                                f"{M.note_name(cell.note)} (pattern "
                                f"{pattern.name!r} row {r} channel {c + 1}) "
                                f"— that note is silent; widen oct= or map "
                                f"the key in kit=")


def _releases(ins: M.Instrument) -> bool:
    """Does a key-off eventually silence this instrument? Impulse Tracker
    ends a released note by its fadeout or by its volume envelope reaching
    zero; with neither, ``===`` changes nothing."""
    if ins.fadeout > 0:
        return True
    env = ins.volume_envelope
    return bool(env and env.enabled and env.nodes and env.nodes[-1][1] == 0)


def compile_text(text: str, base_dir: str = ""):
    """Score text -> (Module, Report). Raises NotationError listing every
    problem in the score. `base_dir` is where a relative `bank` path
    starts."""
    return _Compiler(text, base_dir).run()


def compile_file(path: str):
    import os
    with open(path, encoding="utf-8") as handle:
        return compile_text(handle.read(), os.path.dirname(os.path.abspath(path)))
