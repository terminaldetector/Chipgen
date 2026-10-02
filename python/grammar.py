"""grammar.py — a GBNF grammar per chip, built from the engine's own lists.

## Why a grammar

A briefing asks a model to keep a format; a grammar makes it unable not
to. Grammar-constrained decoding (llama.cpp's `grammar`, vLLM's
`guided_grammar`) only lets the model emit tokens the grammar allows, so
for a local model the whole class of format errors disappears — and so
does the briefing that taught the format. That is the cheapest context
there is: a rule the decoder enforces costs the prompt nothing.

This grammar goes further than syntax. Because it is built per chip and
per request, it also forbids the silent failures that are expressible in
a grammar:

  * the header is fixed — tempo, columns, `vol dac 55` — and every FM or
    OPL column gets an `inst` line, chosen from the real bank;
  * PSG attenuation stops at 14 (15 is silence) and PSG notes start at
    A-2 (below it the divider clamps and plays A-2);
  * NES pulses start at A-1, the triangle at A-0 and takes no velocity,
    and the noise column takes periods 0-15, not notes;
  * the score is exactly the requested number of rows, so it can neither
    stop early nor run on.

What a grammar cannot hold — whether the music is any good — is left to
the model, and to reply.py.

The grammars here use no recursion, so they are regular: `to_regex()`
compiles one into a Python pattern, which is how the tests prove the
grammar accepts what the engine plays and rejects what it should.
"""

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_PITCH = '[A-G] "-" | [ACDFG] "#"'
_VELOCITY = '":" ( [1-9] | [1-9] [0-9] | "1" [01] [0-9] | "12" [0-7] )'


def _alternatives(names) -> str:
    # Longest first, so a reader of the grammar sees `hat_open` before
    # `hat`; the decoder does not care, a regex reading of it might.
    ordered = sorted(names, key=lambda n: (-len(n), n))
    return " | ".join(f'"{name}"' for name in ordered)


def _from_octave(low_octave: int, low_letters: str, top: int) -> str:
    """Pitches from `low_letters` in `low_octave` up through octave `top`.

    `low_letters` is the first octave's allowed spellings, e.g. A-2, A#2,
    B-2 for the PSG floor.
    """
    first = " | ".join(f'"{p}{low_octave}"' for p in low_letters.split())
    rest = f"( {_PITCH} ) [{low_octave + 1}-{top}]"
    return f"{first} | {rest}"


def gbnf(chip: str, request: dict = None) -> str:
    """The grammar for one chip and one request. One rule per line."""
    import prompts

    request = request or {}
    if chip not in prompts.CARDS:
        raise ValueError(f"no grammar for {chip!r}; have "
                         f"{', '.join(prompts.CARDS)}")
    import samples as samples_mod

    bpm = int(request.get("bpm") or 140)
    rows = prompts.rows_for(request)
    columns = prompts.CARDS[chip]["cols"].split()
    rules = []

    if chip == "YM2612":
        fm = [c for c in columns if c.startswith("fm")]
        header = [f'"bpm {bpm}\\n"', '"lpb 4\\n"']
        header += [f"inst-{c}" for c in fm]
        header += ['"vol dac 55\\n"', f'"cols {" ".join(columns)}\\n"']
        rules.append("root ::= header row{%d}" % rows)
        rules.append("header ::= " + " ".join(header))
        for c in fm:
            rules.append(f'inst-{c} ::= "inst {c} " fm-name "\\n"')
        cells = {"fm": "fm-cell", "psg": "psg-cell", "noise": "noise-cell",
                 "dac": "dac-cell"}
        kinds = [("fm" if c.startswith("fm") else
                  "psg" if c.startswith("psg") else c) for c in columns]
        rules.append("row ::= " + ' " "+ '.join(cells[k] for k in kinds)
                     + ' "\\n"')
        rules.append("fm-name ::= "
                     + _alternatives(prompts.instrument_names("fm")))
        rules.append("fm-cell ::= hold | off | fm-note")
        rules.append(f"fm-note ::= ( {_PITCH} ) [0-7] velocity?")
        # A-2 is the PSG floor; G#6 the top that stays within 10 cents.
        rules.append("psg-cell ::= hold | off | psg-note attenuation?")
        rules.append("psg-note ::= " + _from_octave(2, "A- A# B-", 5)
                     + ' | ( "C-" | "C#" | "D-" | "D#" | "E-" | "F-" | "F#"'
                       ' | "G-" | "G#" ) "6"')
        rules.append('attenuation ::= ":" ( [0-9] | "1" [0-4] )')
        rules.append('noise-cell ::= hold | off | [wp] [0-2]')
        rules.append("dac-cell ::= hold | sample")
    elif chip == "RP2A03":
        header = [f'"bpm {bpm}\\n"', '"lpb 4\\n"',
                  '"nes duty nes0 " [0-3] "\\n"',
                  '"nes duty nes1 " [0-3] "\\n"',
                  f'"cols {" ".join(columns)}\\n"']
        rules.append("root ::= header row{%d}" % rows)
        rules.append("header ::= " + " ".join(header))
        rules.append('row ::= pulse-cell " "+ pulse-cell " "+ triangle-cell '
                     '" "+ noise-cell " "+ dmc-cell "\\n"')
        rules.append("pulse-cell ::= hold | off | pulse-note velocity?")
        rules.append("pulse-note ::= " + _from_octave(1, "A- A# B-", 7))
        # No velocity: the triangle has no volume register.
        rules.append("triangle-cell ::= hold | off | triangle-note")
        rules.append("triangle-note ::= " + _from_octave(0, "A- A# B-", 6))
        rules.append('noise-cell ::= hold | off | ( [0-9] | "1" [0-5] ) '
                     '"m"? velocity?')
        rules.append("dmc-cell ::= hold | sample")
    else:                                               # YM3812
        header = [f'"bpm {bpm}\\n"', '"lpb 4\\n"']
        header += [f"inst-{c}" for c in columns]
        header += [f'"cols {" ".join(columns)}\\n"']
        rules.append("root ::= header row{%d}" % rows)
        rules.append("header ::= " + " ".join(header))
        for c in columns:
            rules.append(f'inst-{c} ::= "inst {c} " opl-name "\\n"')
        rules.append("row ::= " + ' " "+ '.join("opl-cell" for _ in columns)
                     + ' "\\n"')
        rules.append("opl-name ::= "
                     + _alternatives(prompts.instrument_names("opl")))
        rules.append(f"opl-cell ::= hold | off | ( {_PITCH} ) [0-7] "
                     f"velocity?")

    rules.append('hold ::= "..."')
    rules.append('off ::= "==="')
    rules.append(f"velocity ::= {_VELOCITY}")
    if chip != "YM3812":
        rules.append("sample ::= " + _alternatives(samples_mod.names()))
    return "\n".join(rules) + "\n"


