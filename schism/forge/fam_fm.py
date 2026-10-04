"""fam_fm.py — the fm family: a four-operator voice, rendered to PCM.

A Mega Drive voice is not a sample but a handful of register values (`fm.py`).
An Impulse Tracker instrument is nothing but samples, so the voice is played
at the pitch of each zone of the keyboard (one per octave, like every forged
instrument) and kept as PCM; the operator numbers stay in the recipe, in the
patch and in the sidecar, so the voice is still the voice.

A voice that holds a level while the key is down (a lead, an organ, a pad) is
a sample with a loop that joins itself; one that dies away (a pluck, a bell,
a stab) is a one-shot. Which it is comes from the numbers: an operator whose
second decay is zero holds, one whose sustain level is 15 is gone.

What the Schism rung of a voice adds to the Sega rung are *recipe* keys of
the same patch, so that nothing has to be rendered twice:

    stereo   the voice twice, a little flat and a little sharp (a loop moves
             in whole bins of its pitch, so it gets the nearest it can hold)
    post     {"drive": 2.0, "cabinet": true, "chorus": {...}}: a soft clip,
             then a small speaker cabinet, then a chorus whose swing is
             locked to the loop, so a looped voice stays looped

and `chord`, `rev` as for any forged sound (see `patch.EDITS`).
"""

from . import fm, splice, spectral
from .dna import AXES
from .family import Compiled, Family, register_family
from .fam_tone import _amp_envelope, stereo_bins
from .genome import Genome
from .patch import Patch, REFERENCE_TICK, PatchError, register_sampler

LONG_FRAMES = 32768
LONGER_FRAMES = 65536
MAX_ONE_SHOT = 2.5
#: a one-shot ends when every carrier is this far down, dB
GONE_DB = 75.0
#: a loop of a voice with nothing to beat is this many frames a cycle, at
#: least: the fall of a sideband under the band limit is what sets it
MIN_FRAMES_PER_CYCLE = 64


def _patch_of(params: dict) -> fm.FmPatch:
    try:
        return fm.FmPatch.from_dict(params["fm"])
    except fm.FmError as error:
        raise PatchError(str(error)) from None


def one_shot_seconds(patch: fm.FmPatch, note: int) -> float:
    """How long the voice sounds, key down, until its carriers are gone."""
    envs = [fm.Envelope(patch.ops[k], note, None) for k in patch.carriers()]
    t = 0.0
    while t < MAX_ONE_SHOT:
        if all(e.held(t) >= GONE_DB for e in envs) and t > 0.05:
            return min(MAX_ONE_SHOT, t + 0.03)
        t += 0.01
    return MAX_ONE_SHOT


def release_seconds(patch: fm.FmPatch, note: int) -> float:
    """Seconds for the slowest carrier's release to fall 20 dB."""
    kc = fm.key_code(note)
    worst = 0.0
    for k in patch.carriers():
        op = patch.ops[k]
        slope = fm.FULL_DB / fm.rate_seconds(
            fm.rate_index(op.rr, kc, op.ks, 1))
        worst = max(worst, 20.0 / slope if slope > 0 else 0.0)
    return max(0.02, min(worst, 8.0))


def fm_sampler(params: dict, low: int, high: int):
    """One sample serving notes `low`..`high`."""
    patch = _patch_of(params)
    base = (low + high) // 2
    f_base = spectral.hz_of_note(base)
    f_high = spectral.hz_of_note(high)
    cents = float(params.get("stereo") or 0.0)
    post = dict(params.get("post") or {})
    limit = splice.band_limit(low, high, base)
    levels = fm.steady_levels(patch, base)
    if levels is None:
        return _one_shot(patch, params, low, high, base, f_base, cents, post)
    long = bool(cents or post.get("chorus") or fm.beats(
        patch, fm.loop_cycles(patch, f_base, LONG_FRAMES, 44100.0)))
    if long:
        frames = LONGER_FRAMES if cents else LONG_FRAMES
        cycles = fm.loop_cycles(patch, f_base, frames, 44100.0)
    else:
        frames = MIN_FRAMES_PER_CYCLE
        while frames < 2.2 * spectral.BAND_LIMIT_HZ / f_high:
            frames *= 2
        cycles = fm.loop_cycles(patch, f_base, frames, f_base * frames)
        frames *= cycles
    rate = f_base * frames / float(cycles)
    intro = int((fm.settle_time(patch, levels, base) + 0.02) * rate)
    shifts = (0,)
    if cents:
        j = stereo_bins(cycles, cents)
        shifts = (-j, j)
    post["limit"] = limit
    rendered = []
    for shift in shifts:
        head, loop = fm.render_steady(patch, max(1, cycles + shift), frames,
                                      intro, rate, base, long)
        head, loop = splice.post_loop(head, loop, post, rate)
        rendered.append((head, loop))
    top = max(max(abs(v) for v in h + l) for h, l in rendered) or 1.0
    packed = [splice.pack([h + l], splice.PEAK / top)[0]
              for h, l in rendered]
    start = len(rendered[0][0])
    looped = (start, start + len(rendered[0][1]))
    return spectral.Built(packed[0], spectral.tuned_c5speed(frames, cycles),
                          looped, f"fm {low}-{high}",
                          right=packed[1] if len(packed) == 2 else None)


