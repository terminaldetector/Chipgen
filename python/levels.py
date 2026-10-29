"""levels.py — which voices are actually audible, and by how much.

This module exists because "the drums are there but the instruments
aren't" took a whole investigation to diagnose, and every step of it was
mechanical: isolate each channel, render it alone, compare. That is one
command's worth of work, and having to do it by hand is the reason the
answer arrived as a guess about WAV levels rather than as a number.

## Why isolation rather than analysing the mix

You cannot get a per-channel level out of a finished mix — the whole
point of a mix is that the channels are summed. So each voice is rendered
ALONE, from the same event list with the other voices' notes removed.
Everything that shapes a voice comes with it: its instrument select, its
volume, its pan, its effects, its operator writes. The result is what
that channel contributes, measured rather than inferred.

It costs one render per sounding voice. That is fine for a diagnostic and
wrong for the render path, which is why this is its own command.

## What the numbers mean

`peak` is what sets the master's level, because normalize_peak() works on
peak. A voice that peaks 15x louder than another owns the whole gain
budget and everything else arrives that much quieter — which is exactly
what a drum track does to five FM channels.

`rms` is closer to what a listener calls loudness, and the gap between
the two columns is a voice's crest factor: a drum is all peak and little
RMS, a sustained pad the reverse. Two voices at the same RMS but 12 dB
apart in peak will fight for the master in a way neither RMS alone nor
peak alone predicts.

`headroom` is the one actionable number. For an FM voice it is the
patch's carrier Total Level: how many 0.75 dB steps of "louder" the patch
still has before its carriers hit TL 0. A patch imported at TL 32 has
24 dB of headroom it is not using, and no amount of `vol` will reach it
because `vol` scales velocity and velocity attenuates from the patch's
level, never above it. That is the difference between "turn it up" and
"this patch needs recalibrating".
"""

import math

import events as events_mod

#: Voices this can isolate, and how to recognise an event as belonging to
#: one. Order is the order the report prints in.
FM_VOICES = tuple(f"fm{i}" for i in range(6))
PSG_VOICES = tuple(f"psg{i}" for i in range(3))
OPL_VOICES = tuple(f"opl{i}" for i in range(9))
NES_VOICES = ("pulse1", "pulse2", "triangle")
OTHER_VOICES = ("noise", "dac", "nesnoise", "dmc")

#: Below this a voice is reported as silent rather than given a number:
#: -90 dBFS is past the 8-bit DAC's own noise floor, so anything quieter
#: is not a level, it is nothing.
SILENT_DBFS = -90.0


def voice_names():
    return FM_VOICES + PSG_VOICES + OPL_VOICES + NES_VOICES + OTHER_VOICES


def _voice_of(event):
    """Which voice an event belongs to, or None for a global event.

    Global events — waits, tempo, the loop point, markers, the LFO — are
    kept for every isolation, because removing them would change the
    timing of what is left and make the measurement meaningless.
    """
    E = events_mod
    name = type(event).__name__

    if name.startswith("FM") and hasattr(event, "channel"):
        return f"fm{event.channel}"
    if name.startswith("OPL") and hasattr(event, "channel"):
        return f"opl{event.channel}"
    if name in ("PSGToneOn", "PSGToneOff", "PSGVolume"):
        return f"psg{event.channel}"
    if name in ("PSGNoiseOn", "PSGNoiseOff"):
        return "noise"
    if name in ("DACSample", "DACEnable", "DACVolume"):
        return "dac"
    if name in ("NESNoiseOn", "NESNoiseOff"):
        return "nesnoise"
    if name in ("NESDMCLevel", "NESSample"):
        return "dmc"
    if name.startswith("NES") and hasattr(event, "voice"):
        return event.voice
    # The effect events carry a target rather than a channel.
    if hasattr(event, "target") and isinstance(getattr(event, "target"), str):
        target = event.target
        return "nesnoise" if target == "noise" and _looks_like_nes(event) \
            else target
    return None


def _looks_like_nes(_event):
    # An effect on `noise` is ambiguous between the two chips by design —
    # a score plays one or the other. Attribute it to the PSG, which is
    # what `noise` means in the tracker's own column list.
    return False


def sounding(event_list):
    """Which voices this score actually plays, in report order."""
    seen = set()
    for event in event_list:
        voice = _voice_of(event)
        if voice is not None:
            seen.add(voice)
    return [name for name in voice_names() if name in seen]


def isolate(event_list, voice: str):
    """The same score with every other voice's events removed.

    Global events stay. So does the voice's own setup — instrument
    select, volume, pan, operator writes — because a level measured
    without them is not the level that voice contributes.
    """
    kept = []
    for event in event_list:
        owner = _voice_of(event)
        if owner is None or owner == voice:
            kept.append(event)
    return kept


