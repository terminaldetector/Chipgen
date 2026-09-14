"""arrange.py — fit music onto a chip that cannot hold all of it.

## What this is for

Getting notes is one problem; getting them onto six channels with hard
pitch floors and one note per voice is a different one, and it is the one
that decides whether a rearrangement sounds like the music or like a
compression artefact. A piano piece has no channel count. A transcription
has as many voices as the transcriber found. A score written for the
Genesis has ten. None of those fit an NES, and "it rendered" says nothing
about whether the arrangement survived.

So this takes a Score from anywhere — a .trk, a transcribed register log,
a JSON score — and fits it to a target chip, reporting every compromise
it had to make. The report is the point. An arranger that silently drops
the counter-melody and hands back a file is worse than one that refuses.

## What it will not do quietly

**Clamp a note that is out of range.** Below the NES pulse floor the
11-bit timer clamps and the note sounds 888 cents SHARP rather than low —
measured. So a part that will not fit is transposed by whole octaves,
which keeps its shape, and the report says by how much. Nothing is
clamped.

**Copy a velocity between chips that mean different things by it.** The
YM2612 reads 1-127 as a fader, linear in amplitude. The SN76489 reads
0-15 as an *attenuator* where 0 is loudest and every step is -2 dB. Move
a part from one to the other without converting and a crescendo comes out
as a diminuendo. Velocities are converted through dB, and where a chip
has no volume register at all — the NES triangle — they are dropped and
the report says so rather than approximating them.

**Drop a voice without saying so.** When there are more parts than
channels, the ones that go are named, with the reason.

**Flatten a chord into one note.** A voice holding three simultaneous
notes cannot play them on one channel. The report says how many notes
were lost and the arrangement keeps the outer one — the top and the
bottom carry a chord's identity; the middle is what a listener
reconstructs.

**Leave a drum track in a score for a chip that cannot play it.** OPL2
has no sample channel here (its rhythm mode, register 0xBD, is the one
layer this engine does not implement), so a drum track arranged for it is
reported as dropped and removed, not carried along as a lie in the data.

## What it is not

Not a transcriber. Notes have to come from somewhere first, and nothing
here turns a recording into notes — see `vgm_transcribe.py` and
`nes_transcribe.py` for register logs, which is a different and much
easier problem, because a register log already contains the notes.
"""

import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import score_model

#: How a chip's volume field is read. Not cosmetic: converting between
#: these wrong inverts a part's dynamics.
FADER = "fader"              # 1-127, higher is louder (YM2612, OPL2, NES)
ATTENUATOR = "attenuator"    # 0-15, 0 is loudest, -2 dB a step (SN76489)
SILENT = "none"              # no volume register exists at all


class Channel:
    """One destination channel and what it can hold.

    `low` and `high` are in the score model's pitch units — octave * 12 +
    semitone — and they are the MUSICAL range, not the register range. A
    channel whose timer reaches lower than its sound stays usable is
    listed at the pitch where it is still worth hearing.
    """

    __slots__ = ("column", "chip", "low", "high", "kind", "roles",
                 "dynamics", "aliases", "note")

    def __init__(self, column, chip, low, high, kind="melodic", roles=(),
                 dynamics=FADER, aliases=(), note=""):
        self.column = column
        self.chip = chip
        self.low = low
        self.high = high
        self.kind = kind
        #: Roles this channel suits, best first. Used to place parts, not
        #: to refuse them — a bass on a lead channel is a choice, not an
        #: error.
        self.roles = tuple(roles)
        self.dynamics = dynamics
        #: Names a source voice may already carry for this channel, so a
        #: score written for this chip arrives home unchanged.
        self.aliases = tuple(aliases)
        self.note = note

    @property
    def velocity(self) -> bool:
        return self.dynamics != SILENT

    def holds(self, low, high):
        return self.low <= low and high <= self.high

    def names(self):
        return (self.column,) + self.aliases


def _p(note: str, octave: int) -> int:
    import events
    return octave * 12 + events.NOTE_NAMES.index(note)


