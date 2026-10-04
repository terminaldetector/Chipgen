"""validate.py — is this a usable instrument, whatever it scores?

Scoring says how close a sound is to what was asked for. Validation says
whether it is a sound at all: audible, not clipping, on the pitch it was
asked to play, not ringing on forever after the key is released, and a
module the file format can hold. A candidate with an error is thrown away
before it is scored; a warning is kept and travels with the instrument into
the bank, so whoever picks it later knows what it is.

Every check names the fix.
"""

import math
from dataclasses import dataclass

#: Quieter than this at full velocity cannot carry a part (-40 dBFS).
AUDIBLE_PEAK = 0.004
#: Louder than this will clip once mixed with anything.
CLIP_PEAK = 0.98
#: A patch whose samples run to more than this many bytes is heavy; past
#: the second it cannot be what anyone wanted in a tracker module.
HEAVY_BYTES = 900_000
ABSURD_BYTES = 6_000_000


@dataclass(frozen=True)
class Issue:
    code: str
    level: str          # "error" | "warn"
    message: str
    fix: str = ""

    def __str__(self):
        text = f"{self.level}: {self.message}"
        return f"{text} — {self.fix}" if self.fix else text


def check(measurement, dna=None, requested_hz=None, rendered_ok: bool = True,
          pitched: bool = True, release_expected: bool = True):
    """-> [Issue] for one measured note. Empty means usable."""
    issues = []
    m = measurement
    if not rendered_ok or not m.measured:
        issues.append(Issue("silent", "error",
                            "the note renders as silence",
                            "raise the gain, or check that the notes asked "
                            "for have a sample (oct=)"))
        return issues
    peak = m.raw.get("peak", 0.0)
    if peak < AUDIBLE_PEAK:
        issues.append(Issue("quiet", "error",
                            f"peak {20 * _log10(peak):.0f} dBFS is under "
                            f"the audible floor",
                            "raise the instrument gain"))
    if peak >= CLIP_PEAK:
        issues.append(Issue("clips", "warn",
                            f"peak {peak:.2f} will clip in a mix",
                            "lower the instrument gain"))
    if not pitched:
        pass                    # a drum has no pitch to be wrong about
    elif m.pitch == "off":
        issues.append(Issue("pitch", "error",
                            "no fundamental or octave at the requested pitch",
                            "keep a partial at ratio 1.0"))
    elif m.pitch == "octave_up":
        issues.append(Issue("octave_up", "warn",
                            "sounds an octave above the note asked for",
                            "give the fundamental more weight"))
    elif m.pitch == "sub_octave" and (dna is None or dna.bass_weight < 0.5):
        issues.append(Issue("octave_down", "warn",
                            "sounds an octave below the note asked for",
                            "lower the sub partial or raise the fundamental"))
    release = m.raw.get("release_s")
    articulation = m.axes.get("articulation")
    if release_expected and release is not None and articulation is not None \
            and release >= 0.45 and dna is not None and dna.articulation < 0.35:
        issues.append(Issue("rings", "warn",
                            f"rings {release * 1000:.0f} ms after note-off "
                            f"though it was meant to be gated",
                            "shorten the volume envelope's release or raise "
                            "the fadeout"))
    if m.raw.get("sustain_db", 0.0) <= -35 and m.raw.get("decay_s") is not None \
            and m.raw["decay_s"] < 0.012 and pitched:
        issues.append(Issue("click", "warn",
                            "dies in under 12 ms, which reads as a click",
                            "lengthen the decay"))
    return issues


def check_patch(patch, built=()):
    """Checks on the instrument itself, not on a note played from it.
    `built` is [(low, high, Built)] if the samples are at hand."""
    issues = []
    for problem in patch.problems():
        issues.append(Issue("illegal", "error", problem,
                            "see docs/FORGE.md for the limits"))
    total = 0
    for low, high, b in built:
        total += 2 * len(b.data) * getattr(b, "channels", 1)
        data = b.data
        n = len(data)
        if n < 2:
            issues.append(Issue("empty", "error", "a sample holds no data"))
            continue
        top = max(abs(v) for v in data) or 1
        if b.loop is not None:
            start, end = b.loop
            # the player reads data[end-1] then data[start]; for a smooth
            # waveform that step is no bigger than its neighbours'
            step = abs(data[start] - data[end - 1])
            steepest = max(abs(data[i + 1] - data[i])
                           for i in range(start, end - 1))
            if step > 1.3 * steepest + 0.01 * top:
                issues.append(Issue(
                    "loop_click", "warn",
                    f"the loop of the sample for notes {low}-{high} does not "
                    f"join: a step of {100 * step / top:.0f}% of full scale "
                    f"at the loop point", "make the loop hold whole cycles"))
        else:
            tail = max(abs(v) for v in data[-16:]) / top
            if tail > 0.03:
                issues.append(Issue(
                    "end_click", "warn",
                    f"the sample for notes {low}-{high} ends at "
                    f"{100 * tail:.0f}% of full scale, which clicks",
                    "fade the end"))
        mean = sum(data) / n
        if abs(mean) > 0.02 * top:
            issues.append(Issue("dc", "warn",
                                f"the sample for notes {low}-{high} has a DC "
                                f"offset of {100 * mean / top:.0f}%",
                                "remove the offset"))
    if total > ABSURD_BYTES:
        issues.append(Issue("size", "error",
                            f"{total / 1e6:.1f} MB of sample data",
                            "narrow the note range or lengthen notes_per_sample"))
    elif total > HEAVY_BYTES:
        issues.append(Issue("heavy", "warn",
                            f"{total / 1e6:.1f} MB of sample data",
                            "narrow the note range (oct=) or use fewer "
                            "samples (notes_per_sample)"))
    return issues


def errors(issues):
    return [i for i in issues if i.level == "error"]


def _log10(x):
    return math.log10(max(x, 1e-9))