def _one_shot(patch, params, low, high, base, f_base, cents, post):
    rate = float(spectral.SAMPLE_RATE)
    seconds = one_shot_seconds(patch, base)
    x = fm.render_note(patch, f_base, seconds, rate, base)
    splice.fade_out(x, int(0.02 * rate))
    post = dict(post)
    post.pop("chorus", None)                 # a swing wants a loop to lock to
    post["limit"] = splice.band_limit(low, high, base)
    x = splice.post_stack(x, post, rate)
    channels = [x]
    if cents:
        channels = list(splice.double(x, cents))
    top = splice.peak(*channels) or 1.0
    packed = splice.pack(channels, splice.PEAK / top)
    c5 = int(round(rate * spectral.C5_HZ / f_base))
    return spectral.Built(packed[0], c5, None, f"fm {low}-{high}",
                          right=packed[1] if len(packed) == 2 else None)


register_sampler("fm", fm_sampler, handles=("stereo",))


class FmFamily(Family):
    name = "fm"
    doc = ("a four-operator FM voice with the registers of a YM2612, rendered "
           "to PCM at each octave: Mega Drive leads, basses, organs, pads")
    default_register = ("A", 3)
    genes: dict = {}
    supports = frozenset(AXES)
    pitched = True

    def compile(self, genome: Genome) -> Compiled:
        """The voice in the genome's style: {"fm": registers, "post": ...,
        "stereo": cents, "nna": "cut", "role": ...}."""
        style = genome.style
        if "fm" not in style:
            raise KeyError("an fm genome carries its registers in its style "
                           "(`fm`)")
        voice = fm.FmPatch.from_dict(style["fm"])
        params = {"fm": voice.to_dict()}
        for key in ("post", "stereo", "chord", "rev"):
            if style.get(key):
                params[key] = style[key]
        o = genome.register[1]
        note = splice_note(genome.register)
        t20 = release_seconds(voice, note)
        amp, sus, _ = _amp_envelope(0.0, True, None, 1.0, True, t20)
        patch = Patch(self.name, params, amp=amp, amp_sustain=sus)
        patch.inst["nna"] = style.get("nna", "cut")
        if patch.inst["nna"] == "fade":
            patch.inst["fade"] = int(max(1, min(256, round(
                2048.0 * REFERENCE_TICK / (2.0 * t20)))))
        patch.inst["gain"] = 128
        patch.inst["pan"] = {"L": 0, "R": 64, "LR": 32}[voice.pan]
        patch.bands = (max(1, 12 * (o - 2) + 1), min(120, 12 * (o + 3)), 12)
        steady = fm.is_steady(voice, note)
        notes = [f"algorithm {voice.algorithm} ({fm.ALGORITHM_DOC[voice.algorithm]}), "
                 f"feedback {voice.feedback}",
                 "operators " + ", ".join(
                     f"{n}: mul {op.mul} dt {op.dt:+d} tl {op.tl}"
                     for n, op in enumerate(voice.ops, 1)),
                 "held: a loop that joins itself" if steady
                 else "dies away: a one-shot"]
        if params.get("stereo"):
            notes.append(f"stereo, {params['stereo']} cents either side")
        return Compiled(self.name, genome.name or "fm", patch, {},
                        {"pitched": True, "release_expected": True,
                         "role": style.get("role", ""), "held": steady,
                         "pan": voice.pan}, notes)


def splice_note(register) -> int:
    from .probe import note_of
    return note_of(*register)


register_family(FmFamily())
