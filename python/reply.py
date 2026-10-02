"""reply.py — a model's reply in; a playable score, or the exact reason not.

## Why a checker and not a stricter prompt

A briefing can say a rule; it cannot make a model keep it. What makes
generation stable is a loop: the model writes, the engine checks, the
model gets back a short list of what is wrong and where, and fixes it. A
model that skimmed half the briefing converges anyway, because the
engine — not the model's memory of a document — is what holds the rules.

So this does three things, in order, and reports every one of them:

**extract** the score from a chat reply. Models wrap scores in fences and
prose however firmly they are asked not to; a fenced block is taken as
the score, prose around a bare score is dropped.

**repair** only what has one possible meaning: a `♯` for `#`, an en-dash
in `C–4`, a `//` comment the tracker reads as cells, a `[Verse]` label,
a markdown table rule. Never a note, never a name, never a cell count —
a repair that guesses is a silent change to the music.

**check** with the real parser and the engine's own silent-failure rules,
and collect *every* error in one pass — the parser stops at the first,
so each failing line is set aside and the rest parsed again. One round
trip with ten fixes beats ten round trips with one.

The rule names are the briefing's (prompts.RULES) and the engine's
(sanity.SILENT_RULES): what a model is told and what it is held to are
the same list.
"""

import difflib
import os
import re
import sys
from typing import List, NamedTuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import tracker as tracker_mod

#: Columns each chip owns. A score briefed for one chip that writes
#: another's columns plays them on the other chip — or nowhere.
CHIP_COLUMNS = {
    "YM2612": {f"fm{i}" for i in range(6)} | {"psg0", "psg1", "psg2",
                                              "noise", "dac"},
    "SN76489": {"psg0", "psg1", "psg2", "noise"},
    "RP2A03": {f"nes{i}" for i in range(5)},
    "YM3812": {f"opl{i}" for i in range(9)},
}

#: The most errors one pass collects. Past this a model is better served
#: by fixing what it has been told and trying again.
MAX_ERRORS = 12


class Issue(NamedTuple):
    rule: str          #: prompts.RULES / sanity.SILENT_RULES id, or "parse"
    severity: str      #: "error" (not playable as written) or "warning"
    line: int          #: 1-based line in the extracted score; 0 = whole score
    message: str
    fix: str = ""
    quote: str = ""    #: the line itself, because a model counting lines
                       #: in its own reply does not count the same way

    def text(self) -> str:
        where = ""
        if self.line:
            where = f"line {self.line}"
            if self.quote:
                where += f" `{self.quote}`"
            where += ": "
        # Ending punctuation is added once, never by collapsing dots: `...`
        # is the empty cell, and an instruction that turns it into `..`
        # teaches a model a token the parser does not mean.
        message = self.message.rstrip()
        if not message.endswith((".", "!", "?")):
            message += "."
        fix = f" Fix: {self.fix}" if self.fix else ""
        return f"{where}{message}{fix}"