#: Per chip: the channels, in the order the arranger prefers to fill
#: them. Ranges are measured, not read off datasheets.
TARGETS = {
    "RP2A03": [
        Channel("nes2", "RP2A03", _p("A", 0), _p("A", 5), "melodic",
                ("bass",), dynamics=SILENT, aliases=("triangle", "tri"),
                note="the triangle is the bass: it reaches A-0 where the "
                     "pulses stop at A-1, and it has NO volume register — "
                     "velocity is ignored, measured bit-identical at 8 "
                     "and 127"),
        Channel("nes0", "RP2A03", _p("A", 1), _p("C", 6), "melodic",
                ("lead", "harmony"), aliases=("pulse1", "pu1", "sq1")),
        Channel("nes1", "RP2A03", _p("A", 1), _p("C", 6), "melodic",
                ("harmony", "lead"), aliases=("pulse2", "pu2", "sq2"),
                note="pulse 2 has no sweep unit worth using for pitch; "
                     "give it the counter-line"),
        Channel("nes3", "RP2A03", 0, 127, "percussion", ("percussion",),
                aliases=("noise", "nnoise", "nesnoise"),
                note="the noise channel's pitch is a 16-entry timer "
                     "index, not a note — it is placed, never transposed"),
        Channel("nes4", "RP2A03", 0, 127, "sample", ("percussion",),
                dynamics=SILENT, aliases=("dmc", "nespcm", "pcm")),
    ],
    "YM2612": [
        Channel("fm0", "YM2612", _p("C", 0), _p("C", 8), "melodic",
                ("bass",)),
        Channel("fm1", "YM2612", _p("C", 0), _p("C", 8), "melodic",
                ("lead",)),
        Channel("fm2", "YM2612", _p("C", 0), _p("C", 8), "melodic",
                ("harmony", "pad")),
        Channel("fm3", "YM2612", _p("C", 0), _p("C", 8), "melodic",
                ("harmony", "pad")),
        Channel("fm4", "YM2612", _p("C", 0), _p("C", 8), "melodic",
                ("pad", "harmony")),
        Channel("psg0", "SN76489", _p("C", 2), _p("C", 6), "melodic",
                ("harmony", "lead"), dynamics=ATTENUATOR,
                note="PSG pitch resolution runs out above about C6 — "
                     "neighbouring semitones land on the same divisor"),
        Channel("psg1", "SN76489", _p("C", 2), _p("C", 6), "melodic",
                ("harmony",), dynamics=ATTENUATOR),
        Channel("psg2", "SN76489", _p("C", 2), _p("C", 6), "melodic",
                ("harmony",), dynamics=ATTENUATOR),
        Channel("noise", "SN76489", 0, 127, "percussion", ("percussion",),
                dynamics=ATTENUATOR, aliases=("nnoise", "psgnoise")),
        Channel("dac", "YM2612", 0, 127, "sample", ("percussion",),
                dynamics=SILENT, aliases=("pcm",),
                note="the DAC takes FM channel 6 outright — six FM voices "
                     "and a drum track are mutually exclusive, which is "
                     "why fm5 is not in this list"),
    ],
    "YM3812": [
        Channel(f"opl{i}", "YM3812", _p("C", 0), _p("C", 8), "melodic",
                ("bass",) if i == 0 else ("lead",) if i == 1
                else ("harmony", "pad"))
        for i in range(9)
    ],
}

#: A voice with fewer notes than this is not a part, it is an accent, and
#: it loses to a real part when channels are scarce.
MIN_NOTES_FOR_A_PART = 4

#: Words in a voice's name that state its role. A MIDI track called
#: "Bass" is a composer saying so, and that outranks a classifier working
#: from four notes — `roles.classify_score` declines to judge a part that
#: short at all, which is exactly when the name is all there is.
ROLE_WORDS = {
    "bass": "bass", "sub": "bass",
    "lead": "lead", "melody": "lead", "solo": "lead", "vocal": "lead",
    "harmony": "harmony", "chord": "harmony", "counter": "harmony",
    "rhythm": "harmony", "guitar": "harmony", "piano": "harmony",
    "pad": "pad", "string": "pad", "strings": "pad", "brass": "pad",
    "organ": "pad",
}

#: Source voice names that mean percussion whatever chip they came from.
#: A noise track must not be allowed to win a melodic channel from a
#: melody just because the transcriber gave it more hits.
PERCUSSIVE = frozenset({"noise", "nnoise", "nesnoise", "psgnoise", "nes3",
                        "dac", "pcm", "dmc", "nespcm", "nes4"})


