"""
opl_import.py — read OPL2 patches from the formats people already have.

Two sources, for the same reason furnace_import.py exists: nobody should
have to hand-write operator numbers when thirty years of AdLib patches are
sitting on disk.

  * `.sbi` — Sound Blaster Instrument. Eleven bytes of OPL2 registers with
    a name in front. The format DOS games shipped their instruments in.

  * `.fui` — Furnace's own container. Unlike the OPN library, where the
    same patches were also available as `.dmp` and `.tfi`, Furnace's OPL
    library is `.fui` and nothing else, so this reads it: a header, then
    length-prefixed feature blocks, of which "NA" (name) and "FM" are the
    two that matter here.

Four-operator OPL3 patches are read as their first two operators, because
the YM3812 has two. That is a real change to the sound and load_directory
reports every patch it had to do it to, rather than quietly halving them.
"""

import json
import os
import struct
import sys

from opl2 import OPLInstrument, OPLOperator

SUPPORTED_EXTENSIONS = (".sbi", ".fui")

#: Furnace instrument type for OPL. Its `.fui` header carries the chip
#: family, so an OPN patch in an OPL folder is refused rather than read as
#: nonsense.
FUI_TYPE_OPL = 14
FUI_TYPE_OPLL = 13


class InstrumentFormatError(ValueError):
    pass


# --------------------------------------------------------------------------
# SBI
# --------------------------------------------------------------------------
def load_sbi(path: str) -> OPLInstrument:
    """Sound Blaster Instrument: 'SBI\\x1a', a 32-byte name, 11 registers."""
    with open(path, "rb") as handle:
        raw = handle.read(52)
    if len(raw) < 47 or raw[:4] != b"SBI\x1a":
        raise InstrumentFormatError("not an SBI file")

    name = raw[4:36].split(b"\0")[0].decode("latin-1", "replace").strip()
    reg = raw[36:47]
    return OPLInstrument(
        modulator=_operator_from_registers(reg[0], reg[2], reg[4], reg[6], reg[8]),
        carrier=_operator_from_registers(reg[1], reg[3], reg[5], reg[7], reg[9]),
        feedback=(reg[10] >> 1) & 7,
        connection=reg[10] & 1,
        name=_clean_name(name or os.path.basename(path)))


def _operator_from_registers(am_vib, ksl_tl, ar_dr, sl_rr, waveform):
    """The five OPL2 registers that describe one operator.

    0x20 AM/VIB/EG-type/KSR/MULT, 0x40 KSL/TL, 0x60 AR/DR, 0x80 SL/RR,
    0xE0 waveform — the same five in every OPL patch format there is.
    """
    return OPLOperator(
        tremolo=(am_vib >> 7) & 1,
        vibrato=(am_vib >> 6) & 1,
        sustaining=bool((am_vib >> 5) & 1),
        key_scale_rate=(am_vib >> 4) & 1,
        multiple=am_vib & 15,
        key_scale_level=(ksl_tl >> 6) & 3,
        total_level=ksl_tl & 63,
        attack=(ar_dr >> 4) & 15,
        decay=ar_dr & 15,
        sustain_level=(sl_rr >> 4) & 15,
        release=sl_rr & 15,
        waveform=waveform & 3)


# --------------------------------------------------------------------------
# Furnace .fui
# --------------------------------------------------------------------------
#: The magic on Furnace's older instrument container, which wraps the same
#: data the module format stored inline. Its OPL library is mostly this.
FUI_OLD_MAGIC = b"-Furnace instr.-"


def load_fui(path: str):
    """Read a Furnace instrument. Returns (instrument, reduced_from_4op).

    Two generations share the extension: the current "FINS" container of
    length-prefixed feature blocks, and an older one that wraps a single
    fixed-layout "INST" record. Furnace's own OPL library is mostly the
    older kind, so reading only the new one would leave four fifths of it
    on the floor.
    """
    with open(path, "rb") as handle:
        raw = handle.read()
    if raw[:16] == FUI_OLD_MAGIC:
        return _load_fui_old(raw)
    if len(raw) < 8 or raw[:4] != b"FINS":
        raise InstrumentFormatError("not a Furnace .fui file")

    _version, kind = struct.unpack_from("<HH", raw, 4)
    if kind not in (FUI_TYPE_OPL, FUI_TYPE_OPLL):
        raise InstrumentFormatError(
            f"instrument type {kind} is not OPL — this reads OPL only")

    name = ""
    fm = None
    position = 8
    while position + 4 <= len(raw):
        code = raw[position:position + 2]
        if code in (b"EN", b"\0\0"):
            break
        (length,) = struct.unpack_from("<H", raw, position + 2)
        payload = raw[position + 4:position + 4 + length]
        if len(payload) < length:
            raise InstrumentFormatError("truncated feature block")
        if code == b"NA":
            name = payload.split(b"\0")[0].decode("utf-8", "replace")
        elif code == b"FM":
            fm = payload
        position += 4 + length

    if fm is None:
        raise InstrumentFormatError("no FM block")
    instrument, reduced = _instrument_from_fui_fm(fm)
    instrument.name = _clean_name(
        name or os.path.splitext(os.path.basename(path))[0])
    return instrument, reduced