class Verdict:
    """Everything known about one reply."""

    def __init__(self, score, extracted, repairs, issues, stats):
        self.score = score
        self.extracted = extracted
        self.repairs = repairs
        self.issues = issues
        self.stats = stats

    @property
    def errors(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> List[Issue]:
        return [i for i in self.issues if i.severity != "error"]

    @property
    def ok(self) -> bool:
        return bool(self.score.strip()) and not self.errors

    def feedback(self, delivery: str = "chat", limit: int = 10) -> str:
        """What to send back to the model — short, numbered, actionable."""
        resend = {
            "chat": "Send the whole corrected score again, in one ```trk "
                    "block.",
            "agent": "Fix them in work/song.trk and run --check again. "
                     "Do not edit the engine.",
            "grammar": "Write the whole corrected score.",
        }.get(delivery, "Send the whole corrected score again.")
        if self.ok:
            head = "The score is playable."
            minor = self.warnings[:limit]
            if not minor:
                return head
            return "\n".join([head + " Worth fixing:"] +
                             [f"{n}. {i.text()}"
                              for n, i in enumerate(minor, 1)])
        errors = self.errors
        lines = [f"Not playable yet — {len(errors)} problem"
                 f"{'s' if len(errors) != 1 else ''}. {resend}"]
        for n, issue in enumerate(errors[:limit], 1):
            lines.append(f"{n}. {issue.text()}")
        if len(errors) > limit:
            lines.append(f"…and {len(errors) - limit} more of the same "
                         f"kinds.")
        return "\n".join(lines)

    def to_json(self) -> dict:
        return {
            "ok": self.ok,
            "score": self.score,
            "extracted": list(self.extracted),
            "repairs": list(self.repairs),
            "issues": [i._asdict() for i in self.issues],
            "stats": dict(self.stats),
        }

    def report(self) -> str:
        """For a person at a terminal: what happened, then what is wrong."""
        out = []
        for note in self.extracted:
            out.append(f"extract: {note}")
        for note in self.repairs:
            out.append(f"repair:  {note}")
        stats = self.stats
        if stats.get("rows"):
            out.append(f"score:   {stats['rows']} rows, "
                       f"{stats.get('seconds', 0):.1f}s, "
                       f"cols {' '.join(stats.get('columns', []))}")
        for issue in self.issues:
            out.append(f"{issue.severity:7}  [{issue.rule}] {issue.text()}")
        out.append("PLAYABLE" if self.ok else
                   f"NOT PLAYABLE — {len(self.errors)} error"
                   f"{'s' if len(self.errors) != 1 else ''}")
        return "\n".join(out)


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------
_FENCE = re.compile(r"```[ \t]*([\w+.-]*)[^\n]*\n(.*?)(```|\Z)", re.DOTALL)
#: Fence labels that are never a score.
_NOT_SCORES = {"python", "py", "bash", "sh", "shell", "console", "javascript",
               "js", "c", "cpp", "diff", "patch", "yaml", "toml", "ini"}
_NOTE = r"[A-Ga-g][-#b]?\d(?::\d+)?"
_HOLDS = r"\.{1,3}|-{1,3}|={2,3}|\^{1,3}|~|off"


def _cell_pattern():
    import samples as samples_mod
    names = "|".join(sorted(samples_mod.names(), key=len, reverse=True))
    return re.compile(
        rf"^(?:{_NOTE}|{_HOLDS}|[wp][0-3](?::\d+)?|\d{{1,2}}m?(?::\d+)?|"
        rf"(?:{names})(?::\d*\.?\d+)?|\|)$")


def _score_like(line: str, cell) -> bool:
    """A directive, or a row — including a row with a typo in it.

    "All cells valid" was the first rule, and it read a final row with
    one bad cell (`C-4 xyz`) as prose after the score and dropped it, so
    the typo vanished instead of being reported. Half the tokens being
    cells is a row with a mistake; prose — "Let me know if you want
    changes." — has none.
    """
    words = tracker_mod._COMMENT.split(line, maxsplit=1)[0].split()
    if not words:
        return False
    if words[0].lower() in tracker_mod.DIRECTIVES:
        return True
    cells = sum(1 for w in words if cell.match(w))
    return cells * 2 >= len(words) and cells > 0


_THINKING = re.compile(r"<think(?:ing)?>.*?(?:</think(?:ing)?>|\Z)",
                       re.DOTALL | re.IGNORECASE)


def extract(text: str):
    """The score inside a reply. -> (score, notes on what was done)."""
    notes = []
    # Reasoning models put their working inside <think> tags, drafts and
    # half-scores included. A draft fenced in there would win extraction
    # over the real answer, so the thinking goes before anything is read.
    text, thought = _THINKING.subn("", text)
    if thought:
        notes.append("set aside the model's <think> block")
    cell = _cell_pattern()
    blocks = []
    for match in _FENCE.finditer(text):
        label, body, closer = match.group(1).lower(), match.group(2), \
            match.group(3)
        if label in _NOT_SCORES:
            continue
        lines = body.splitlines()
        weight = sum(1 for line in lines if _score_like(line, cell))
        blocks.append((weight, len(lines), body, closer == "", label))
    if blocks:
        blocks.sort(key=lambda b: (-b[0], -b[1]))
        weight, _, body, unclosed, label = blocks[0]
        if weight:
            found = len(blocks)
            notes.append(f"took the {'```' + label if label else 'fenced'} "
                         f"block" + (f" (best of {found})" if found > 1
                                     else ""))
            if unclosed:
                notes.append("the fence never closes — the reply was cut "
                             "off, so the score may be incomplete")
            return body.strip("\n") + "\n", notes

    lines = text.splitlines()
    marks = [i for i, line in enumerate(lines) if _score_like(line, cell)]
    if not marks:
        notes.append("no score in the reply")
        return "", notes
    first, last = marks[0], marks[-1]
    if first:
        notes.append(f"dropped {first} line{'s' if first != 1 else ''} of "
                     f"text before the score")
    after = len(lines) - 1 - last
    if after and any(line.strip() for line in lines[last + 1:]):
        notes.append(f"dropped {after} line{'s' if after != 1 else ''} of "
                     f"text after the score")
    return "\n".join(lines[first:last + 1]) + "\n", notes


# --------------------------------------------------------------------------
# Repair — only what has exactly one meaning
# --------------------------------------------------------------------------
_DASH_IN_NOTE = re.compile(r"(?<=[A-Ga-g])[‐-―−](?=\d)")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
_LABEL = re.compile(
    r"^\s*(\[[^\]]*\]|\(?(bar|measure|section|verse|chorus|intro|outro|"
    r"part|phrase)\b[^\n]*?\)?:?)\s*$", re.IGNORECASE)
_ROW_NUMBER = re.compile(r"^\s*\d{1,4}\s*[:|]\s+")


def repair(score: str):
    """Fix what can only mean one thing. -> (score, notes)."""
    counts = {}

    def bump(kind, n=1):
        counts[kind] = counts.get(kind, 0) + n

    out = []
    columns = 0
    for line in score.splitlines():
        original = line
        for bad, good in (("♯", "#"), ("♭", "b"), ("…", "..."),
                          (" ", " "), (" ", " "), (" ", " ")):
            if bad in line:
                line = line.replace(bad, good)
        line, n = _DASH_IN_NOTE.subn("-", line)
        if line != original:
            bump("typographic characters (♯ ♭ … – nbsp)")
        if "//" in line:
            line = line.replace("//", ";", 1)
            bump("`//` comments turned into `;`")
        words = line.split()
        head = words[0].lower() if words else ""
        if head in ("cols", "columns"):
            columns = len(words) - 1
        if _TABLE_RULE.match(line):
            # Blanked rather than dropped: line numbers in the feedback
            # have to point at the model's own lines.
            bump("markdown table rules removed")
            out.append("")
            continue
        if words and head not in tracker_mod.DIRECTIVES and \
                _LABEL.match(line) and not line.lstrip().startswith(";"):
            line = "; " + line.strip()
            bump("section labels turned into comments")
        elif columns and _ROW_NUMBER.match(line):
            rest = _ROW_NUMBER.sub("", line)
            if len(rest.replace("|", " ").split()) == columns:
                line = rest
                bump("row numbers removed from the start of rows")
        out.append(line)
    notes = [f"{what} ({n}x)" for what, n in counts.items()]
    return "\n".join(out) + "\n", notes


# --------------------------------------------------------------------------
# Checking
# --------------------------------------------------------------------------
_LINE = re.compile(r"^line (\d+): (.*)$", re.DOTALL)


def _rule_for(message: str) -> str:
    text = message.lower()
    if "cells but" in text:
        return "cols_first"
    if "unknown column" in text:
        return "chip_columns"
    if "not a nes noise cell" in text:
        return "nes_noise_cells"
    if "is not a note" in text:
        return "note_format"
    if "unknown sample" in text:
        return "closed_lists"
    return "parse"


def _fix_for(rule: str) -> str:
    return {
        "cols_first": "every row needs exactly one cell per column named "
                      "in `cols`; use `...` for an empty cell",
        "chip_columns": "use only the columns in the briefing",
        "nes_noise_cells": "nes3 takes a number 0-15, not a note",
        "note_format": "write notes as `C-4` or `F#3`",
        "closed_lists": "use a name from the list in the briefing",
    }.get(rule, "")


def _parse_census(score: str):
    """Parse, setting aside each failing line, until it parses or the
    error budget runs out. -> (events, meta, issues, cleaned score)."""
    lines = score.splitlines()
    issues = []
    seen = set()
    while True:
        try:
            cleaned = "\n".join(lines) + "\n"
            events, meta = tracker_mod.loads(cleaned)
            return events, meta, issues, cleaned
        except tracker_mod.TrackerError as error:
            first = str(error).split("\n")[0]
            match = _LINE.match(first)
            if not match:
                issues.append(Issue("parse", "error", 0, first))
                return None, None, issues, None
            number, message = int(match.group(1)), match.group(2)
            rule = _rule_for(message)
            issues.append(Issue(rule, "error", number,
                                message.rstrip(". "), _fix_for(rule)))
            if number in seen or not 1 <= number <= len(lines) or \
                    len(issues) >= MAX_ERRORS:
                return None, None, issues, None
            seen.add(number)
            lines[number - 1] = "; " + lines[number - 1]


def _row_lines(score: str) -> list:
    """Source line number of each row, in order. Exact for scores that do
    not use `pattern`; for those that do, rows are reported by number."""
    out = []
    started = False
    for number, raw in enumerate(score.splitlines(), 1):
        body = tracker_mod._COMMENT.split(raw, maxsplit=1)[0].strip()
        if not body:
            continue
        head = body.split()[0].lower()
        if head in ("pattern", "order"):
            return []
        if head in ("cols", "columns"):
            started = True
            continue
        if head in tracker_mod.DIRECTIVES:
            continue
        if started:
            out.append(number)
    return out


def check(text: str, chip: str = None, request: dict = None,
          render: bool = False) -> Verdict:
    """Everything wrong with a reply, as a Verdict.

    `chip` holds the score to that chip's columns; `request` (bars) holds
    it to a length. `render` also renders it and reports a silent result
    — slower, and the static checks already name every known cause.
    """
    import instruments as instruments_mod
    import opl_instruments
    import sanity

    score, extracted = extract(text)
    if not score.strip():
        issue = Issue("no_score", "error", 0,
                      "the reply contains no score",
                      "send the score itself — the header lines and the "
                      "rows — not a description of it or code that would "
                      "write it")
        return Verdict("", extracted, [], [issue], {})
    score, repairs = repair(score)

    events, meta, issues, parsed = _parse_census(score)
    stats = {}
    text_lines = score.splitlines()

    # Names: an unknown instrument parses fine and fails at render, after
    # the work is done. Caught here with the nearest real names.
    fm_names = set(instruments_mod.describe())
    opl_names = set(opl_instruments.names())
    for number, raw in enumerate(text_lines, 1):
        words = tracker_mod._COMMENT.split(raw, maxsplit=1)[0].split()
        if len(words) >= 3 and words[0].lower() == "inst":
            column, name = words[1].lower(), words[2]
            known = opl_names if column.startswith("opl") else fm_names
            if name not in known:
                close = difflib.get_close_matches(name, sorted(known), n=2)
                hint = (f" (closest: {', '.join(close)})" if close else "")
                kind = "OPL2" if column.startswith("opl") else "FM"
                issues.append(Issue(
                    "closed_lists", "error", number,
                    f"there is no {kind} instrument {name!r}{hint}",
                    f"use one of: {', '.join(sorted(known))}"))

    columns_used = []
    for number, raw in enumerate(text_lines, 1):
        words = tracker_mod._COMMENT.split(raw, maxsplit=1)[0].split()
        if words and words[0].lower() in ("cols", "columns"):
            columns_used = [w.lower() for w in words[1:]]
            if chip in CHIP_COLUMNS:
                foreign = [c for c in columns_used
                           if tracker_mod.column_aliases().get(c, c)
                           not in CHIP_COLUMNS[chip]]
                if foreign:
                    issues.append(Issue(
                        "chip_columns", "error", number,
                        f"{', '.join(foreign)} "
                        f"{'is not a' if len(foreign) == 1 else 'are not'} "
                        f"{chip} column{'s' if len(foreign) != 1 else ''}",
                        f"use only: {' '.join(sorted(CHIP_COLUMNS[chip]))}"))
            if "fm5" in columns_used and "dac" in columns_used:
                issues.append(Issue(
                    "fm5_vs_dac", "error", number,
                    "fm5 and dac are both in `cols` — the DAC takes FM "
                    "channel 6, so one of them is lost",
                    "move the fm5 part to fm0-fm4"))
    if "dac" in columns_used and not re.search(
            r"^\s*vol\s+dac\s+\d+", score, re.MULTILINE):
        issues.append(Issue(
            "dac_level", "warning", 0,
            "no `vol dac` line — at full level the drums take all the "
            "headroom and the music comes out quiet",
            "add `vol dac 55` to the header"))

    if events is not None:
        stats = _stats(events, meta, columns_used)
        # Rows of the text that actually parsed: a line the census set
        # aside is not a row, and counting it would put every later
        # finding one line too early.
        rows = _row_lines(parsed)
        row_seconds = 60.0 / meta.bpm / max(1, meta.lpb)
        rate = meta.ticks_per_second
        for finding in sanity.silent_failures(events, rate):
            row = int(round(finding.at / row_seconds)) if row_seconds else 0
            line = rows[row] if 0 <= row < len(rows) else 0
            message = finding.message
            if finding.count > 1:
                message += f" ({finding.count} notes)"
            issues.append(Issue(finding.rule, finding.severity, line,
                                message, finding.fix))
        silent = {f.text() for f in sanity.silent_failures(events, rate)}
        for warning in sanity.check(events, rate):
            if warning not in silent:
                issues.append(Issue("sanity", "warning", 0,
                                    warning.rstrip(".")))

        if request and request.get("bars"):
            wanted = int(request["bars"]) * 4 * max(1, meta.lpb)
            have = stats.get("rows", 0)
            bars = int(request["bars"])
            asked = f"{bars} bar{'s' if bars != 1 else ''}"
            if have < wanted * 0.75:
                issues.append(Issue(
                    "row_count", "error", 0,
                    f"the score is {have} rows and {asked} is {wanted}",
                    f"write all {wanted} rows"))
            elif have > wanted * 1.5:
                issues.append(Issue(
                    "row_count", "warning", 0,
                    f"the score is {have} rows; {asked} is {wanted}", ""))

        if render:
            import chipgen
            result = chipgen.compose(score)
            stats["peak"] = round(result.peak, 4)
            if result.peak == 0.0:
                issues.append(Issue(
                    "silent", "error", 0,
                    "the score renders to silence", ""))

    try:
        import integrity
        for changed in integrity.changed():
            issues.append(Issue(
                "engine_modified", "error", 0,
                f"the engine file {changed} has been edited — a render "
                f"from a modified engine is not a chipgen render",
                "restore it from the archive and fix the SCORE instead"))
    except ImportError:
        pass

    def quoted(issue):
        if not issue.line or issue.quote or issue.line > len(text_lines):
            return issue
        body = " ".join(text_lines[issue.line - 1].split())
        if len(body) > 60:
            body = body[:57] + "..."
        return issue._replace(quote=body)

    issues = [quoted(i) for i in issues]
    issues.sort(key=lambda i: (i.severity != "error", i.line or 10**6))
    return Verdict(score, extracted, repairs, issues, stats)


def _stats(events, meta, columns) -> dict:
    import events as events_mod
    ticks = sum(e.ticks for e in events if isinstance(e, events_mod.Wait))
    ticks_per_row = max(1, meta.ticks_per_row())
    return {
        "rows": int(round(ticks / ticks_per_row)),
        "seconds": round(ticks / meta.ticks_per_second, 2),
        "bpm": meta.bpm,
        "lpb": meta.lpb,
        "columns": list(columns),
        "events": len(events),
    }


def main(argv):
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="check a model's reply: extract the score, repair "
                    "what has one meaning, report everything else")
    parser.add_argument("source", help="a reply or a score, or - for stdin")
    parser.add_argument("--chip", help="hold it to one chip's columns")
    parser.add_argument("--bars", type=int, help="hold it to a length")
    parser.add_argument("--render", action="store_true",
                        help="also render it and catch a silent result")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--feedback", action="store_true",
                        help="print what would be sent back to the model")
    args = parser.parse_args(argv)

    text = sys.stdin.read() if args.source == "-" else \
        open(args.source, encoding="utf-8").read()
    verdict = check(text, chip=args.chip,
                    request={"bars": args.bars} if args.bars else None,
                    render=args.render)
    if args.json:
        print(json.dumps(verdict.to_json(), indent=1, ensure_ascii=False))
    elif args.feedback:
        print(verdict.feedback())
    else:
        print(verdict.report())
    return 0 if verdict.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
