"""
vgm_import.py — pull FM instruments out of any Genesis VGM.

Hand-authoring FM patches is slow and the results are uneven; meanwhile
every Genesis game ever ripped is a VGM file, and a VGM is a complete
record of the register writes that made its sound. The patches are right
there. This reads them out.

    python3 python/vgm_import.py song.vgm                  # list what is in it
    python3 python/vgm_import.py song.vgm -o bank.json     # save a bank
    python3 python/vgm_import.py song.vgm --audition out/  # render each patch

and then, in a score:

    import instruments
    instruments.load_bank("bank.json")      # names are now selectable

How it works: replay the register stream while keeping a shadow copy of
each FM channel's 30 patch registers, and snapshot a channel whenever it
is keyed on. A snapshot taken at key-on is the patch as the driver meant
it to sound, which is the only moment that is reliably true — drivers
rewrite operator registers mid-note for envelopes and effects.

Identical snapshots collapse into one entry with a use count, so a track
that plays its bass 300 times yields one bass, and ranking by use count
puts the instruments the composer actually leaned on at the top.

## On licensing

Register values are the settings a musician dialled in, not audio. That
said, lifting a game's whole instrument set into your own release is a
question about that game's rights, not about this tool. Extracting to
study, to learn how a sound was built, or to work with your own or freely
licensed VGMs is uncontroversial; shipping someone's soundtrack patches as
your own is a decision to make deliberately.
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import instruments as instruments_mod
import vgm as vgm_mod
import vgm_player
from opn2 import _OP_OFFSETS, FMInstrument, Operator

#: Registers 0x30..0x9F hold the four operators, 0xB0 the algorithm and
#: feedback. 0xB4 (pan/LFO depth) is performance, not timbre, so it is not
#: part of a patch's identity.
_OPERATOR_GROUPS = (0x30, 0x40, 0x50, 0x60, 0x70, 0x80, 0x90)


class ExtractedPatch:
    """One distinct patch, plus where it came from."""

    __slots__ = ("instrument", "uses", "channels", "first_seconds")

    def __init__(self, instrument, first_seconds):
        self.instrument = instrument
        self.uses = 0
        self.channels = set()
        self.first_seconds = first_seconds

    def __repr__(self):
        return (f"<ExtractedPatch {self.instrument.name!r} alg="
                f"{self.instrument.algorithm} uses={self.uses}>")


class _ChannelState:
    """Shadow copy of one FM channel's patch registers."""

    __slots__ = ("regs", "algorithm", "feedback")

    def __init__(self):
        self.regs = {}                 # (group, op_index) -> byte
        self.algorithm = 0
        self.feedback = 0

    def key(self):
        """Hashable identity: every register that defines the timbre."""
        return (self.algorithm, self.feedback,
                tuple(self.regs.get((group, op), 0)
                      for op in range(4) for group in _OPERATOR_GROUPS))

    def to_instrument(self, name: str) -> FMInstrument:
        operators = []
        for op in range(4):
            byte = lambda group: self.regs.get((group, op), 0)
            operators.append(Operator(
                detune=(byte(0x30) >> 4) & 0x7,
                multiple=byte(0x30) & 0xF,
                total_level=byte(0x40) & 0x7F,
                rate_scaling=(byte(0x50) >> 6) & 0x3,
                attack_rate=byte(0x50) & 0x1F,
                am_enable=(byte(0x60) >> 7) & 0x1,
                decay_rate=byte(0x60) & 0x1F,
                sustain_rate=byte(0x70) & 0x1F,
                sustain_level=(byte(0x80) >> 4) & 0xF,
                release_rate=byte(0x80) & 0xF,
                ssg_eg=byte(0x90) & 0xF,
            ))
        # Registers ascend as op1, op3, op2, op4 and FMInstrument stores its
        # operators in that same order, so this needs no reshuffling — see
        # the operator-order note in instruments.py.
        return FMInstrument(self.algorithm, self.feedback, operators, name)


def _is_silent(instrument: FMInstrument) -> bool:
    """A patch whose every carrier is fully attenuated makes no sound.

    Drivers park unused channels like this, and a bank full of silence is
    worse than useless — it looks like choice and delivers nothing.
    """
    return all(instrument.operators[i].total_level >= 127
               for i in instrument.carrier_indices())


def extract(path_or_bytes, prefix: str = "", min_uses: int = 1,
            max_seconds: float = 3600.0):
    """Return ExtractedPatch objects, most-used first."""
    raw = vgm_player.load(path_or_bytes)
    header = vgm_mod.read_header(raw)

    channels = [_ChannelState() for _ in range(6)]
    found = {}
    elapsed = 0.0
    max_samples = int(max_seconds * vgm_mod.DEFAULT_SAMPLE_RATE)

    if not prefix:
        prefix = _prefix_from_source(path_or_bytes)

    for command in vgm_player.iter_commands(raw, header, max_samples):
        kind = command[0]
        if kind == "wait":
            elapsed += command[1] / float(vgm_mod.DEFAULT_SAMPLE_RATE)
            continue
        if kind != "ym":
            continue

        _, port, addr, data = command
        bank = 1 if port >= 2 else 0

        if bank == 0 and addr == 0x28:
            index = data & 7
            if index in (3, 7) or not (data & 0xF0):
                continue                      # key-off, or a channel that has none
            _snapshot(channels[index if index < 3 else index - 1],
                      found, prefix, elapsed, index if index < 3 else index - 1)
            continue

        if 0x30 <= addr < 0xA0:
            index = addr & 3
            if index == 3:
                continue
            state = channels[bank * 3 + index]
            state.regs[(addr & 0xF0, (addr >> 2) & 3)] = data
        elif 0xB0 <= addr <= 0xB2:
            index = addr & 3
            if index == 3:
                continue
            state = channels[bank * 3 + index]
            state.algorithm = data & 7
            state.feedback = (data >> 3) & 7

    patches = [p for p in found.values() if p.uses >= min_uses]
    patches.sort(key=lambda p: (-p.uses, p.first_seconds))
    for position, patch in enumerate(patches, start=1):
        patch.instrument.name = f"{prefix}_{position:02d}"
    return patches