#: One operator in the old layout: 22 named bytes then 10 reserved.
_OLD_OPERATOR_SIZE = 32
#: Where each field sits inside those 22.
_OLD_FIELDS = {"am": 0, "ar": 1, "dr": 2, "mult": 3, "rr": 4, "sl": 5,
               "tl": 6, "ksl": 15, "sus": 16, "vib": 17, "ws": 18, "ksr": 19}


def _load_fui_old(raw: bytes):
    (pointer,) = struct.unpack_from("<I", raw, 20)
    if raw[pointer:pointer + 4] != b"INST":
        raise InstrumentFormatError("no INST block")
    position = pointer + 4 + 4                  # skip the magic and a length
    (_version,) = struct.unpack_from("<H", raw, position)
    kind = raw[position + 2]
    if kind not in (FUI_TYPE_OPL, FUI_TYPE_OPLL):
        raise InstrumentFormatError(
            f"instrument type {kind} is not OPL — this reads OPL only")
    position += 4

    end = raw.index(b"\0", position)
    name = raw[position:end].decode("utf-8", "replace")
    position = end + 1

    _alg, feedback = raw[position], raw[position + 1]
    op_count = raw[position + 4]
    position += 8

    operators = []
    for index in range(2):
        chunk = raw[position + _OLD_OPERATOR_SIZE * index:
                    position + _OLD_OPERATOR_SIZE * (index + 1)]
        if len(chunk) < 22:
            raise InstrumentFormatError("truncated operator data")
        field = lambda key: chunk[_OLD_FIELDS[key]]
        operators.append(OPLOperator(
            tremolo=field("am") & 1,
            vibrato=field("vib") & 1,
            sustaining=bool(field("sus") & 1),
            key_scale_rate=field("ksr") & 1,
            multiple=field("mult") & 15,
            key_scale_level=field("ksl") & 3,
            total_level=field("tl") & 63,
            attack=field("ar") & 15,
            decay=field("dr") & 15,
            sustain_level=field("sl") & 15,
            release=field("rr") & 15,
            waveform=field("ws") & 3))

    instrument = OPLInstrument(modulator=operators[0], carrier=operators[1],
                               feedback=feedback & 7, connection=_alg & 1,
                               name=_clean_name(name))
    return instrument, op_count == 4


def _instrument_from_fui_fm(payload: bytes):
    if len(payload) < 5:
        raise InstrumentFormatError("FM block too short")
    op_count = payload[0] & 15
    if op_count not in (2, 4):
        raise InstrumentFormatError(f"{op_count} operators is not 2 or 4")

    # The base header grew a `block` byte in later versions. Deriving its
    # size from the length rather than the version number means one reader
    # handles both, and fails loudly on anything else.
    base = len(payload) - 8 * op_count
    if base not in (4, 5):
        raise InstrumentFormatError(
            f"FM block is {len(payload)} bytes, which is neither "
            f"{4 + 8 * op_count} nor {5 + 8 * op_count}")

    feedback = payload[1] & 7
    connection = (payload[1] >> 4) & 1        # OPL uses one algorithm bit

    operators = []
    for index in range(op_count):
        chunk = payload[base + 8 * index: base + 8 * index + 8]
        operators.append(OPLOperator(
            key_scale_rate=(chunk[0] >> 7) & 1,
            multiple=chunk[0] & 15,
            sustaining=bool((chunk[1] >> 7) & 1),   # Furnace `sus` = EG type
            total_level=chunk[1] & 63,
            vibrato=(chunk[2] >> 5) & 1,
            attack=chunk[2] & 15,
            tremolo=(chunk[3] >> 7) & 1,
            key_scale_level=(chunk[3] >> 5) & 3,
            decay=chunk[3] & 15,
            sustain_level=(chunk[5] >> 4) & 15,
            release=chunk[5] & 15,
            waveform=chunk[7] & 3))

    return (OPLInstrument(modulator=operators[0], carrier=operators[1],
                          feedback=feedback, connection=connection),
            op_count == 4)