def velocity_db(value: int, domain: str) -> float:
    """A chip's volume field as dB of attenuation below its loudest."""
    if domain == SILENT:
        return 0.0
    if domain == ATTENUATOR:
        # The SN76489's 4-bit attenuator: -2 dB a step, 15 a hard mute.
        return 2.0 * max(0, min(15, int(value)))
    # The fader is linear in amplitude, the same curve opn2.py uses to
    # turn a 0-127 velocity into Total Level steps.
    level = max(1, min(127, int(value)))
    return -20.0 * math.log10(level / 127.0)


def convert_velocity(value: int, source: str, destination: str) -> int:
    """Move a velocity between chips that mean different things by it."""
    if destination == SILENT:
        return 127
    if source == destination:
        return int(value)
    db = velocity_db(value, source)
    if destination == ATTENUATOR:
        # 15 is a hard mute: a note-on written at 15 is a dropped note,
        # so the quietest a converted note gets is 14 (-28 dB).
        return max(0, min(14, int(round(db / 2.0))))
    return max(1, min(127, int(round(127.0 * (10.0 ** (-db / 20.0))))))


#: Voice names that only ever belong to one chip. "noise" is missing on
#: purpose — both the SN76489 and the NES have one, and they disagree
#: about what a volume number means.
_NES_NAMES = frozenset({"pulse1", "pulse2", "triangle", "pu1", "pu2",
                        "sq1", "sq2", "tri", "dmc", "nes0", "nes1",
                        "nes2", "nes3", "nes4"})


def source_domains(score) -> dict:
    """What each of a score's voices means by a velocity.

    Almost every voice answers from its own name: `psg0` is the
    SN76489's attenuator, everything else is a 1-127 fader. `noise` is
    the exception and the reason this takes a whole score rather than a
    name — the SN76489 and the NES both have a channel called that, and
    they read the number in opposite directions. The company the voice
    keeps settles it: a score holding `pulse1` is an NES score.
    """
    names = {v.lower() for v in score.voices}
    nes = bool(names & _NES_NAMES)
    out = {}
    for voice in score.voices:
        name = voice.lower()
        if name.startswith("psg") or name == "psgnoise":
            out[voice] = ATTENUATOR
        elif name == "noise":
            out[voice] = FADER if nes else ATTENUATOR
        else:
            out[voice] = FADER
    return out


class Report:
    """What the arrangement had to give up, and why."""

    def __init__(self, target):
        self.target = target
        self.placed = {}          # source voice -> destination column
        self.transposed = {}      # source voice -> octaves moved
        self.folded = {}          # source voice -> notes moved on their own
        self.flattened = {}       # source voice -> notes lost to polyphony
        self.converted = {}       # source voice -> "fader -> attenuator"
        self.dropped = {}         # source voice -> reason
        self.notes = []

    @property
    def lossless(self) -> bool:
        return not (self.dropped or self.flattened or self.transposed
                    or self.folded)

    def lines(self) -> list:
        lost = [v for v in self.dropped if v != "drums"]
        out = [f"arranged for {self.target}: {len(self.placed)} of "
               f"{len(self.placed) + len(lost)} voices placed"]
        for voice, column in sorted(self.placed.items()):
            detail = []
            moved = self.transposed.get(voice)
            if moved:
                detail.append(f"transposed {moved:+d} octave"
                              f"{'s' if abs(moved) != 1 else ''}")
            folded = self.folded.get(voice)
            if folded:
                detail.append(f"{folded} note{'s' if folded != 1 else ''} "
                              f"folded an octave to fit")
            lost = self.flattened.get(voice)
            if lost:
                detail.append(f"{lost} note{'s' if lost != 1 else ''} lost "
                              f"to polyphony")
            change = self.converted.get(voice)
            if change:
                detail.append(change)
            suffix = f"  ({', '.join(detail)})" if detail else ""
            arrow = "stays on" if voice == column else "->"
            out.append(f"  {voice} {arrow} {column}{suffix}")
        for voice, reason in sorted(self.dropped.items()):
            out.append(f"  {voice} DROPPED — {reason}")
        out.extend(f"  note: {n}" for n in self.notes)
        return out

    def to_json(self) -> dict:
        return {"target": self.target, "lossless": self.lossless,
                "placed": dict(self.placed),
                "transposed": dict(self.transposed),
                "folded": dict(self.folded),
                "flattened": dict(self.flattened),
                "converted": dict(self.converted),
                "dropped": dict(self.dropped),
                "notes": list(self.notes)}

    def __str__(self):
        return "\n".join(self.lines())