def _snapshot(state, found, prefix, elapsed, channel):
    key = state.key()
    patch = found.get(key)
    if patch is None:
        instrument = state.to_instrument(f"{prefix}_{len(found) + 1:02d}")
        if _is_silent(instrument):
            return
        patch = ExtractedPatch(instrument, elapsed)
        found[key] = patch
    patch.uses += 1
    patch.channels.add(channel)


def _prefix_from_source(source) -> str:
    if isinstance(source, (bytes, bytearray)):
        return "vgm"
    stem = os.path.splitext(os.path.basename(str(source)))[0]
    cleaned = "".join(c if c.isalnum() else "_" for c in stem).strip("_").lower()
    return cleaned or "vgm"


def to_bank(patches, calibrate: bool = True) -> dict:
    """name -> FMInstrument, optionally levelled against the built-in bank."""
    bank = {p.instrument.name: p.instrument for p in patches}
    if calibrate:
        calibrate_bank(bank)
    return bank


def calibrate_bank(bank: dict) -> dict:
    """Measure imported patches and set their trims to match the main bank.

    Imported patches arrive at whatever level their source game mixed them
    at, which is not this bank's level, so dropping them in unlevelled
    reintroduces exactly the problem calibration was meant to remove.
    """
    import calibrate_bank as calibrator
    from sequencer import Sequencer

    seq = Sequencer()
    reference = calibrator.measure("organ", seq)     # a mid-bank sustained voice
    if reference <= 0:
        return bank

    original = dict(instruments_mod.BANK)
    try:
        for name, instrument in bank.items():
            instrument.trim = 0
            instruments_mod.BANK[name] = instrument
            level = calibrator.measure(name, seq)
            if level <= 0:
                continue
            import math
            steps = int(round(20.0 * math.log10(level / reference)
                              / calibrator.TL_STEP_DB))
            instrument.trim = max(-instrument.headroom(), min(127, steps))
    finally:
        instruments_mod.BANK.clear()
        instruments_mod.BANK.update(original)
    return bank


def save_bank(patches, path: str, calibrate: bool = True) -> str:
    bank = to_bank(patches, calibrate=calibrate)
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    data = [instruments_mod.instrument_to_dict(i) for i in bank.values()]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return path


def audition(patches, directory: str, note: str = "A", octave: int = 3) -> list:
    """Render one WAV per patch so you can hear what you extracted."""
    import wavio
    from events import End, FMInstrumentSelect, FMNoteOff, FMNoteOn, Wait
    from sequencer import Sequencer

    os.makedirs(directory, exist_ok=True)
    seq = Sequencer()
    written = []
    original = dict(instruments_mod.BANK)
    try:
        for patch in patches:
            name = patch.instrument.name
            instruments_mod.BANK[name] = patch.instrument
            events = [FMInstrumentSelect(channel=0, instrument=name),
                      FMNoteOn(channel=0, note=note, octave=octave),
                      Wait(ticks=192), FMNoteOff(channel=0), Wait(ticks=96), End()]
            path = os.path.join(directory, f"{name}.wav")
            wavio.write(path, seq.render(events), seq.target_rate)
            written.append(path)
    finally:
        instruments_mod.BANK.clear()
        instruments_mod.BANK.update(original)
    return written


def describe(patches) -> str:
    lines = [f"{'патч':18s} {'alg':>3s} {'fb':>3s} {'нот':>6s} {'каналы':>8s} "
             f"{'первая':>8s}"]
    for patch in patches:
        instrument = patch.instrument
        lines.append(
            f"{instrument.name:18s} {instrument.algorithm:3d} "
            f"{instrument.feedback:3d} {patch.uses:6d} "
            f"{','.join(str(c) for c in sorted(patch.channels)):>8s} "
            f"{patch.first_seconds:7.1f}s")
    return "\n".join(lines)


def main(argv):
    import argparse

    parser = argparse.ArgumentParser(
        prog="vgm_import",
        description="Extract FM instruments from a Genesis VGM.")
    parser.add_argument("source", help="a .vgm or .vgz file")
    parser.add_argument("-o", "--out", help="write a bank JSON here")
    parser.add_argument("--prefix", default="", help="name prefix (default: filename)")
    parser.add_argument("--min-uses", type=int, default=1,
                        help="drop patches keyed fewer than this many times")
    parser.add_argument("--top", type=int, default=0,
                        help="keep only the N most-used patches")
    parser.add_argument("--audition", metavar="DIR",
                        help="render one WAV per patch into DIR")
    parser.add_argument("--no-calibrate", action="store_true",
                        help="skip loudness levelling against the built-in bank")
    args = parser.parse_args(argv)

    patches = extract(args.source, prefix=args.prefix, min_uses=args.min_uses)
    if args.top:
        patches = patches[:args.top]

    if not patches:
        print("no FM instruments found — is this a PSG-only or non-Genesis VGM?")
        return 1

    print(f"{len(patches)} инструментов из {os.path.basename(str(args.source))}\n")
    print(describe(patches))

    if args.out:
        save_bank(patches, args.out, calibrate=not args.no_calibrate)
        print(f"\nwrote {args.out}")
        print(f'  instruments.load_bank("{args.out}")  — и имена доступны в партитуре')
    if args.audition:
        written = audition(patches, args.audition)
        print(f"\nrendered {len(written)} WAV в {args.audition}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
