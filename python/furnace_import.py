"""
furnace_import.py — read Furnace / DefleMask / TFM instrument files.

chipgen's own bank is nineteen patches designed by hand. Furnace ships a
curated library of several hundred YM2612 instruments sorted by category
(bass, guitar, keys, strings, horn, synth, percussion...), and DefleMask
`.dmp` presets have been traded around the Genesis scene for well over a
decade. Those are the same four operators writing the same registers this
engine already drives — there is no conversion to argue about, only a file
format to read.

    python3 python/furnace_import.py path/to/instruments/OPN -o bank.json
    python3 python/chipgen.py score.trk --bank bank.json -o out.wav

Formats read here:

    .tfi   TFM Music Maker. 42 bytes, no header, no version: alg, fb, then
           four operators of ten bytes each.
    .dmp   DefleMask preset. Versioned; this reads the FM instruments of
           version 9 and up, which is what the modern library is.
    .vgi   VGM Music Maker. 43 bytes, like .tfi plus an FMS/AMS byte.

`.fui` (Furnace's own format) is NOT read: it is a versioned container of
optional feature blocks, and the library's `.fui` files duplicate patches
already available as `.dmp` or `.tfi`. Reading a third of a format badly
is worse than not reading it.

## Two things that are easy to get wrong

OPERATOR ORDER. The YM2612's register offsets ascend op1, op3, op2, op4,
and both Furnace's `op[]` array and this project's `FMInstrument.operators`
store operators in exactly that register order (Furnace's `orderedOps` =
{0,2,1,3} maps its UI numbering onto the same array). So operators copy
across positionally with no reshuffling — but only because BOTH sides use
register order, not because order does not matter.

DETUNE IS NOT THE REGISTER VALUE. Furnace stores detune in its own
0-7 space centred on 3, and converts through a table when writing the
chip: `dtTable = {7,6,5,0,1,2,3,4}`. `.tfi` and `.dmp` both store the
Furnace-space value, so it has to go through that table on the way in.
Skipping this silently detunes every imported patch — audible as a patch
that beats against itself, not as anything that looks like a bug.

Both facts are lifted from Furnace's own source (`fileOpsIns.cpp`,
`platform/fmsharedbase.h`, `platform/genesis.cpp`) rather than guessed.

## Licensing

Instrument files are settings someone dialled in, not audio, and the
Furnace library is distributed with Furnace itself — but "distributed
with an open-source tracker" is not the same as "public domain", and
individual `.dmp` presets circulating in the scene have their own
histories. Importing to study or to build on is uncontroversial;
redistributing a bank wholesale as your own is a decision to make
deliberately. Same note as vgm_import.py, same reasoning.
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import instruments as instruments_mod
from opn2 import FMInstrument, Operator

#: Furnace's detune space -> the chip's DT1 register field.
#: From src/engine/platform/fmsharedbase.h. Furnace's 3 is the chip's 0
#: (no detune); below 3 detunes one way, above it the other.
DT_TABLE = (7, 6, 5, 0, 1, 2, 3, 4)

#: DefleMask system byte -> whether it is a 4-operator OPN-family part we
#: can use. 2 = Genesis (YM2612), 9 = Neo Geo (YM2610), both OPN.
DMP_OPN_SYSTEMS = {2: "Genesis", 9: "Neo Geo"}

SUPPORTED_EXTENSIONS = (".tfi", ".dmp", ".vgi")


class InstrumentFormatError(ValueError):
    """Raised with the file name, because a bad byte 30 files in is useless
    information without one."""


def _operator(mult, dt, tl, rs, ar, dr, d2r, rr, sl, ssg, am=0) -> Operator:
    """One operator, with every field already in REGISTER units."""
    return Operator(
        detune=DT_TABLE[dt & 7],
        multiple=mult & 0x0F,
        total_level=tl & 0x7F,
        rate_scaling=rs & 0x03,
        attack_rate=ar & 0x1F,
        decay_rate=dr & 0x1F,
        sustain_rate=d2r & 0x1F,
        release_rate=rr & 0x0F,
        sustain_level=sl & 0x0F,
        ssg_eg=ssg & 0x0F,
        am_enable=1 if am else 0,
    )


# --------------------------------------------------------------------------
# .tfi / .vgi — flat, headerless
# --------------------------------------------------------------------------
def load_tfi(data: bytes, name: str = "") -> FMInstrument:
    """TFM Music Maker: alg, fb, then 4 x (mult dt tl rs ar dr d2r rr sl ssg)."""
    if len(data) < 42:
        raise InstrumentFormatError(
            f"{name or '.tfi'}: expected 42 bytes, got {len(data)}")
    algorithm, feedback = data[0] & 7, data[1] & 7
    operators = []
    for i in range(4):
        base = 2 + i * 10
        mult, dt, tl, rs, ar, dr, d2r, rr, sl, ssg = data[base:base + 10]
        operators.append(_operator(mult, dt, tl, rs, ar, dr, d2r, rr, sl, ssg))
    return FMInstrument(algorithm, feedback, operators, name)


def load_vgi(data: bytes, name: str = "") -> FMInstrument:
    """VGM Music Maker: .tfi with an FMS/AMS byte wedged in after feedback."""
    if len(data) < 43:
        raise InstrumentFormatError(
            f"{name or '.vgi'}: expected 43 bytes, got {len(data)}")
    algorithm, feedback = data[0] & 7, data[1] & 7
    operators = []
    for i in range(4):
        base = 3 + i * 10       # byte 2 is fms/ams, which is per-channel
        mult, dt, tl, rs, ar, dr, d2r, rr, sl, ssg = data[base:base + 10]
        # VGI packs AM into the top bit of dr, unlike .tfi
        operators.append(_operator(mult, dt, tl, rs, ar, dr & 0x1F, d2r, rr,
                                   sl, ssg, am=dr & 0x80))
    return FMInstrument(algorithm, feedback, operators, name)


# --------------------------------------------------------------------------
# .dmp — DefleMask, versioned
# --------------------------------------------------------------------------
def load_dmp(data: bytes, name: str = "") -> FMInstrument:
    """DefleMask preset, FM instruments of version 9+.

    Layout for the modern (version 11) form this reads:

        0  version        1  system      2  mode (1 = FM)
        3  fms            4  feedback    5  algorithm    6  ams
        7+ four operators of eleven bytes:
           mult tl ar dr sl rr am rs dt d2r ssgEnv
    """
    label = name or ".dmp"
    if len(data) < 3:
        raise InstrumentFormatError(f"{label}: too short to be a .dmp")

    version = data[0]
    if version > 11:
        raise InstrumentFormatError(
            f"{label}: .dmp version {version} is newer than this reader knows")
    if version < 9:
        # Older files omit the system byte and shuffle the operator layout;
        # the shipped library is all version 11, so rather than guess at a
        # format nothing here can be tested against, say so.
        raise InstrumentFormatError(
            f"{label}: .dmp version {version} predates the layout this reads "
            f"(needs 9 or newer)")

    offset = 1
    system = None
    if version >= 11:
        system = data[offset]
        offset += 1
        if system not in DMP_OPN_SYSTEMS:
            raise InstrumentFormatError(
                f"{label}: system {system} is not an OPN-family FM chip "
                f"(this reads {', '.join(DMP_OPN_SYSTEMS.values())})")

    mode = data[offset]
    offset += 1
    if not mode:
        raise InstrumentFormatError(
            f"{label}: this is a PSG/standard instrument, not a 4-operator "
            f"FM patch")

    fms = data[offset]; offset += 1          # noqa: F841 - per channel, not per patch
    feedback = data[offset] & 7; offset += 1
    algorithm = data[offset] & 7; offset += 1
    ams = data[offset]; offset += 1          # noqa: F841 - per channel, not per patch

    operators = []
    for i in range(4):
        chunk = data[offset:offset + 11]
        if len(chunk) < 11:
            raise InstrumentFormatError(
                f"{label}: ran out of data in operator {i + 1}")
        mult, tl, ar, dr, sl, rr, am, rs, dt, d2r, ssg = chunk
        # DefleMask packs OPM's DT2 into the high nibble of dt; OPN has no
        # DT2, so only the low nibble is meaningful here.
        operators.append(_operator(mult, dt & 0x0F, tl, rs, ar, dr, d2r, rr,
                                   sl, ssg, am=am))
        offset += 11

    return FMInstrument(algorithm, feedback, operators, name)


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------
_LOADERS = {".tfi": load_tfi, ".dmp": load_dmp, ".vgi": load_vgi}


def load_file(path: str, name: str = None) -> FMInstrument:
    extension = os.path.splitext(path)[1].lower()
    loader = _LOADERS.get(extension)
    if loader is None:
        raise InstrumentFormatError(
            f"{path}: no reader for {extension!r} "
            f"(have {', '.join(sorted(_LOADERS))})")
    with open(path, "rb") as fh:
        data = fh.read()
    return loader(data, name if name is not None else _clean_name(path))


def _clean_name(path: str) -> str:
    """File name -> a bank key that a score can actually type."""
    stem = os.path.splitext(os.path.basename(path))[0]
    cleaned = "".join(c if c.isalnum() else "_" for c in stem).strip("_").lower()
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned or "instrument"


def load_directory(directory: str, prefix: str = "", recursive: bool = True,
                   categories: bool = True):
    """Read every readable instrument under `directory`.

    Returns (bank, failures) where bank is name -> FMInstrument and
    failures is a list of (path, reason) — some libraries mix in formats
    or chip families this cannot read, and dropping those silently would
    hide a real "why is my patch missing" question.

    With `categories`, an instrument in a subdirectory is named
    `<subdir>_<file>`, which is what keeps 600 patches navigable and stops
    two "bass 1" files in different folders from colliding.
    """
    bank = {}
    failures = []
    directory = os.path.abspath(directory)

    for root, dirs, files in os.walk(directory):
        dirs.sort()
        if not recursive and root != directory:
            continue
        for filename in sorted(files):
            if not filename.lower().endswith(SUPPORTED_EXTENSIONS):
                continue
            path = os.path.join(root, filename)
            relative = os.path.relpath(root, directory)
            parts = []
            if prefix:
                parts.append(prefix)
            if categories and relative not in (".", ""):
                parts.extend(_clean_name(p) for p in relative.split(os.sep))
            parts.append(_clean_name(path))
            name = "_".join(parts)

            try:
                instrument = load_file(path, name)
            except (InstrumentFormatError, OSError, IndexError) as exc:
                failures.append((os.path.relpath(path, directory), str(exc)))
                continue
            if _is_silent(instrument):
                failures.append((os.path.relpath(path, directory),
                                 "every carrier fully attenuated (silent)"))
                continue
            bank[_unique(name, bank)] = instrument

    return bank, failures


def _unique(name: str, bank: dict) -> str:
    if name not in bank:
        return name
    n = 2
    while f"{name}_{n}" in bank:
        n += 1
    return f"{name}_{n}"


def _is_silent(instrument: FMInstrument) -> bool:
    return all(instrument.operators[i].total_level >= 127
               for i in instrument.carrier_indices())


def filter_bank(bank: dict, needles) -> dict:
    """Keep the patches whose name contains any of `needles`.

    Accepts a comma-separated string or an iterable. Order follows the
    needles, not the bank, so `--filter bass,lead,pad` lays a working set
    out in the order you named it instead of alphabetically — which is
    what you want when the next step is reading the bank as a palette.
    """
    if isinstance(needles, str):
        needles = needles.split(",")
    needles = [n.strip().lower() for n in needles if n and n.strip()]
    if not needles:
        return dict(bank)

    picked = {}
    for needle in needles:
        for name, instrument in bank.items():
            if needle in name.lower():
                picked[name] = instrument
    return picked


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
def save_bank(bank: dict, path: str, calibrate: bool = True) -> str:
    """Write a bank JSON that instruments.load_bank() reads back."""
    if calibrate:
        import vgm_import
        vgm_import.calibrate_bank(bank)
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    data = [instruments_mod.instrument_to_dict(i) for i in bank.values()]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return path


def main(argv):
    import argparse

    parser = argparse.ArgumentParser(
        prog="furnace_import",
        description="Read Furnace/DefleMask/TFM instrument files into a "
                    "chipgen bank.")
    parser.add_argument("source", help="an instrument file or a directory")
    parser.add_argument("-o", "--out", help="write a bank JSON here")
    parser.add_argument("--prefix", default="", help="prefix every name")
    parser.add_argument("--no-categories", action="store_true",
                        help="do not prefix names with their subdirectory")
    parser.add_argument("--no-calibrate", action="store_true",
                        help="skip loudness levelling against the built-in bank")
    parser.add_argument("--filter", default="",
                        help="keep only names containing any of these "
                             "comma-separated substrings — names carry their "
                             "category, so `--filter bass` picks the bass "
                             "folder out of a whole library and "
                             "`--filter slap_bass_3,rough_square` picks two "
                             "named patches out of six hundred")
    parser.add_argument("--limit", type=int, default=0,
                        help="keep only the first N (calibration renders each "
                             "one, so a 600-patch library takes a while)")
    parser.add_argument("--list", action="store_true",
                        help="print what was read and exit")
    args = parser.parse_args(argv)

    if os.path.isdir(args.source):
        bank, failures = load_directory(args.source, prefix=args.prefix,
                                        categories=not args.no_categories)
    else:
        bank = {}
        failures = []
        try:
            instrument = load_file(args.source)
            bank[instrument.name] = instrument
        except (InstrumentFormatError, OSError) as exc:
            failures.append((args.source, str(exc)))

    if args.filter:
        bank = filter_bank(bank, args.filter)
    if args.limit:
        bank = dict(list(bank.items())[:args.limit])

    print(f"прочитано {len(bank)} инструментов, "
          f"не удалось {len(failures)}")
    if args.list or not args.out:
        for name, instrument in sorted(bank.items()):
            print(f"  {name:44s} alg{instrument.algorithm} "
                  f"fb{instrument.feedback}")
    if failures:
        print(f"\nпропущено:")
        for path, reason in failures[:15]:
            print(f"  {path}: {reason}")
        if len(failures) > 15:
            print(f"  ... и ещё {len(failures) - 15}")

    if args.out and bank:
        save_bank(bank, args.out, calibrate=not args.no_calibrate)
        print(f"\nwrote {args.out}")
        print(f'  python3 python/chipgen.py score.trk --bank {args.out} -o out.wav')
    return 0 if bank else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