# --------------------------------------------------------------------------
# Directories and banks
# --------------------------------------------------------------------------
def _clean_name(name: str) -> str:
    out = []
    for char in name.strip().lower():
        out.append(char if char.isalnum() else "_")
    cleaned = "_".join(part for part in "".join(out).split("_") if part)
    return cleaned or "patch"


def load_file(path: str):
    """Returns (instrument, reduced_from_4op)."""
    lower = path.lower()
    if lower.endswith(".sbi"):
        return load_sbi(path), False
    if lower.endswith(".fui"):
        return load_fui(path)
    raise InstrumentFormatError(f"unknown extension: {os.path.basename(path)}")


def load_directory(directory: str, prefix: str = "", recursive: bool = True):
    """Read every readable OPL patch under `directory`.

    Returns (bank, failures, reduced) — `reduced` names the four-operator
    patches that were folded down to two, because that is a change to the
    sound the caller should know about rather than discover by ear.
    """
    bank, failures, reduced = {}, [], []
    directory = os.path.abspath(directory)

    for root, dirs, files in os.walk(directory):
        dirs.sort()
        if not recursive and root != directory:
            continue
        for filename in sorted(files):
            if not filename.lower().endswith(SUPPORTED_EXTENSIONS):
                continue
            path = os.path.join(root, filename)
            try:
                instrument, was_4op = load_file(path)
            except (InstrumentFormatError, OSError, IndexError, struct.error) as exc:
                failures.append((os.path.relpath(path, directory), str(exc)))
                continue
            if _is_silent(instrument):
                failures.append((os.path.relpath(path, directory),
                                 "carrier fully attenuated (silent)"))
                continue
            name = _unique(bank, f"{prefix}_{instrument.name}" if prefix
                           else instrument.name)
            instrument.name = name
            bank[name] = instrument
            if was_4op:
                reduced.append(name)
    return bank, failures, reduced


def _unique(bank, name):
    if name not in bank:
        return name
    n = 2
    while f"{name}_{n}" in bank:
        n += 1
    return f"{name}_{n}"


def _is_silent(instrument) -> bool:
    if instrument.connection:
        return (instrument.modulator.total_level >= 63
                and instrument.carrier.total_level >= 63)
    return instrument.carrier.total_level >= 63


def instrument_to_dict(instrument) -> dict:
    def operator(op):
        return {field: getattr(op, field) for field in OPLOperator.__slots__}
    return {"name": instrument.name, "feedback": instrument.feedback,
            "connection": instrument.connection, "trim": instrument.trim,
            "modulator": operator(instrument.modulator),
            "carrier": operator(instrument.carrier)}


def instrument_from_dict(data: dict) -> OPLInstrument:
    def operator(values):
        op = OPLOperator()
        for field, value in values.items():
            if field in OPLOperator.__slots__:
                setattr(op, field, value)
        return op
    return OPLInstrument(modulator=operator(data["modulator"]),
                         carrier=operator(data["carrier"]),
                         feedback=data.get("feedback", 0),
                         connection=data.get("connection", 0),
                         name=data.get("name", ""),
                         trim=data.get("trim", 0))


def save_bank(bank: dict, path: str, calibrate: bool = True) -> str:
    if calibrate:
        calibrate_bank(bank)
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump([instrument_to_dict(i) for i in bank.values()], handle,
                  indent=2)
    return path