def _span(notes):
    pitches = [n.pitch for n in notes]
    return min(pitches), max(pitches)


def _best_shift(notes, channel):
    """The whole-octave shift that leaves the fewest notes out of range.

    Whole octaves because that is the only transposition that keeps a
    part recognisable, and "fewest outside" rather than "the whole span
    fits" because a transcribed register log routinely holds five octaves
    on one channel — a rule that only accepts a perfect fit would fold
    half of a part that a one-octave move would have placed whole.

    Returns (octaves, notes still outside).
    """
    if channel.low <= 0 and channel.high >= 127:
        return 0, 0
    best = None
    for octaves in (0, 1, -1, 2, -2, 3, -3, 4, -4):
        shift = octaves * 12
        outside = sum(1 for n in notes
                      if not channel.low <= n.pitch + shift <= channel.high)
        if best is None or outside < best[1]:
            best = (octaves, outside)
        if outside == 0:
            break
    return best


def _fold(pitch: int, channel) -> int:
    """Move one pitch into a channel's range by whole octaves."""
    while pitch < channel.low:
        pitch += 12
    while pitch > channel.high:
        pitch -= 12
    return pitch


def _polyphony(notes) -> int:
    """How many notes sound at once, at the busiest moment."""
    edges = []
    for note in notes:
        edges.append((note.row, 1))
        edges.append((note.row + max(1, note.length), -1))
    edges.sort()
    live = worst = 0
    for _row, delta in edges:
        live += delta
        worst = max(worst, live)
    return worst


def _monophonic(notes):
    """Keep one note per moment: the outer voice of any pile-up.

    The top and the bottom of a chord carry its identity; a listener
    reconstructs the middle. So when a channel has to choose it keeps the
    extreme — highest for a part sitting high, lowest for one sitting low
    — rather than whatever happened to be first in the list.
    """
    if not notes:
        return [], 0
    centre = sum(n.pitch for n in notes) / len(notes)
    prefer_high = centre >= 60          # middle C-ish, in score units
    by_row = {}
    for note in notes:
        current = by_row.get(note.row)
        if current is None:
            by_row[note.row] = note
        elif (note.pitch > current.pitch) == prefer_high:
            by_row[note.row] = note
    kept = sorted(by_row.values(), key=lambda n: n.row)

    out = []
    for index, note in enumerate(kept):
        length = note.length
        if index + 1 < len(kept):
            length = min(length, kept[index + 1].row - note.row)
        if length >= 1:
            out.append(note._replace(length=length))
    return out, len(notes) - len(out)


def _role_of(name, classified):
    """The role to place a part by, and where that came from.

    The name first: a part called "Bass" is a statement of intent, and
    the classifier explicitly declines to judge a part with only a few
    notes, which is the case where a name is all there is. Measurement
    second, "harmony" last — the role that suits the most channels, so an
    unknown part is placed rather than refused.
    """
    lowered = name.lower()
    for word, role in ROLE_WORDS.items():
        if word in lowered:
            return role, "its name"
    scores = classified.get(name) or {}
    if scores:
        best = max(scores.items(), key=lambda kv: kv[1])
        if best[1] > 0:
            return best[0], f"measurement ({best[1]:.2f})"
    return "harmony", "nothing to go on"


def _rank(voices, classified):
    """Which parts matter most, when there are not enough channels.

    Note count first, because a part with four notes in a two-minute
    piece is an accent and a part with two hundred is the music. Role
    confidence breaks ties: a confident bass outranks an uncertain pad.
    """
    def key(item):
        name, notes = item
        confidence = max((classified.get(name) or {}).values(), default=0.0)
        return (-len(notes), -confidence, name)
    return sorted(voices.items(), key=key)


def _index(channels) -> dict:
    """Every name a channel answers to -> that channel."""
    out = {}
    for channel in channels:
        for name in channel.names():
            out.setdefault(name, channel)
    return out


