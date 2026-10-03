"""make_docs.py — keep the tables in docs/NOTATION.md equal to the code.

    python3 tools/make_docs.py            rewrite the generated tables
    python3 tools/make_docs.py --check    exit 1 if a table is out of date

The vocabulary of a score — waveforms, instrument settings, envelopes,
effects — lives in tables in schism/recipes.py and schism/model.py, because
the same tables are what an error message lists when a model gets a key
wrong. The tables in the documentation are written from those, between
`<!-- generated:NAME -->` and `<!-- /generated -->`, so a setting added to
the code cannot be missing from the manual. The prose around them is
written by hand, and every ```sch example in it is compiled by the tests.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from schism import model as M, recipes  # noqa: E402

DOC = os.path.join(ROOT, "docs", "NOTATION.md")

#: how each waveform is stored, which is what decides how it behaves when
#: played at other notes
KIND = {
    "sine": "pitched", "saw": "pitched", "square": "pitched",
    "triangle": "pitched", "pulse": "pitched", "fm": "pitched",
    "pad": "looped", "noise": "looped",
    "kick": "one-shot", "snare": "one-shot", "hat": "one-shot",
    "openhat": "one-shot", "clap": "one-shot", "tom": "one-shot",
    "rim": "one-shot", "pluck": "one-shot, tuned", "bell": "one-shot, tuned",
}

#: ITTECH's parameter for each effect letter
EFFECT_PARAM = {
    "A": "Axx  xx = ticks per row (A00 does nothing)",
    "B": "Bxx  xx = order to jump to",
    "C": "Cxx  xx = row (hex) of the next order",
    "D": "Dx0 up, D0y down, DFy fine down, DxF fine up",
    "E": "Exx down xx a tick; EFx fine; EEx extra fine",
    "F": "Fxx up xx a tick; FFx fine; FEx extra fine",
    "G": "Gxx  xx = speed toward the row's note",
    "H": "Hxy  x = speed, y = depth",
    "I": "Ixy  x = ticks on, y = ticks off",
    "J": "Jxy  steps 0, +x, +y semitones, one a tick",
    "K": "Kxy  as H00 and Dxy together",
    "L": "Lxy  as G00 and Dxy together",
    "M": "Mxx  xx = 00..40",
    "N": "Nxy  as D, on the channel volume",
    "O": "Oxx  start xx*256 frames in",
    "P": "Pxy  as D, on the panning",
    "Q": "Qxy  every y ticks, volume change x",
    "R": "Rxy  x = speed, y = depth",
    "S": "S1x glissando, S3x/S4x/S5x waveforms, S6x delay x ticks, "
         "S7x NNA and envelope, S8x pan, S9x surround, SAx high offset, "
         "SBx loop, SCx cut after x ticks, SDx note delay x ticks, "
         "SEx delay x rows, SFx macro",
    "T": "Txx  20..FF; T0x slides down, T1x up",
    "U": "Uxy  as H, a quarter as deep",
    "V": "Vxx  xx = 00..80",
    "W": "Wxy  as D, on the global volume",
    "X": "Xxx  00 left, 80 centre, FF right",
    "Y": "Yxy  x = speed, y = depth",
    "Z": "Zxx  macros; with IT's defaults Z00..Z7F sets the filter cutoff "
         "and Z80..Z8F the resonance",
}


def _cell(text):
    return str(text).replace("|", "\\|")


def waves():
    rows = ["| `wave=` | stored as | what it is | settings (default, range) |",
            "|---|---|---|---|"]
    for kind, (what, params) in recipes.WAVES.items():
        shown = []
        for name, (parser, default) in params.items():
            if parser is recipes._note:
                value = M.note_name(default)
            elif isinstance(default, float):
                value = f"{default:g}"
            else:
                value = str(default)
            shown.append(f"`{name}={value}` ({parser.doc})")
        rows.append(f"| `{kind}` | {KIND[kind]} | {_cell(what)} | "
                    f"{_cell(', '.join(shown)) or '—'} |")
    return "\n".join(rows)


def settings():
    forms = {
        "vib": "`speed/depth/rate[/wave]`",
        "kit": "`KEY:sample,KEY:sample,...`",
    }
    rows = ["| setting | values | what it does |", "|---|---|---|"]
    for key, (parser, doc) in recipes.SETTINGS.items():
        if key in recipes.ENVELOPES:
            continue
        if key in forms:
            values = forms[key]
        elif key == "wave":
            values = "see the waveform table"
        else:
            values = f"`{parser.doc}`"
        rows.append(f"| `{key}=` | {_cell(values)} | {_cell(doc)} |")
    return "\n".join(rows)


def envelopes():
    rows = ["| setting | slot | value range | meaning |", "|---|---|---|---|"]
    for key, (slot, (low, high), doc) in recipes.ENVELOPES.items():
        rows.append(f"| `{key}=` | {slot} | {low}..{high} | {_cell(doc)} |")
    return "\n".join(rows)


def effects():
    rows = ["| letter | effect | parameter |", "|---|---|---|"]
    for letter in M.EFFECT_LETTERS[1:]:
        rows.append(f"| `{letter}` | {_cell(M.EFFECT_MEANING[letter])} | "
                    f"{_cell(EFFECT_PARAM[letter])} |")
    return "\n".join(rows)


def volume_column():
    rows = ["| written | means |", "|---|---|"]
    for letter, meaning in M.VOLUME_MEANING.items():
        rows.append(f"| `{letter}00`..`{letter}{'64' if letter in 'vp' else '09'}`"
                    f" | {_cell(meaning)} |")
    return "\n".join(rows)


#: a score with three mistakes, compiled when the docs are written so the
#: message printed beside it is the compiler's own
BAD_SCORE = """title bad
tempo 120
inst 1 name=Lead wave=saww
inst 2 wave=pulse duty=0.3 bar=2
pattern a rows 32
C-5 01 v64 ... | ...
rest 31
end
order a
"""


def badscore():
    from schism import notation
    try:
        notation.compile_text(BAD_SCORE)
    except notation.NotationError as error:
        message = str(error)
    else:
        raise SystemExit("the example bad score compiled")
    return (f"```sch-bad\n{BAD_SCORE}```\n\n```text\n{message}\n```")


BLOCKS = {"waves": waves, "settings": settings, "envelopes": envelopes,
          "effects": effects, "volume": volume_column, "badscore": badscore}
#: opening marker, whatever is between, closing marker; the body may be empty
_BLOCK = re.compile(r"(<!-- generated:(\w+) -->\n)(.*?)(<!-- /generated -->)",
                    re.S)


def render(text: str) -> str:
    def fill(match):
        name = match.group(2)
        if name not in BLOCKS:
            raise SystemExit(f"docs/NOTATION.md asks for an unknown table "
                             f"{name!r}; have {', '.join(BLOCKS)}")
        return match.group(1) + BLOCKS[name]() + "\n" + match.group(4)
    return _BLOCK.sub(fill, text)


def main(argv):
    with open(DOC, encoding="utf-8") as handle:
        old = handle.read()
    new = render(old)
    if "--check" in argv:
        if new != old:
            print("docs/NOTATION.md has stale tables; run "
                  "python3 tools/make_docs.py", file=sys.stderr)
            return 1
        return 0
    if new != old:
        with open(DOC, "w", encoding="utf-8") as handle:
            handle.write(new)
        print("rewrote the tables in docs/NOTATION.md")
    else:
        print("docs/NOTATION.md is up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