def load_bank(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return {i["name"]: instrument_from_dict(i) for i in json.load(handle)}


# --------------------------------------------------------------------------
# Loudness calibration
# --------------------------------------------------------------------------
#: The window a patch's loudness is measured over. Long enough that a pad's
#: sustain counts, short enough that a drum is judged on its hit rather
#: than on the silence after it.
CALIBRATION_WINDOW = 0.30
CALIBRATION_NOTE = ("C", 5)


def measure_loudness(instrument, note=CALIBRATION_NOTE,
                     window: float = CALIBRATION_WINDOW) -> float:
    """Loudest sliding-window RMS while the note is held.

    Peak would be decided by a single sample and mean would punish a short
    drum for being short; the loudest window is what a listener actually
    balances against.
    """
    import opl2 as chip_module

    chip = chip_module.YM3812()
    chip.set_instrument(0, instrument)
    chip.note_on(0, note[0], note[1])
    rate = chip.native_rate
    samples = [float(v) for v in chip.render(int(rate * 0.6))]
    chip.close()

    span = max(1, int(rate * window))
    if len(samples) <= span:
        return _rms(samples)
    # Running sum of squares, so this stays linear rather than quadratic.
    total = sum(v * v for v in samples[:span])
    best = total
    for index in range(span, len(samples)):
        total += samples[index] ** 2 - samples[index - span] ** 2
        if total > best:
            best = total
    return (best / span) ** 0.5


def _rms(values):
    if not values:
        return 0.0
    return (sum(v * v for v in values) / len(values)) ** 0.5


def calibrate_bank(bank: dict, target: float = None) -> dict:
    """Set each patch's `trim` so the bank sits at one level.

    Furnace's OPL library measured 45.6 dB from its quietest patch to its
    loudest, which means swapping one lead for another silently remixes
    the whole arrangement. `trim` is in Total Level steps of 0.75 dB and is
    applied to the carrier when the operators are in FM, to both when they
    are additive — raising a modulator would change the timbre, not the
    volume.

    Returns {name: measured_rms} so a caller can show its work.
    """
    measured = {}
    for name, instrument in bank.items():
        instrument.trim = 0
        measured[name] = measure_loudness(instrument)

    live = sorted(v for v in measured.values() if v > 1e-5)
    if not live:
        return measured
    if target is None:
        target = live[len(live) // 2]          # the bank's own median

    import math
    for name, instrument in bank.items():
        level = measured[name]
        if level <= 1e-5:
            continue
        steps = round(20.0 * math.log10(level / target) / 0.75)
        headroom = _headroom(instrument)
        # Clamp rather than let a quiet patch demand a negative TL that
        # the register cannot hold.
        instrument.trim = max(-headroom, min(63, int(steps)))
    return measured


def _headroom(instrument) -> int:
    """How many TL steps this patch can be made louder before hitting 0."""
    if instrument.connection:
        return min(instrument.modulator.total_level,
                   instrument.carrier.total_level)
    return instrument.carrier.total_level


def main(argv):
    import argparse

    parser = argparse.ArgumentParser(
        prog="opl_import",
        description="Read OPL2 patches (.sbi, Furnace .fui) into a bank.")
    parser.add_argument("source", help="an instrument file or a directory")
    parser.add_argument("-o", "--out", help="write a bank JSON here")
    parser.add_argument("--prefix", default="", help="prefix every name")
    parser.add_argument("--filter", default="",
                        help="keep only names containing any of these "
                             "comma-separated substrings")
    parser.add_argument("--no-calibrate", action="store_true",
                        help="skip loudness levelling")
    parser.add_argument("--list", action="store_true",
                        help="print what was read and exit")
    args = parser.parse_args(argv)

    if os.path.isdir(args.source):
        bank, failures, reduced = load_directory(args.source, prefix=args.prefix)
    else:
        bank, failures, reduced = {}, [], []
        try:
            instrument, was_4op = load_file(args.source)
            bank[instrument.name] = instrument
            if was_4op:
                reduced.append(instrument.name)
        except (InstrumentFormatError, OSError) as exc:
            failures.append((args.source, str(exc)))

    if args.filter:
        import furnace_import
        bank = furnace_import.filter_bank(bank, args.filter)

    print(f"прочитано {len(bank)} инструментов, не удалось {len(failures)}")
    if reduced:
        print(f"свёрнуто с 4 операторов до 2: {len(reduced)} "
              f"(YM3812 имеет два) — звук изменится")
    if args.list or not args.out:
        for name, instrument in sorted(bank.items()):
            print(f"  {name:40s} fb{instrument.feedback} "
                  f"{'add' if instrument.connection else ' fm'} "
                  f"ws{instrument.modulator.waveform}{instrument.carrier.waveform}")
    if failures:
        print("\nпропущено:")
        for path, reason in failures[:15]:
            print(f"  {path}: {reason}")
        if len(failures) > 15:
            print(f"  ... и ещё {len(failures) - 15}")

    if args.out and bank:
        save_bank(bank, args.out, calibrate=not args.no_calibrate)
        print(f"\nwrote {args.out}")
        print(f"  python3 python/chipgen.py score.trk --opl-bank {args.out} "
              f"-o out.wav")
    return 0 if bank else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