def _fit(name, notes, channel, report, source=FADER, refit=True):
    """Put one part on one channel, recording what that cost.

    `refit=False` for a part that already names this channel. The ranges
    here are an editorial judgement — the pitch at which a channel is
    still worth hearing — and applying that to a score written for this
    chip would be overruling its composer with a guess. Dynamics are
    still converted: what a volume field means is hardware, not taste.
    """
    if refit and channel.kind == "melodic":
        span = _span(notes)
        octaves, outside = _best_shift(notes, channel)
        if octaves:
            notes = [n._replace(pitch=n.pitch + octaves * 12) for n in notes]
            report.transposed[name] = octaves
        if outside:
            notes = [n if channel.low <= n.pitch <= channel.high
                     else n._replace(pitch=_fold(n.pitch, channel))
                     for n in notes]
            report.folded[name] = outside
            report.notes.append(
                f"{name} spans {span[1] - span[0]} semitones and "
                f"{channel.column} holds {channel.high - channel.low}; "
                f"{outside} of {len(notes)} notes were folded by an octave "
                f"to fit, which changes the line at those notes")

    if channel.kind == "percussion" and (name != channel.column
                                        or channel.dynamics != source):
        report.notes.append(
            f"{name} lands on {channel.column}, a noise channel: the rows "
            f"and the velocities carry, the pitch does not — noise is a "
            f"timer index on one chip and three fixed rates on the other, "
            f"and neither is a note")

    if _polyphony(notes) > 1:
        notes, lost = _monophonic(notes)
        if lost:
            report.flattened[name] = lost

    if channel.dynamics != source:
        if channel.dynamics == SILENT:
            report.converted[name] = "velocities dropped"
            report.notes.append(
                f"{channel.column} has no volume register — {name}'s "
                f"velocities were dropped rather than approximated")
        else:
            report.converted[name] = f"{source} -> {channel.dynamics}"
        notes = [n._replace(velocity=convert_velocity(
            n.velocity, source, channel.dynamics)) for n in notes]

    report.placed[name] = channel.column
    return notes


def arrange(score, target: str):
    """Fit a score onto a chip. -> (Score, Report).

    A score already written for the target comes back unchanged: a voice
    named for one of the target's channels keeps that channel, whatever
    the role classifier would have preferred. The channel assignment in a
    score for this chip is the composer's, not ours to improve.
    """
    if target not in TARGETS:
        raise ValueError(f"no target {target!r}. Have: "
                         f"{', '.join(sorted(TARGETS))}")
    channels = list(TARGETS[target])
    report = Report(target)
    index = _index(channels)
    melodic = [c for c in channels if c.kind == "melodic"]
    percussion = [c for c in channels if c.kind in ("percussion", "sample")]

    import roles
    try:
        classified = roles.classify_score(score)
    except Exception:
        classified = {}
    domains = source_domains(score)

    placed = {}
    taken = set()
    pending = []
    by_name = []

    # Pass 1 — identity. A voice that names a channel of this chip is
    # already where its composer put it.
    for name, notes in _rank(score.voices, classified):
        if not notes:
            continue
        channel = index.get(name.lower())
        if channel is not None and channel.column not in taken:
            placed[channel.column] = _fit(name, notes, channel, report,
                                          domains[name], refit=False)
            taken.add(channel.column)
        else:
            pending.append((name, notes))

    # Pass 2 — everything else, best channel first.
    for name, notes in pending:
        if name.lower() in PERCUSSIVE:
            free = [c for c in percussion if c.column not in taken]
            if not free:
                report.dropped[name] = (
                    f"a percussion part, and {target} has no free "
                    f"percussion channel")
                continue
            chosen = free[0]
            placed[chosen.column] = _fit(name, notes, chosen, report,
                                         domains[name])
            taken.add(chosen.column)
            continue

        options = [c for c in melodic if c.column not in taken]
        if not options:
            report.dropped[name] = (
                f"{len(melodic)} melodic channels on {target} and this is "
                f"part {len(taken) + 1}")
            continue
        if len(notes) < MIN_NOTES_FOR_A_PART and len(options) < len(pending):
            report.dropped[name] = (
                f"only {len(notes)} note{'s' if len(notes) != 1 else ''} "
                f"and channels are scarce — an accent loses to a part")
            continue

        role, why = _role_of(name, classified)
        if why == "its name":
            by_name.append(name)
        dynamic = len({n.velocity for n in notes}) > 1

        def cost(channel):
            _octaves, outside = _best_shift(notes, channel)
            # A part whose velocities move, placed on a channel with no
            # volume register, loses its dynamics outright. That is a
            # real cost and it used to be invisible: with no channel
            # suiting the role every option tied, and the tie went to
            # whichever was first in the list — on the NES, the triangle.
            deaf = dynamic and channel.dynamics == SILENT
            return (0 if not outside else 1,
                    0 if role in channel.roles else 1,
                    1 if deaf else 0,
                    outside)

        options.sort(key=lambda c: (cost(c), melodic.index(c)))
        chosen = options[0]
        placed[chosen.column] = _fit(name, notes, chosen, report,
                                     domains[name])
        taken.add(chosen.column)

    if by_name:
        # Said once rather than per voice, and said at all because this
        # project places parts by measurement everywhere else: a label
        # overriding a measurement is the surprising direction.
        report.notes.append(
            f"{', '.join(sorted(by_name))} "
            f"{'was' if len(by_name) == 1 else 'were'} placed by name — "
            f"the role classifier declines to judge a part with only a few "
            f"notes, and a part called \"Bass\" is its composer saying so")

    drums = _drums(score, report, percussion, taken, placed)
    fitted = score._replace(voices=placed, drums=drums,
                            title=f"{score.title} [{target}]")
    return fitted, report