class VoiceLevel:
    """One voice's contribution, measured."""

    __slots__ = ("voice", "rms", "peak", "headroom_db", "instrument")

    def __init__(self, voice, rms, peak, headroom_db=None, instrument=None):
        self.voice = voice
        self.rms = rms
        self.peak = peak
        #: dB of "louder" the patch still has before its carriers reach
        #: Total Level 0. None when the voice has no patch (PSG, DAC).
        self.headroom_db = headroom_db
        self.instrument = instrument

    @property
    def rms_db(self):
        return 20.0 * math.log10(self.rms) if self.rms > 0 else -math.inf

    @property
    def peak_db(self):
        return 20.0 * math.log10(self.peak) if self.peak > 0 else -math.inf

    @property
    def silent(self):
        return self.peak_db < SILENT_DBFS

    @property
    def crest_db(self):
        if self.rms <= 0 or self.peak <= 0:
            return 0.0
        return self.peak_db - self.rms_db


def _measure(buf):
    """RMS and peak, through audio.py's own helpers.

    Not hand-rolled: a rendered buffer is a numpy array of stereo frames
    on one backend and a list on the other, and iterating it gives rows
    that are arrays rather than tuples — `isinstance(frame, tuple)` is
    False for them, which silently measures the wrong thing. audio.rms
    and audio.peak already handle both.
    """
    import audio as _audio
    return _audio.rms(buf), _audio.peak(buf)


def _headroom_db(voice, event_list):
    """How much louder this voice's patch could still go, in dB.

    Only meaningful for the FM chips, where a patch carries its own
    carrier levels. The PSG and the DAC take their level from the score
    directly, so there is nothing held back to report.
    """
    import instruments as instruments_mod

    if not voice.startswith("fm"):
        return None, None
    channel = int(voice[2:])
    name = None
    for event in event_list:
        if (type(event).__name__ == "FMInstrumentSelect"
                and event.channel == channel):
            name = event.instrument
    if name is None:
        return None, None
    try:
        instrument = instruments_mod.get(name)
    except Exception:
        return None, name
    # headroom() is the smallest carrier Total Level, i.e. the number of
    # 0.75 dB steps between this patch and the loudest it could be — and
    # it deliberately ignores `trim`, because calibration uses it as the
    # clamp bound while trim is still zero. What matters here is what is
    # STILL unused, so the trim already applied comes off: a patch
    # imported at carrier TL 22 and then calibrated to trim -8 is holding
    # back 14 steps, not 22. Reporting the raw number made the warning
    # keep firing on a bank that had just been recalibrated.
    remaining = max(0, instrument.headroom() + instrument.trim)
    return remaining * 0.75, name


def measure(event_list, sequencer=None, voices=None):
    """Render each sounding voice alone and measure it.

    Returns [VoiceLevel], loudest peak first — because peak is what sets
    the master, so the top of this list is what everything else is
    quieter than.
    """
    from sequencer import Sequencer

    seq = sequencer or Sequencer()
    wanted = voices if voices is not None else sounding(event_list)
    out = []
    for voice in wanted:
        alone = isolate(event_list, voice)
        buf = seq.render(alone)
        rms, peak = _measure(buf)
        headroom, instrument = _headroom_db(voice, alone)
        out.append(VoiceLevel(voice, rms, peak, headroom, instrument))
    out.sort(key=lambda v: -v.peak)
    return out


def format_table(levels) -> str:
    """The report, as text. Deliberately absolute AND relative.

    Relative-to-loudest is the number that answers "why can't I hear
    it"; absolute dBFS is the one that survives being compared against
    a different render.
    """
    if not levels:
        return "no sounding voices"

    loudest = max((v.peak_db for v in levels if not v.silent),
                  default=0.0)
    lines = ["voice      patch             rms      peak    vs loudest  "
             "crest   headroom",
             "-" * 78]
    for level in levels:
        if level.silent:
            lines.append(f"{level.voice:<10} {level.instrument or '':<17} "
                         f"{'SILENT':>8}")
            continue
        patch = (level.instrument or "")[:17]
        head = (f"{level.headroom_db:5.1f} dB"
                if level.headroom_db is not None else "      —")
        lines.append(
            f"{level.voice:<10} {patch:<17} "
            f"{level.rms_db:7.1f} {level.peak_db:8.1f} "
            f"{level.peak_db - loudest:+10.1f} "
            f"{level.crest_db:6.1f} {head:>10}")
    return "\n".join(lines)


#: How far above the loudest melodic voice a percussion channel has to
#: peak before it is worth saying so. 4 dB is about where the drums stop
#: sharing the master and start owning it: normalize_peak works on peak,
#: so a voice this much above everything else takes that much gain out of
#: the rest of the mix.
PEAK_DOMINANCE_DB = 4.0

