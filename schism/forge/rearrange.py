"""rearrange.py — a second pass over a module: not a report, a rewrite.

`arrangement.classify` labels the parts of a song (lead, bass, pad, arp,
drums). This pass acts on the labels. It takes a module, from a `.it` or a
`.sch`, and writes another that plays the same notes with other sounds, on
other channels, in another register, and says what it did as a diff of roles.

    remap      each part gets the voice its job has on the target rung (see
               `ladder`): a lead is a lead on the Mega Drive's FM patch or on
               the Schism overdriven sample, and the instrument number in its
               cells changes. The notes stay.
    register   a part that is the bass but sits above C-3 is dropped by
               octaves into the bass's range, and a sub follows it on a
               channel of its own. (A bass above the range of its instrument
               is a silent note.)
    split      a channel that is one arpeggio doing a chord is, on the
               Mega Drive, an FM pad holding the chord and the arp over it;
               on Schism the pad is spread left, centre and right on three
               channels, the arp is a struck voice, and an echo follows it.
    echo       a channel copied to the next, shifted by some rows and turned
               down: written by the pass, not by the composer.
    fill       the last four rows of a drum pattern that has no fill get a
               roll of toms or snares, using only the keys the kit has.

Pitch is the caller's. The pass changes timbre, the split of channels and
the register of a bass; it does not write a melody. The notes of the lead,
the counter-melody, the harmony and the arpeggio come out exactly as they
went in.
"""

import copy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .. import model as M, recipes
from . import arrangement, ladder, patch as patch_mod
from ..export import is_kit as export_is_kit
from .family import get_family

TARGETS = ladder.RUNGS
#: how many channels a rung's console has: the NES's four (two pulses, a
#: triangle and noise), the Mega Drive's seven (six FM voices and a DAC, as
#: the export preset has it), and IT's sixty-four
CHANNELS = {"nes": 4, "sega": 7, "schism": 64}
#: the bass sits at or below C-3 (IT note 37)
BASS_CEILING = 37
#: the job a part has on the ladder
VOICE = {"lead": "lead", "counter": "lead", "harmony": "pad", "pad": "pad",
         "bass": "bass", "arp": "bell"}
#: volumes the added parts are played at
PAD_VOLUME = 38
SUB_VOLUME = 56
#: pans of the three channels of a spread pad, left to right
PAD_PANS = (8, 32, 56)
MAX_CHANNELS = 64
#: effects that belong to the song, not to a part: not copied by an echo
SONG_EFFECTS = "ABCTVWS"


class RearrangeError(ValueError):
    """A module that cannot be rearranged that way, with the reason."""


@dataclass
class Change:
    kind: str              # remap register sub split echo fill note
    text: str
    channel: Optional[int] = None        # 0-based

    def to_dict(self) -> dict:
        return {"kind": self.kind, "text": self.text,
                "channel": None if self.channel is None else self.channel + 1}


@dataclass
class Result:
    module: M.Module
    to: str
    changes: List[Change] = field(default_factory=list)
    #: voice (ch01...) -> the role the classifier gave it
    roles: Dict[str, str] = field(default_factory=dict)
    channels_before: int = 0

    def add(self, kind: str, text: str, channel: Optional[int] = None):
        self.changes.append(Change(kind, text, channel))

    def text(self) -> str:
        after = self.module.channels_used()
        lines = [f"rearranged for {self.to}: {self.channels_before} channels "
                 f"-> {after}"]
        for voice, role in sorted(self.roles.items()):
            lines.append(f"  {voice} {role or '-'}")
        for c in self.changes:
            where = f"ch{c.channel + 1:02d} " if c.channel is not None else ""
            lines.append(f"  [{c.kind}] {where}{c.text}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"to": self.to, "channels": [self.channels_before,
                                            self.module.channels_used()],
                "roles": dict(self.roles),
                "changes": [c.to_dict() for c in self.changes]}