def _drums(score, report, percussion, taken, placed):
    """Where the drum track goes, or why it does not go anywhere.

    The NES corpus stores its noise hits twice — once as a voice and once
    as the drum list, because `nes_transcribe.py` builds the drum list
    *from* the noise voice. Carrying both through would double every
    drum, so when they are the same hits the voice keeps them.
    """
    if not score.drums:
        return []
    for column in taken:
        notes = placed.get(column) or []
        if len(notes) == len(score.drums) and \
                [n.row for n in notes] == list(score.drums):
            report.notes.append(
                f"the drum list and {column} are the same "
                f"{len(score.drums)} hits — kept once, on {column}")
            return []
    free = [c for c in percussion if c.column not in taken]
    # A drum list is a list of sample triggers, so it wants the sample
    # channel — the DAC or the DMC — and only falls back to a noise
    # channel if there is no sampler to be had.
    free.sort(key=lambda c: 0 if c.kind == "sample" else 1)
    if not free:
        report.dropped["drums"] = (
            f"{len(score.drums)} hits and no sample channel on "
            f"{report.target}"
            + (" — OPL2's rhythm mode (register 0xBD) is the one layer "
               "this engine does not implement" if report.target == "YM3812"
               else ""))
        return []
    report.notes.append(f"{len(score.drums)} drum hits play through "
                        f"{free[0].column}")
    return list(score.drums)


def targets() -> dict:
    """What an interface offers, and what each target costs."""
    out = {}
    for name, channels in sorted(TARGETS.items()):
        melodic = [c for c in channels if c.kind == "melodic"]
        out[name] = {
            "melodic_voices": len(melodic),
            "percussion": [c.column for c in channels
                           if c.kind in ("percussion", "sample")],
            "channels": [
                {"column": c.column, "chip": c.chip, "kind": c.kind,
                 "suits": list(c.roles), "dynamics": c.dynamics,
                 "velocity": c.velocity, "aliases": list(c.aliases),
                 "low": c.low, "high": c.high, "note": c.note}
                for c in channels],
        }
    return out


def main(argv):
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="fit a score onto a chip, and say what it cost")
    parser.add_argument("source", nargs="?", help="a .trk or .json score")
    parser.add_argument("--for", dest="target", default="RP2A03",
                        help=f"target chip: {', '.join(sorted(TARGETS))}")
    parser.add_argument("-o", "--out",
                        help="write the arranged score here (.trk or .json)")
    parser.add_argument("--targets", action="store_true",
                        help="list what each target can hold, as JSON")
    parser.add_argument("--json", action="store_true",
                        help="print the report as JSON")
    args = parser.parse_args(argv)

    if args.targets:
        print(json.dumps(targets(), indent=1))
        return 0
    if not args.source:
        parser.error("give a score to arrange, or --targets")

    score = score_model.load(args.source)
    fitted, report = arrange(score, args.target)
    print(json.dumps(report.to_json(), indent=1) if args.json else report)

    if args.out:
        if args.out.endswith(".json"):
            with open(args.out, "w", encoding="utf-8") as handle:
                json.dump(score_model.to_json(fitted), handle, indent=1)
        else:
            import tracker
            meta = tracker.Metadata()
            meta.bpm, meta.lpb = fitted.bpm, fitted.lpb
            meta.title = fitted.title
            text = tracker.dumps(score_model.to_events(fitted), meta)
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(text)
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