#: How far off the bank's own reference level a patch may sit before it
#: is worth saying so. This is the measurement calibration itself makes —
#: the patch playing one note against `organ` playing the same one — and
#: it is the only honest test of "is this bank levelled", because it does
#: not depend on what else happens to be in the score.
#:
#: Two heuristics were tried first and both were wrong. Unused headroom
#: alone flags the built-in bank, whose patches carry 4.5-9 dB of it on
#: purpose — that space is what lets several voices sum without clipping.
#: Headroom plus "quiet relative to the mix" then misses the case that
#: matters: an uncalibrated bank holds EVERY patch back by a similar
#: amount, so turning the drums down makes the mix look level while the
#: bank is still 12-16 dB under where it should be.
OFF_REFERENCE_DB = 3.0

#: Percussion voices: level rather than pitch, and the usual peak owners.
_PERCUSSION = frozenset({"dac", "dmc", "noise", "nesnoise"})


def warnings(measured) -> list:
    """What the numbers mean, for whoever is not going to read them.

    Both checks exist because the same diagnosis was reached by hand
    twice: the drums own the peak, and imported patches sit below the
    bank they were dropped into. Neither shows up in a render that
    "worked".
    """
    out = []
    audible = [v for v in measured if not v.silent]
    if not audible:
        return ["every voice in this score measures silent"]

    melodic = [v for v in audible if v.voice not in _PERCUSSION]
    percussion = [v for v in audible if v.voice in _PERCUSSION]

    if melodic and percussion:
        loudest_melodic = max(melodic, key=lambda v: v.peak_db)
        for hit in percussion:
            lead = hit.peak_db - loudest_melodic.peak_db
            if lead >= PEAK_DOMINANCE_DB:
                out.append(
                    f"{hit.voice} peaks {lead:.1f} dB above the loudest "
                    f"melodic voice ({loudest_melodic.voice} at "
                    f"{loudest_melodic.peak_db:.1f} dBFS) while sitting "
                    f"{hit.rms_db - loudest_melodic.rms_db:+.1f} dB from "
                    f"it in RMS — its crest factor is {hit.crest_db:.1f} dB "
                    f"against {loudest_melodic.crest_db:.1f}. Mastering "
                    f"normalises PEAK, so this takes {lead:.1f} dB of gain "
                    f"out of everything else. Bring it down with "
                    f"`vol {hit.voice} N`")

    for voice in melodic:
        offset = bank_offset_db(voice.instrument)
        if offset is None or abs(offset) < OFF_REFERENCE_DB:
            continue
        direction = "under" if offset < 0 else "over"
        remedy = (
            f"Recalibrate the bank (`python3 python/vgm_import.py "
            f"--recalibrate BANK.json`)"
            if voice.headroom_db is None or voice.headroom_db > 0
            else "The patch has no carrier headroom left, so it cannot be "
                 "levelled up — it needs rebuilding, not trimming")
        out.append(
            f"{voice.voice} plays {voice.instrument!r}, which measures "
            f"{abs(offset):.1f} dB {direction} this bank's own reference "
            f"level — it was imported without levelling. `vol` cannot fix "
            f"it: velocity attenuates DOWN from the patch's own level and "
            f"never above it. {remedy}")
    return out


def bank_offset_db(name):
    """How far one patch sits from the bank's reference, in dB.

    The same measurement calibration makes: this patch playing one note
    against `organ` playing the same one. None when the patch is not
    loaded, or when the reference itself measures silent.
    """
    if not name:
        return None
    import instruments as instruments_mod
    if name not in instruments_mod.BANK:
        return None
    import calibrate_bank as calibrator
    from sequencer import Sequencer

    seq = _reference_sequencer()
    reference = _reference_level(seq)
    if reference is None:
        return None
    level = calibrator.measure(name, seq)
    if level <= 0:
        return None
    return 20.0 * math.log10(level / reference)


_REFERENCE_CACHE = {}


def _reference_sequencer():
    if "seq" not in _REFERENCE_CACHE:
        from sequencer import Sequencer
        _REFERENCE_CACHE["seq"] = Sequencer()
    return _REFERENCE_CACHE["seq"]


def _reference_level(seq):
    """`organ` at one note, measured once and remembered.

    Cached because it is the same number for every patch in a report and
    measuring it per patch would double the cost of --levels.
    """
    if "level" not in _REFERENCE_CACHE:
        import calibrate_bank as calibrator
        level = calibrator.measure("organ", seq)
        _REFERENCE_CACHE["level"] = level if level > 0 else None
    return _REFERENCE_CACHE["level"]