# -- reading and writing the cells ------------------------------------------------------------------
def _cells(module: M.Module, channel: int):
    """(pattern, row, cell) of every cell of a channel that has anything."""
    for pattern in module.patterns:
        for row, line in enumerate(pattern.cells):
            if channel < len(line) and not line[channel].empty():
                yield pattern, row, line[channel]


def _occupied(module: M.Module) -> set:
    return {c for p in module.patterns for line in p.cells
            for c, cell in enumerate(line) if not cell.empty()}


def _free_channel(module: M.Module, taken: set) -> int:
    busy = _occupied(module) | taken
    for channel in range(MAX_CHANNELS):
        if channel not in busy:
            taken.add(channel)
            return channel
    raise RearrangeError(f"all {MAX_CHANNELS} channels are in use")


def _is_note(cell: M.Cell) -> bool:
    return 1 <= cell.note <= 120


def _is_stop(cell: M.Cell) -> bool:
    return cell.note in (M.NOTE_OFF, M.NOTE_CUT, M.NOTE_FADE)


def _scaled_volume(cell: M.Cell, db: float) -> int:
    base = 64 if cell.volume < 0 or cell.volume > 64 else cell.volume
    return max(1, min(64, int(round(base * 10.0 ** (db / 20.0)))))


# -- percussion that is not in a kit -------------------------------------------------------------------
#: a one-shot this long is a sound, not a hit
PERCUSSION_SECONDS = 1.5


def _ends_itself(module: M.Module, ins: M.Instrument) -> bool:
    """Does the instrument fall silent by itself, key down or not: a volume
    envelope with no sustain and no loop that ends at nothing, in a second
    and a half or less? That is what a looped noise does to be a snare."""
    env = ins.volume_envelope
    return bool(env and env.enabled and env.nodes and not env.sustain
                and not env.loop and env.nodes[-1][1] == 0
                and env.nodes[-1][0] * 2.5 / module.tempo
                <= PERCUSSION_SECONDS)


def _percussive(module: M.Module, arr, part) -> bool:
    """Is this channel a drum that is not in a kit: a sound that ends by
    itself (a short one-shot, or a loop under an envelope that dies), played
    on one or two pitches over and over? `arrangement` classifies by the
    notes, and a hat on a channel of its own plays a few notes the way a
    counter-melody might."""
    notes = arr.song.notes.get(part.channel, [])
    if not notes or len({n.pitch for n in notes}) > 2:
        return False
    if not any(n.sample for n in notes):
        return False
    for number in part.instruments:
        if not number or number > len(module.instruments):
            return False
        ins = module.instruments[number - 1]
        for index in {s for _, s in ins.note_map if s}:
            sample = module.samples[index - 1]
            if sample.loop or sample.sustain_loop:
                if not _ends_itself(module, ins):
                    return False
            elif sample.frames / max(1, sample.c5speed) > PERCUSSION_SECONDS:
                return False
    return True


def _hit(module: M.Module, number: int, seconds: float = 0.5):
    """(floats, rate) of the first half second of an instrument's first
    sample as it plays: the sample under the volume envelope."""
    ins = module.instruments[number - 1]
    index = next(s for _, s in ins.note_map if s)
    sample = module.samples[index - 1]
    rate = float(sample.c5speed)
    n = min(sample.frames, int(seconds * rate))
    data = sample.data
    x = [data[i % max(1, len(data))] / (128.0 if sample.bits == 8 else 32768.0)
         for i in range(n)]
    env = ins.volume_envelope
    if env and env.enabled and env.nodes:
        tick = 2.5 / module.tempo
        nodes = [(t * tick, v / 64.0) for t, v in env.nodes]
        k = 0
        for i in range(n):
            t = i / rate
            while k + 1 < len(nodes) and nodes[k + 1][0] <= t:
                k += 1
            if k + 1 >= len(nodes):
                level = nodes[-1][1] if t >= nodes[-1][0] else nodes[k][1]
            else:
                (t0, v0), (t1, v1) = nodes[k], nodes[k + 1]
                level = v0 + (v1 - v0) * (t - t0) / max(1e-9, t1 - t0)
            x[i] *= level
    return x, rate


