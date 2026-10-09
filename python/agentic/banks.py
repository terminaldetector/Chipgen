"""banks.py — instruments from outside the built-in bank, in a project.

A project's voices name patches. The built-in bank has 19; the forge
(python/forge.py, bridge/SYNTHESIS.md) makes more, and an FM bank file
(the CLI's --bank) can hold others. A project keeps the banks it uses in
its own folder (banks/), lists them in its structure — so a revision and a
rollback carry them like the rest of the music — and installs them when it
is opened, before any score is parsed: the forge's detune layers and
programs are placed by the tracker as it reads (synthesis/placement.py).

A forge instrument with a detune layer plays the same note on the next FM
channel when nothing else plays there. That channel belongs to the voice:
`layer_columns` names it, the range render puts it in the voice's stem and
takes it out of the voice's background, and the timeline does not count
its notes as a voice of their own. A layer whose channel another voice
claims is dropped by the placement pass (its report says so), and then
there is nothing to attribute.

Only YM2612 instruments play on a Mega Drive project. A forge bank's NES
and OPL2 instruments are listed as not for this target, never installed
under a Mega Drive voice.
"""

import hashlib
import os
import shutil
from typing import Dict, List

from .state import AgenticError

KINDS = ("forge", "fm")
#: FM channels on the YM2612.
_FM_CHANNELS = 6


def _sha(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()[:16]


def describe(path: str, kind: str) -> Dict[str, list]:
    """What a bank file brings: the instruments a Mega Drive voice can
    play (with their layer count), and the ones it cannot."""
    if kind == "forge":
        from synthesis import bank as forge_bank
        b = forge_bank.Bank.load(path)
        playable, other = [], []
        for e in b.entries:
            if e.backend == "ym2612":
                style = getattr(e.compiled, "style", None) or {}
                playable.append({"name": e.name,
                                 "layers": len(style.get("layers", ()))})
            else:
                other.append({"name": e.name, "backend": e.backend})
        return {"for_megadrive": playable, "not_for_megadrive": other}
    import instruments
    loaded = instruments.load_bank(path, merge=False)
    return {"for_megadrive": [{"name": n, "layers": 0} for n in loaded],
            "not_for_megadrive": []}


def install(root: str, structure: dict) -> List[str]:
    """Install every bank the structure lists (paths under `root`), in
    order; a later bank's instrument replaces an earlier one of the same
    name, as the CLI's --bank does. -> the names installed."""
    names = []
    for b in structure.get("banks", []) or []:
        path = os.path.join(root, b["path"])
        if not os.path.exists(path):
            raise AgenticError("missing_bank", f"the project lists "
                               f"{b['path']} and it is not in {root}",
                               path=b["path"])
        if b.get("sha256") and _sha(path) != b["sha256"]:
            raise AgenticError("bank_changed", f"{b['path']} is not the "
                               f"file the project recorded (its hash "
                               f"differs); add it again to use it",
                               path=b["path"])
        if b["kind"] == "forge":
            from synthesis import bank as forge_bank
            names += forge_bank.Bank.load(path).install()
        elif b["kind"] == "fm":
            import instruments
            names += list(instruments.load_bank(path))
        else:
            raise AgenticError("bad_bank", f"bank kind is one of "
                               f"{', '.join(KINDS)}, not {b['kind']!r}")
    return names


def add(project, source: str, kind: str = "forge") -> dict:
    """Copy a bank file into the project, check it loads, install it and
    record it in the structure (a revision). -> what it brought."""
    from . import state as S
    if kind not in KINDS:
        raise AgenticError("bad_bank", f"bank kind is one of "
                           f"{', '.join(KINDS)}, not {kind!r}")
    if not os.path.isfile(source):
        raise AgenticError("no_file", f"no bank file at {source}",
                           path=source)
    try:
        brought = describe(source, kind)
    except AgenticError:
        raise
    except Exception as error:
        raise AgenticError("bad_bank", f"{os.path.basename(source)} is not "
                           f"a {kind} bank this engine reads: "
                           f"{str(error)[:160]}") from None
    rel = os.path.join("banks", os.path.basename(source))
    os.makedirs(project.path("banks"), exist_ok=True)
    if os.path.abspath(source) != os.path.abspath(project.path(rel)):
        shutil.copy(source, project.path(rel))
    entry = {"kind": kind, "path": rel, "sha256": _sha(project.path(rel))}
    c = S.content(project.state)
    banks = [b for b in c["structure"].get("banks", []) or []
             if b["path"] != rel] + [entry]
    c["structure"]["banks"] = banks
    install(project.root, c["structure"])
    rev = project.commit(c, {"kind": "bank", "added": rel})
    return {"rev": rev, "bank": entry, **brought}


def _playing(content: dict) -> set:
    """FM columns with at least one note in some section — the channels
    the placement pass counts as taken."""
    from . import patch as P
    out = set()
    for k, column in enumerate(content["columns"]):
        if not column.startswith("fm"):
            continue
        for section in content["sections"]:
            if any(P._pitch(P._split(row[k])[0]) for row in section["rows"]):
                out.add(column)
                break
    return out


def layer_columns(content: dict, voice: str) -> List[str]:
    """The FM channels a voice's forge instrument puts its detune layers
    on: the ones after its own, if none of them carries a note of the
    score (otherwise the placement pass drops the layers, and says so)."""
    ins = content["instruments"].get(voice) or {}
    channel = ins.get("channel", "")
    if not channel.startswith("fm") or not ins.get("patch"):
        return []
    try:
        from synthesis import registry
    except ImportError:            # pragma: no cover - the layer is optional
        return []
    compiled = registry.lookup("ym2612", ins["patch"])
    if compiled is None:
        return []
    style = getattr(compiled, "style", None) or {}
    n = len(style.get("layers", ()))
    taken = _playing(content)
    base = int(channel[2:])
    out = []
    for k in range(1, n + 1):
        c = base + k
        name = f"fm{c}"
        if c >= _FM_CHANNELS or name in taken:
            return []          # placement drops the whole layer set
        out.append(name)
    return out


def layers(content: dict) -> Dict[str, str]:
    """{layer column: the voice it belongs to} for the whole project."""
    out = {}
    for voice in content["instruments"]:
        for column in layer_columns(content, voice):
            out[column] = voice
    return out