# --------------------------------------------------------------------------
# Reading a grammar back — for the tests, and for anyone checking a score
# against the grammar it was meant to be decoded under.
# --------------------------------------------------------------------------
_TOKEN = re.compile(r'\s*(?:("(?:[^"\\]|\\.)*")|(\[(?:[^\]\\]|\\.)*\])|'
                    r'([A-Za-z][\w-]*)|(\{\d+(?:,\d*)?\})|([()|?*+]))')


def _parse_rules(text: str) -> dict:
    rules = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, body = line.partition("::=")
        rules[name.strip()] = body.strip()
    return rules


def to_regex(text: str) -> "re.Pattern":
    """Compile a non-recursive GBNF grammar into one Python regex."""
    rules = _parse_rules(text)
    compiled = {}

    def expand(name, stack):
        if name in compiled:
            return compiled[name]
        if name in stack:
            raise ValueError(f"rule {name!r} is recursive; to_regex() "
                             f"reads regular grammars only")
        if name not in rules:
            raise ValueError(f"rule {name!r} is used and never defined")
        compiled[name] = f"(?:{body(rules[name], stack | {name})})"
        return compiled[name]

    def body(source, stack):
        out = []
        position = 0
        while position < len(source):
            match = _TOKEN.match(source, position)
            if not match or match.end() == position:
                rest = source[position:].strip()
                if not rest:
                    break
                raise ValueError(f"cannot read grammar at {rest[:30]!r}")
            position = match.end()
            literal, charclass, name, count, symbol = match.groups()
            if literal:
                value = literal[1:-1].encode().decode("unicode_escape")
                out.append(re.escape(value))
            elif charclass:
                out.append(charclass)
            elif name:
                out.append(expand(name, stack))
            elif count:
                out[-1] = f"(?:{out[-1]}){count}"
            elif symbol == "(":
                out.append("(?:")
            elif symbol == ")":
                out.append(")")
            elif symbol == "|":
                out.append("|")
            else:                                   # ? * +
                out[-1] = f"(?:{out[-1]}){symbol}" if out[-1] != ")" \
                    else out[-1] + symbol
        return "".join(out)

    pattern = expand("root", set())
    return re.compile(pattern, re.DOTALL)


def matches(grammar: str, text: str) -> bool:
    return to_regex(grammar).fullmatch(text) is not None


def main(argv):
    import argparse
    parser = argparse.ArgumentParser(
        description="print the GBNF grammar for one chip")
    parser.add_argument("chip", nargs="?", default="YM2612")
    parser.add_argument("--bpm", type=int, default=140)
    parser.add_argument("--bars", type=int, default=8)
    args = parser.parse_args(argv)
    print(gbnf(args.chip, {"bpm": args.bpm, "bars": args.bars}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