def drum_kind(module: M.Module, number: int) -> str:
    """kick, snare or hat, by how a percussion instrument sounds: low is a
    kick, one that is 20 dB down in under 150 ms is a hat, the rest a
    snare."""
    from . import dsp
    x, rate = _hit(module, number)
    if len(x) < 64:
        return "snare"
    size = dsp.next_pow2(min(len(x), 8192))
    mags, width = dsp.spectrum(x[:size] + [0.0] * (size - len(x[:size])), 0,
                               size, rate)
    total = sum(m * m for m in mags) or 1.0
    low = sum(m * m for m in mags[:int(250.0 / width) + 1]) / total
    if low > 0.5:
        return "kick"
    peak = max(abs(v) for v in x) or 1.0
    window = max(1, int(0.005 * rate))
    short = len(x)
    for i in range(0, len(x) - window, window):
        if max(abs(v) for v in x[i:i + window]) < 0.1 * peak:
            short = i
            break
    return "hat" if short / rate < 0.15 else "snare"


_DRUMS: Dict[tuple, object] = {}


def _install_drum(module: M.Module, kind: str, rung: str) -> int:
    """The drum the ladder has for `kind` on `rung` as an instrument that
    plays it as recorded on every key, so the notes a drum channel plays
    stay what they were. -> its number."""
    key = (kind, rung)
    if key not in _DRUMS:
        name = f"{kind} {rung}"[:25]
        sega = ladder.lift_to_sega(ladder.nes_voice(name, kind))
        if rung == "sega":
            genome = ladder.sega_genome(sega, name)
            compiled = get_family(genome.family).compile(genome)
        else:
            genome, compiled, _ = ladder.lift_to_schism(sega, name, _bodies)
        _DRUMS[key] = ladder.make_entry(name, genome, compiled, kind,
                                        "").compiled
    compiled = copy.deepcopy(_DRUMS[key])
    built = patch_mod.make_sample(compiled.patch.family,
                                  compiled.patch.params, 61, 61)
    number = len(module.instruments) + 1
    ins = M.Instrument(name=f"{kind} {rung}"[:25])
    ins.nna = 0
    module.samples.append(M.Sample(
        name=f"{kind} {rung}"[:25], data=built.data, right=built.right,
        c5speed=44100, volume=max(1, min(64, int(round(
            compiled.patch.inst.get("gain", 128) / 2.0))))))
    ins.note_map = [(60, len(module.samples))] * 120
    while len(module.instruments) < number - 1:
        module.instruments.append(M.Instrument(
            name="", note_map=[(n, 0) for n in range(120)]))
    module.instruments.append(ins)
    dac = getattr(module, "dac", None)
    if dac is None:
        dac = module.dac = {}
    found = compiled.patch.params.get("dac")
    for part in compiled.patch.params.get("parts", []):
        found = found or part["patch"].get("params", {}).get("dac")
    if found:
        dac[number] = dict(found)
    return number


# -- the voices ---------------------------------------------------------------------------------------
def _bodies(kind: str):
    from . import fam_layer, notation_hook
    entry = notation_hook.starter().get(kind)
    family = get_family(entry.family)
    return fam_layer.Source(kind + "_body", family.from_dict(
        family.to_dict(entry.compiled)), entry.genome, "body")


_VOICES: Dict[tuple, object] = {}


