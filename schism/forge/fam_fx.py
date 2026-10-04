"""fam_fx.py — the fx family: one-shot effects as layers of sound.

Risers, impacts, zaps, swooshes: the things a track puts between its
sections. Built the same way as the drums (`layers.py`), and judged by the
same dials — an impact is bass-heavy and punchy, a riser is noisy and slow
to start, a zap is a short bright fall — but each kind has a different idea
of what the dials are for:

    riser   attack is the length of the build; brightness is how far the
            filter opens; decay is a short tail after the cut
    impact  a falling sine and a burst of noise; decay is the boom
    zap     a fast fall in pitch; punch is how far it falls
    swoosh  a pass of band-passed noise that rises and falls
"""

from . import tonelib as T
from .family import register_family
from .fam_drum import LayerFamily, _clip


def riser(d, f):
    build = T.attack_seconds(d.attack) / 0.8 * 1.6
    build = _clip(build, 0.4, 4.0)
    tail = T.decay_seconds(d.decay)
    top = f * 16.0 ** d.brightness
    layers = [{"t": "noise", "amp": 1.0, "decay": 1000.0, "dur": build,
               "shape": "ramp", "power": 1.6,
               "sweep": {"kind": "lp", "f0": max(120.0, 0.3 * f),
                         "f1": min(16000.0, top * 1.6), "tau": 1.0,
                         "linear": True, "q": 0.8 + 3.0 * d.metallicity}}]
    if d.bass_weight > 0.15:
        layers.append({"t": "osc", "wave": "saw", "f0": f * 0.5, "f1": f * 1.5,
                       "sweep": build * 0.7, "decay": 1000.0, "dur": build,
                       "amp": 0.5 * d.bass_weight, "attack": build * 0.5})
    if tail > 0.06:
        layers.append({"t": "noise", "amp": 0.5, "at": build, "dur": tail * 2,
                       "hp": 2000.0, "decay": tail * 0.5})
    return layers, build + (tail * 2 if tail > 0.06 else 0.05) + 0.02, 0.0


def impact(d, f):
    tau = T.decay_seconds(d.decay)
    layers = [
        {"t": "osc", "wave": "sine", "f0": f * (3.0 + 4.0 * d.punch),
         "f1": f, "sweep": 0.12, "decay": tau, "amp": 1.0, "attack": 0.001,
         "drive": 0.8 + 2.0 * d.brightness},
        {"t": "noise", "amp": 0.3 + 1.0 * d.noise,
         "lp": 300.0 + 6000.0 * d.brightness, "decay": 0.3 * tau},
        {"t": "click", "amp": 0.8 * d.punch, "ms": 4.0, "hp": 800.0}]
    return layers, min(4.0, 5.0 * tau + 0.1), 0.0


def zap(d, f):
    tau = T.decay_seconds(d.decay)
    start = f * (4.0 + 12.0 * d.punch)
    layers = [
        {"t": "osc", "wave": "saw" if d.brightness > 0.5 else "square",
         "f0": start, "f1": f * 0.6, "sweep": 0.05 + 0.1 * (1 - d.punch),
         "decay": tau, "amp": 1.0, "attack": 0.0005},
        {"t": "osc", "wave": "sine", "f0": start * (1.5 + d.metallicity),
         "f1": f * 0.9, "sweep": 0.06, "decay": tau * 0.6,
         "amp": 0.3 + 0.8 * d.metallicity}]
    if d.noise > 0.05:
        layers.append({"t": "noise", "amp": d.noise * 0.8, "hp": 2000.0,
                       "decay": tau * 0.3})
    return layers, min(2.0, 5.0 * tau + 0.05), 0.0


def swoosh(d, f):
    rise = T.attack_seconds(d.attack) / 0.8
    tau = T.decay_seconds(d.decay)
    length = _clip(rise + 4.0 * tau, 0.25, 3.0)
    top = min(14000.0, f * 16.0 ** d.brightness)
    layers = [{"t": "noise", "amp": 1.0, "decay": 1000.0, "dur": length,
               "shape": "hump",
               "sweep": {"kind": "bp", "f0": max(200.0, 0.2 * top),
                         "f1": top, "tau": 1.0, "linear": True, "q": 0.9}}]
    return layers, length + 0.02, 0.0


KINDS = {
    "riser": (riser, ("A", 3), "noise through a filter that opens"),
    "impact": (impact, ("A", 1), "a falling sine and a burst of noise"),
    "zap": (zap, ("A", 4), "a fast fall in pitch"),
    "swoosh": (swoosh, ("A", 3), "a pass of band-passed noise"),
}


class FxFamily(LayerFamily):
    name = "fx"
    doc = "one-shot effects: riser, impact, zap, swoosh"
    default_register = ("A", 3)
    default_kind = "impact"
    kinds = KINDS
    genes = {"kind": sorted(KINDS)}
    supports = frozenset(("brightness", "attack", "body", "decay",
                          "metallicity", "bass_weight", "noise", "punch"))
    quick_hold = 0.84
    full_hold = 2.4
    tail = 1.2


register_family(FxFamily())
