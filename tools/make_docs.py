"""make_docs.py — keep the tables in docs/*.md equal to the code.

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
DOCS = [DOC, os.path.join(ROOT, "docs", "FORGE.md")]

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
        "forge": "`NAME`, `bank:NAME`, `kit:NAME`",
        "dna": "`axis:value,...`",
        "reg": "a note, `A3`",
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


def axes():
    from schism.forge import dna
    rows = ["| dial | 0 is | 1 is | how it is measured |", "|---|---|---|---|"]
    for axis in dna.AXES:
        low, high, how = dna.AXIS_DOC[axis]
        rows.append(f"| `{axis}` | {_cell(low)} | {_cell(high)} | "
                    f"{_cell(how)} |")
    return "\n".join(rows)


def families():
    from schism.forge import family
    rows = ["| family | what it is | structure it can choose |", "|---|---|---|"]
    for name in family.families():
        f = family.get_family(name)
        kinds = getattr(f, "kinds", None)
        if kinds:
            choice = "kind: " + ", ".join(f"`{k}`" for k in sorted(kinds))
        else:
            choice = "; ".join(f"{g}: " + ", ".join(f"`{o}`" for o in opts)
                               for g, opts in sorted(f.genes.items()))
        missing = [a for a in ("detune", "motion", "articulation")
                   if a not in f.supports]
        note = f" (no {', '.join(missing)})" if missing else ""
        rows.append(f"| `{name}` | {_cell(f.doc)}{note} | {_cell(choice)} |")
    return "\n".join(rows)


def archetypes():
    from schism.forge import archetypes as A
    rows = ["| name | family | tuned to | plays | what it is |",
            "|---|---|---|---|---|"]
    for name in sorted(A.ARCHETYPES, key=lambda n: (A.get(n).family, n)):
        a = A.get(name)
        rows.append(f"| `{name}` | {a.family} | {a.register[0]}{a.register[1]}"
                    f" | {', '.join(a.roles)} | {_cell(a.doc)} |")
    return "\n".join(rows)


def scenario_keys():
    from schism.forge import bank
    return ", ".join(f"`{k}`" for k in sorted(bank.SCENARIO_KEYS))


def kit_styles():
    from schism.forge import kits
    rows = ["| style | what it changes |", "|---|---|"]
    for name in kits.style_names():
        moves = kits.STYLES[name]
        parts = []
        for role, deltas in moves.items():
            if deltas:
                parts.append(f"{role}: " + ", ".join(
                    f"{a} {d:+g}" for a, d in deltas.items()))
        rows.append(f"| `{name}` | {_cell('; '.join(parts))} |")
    return "\n".join(rows)


def kit_roles():
    from schism.forge import kits
    rows = ["| drum | archetype | key |", "|---|---|---|"]
    for role, arch, key in kits.ROLES:
        rows.append(f"| `{role}` | `{arch}` | `{key}` |")
    return "\n".join(rows)


def forge_cli():
    """The command list in cli.py's docstring: its second paragraph."""
    import textwrap
    from schism.forge import cli
    commands = cli.__doc__.strip("\n").split("\n\n")[1]
    return "```text\n" + textwrap.dedent(commands).strip("\n") + "\n```"


def conflicts():
    return "\n".join([
        "| conflict | what it names |", "|---|---|",
        "| `masking` | two parts share most of their spectrum at the same "
        "times, and one is quieter |",
        "| `bass_stack` | two parts below D-3 share their low bands |",
        "| `mud` | a part above the bass puts a large share of its energy "
        "under 300 Hz |",
        "| `sustain_blanket` | a part that holds long notes sits within an "
        "octave of a part that moves |",
        "| `onsets` | most notes of two sharp-attacked parts start on the "
        "same row |",
        "| `clash` | notes a second, tritone or seventh apart in two parts "
        "(worse if both timbres are metallic) |",
        "| `dynamics` | a part is louder than its job allows against the "
        "lead |",
        "| `pan_stack` | three or more parts in the middle that share a "
        "spectrum |",
        "| `voices` | more than 32 voices sound at once (NNA continue/fade "
        "instruments pile up) |"])


BLOCKS = {"waves": waves, "settings": settings, "envelopes": envelopes,
          "effects": effects, "volume": volume_column, "badscore": badscore,
          "axes": axes, "families": families, "archetypes": archetypes,
          "scenario_keys": scenario_keys, "kit_styles": kit_styles,
          "kit_roles": kit_roles, "forge_cli": forge_cli,
          "conflicts": conflicts}
#: opening marker, whatever is between, closing marker; the body may be empty
_BLOCK = re.compile(r"(<!-- generated:(\w+) -->\n)(.*?)(<!-- /generated -->)",
                    re.S)


def render(text: str) -> str:
    def fill(match):
        name = match.group(2)
        if name not in BLOCKS:
            raise SystemExit(f"a document asks for an unknown table "
                             f"{name!r}; have {', '.join(BLOCKS)}")
        return match.group(1) + BLOCKS[name]() + "\n" + match.group(4)
    return _BLOCK.sub(fill, text)


def main(argv):
    status = 0
    for path in DOCS:
        if not os.path.exists(path):
            continue
        name = os.path.relpath(path, ROOT)
        with open(path, encoding="utf-8") as handle:
            old = handle.read()
        new = render(old)
        if "--check" in argv:
            if new != old:
                print(f"{name} has stale tables; run "
                      f"python3 tools/make_docs.py", file=sys.stderr)
                status = 1
        elif new != old:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(new)
            print(f"rewrote the tables in {name}")
        else:
            print(f"{name} is up to date")
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