def _voice(role: str, rung: str, low: int, high: int):
    """-> (compiled, note): the voice a job has on a rung, for keys
    `low`..`high`, calibrated if a player is there to do it. Made once."""
    key = (role, rung, low, high)
    if key not in _VOICES:
        name = f"{role} {rung}"[:25]
        sega = ladder.lift_to_sega(ladder.nes_voice(name, role))
        if rung == "sega":
            genome = ladder.sega_genome(sega, name)
            compiled = get_family(genome.family).compile(genome)
            said = f"{name}: FM, algorithm {sega.fm['algorithm']}"
        else:
            genome, compiled, _ = ladder.lift_to_schism(sega, name, _bodies)
            said = (f"{name}: FM from the Mega Drive voice, "
                    + ("stereo, " if compiled.patch.params.get("stereo")
                       else "") + f"NNA {compiled.patch.inst['nna']}")
        compiled.patch.bands = (low, high, 12)
        entry = ladder.make_entry(name, genome, compiled, role, "")
        _VOICES[key] = (entry.compiled, said)
    compiled, said = _VOICES[key]
    return copy.deepcopy(compiled), said


def _install_voice(module: M.Module, role: str, rung: str, low: int,
                   high: int, notes: List[str]) -> int:
    """Put the voice a job has on `rung` in the module, for keys `low`..
    `high`. -> its instrument number."""
    number = len(module.instruments) + 1
    name = f"{role} {rung}"[:25]
    tick = 2.5 / module.tempo
    low, high = max(1, low), min(120, high)
    pulse = rung == "nes" or (role == "bell" and rung == "sega")
    if pulse:
        # the NES's pulse, and the Mega Drive's PSG, which is the same sound
        nes = ladder.nes_voice(name, "lead" if role == "bell" else role)
        options = recipes.split_options(ladder.nes_line(nes).split())
        options["oct"] = f"{(low - 1) // 12}-{min(9, (high - 1) // 12)}"
        options["name"] = name.replace(" ", "_")
        recipes.compile_instrument(number, options, recipes.Library(), module)
        notes.append(f"{name}: {nes.channel}"
                     + (f" at duty {nes.duty:g}" if nes.channel == "pulse"
                        else ""))
        return number
    compiled, said = _voice(role, rung, low, high)
    notes.append(said)
    patch_mod.install(compiled.patch, module, number, tick, name)
    fm = getattr(module, "fm", None)
    if fm is None:
        fm = module.fm = {}
    fm[number] = copy.deepcopy(compiled.patch.params["fm"])
    return number


# -- register ------------------------------------------------------------------------------------------------
def _octaves_down(part: arrangement.PartProfile) -> int:
    """How many octaves bring a bass whose median is above C-3 into range
    without taking its lowest note under C-0."""
    shift = 0
    while part.median - 12 * shift > BASS_CEILING \
            and part.low - 12 * (shift + 1) >= 1:
        shift += 1
    return shift


def _transpose(module: M.Module, channel: int, semitones: int) -> int:
    moved = 0
    for _, _, cell in _cells(module, channel):
        if _is_note(cell) and 1 <= cell.note + semitones <= 120:
            cell.note += semitones
            moved += 1
    return moved


def _copy_channel(module: M.Module, source: int, target: int, instrument: int,
                  note_shift: int = 0, db: float = 0.0, delay: int = 0,
                  effects: bool = False) -> int:
    """The notes (and stops) of `source` written on `target`, `delay` rows
    later, `db` quieter, `note_shift` semitones away, with `instrument`.
    A copy does not cross the end of a pattern. -> how many cells."""
    count = 0
    for pattern in module.patterns:
        for row in range(pattern.rows):
            line = pattern.cells[row]
            if source >= len(line):
                continue
            cell = line[source]
            to = row + delay
            if to >= pattern.rows or not (_is_note(cell) or _is_stop(cell)):
                continue
            copy_ = pattern.cell(to, target)
            if _is_note(cell):
                shifted = cell.note + note_shift
                if not 1 <= shifted <= 120:
                    continue
                copy_.note = shifted
                copy_.instrument = instrument
                copy_.volume = _scaled_volume(cell, db) if db else cell.volume
            else:
                copy_.note = cell.note
            if effects and cell.effect \
                    and M.EFFECT_LETTERS[cell.effect] not in SONG_EFFECTS:
                copy_.effect, copy_.param = cell.effect, cell.param
            count += 1
    return count


