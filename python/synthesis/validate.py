"""validate.py — is this a usable instrument, whatever it scores?

Scoring says how close a sound is to what was asked for. Validation says
whether it is a sound at all: audible, not clipping, on the pitch it was
asked to play, and not ringing on forever after the key is released. A
candidate with an error is thrown away before it is scored; a warning is
kept and travels with the instrument into the bank, so whoever picks it
later knows what it is.

Every check names the fix, in the same words the rest of chipgen uses.
"""

from dataclasses import dataclass

#: Quieter than this at full velocity cannot carry a part (-40 dBFS), the
#: same line audition.py draws.
AUDIBLE_PEAK = 0.01
#: Louder than this will clip once mixed with anything.
CLIP_PEAK = 0.98


@dataclass(frozen=True)
class Issue:
    code: str
    level: str          # "error" | "warn"
    message: str
    fix: str = ""

    def __str__(self):
        text = f"{self.level}: {self.message}"
        return f"{text} — {self.fix}" if self.fix else text


def check(measurement, backend=None, dna=None, requested_hz=None,
          rendered_ok: bool = True, pitched: bool = True):
    """-> [Issue]. Empty means a usable instrument."""
    issues = []
    m = measurement
    if not rendered_ok or not m.measured:
        issues.append(Issue("silent", "error",
                            "the note renders as silence",
                            "raise a carrier's level, or choose a structure "
                            "with more carriers"))
        return issues
    peak = m.raw.get("peak", 0.0)
    if peak < AUDIBLE_PEAK:
        issues.append(Issue("quiet", "error",
                            f"peak {20 * _log10(peak):.0f} dBFS is under "
                            f"the audible floor",
                            "raise the carrier level"))
    if peak >= CLIP_PEAK:
        issues.append(Issue("clips", "warn",
                            f"peak {peak:.2f} will clip in a mix",
                            "trim the carriers by a few steps"))
    if not pitched:
        pass                    # noise has no pitch to be wrong about
    elif m.pitch == "off":
        issues.append(Issue("pitch", "error",
                            "no fundamental or octave at the requested pitch",
                            "keep one carrier at multiple 1"))
    elif pitched and m.pitch == "octave_up":
        issues.append(Issue("octave_up", "warn",
                            "sounds an octave above the note asked for",
                            "give a carrier multiple 1 (or 0 for a sub)"))
    elif pitched and m.pitch == "sub_octave" and (
            dna is None or dna.bass_weight < 0.5):
        issues.append(Issue("octave_down", "warn",
                            "sounds an octave below the note asked for",
                            "lower the sub carrier or raise the fundamental"))
    release = m.raw.get("release_s")
    articulation = m.axes.get("articulation")
    if release is not None and articulation is not None and release >= 0.45 \
            and dna is not None and dna.articulation < 0.35:
        issues.append(Issue("rings", "warn",
                            f"rings {release * 1000:.0f} ms after note-off "
                            f"though it was meant to be gated",
                            "raise the release rate"))
    if m.raw.get("sustain_db", 0.0) <= -35 and m.raw.get("decay_s") is not None \
            and m.raw["decay_s"] < 0.012:
        issues.append(Issue("click", "warn",
                            "dies in under 12 ms, which reads as a click",
                            "lengthen the decay"))
    return issues


def errors(issues):
    return [i for i in issues if i.level == "error"]


def _log10(x):
    import math
    return math.log10(max(x, 1e-9))