# -- the split ----------------------------------------------------------------------------------------------------
def _chord_of(notes: List[int]) -> List[int]:
    """Up to three notes that hold the chord an arpeggio plays: its three
    commonest pitch classes, one octave under the arpeggio's middle, lowest
    first."""
    counts: Dict[int, int] = {}
    for n in notes:
        counts[n % 12] = counts.get(n % 12, 0) + 1
    chosen = sorted(counts, key=lambda pc: (-counts[pc], pc))[:3]
    middle = sorted(notes)[len(notes) // 2]
    out = []
    for pc in chosen:
        note = pc + 12 * ((middle - 10) // 12)
        while note > middle - 3:
            note -= 12
        while note < middle - 16:
            note += 12
        out.append(max(1, min(120, note)))
    return sorted(set(out))


def _split_arp(module: M.Module, channel: int, pad: int, window: int,
               taken: set, result: Result) -> List[int]:
    """Put the chord of an arpeggio channel on three pad channels, a window
    of rows at a time. -> the channels."""
    channels = [_free_channel(module, taken) for _ in range(3)]
    holding = False
    placed = 0
    for pattern in module.patterns:
        for start in range(0, pattern.rows, window):
            notes = []
            for row in range(start, min(pattern.rows, start + window)):
                line = pattern.cells[row]
                if channel < len(line) and _is_note(line[channel]):
                    notes.append(line[channel].note)
            if not notes:
                if holding:
                    for ch in channels:
                        pattern.cell(start, ch).note = M.NOTE_OFF
                    holding = False
                continue
            for ch, note in zip(channels, _chord_of(notes)):
                cell = pattern.cell(start, ch)
                cell.note, cell.instrument, cell.volume = note, pad, PAD_VOLUME
            holding = True
            placed += 1
    for ch, pan in zip(channels, PAD_PANS):
        module.channel_pan[ch] = pan
    result.add("split", f"the chord of the arpeggio is a pad held on "
               f"ch{channels[0] + 1:02d} ch{channels[1] + 1:02d} "
               f"ch{channels[2] + 1:02d} (left, centre, right), {placed} "
               f"windows of {window} rows", channel)
    return channels


# -- fills --------------------------------------------------------------------------------------------------------------
def _kit_keys(module: M.Module, number: int) -> Dict[str, List[int]]:
    """{'snare': [notes], 'tom': [notes, high to low]} on the keys of kit
    instrument `number`, by the names of its samples."""
    from .. import export
    out: Dict[str, List[int]] = {"snare": [], "tom": []}
    ins = module.instruments[number - 1]
    for first, last, sample in export.key_runs(ins):
        name = (module.samples[sample - 1].name or "").lower()
        for kind in out:
            if kind in name:
                out[kind].extend(range(first, last + 1))
    out["tom"].sort(reverse=True)
    return out


def _fill_hits(keys: Dict[str, List[int]]) -> List[Tuple[int, int]]:
    """[(note, volume)] for the last four rows, or [] if the kit has neither
    a tom nor a snare."""
    snare = keys["snare"][0] if keys["snare"] else None
    toms = keys["tom"]
    if snare and toms:
        return [(snare, 40), (snare, 52), (toms[0], 60), (toms[-1], 64)]
    if snare:
        return [(snare, v) for v in (36, 46, 56, 64)]
    if toms:
        return [(toms[0], 48), (toms[0], 56), (toms[-1], 60), (toms[-1], 64)]
    return []


def _own_pattern(module: M.Module, position: int) -> M.Pattern:
    """The pattern played at order `position`, copied first if another place
    in the order list plays it too, so that an edit is made once."""
    index = module.orders[position]
    if sum(1 for o in module.orders if o == index) > 1 \
            and len(module.patterns) < M.IT_MAX_PATTERNS:
        module.patterns.append(copy.deepcopy(module.patterns[index]))
        module.orders[position] = len(module.patterns) - 1
        index = module.orders[position]
    return module.patterns[index]


def _fill(module: M.Module, channels: List[int], instrument: int,
          every: int, result: Result):
    """A roll on the last rows of the patterns that end a phrase, written on
    the one channel of the kit (of `channels`, which play it) that plays the
    snare, whatever the others do."""
    keys = _kit_keys(module, instrument)
    hits = _fill_hits(keys)
    where = channels[0]
    if not hits:
        result.add("fill", "the kit has neither a tom nor a snare on its "
                   "keys, so there is nothing to fill with", where)
        return
    roll_keys = set(keys["snare"]) | set(keys["tom"])

    def plays(pattern, row, channel):
        line = pattern.cells[row]
        return channel < len(line) and _is_note(line[channel])

    # the channel that plays the snare carries the roll
    where = max(channels, key=lambda c: sum(
        1 for p in module.patterns for r in range(p.rows)
        if plays(p, r, c) and p.cells[r][c].note in roll_keys))
    positions = [i for i, o in enumerate(module.orders)
                 if o < len(module.patterns)]
    chosen = [p for n, p in enumerate(positions, 1)
              if n % every == 0 or p == positions[-1]]
    filled = []
    for position in chosen:
        source = module.patterns[module.orders[position]]
        if source.rows < 32:
            continue
        last = range(source.rows - 4, source.rows)
        if not any(plays(source, r, c) for r in range(source.rows)
                   for c in channels):
            continue                           # not a drum pattern
        rolled = [r for r in last for c in channels
                  if plays(source, r, c)
                  and source.cells[r][c].note in roll_keys]
        if len(rolled) >= 2:
            continue                           # it has a fill already
        pattern = _own_pattern(module, position)
        for row, (note, volume) in zip(last, hits):
            cell = pattern.cell(row, where)
            cell.note, cell.instrument, cell.volume = note, instrument, volume
            cell.effect = cell.param = 0
        filled.append(position)
    names = ", ".join(M.note_name(n) for n, _ in hits)
    if filled:
        result.add("fill", f"a roll on the last 4 rows of the patterns at "
                   f"orders {', '.join(str(p) for p in filled)} ({names}: "
                   f"keys the kit has)", where)
    else:
        result.add("note", "every drum pattern that ends a phrase already has "
                   "a fill", where)


# -- the pass ------------------------------------------------------------------------------------------------------------
def rearrange(module: M.Module, to: str, roles: Optional[Dict[str, str]] = None,
              split: bool = True, register: bool = True, sub: bool = True,
              echo=(), fill: bool = True, every: int = 4,
              kit: Optional[str] = None, window: Optional[int] = None
              ) -> Result:
    """-> Result: a copy of `module` played by the voices of the rung `to`.
    `roles` names a channel's job when its notes do not ({'ch03': 'bass'});
    `echo` is [(channel (1-based), delay rows, dB)]."""
    if to not in TARGETS:
        raise RearrangeError(f"{to!r} is not a rung; the rungs are "
                             f"{', '.join(TARGETS)}")
    work = copy.deepcopy(module)
    arr = arrangement.Arrangement(work, roles)
    voices = arr.voices()
    result = Result(work, to, roles={}, channels_before=work.channels_used())
    parts = {}
    for voice in voices:
        parts[voice] = arr.part(voice)
        if parts[voice].role not in ("drums", "fx") \
                and voice not in (roles or {}) \
                and _percussive(work, arr, parts[voice]):
            result.add("note", f"it is {parts[voice].role} by its notes but "
                       f"plays a sound that ends by itself on one or two "
                       f"pitches: percussion", parts[voice].channel)
            parts[voice].role = "drums"
        result.roles[voice] = parts[voice].role
    taken: set = set()
    window = window or max(4, work.rows_per_measure)
    limit = CHANNELS[to]

    def room(wanted: int) -> bool:
        return len(_occupied(work) | taken) + wanted <= limit

    # -- register: a bass that is too high ------------------------------------------------
    sub_for: Dict[int, int] = {}
    if register:
        for voice, part in parts.items():
            if part.role != "bass" or part.median <= BASS_CEILING:
                continue
            octaves = _octaves_down(part)
            if not octaves:
                result.add("note", f"the bass is above C-3 (median "
                           f"{arrangement.note_label(int(part.median))}) and "
                           f"cannot go lower without leaving the keyboard",
                           part.channel)
                continue
            _transpose(work, part.channel, -12 * octaves)
            part.low -= 12 * octaves
            part.high -= 12 * octaves
            part.median -= 12 * octaves
            result.add("register", f"dropped {octaves} octave"
                       f"{'s' if octaves > 1 else ''}, median now "
                       f"{arrangement.note_label(int(part.median))}",
                       part.channel)
            sub_for[part.channel] = part.channel

    # -- the sub that follows a dropped bass --------------------------------------------------------
    if sub and sub_for and not room(len(sub_for)):
        result.add("note", f"no room for a sub: the {to} has {limit} channels")
    elif sub and sub_for:
        sub_number = _install_voice(work, "bass", to, 1, 48, [])
        work.instruments[sub_number - 1].name = f"sub {to}"[:25]
        for channel in sub_for:
            target = _free_channel(work, taken)
            count = _copy_channel(work, channel, target, sub_number,
                                  note_shift=-12)
            result.add("sub", f"a sub an octave under it on ch{target + 1:02d}"
                       f" ({count} cells)", channel)

    # -- remap: each part gets the voice its job has on the rung ---------------------------------
    by_role: Dict[str, List[arrangement.PartProfile]] = {}
    for part in parts.values():
        voice_role = VOICE.get(part.role)
        if voice_role:
            by_role.setdefault(voice_role, []).append(part)
    # the arpeggios' chord needs a pad, whose range is under theirs
    arps = [p for p in parts.values() if p.role == "arp"]
    pad_range = None
    if split and arps and to != "nes":
        if room(3 * len(arps)):
            pad_range = (min(p.low for p in arps) - 17,
                         max(p.high for p in arps) - 3)
        else:
            result.add("note", f"no room to split the arpeggio into a pad: "
                       f"the {to} has {limit} channels")
    numbers: Dict[str, int] = {}
    notes: List[str] = []
    for voice_role, members in by_role.items():
        low = min(p.low for p in members)
        high = max(p.high for p in members)
        if voice_role == "pad" and pad_range:
            low, high = min(low, pad_range[0]), max(high, pad_range[1])
        numbers[voice_role] = _install_voice(work, voice_role, to, low, high,
                                             notes)
    if pad_range and "pad" not in numbers:
        numbers["pad"] = _install_voice(work, "pad", to, *pad_range, notes)
    for line in notes:
        result.add("note", line)
    old = {}
    for part in parts.values():
        voice_role = VOICE.get(part.role)
        if not voice_role:
            continue
        new = numbers[voice_role]
        for _, _, cell in _cells(work, part.channel):
            if cell.instrument:
                old.setdefault(part.channel, set()).add(cell.instrument)
                cell.instrument = new
        was = ", ".join(f"{i:02d} {work.instruments[i - 1].name or '-'}"
                        for i in sorted(old.get(part.channel, ())))
        result.add("remap", f"{part.role} -> {voice_role}: instrument "
                   f"{was or '-'} -> {new:02d} "
                   f"{work.instruments[new - 1].name}; notes untouched",
                   part.channel)

    # -- split an arpeggio into a pad and an arp -------------------------------------------------------
    echoes = list(echo)
    if split and pad_range:
        pad = numbers["pad"]
        for part in arps:
            _split_arp(work, part.channel, pad, window, taken, result)
            if to == "schism" and not any(e[0] == part.channel + 1
                                          for e in echoes):
                echoes.append((part.channel + 1, 2, -14.0))
            # the arp itself is quieter now that something holds the chord
            for _, _, cell in _cells(work, part.channel):
                if _is_note(cell):
                    cell.volume = _scaled_volume(cell, -4.0)

    # -- echo -------------------------------------------------------------------------------------------------
    for channel_1, delay, db in echoes:
        channel = channel_1 - 1
        if not 0 <= channel < MAX_CHANNELS or channel not in _occupied(work):
            raise RearrangeError(f"echo: channel {channel_1} has nothing in "
                                 f"it")
        instrument = next((c.instrument for _, _, c in _cells(work, channel)
                           if c.instrument), 0)
        if not room(1):
            result.add("note", f"no room for the echo of ch{channel_1:02d}: "
                       f"the {to} has {limit} channels")
            continue
        target = _free_channel(work, taken)
        count = _copy_channel(work, channel, target, instrument, db=db,
                              delay=delay, effects=True)
        work.channel_pan[target] = 64 - work.channel_pan[channel]
        result.add("echo", f"copied to ch{target + 1:02d}, {delay} rows "
                   f"later, {db:g} dB ({count} cells; an echo does not cross "
                   f"the end of a pattern)", channel)

    # -- the drums ----------------------------------------------------------------------------------------------
    kits: Dict[int, List[int]] = {}
    drums: Dict[str, int] = {}
    for voice, part in parts.items():
        if part.role != "drums":
            continue
        instrument = part.instruments[0] if part.instruments else 0
        if instrument and export_is_kit(work.instruments[instrument - 1]):
            kits.setdefault(instrument, []).append(part.channel)
        elif instrument and to != "nes":
            # a drum on a channel of its own: the rung's drum of its kind,
            # played as recorded on every key so that the notes stay
            kind = drum_kind(work, instrument)
            if kind not in drums:
                drums[kind] = _install_drum(work, kind, to)
            for _, _, cell in _cells(work, part.channel):
                if cell.instrument:
                    cell.instrument = drums[kind]
            result.add("remap", f"percussion -> {kind}: instrument "
                       f"{instrument:02d} {work.instruments[instrument - 1].name}"
                       f" -> {drums[kind]:02d} {kind} {to}"
                       + (" (a DAC one-shot)" if to == "sega"
                          else " (the DAC crack over a body)")
                       + "; notes untouched", part.channel)
        elif instrument:
            result.add("remap", "percussion kept: it is the NES's own",
                       part.channel)
    for instrument, channels in kits.items():
        if kit:
            from . import notation_hook
            number = len(work.instruments) + 1
            notation_hook._compile_kit(number, kit, {}, recipes.Library(),
                                       work, None)
            for channel in channels:
                for _, _, cell in _cells(work, channel):
                    if cell.instrument:
                        cell.instrument = number
            result.add("remap", f"kit -> {kit} (instrument {number:02d})",
                       channels[0])
            instrument = number
        else:
            result.add("remap", "the kit is kept: its keys are the drums the "
                       "composer wrote", channels[0])
        if fill:
            _fill(work, channels, instrument, every, result)

    # -- what nobody plays any more -------------------------------------------------------------------------------
    used = {cell.instrument for p in work.patterns for line in p.cells
            for cell in line if cell.instrument}
    for number in range(1, len(work.instruments) + 1):
        if number not in used and work.instruments[number - 1].name:
            work.instruments[number - 1] = M.Instrument(
                name="", note_map=[(n, 0) for n in range(120)])
    arrangement.compact_samples(work)
    return result
