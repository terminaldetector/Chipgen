"""fam_drum.py — the drum family: percussion as layers of simple sound.

Kick, snare, hat, clap, tom, rim, cymbal, shaker, cowbell. The sample is a
one-shot (`layers.py`), tuned so that the family's register note plays it at
its recorded pitch: a tom is built at its pitch and the other notes of the
keyboard transpose it, as a sampler would.

Each kind is a function from a DNA and the pitch the drum is tuned to (the
register: a kick at A1, a hat at A5) to a list of layers. The dials mean
what they mean everywhere else — brightness is the spectral centroid over
the tuning, decay the fall of the amplitude — and the kind decides which
layer each one moves:

    decay        the time constant every layer's fall is a multiple of
    brightness   the high-pass and low-pass of the noise, the drive of the
                 body (a harder-driven sine has more harmonics)
    punch        the click, and how far and how fast the pitch falls
    noise        how much of the sound is noise rather than tone
    metallicity  how far the ratios of the metal layer are from harmonic
    bass_weight  the level of the low body against the rest
    attack       a ramp, for the sounds that do not start at once (shaker)
"""

from . import layers as L, spectral, tonelib as T
from .family import Compiled, Family, register_family
from .fam_tone import register_hz
from .genome import Genome
from .patch import Patch, register_sampler

#: the ratios of the 808's hat oscillators, and a harmonic set to blend to
METAL_808 = (2.0, 3.0, 4.16, 5.43, 6.79, 8.21)
METAL_PLAIN = (2.0, 3.0, 4.0, 5.0, 6.0, 7.0)


def _blend(a, b, t):
    return tuple(x * (1.0 - t) + y * t for x, y in zip(a, b))


def _clip(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


# -- the kinds ---------------------------------------------------------------------
def kick(d, f):
    tau = T.decay_seconds(d.decay)
    f_start = f * (2.0 + 6.0 * d.punch)
    sweep = 0.02 + 0.05 * d.punch
    layers = [
        {"t": "osc", "wave": "sine", "f0": f_start, "f1": f, "sweep": sweep,
         "decay": tau, "amp": 1.0, "attack": 0.0006,
         "drive": 1.0 + 3.0 * (0.25 + 0.75 * d.brightness)},
        {"t": "click", "amp": 0.15 + 0.9 * d.punch * (0.4 + d.brightness),
         "ms": 3.0, "hp": 1200.0 + 2500.0 * d.brightness}]
    knock = 0.55 * (1.0 - d.bass_weight)
    if knock > 0.02:
        layers.append({"t": "osc", "wave": "sine", "f0": 3.4 * f,
                       "f1": 2.4 * f, "sweep": 0.02, "decay": 0.04,
                       "amp": knock})
    if d.noise > 0.05:
        layers.append({"t": "noise", "amp": 0.8 * d.noise, "lp": 500.0 + 1500.0
                       * d.brightness, "decay": 0.35 * tau})
    return layers, min(1.6, 5.0 * tau + 0.06), 0.0


def snare(d, f):
    tau = T.decay_seconds(d.decay)
    centre = f * 16.0 ** d.brightness           # where the noise should sit
    ratio = 1.58 + 0.9 * d.metallicity
    body = 0.5 * f              # the dials read against the register, which
    layers = [                  # is an octave over the snare's own tone
        {"t": "osc", "wave": "sine", "f0": body * 1.25, "f1": body,
         "sweep": 0.01, "decay": 0.35 * tau,
         "amp": 0.9 * (1.0 - 0.6 * d.noise)},
        {"t": "osc", "wave": "sine", "f0": body * ratio, "f1": body * ratio,
         "decay": 0.28 * tau, "amp": 0.45 * (1.0 - 0.6 * d.noise)},
        {"t": "noise", "amp": 0.35 + 0.9 * d.noise, "hp": 0.15 * centre,
         "sweep": {"kind": "lp", "f0": min(20000.0, 0.8 * centre
                                           * 2.0 ** (2.5 * d.punch)),
                   "f1": min(16000.0, 0.8 * centre), "tau": 0.05},
         "decay": tau},
        {"t": "click", "amp": 0.15 + 0.5 * d.punch, "ms": 2.0, "hp": 2500.0}]
    if d.bass_weight > 0.45:
        layers.append({"t": "osc", "wave": "sine", "f0": body * 0.6,
                       "f1": body * 0.5, "decay": 0.6 * tau,
                       "amp": 0.8 * d.bass_weight})
    return layers, min(1.5, 5.0 * tau + 0.05), 0.0


def hat(d, f):
    tau = T.decay_seconds(d.decay)
    ratios = _blend(METAL_PLAIN, METAL_808, _clip(d.metallicity * 1.4))
    hp = 2500.0 + 6500.0 * d.brightness
    layers = [{"t": "metal", "base": f * 0.46, "ratios": ratios, "hp": hp,
               "decay": tau, "amp": 0.2 + 0.9 * d.metallicity}]
    if d.noise > 0.02:
        layers.append({"t": "noise", "amp": 0.15 + 1.1 * d.noise, "hp": hp,
                       "decay": tau})
    if d.punch > 0.05:
        layers.append({"t": "click", "amp": 0.5 * d.punch, "ms": 1.5,
                       "hp": 6000.0})
    return layers, min(2.0, 6.0 * tau + 0.03), 0.0


def clap(d, f):
    tau = T.decay_seconds(d.decay)
    gap = 0.011 * (1.2 - 0.4 * d.punch)
    bursts = [(0.0, 0.004, 1.0), (gap, 0.004, 0.9), (2 * gap, 0.004, 0.8),
              (3 * gap, 0.4 * tau, 1.0)]
    layers = [{"t": "noise", "amp": 0.6 + 0.6 * d.noise, "bursts": bursts,
               "hp": 600.0 + 600.0 * d.brightness,
               "lp": 1800.0 + 5500.0 * d.brightness,
               "dur": 3 * gap + 5.0 * 0.4 * tau}]
    if d.bass_weight > 0.2:
        layers.append({"t": "osc", "wave": "sine", "f0": f, "f1": f * 0.9,
                       "decay": 0.1 * tau + 0.01, "amp": d.bass_weight})
    return layers, 3 * gap + 5.0 * 0.4 * tau + 0.03, 0.0


def tom(d, f):
    tau = T.decay_seconds(d.decay)
    layers = [
        {"t": "osc", "wave": "sine", "f0": f * (1.0 + 0.7 * d.punch),
         "f1": f * 0.72, "sweep": 0.05, "decay": tau, "amp": 1.0,
         "drive": 0.5 + 2.0 * d.brightness},
        {"t": "click", "amp": 0.1 + 0.6 * d.punch, "ms": 2.5, "hp": 1500.0}]
    if d.noise > 0.05:
        layers.append({"t": "noise", "amp": 0.5 * d.noise, "bp": [f * 2.5, 1.0],
                       "decay": 0.3 * tau})
    return layers, min(1.5, 5.0 * tau + 0.05), 0.0


def rim(d, f):
    tau = 0.25 * T.decay_seconds(d.decay)
    ratio_b = 2.2 + 3.0 * d.metallicity
    layers = [
        {"t": "osc", "wave": "sine", "f0": f * ratio_b, "f1": f * ratio_b,
         "decay": tau, "amp": 1.0, "attack": 0.0001},
        {"t": "osc", "wave": "sine", "f0": f * 1.1, "f1": f * 1.1,
         "decay": tau * 1.6, "amp": 0.7 * (0.4 + d.bass_weight)},
        {"t": "click", "amp": 0.3 + 1.0 * d.punch, "ms": 1.0, "hp": 3000.0}]
    return layers, min(0.8, 7.0 * tau * 1.6 + 0.01), 0.0


def cymbal(d, f):
    tau = T.decay_seconds(d.decay)
    ratios = _blend((2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0),
                    (1.0, 1.4, 2.0, 3.16, 4.18, 5.43, 6.79, 8.21),
                    _clip(d.metallicity * 1.4))
    hp = 2500.0 + 5000.0 * d.brightness
    layers = [{"t": "metal", "base": f * 0.4, "ratios": ratios, "hp": hp,
               "decay": tau, "amp": 0.2 + 0.8 * d.metallicity,
               "attack": 0.001},
              {"t": "noise", "amp": 0.2 + 1.0 * d.noise, "hp": hp,
               "decay": tau, "attack": 0.001}]
    if d.punch > 0.1:
        layers.append({"t": "click", "amp": 0.6 * d.punch, "ms": 2.0,
                       "hp": 5000.0})
    return layers, min(3.0, 5.0 * tau + 0.05), 0.0


def shaker(d, f):
    tau = T.decay_seconds(d.decay)
    rise = T.attack_seconds(d.attack) / 0.8
    layers = [{"t": "noise", "amp": 1.0,
               "bp": [3000.0 + 6000.0 * d.brightness, 0.9], "hp": 2000.0,
               "decay": tau, "attack": min(0.08, max(0.003, rise))}]
    return layers, min(1.0, 5.0 * tau + rise + 0.02), 0.0


def cowbell(d, f):
    tau = T.decay_seconds(d.decay)
    ratio = 1.48 + 0.3 * d.metallicity
    layers = [{"t": "metal", "base": f, "ratios": (1.0, ratio),
               "bp": [f * (1.5 + 1.5 * d.brightness), 1.2],
               "decay": tau, "amp": 1.0, "attack": 0.0002},
              {"t": "click", "amp": 0.3 * d.punch, "ms": 1.5, "hp": 2000.0}]
    return layers, min(1.5, 5.0 * tau + 0.03), 0.0


KINDS = {
    "kick": (kick, ("A", 1), "a sine falling from a click"),
    "snare": (snare, ("G", 4), "tones and high-passed noise"),
    "hat": (hat, ("A", 5), "metal squares and noise; the decay opens it"),
    "clap": (clap, ("D", 4), "bursts of band-passed noise and a tail"),
    "tom": (tom, ("A", 2), "a sine falling gently, tuned"),
    "rim": (rim, ("A", 4), "two short tones and a click"),
    "cymbal": (cymbal, ("A", 5), "a wash of metal and noise"),
    "shaker": (shaker, ("A", 5), "band-passed noise with a soft start"),
    "cowbell": (cowbell, ("G", 4), "two squares through a band-pass"),
}


# -- the family -----------------------------------------------------------------------
class LayerFamily(Family):
    """Shared by the drum and fx families: a kind chooses a function from
    DNA to layers."""
    pitched = False
    kinds = KINDS

    def kind_of(self, genome: Genome) -> str:
        kind = genome.style.get("kind")
        if kind not in self.kinds:
            raise KeyError(f"{self.name} has no kind {kind!r}; the kinds "
                           f"are {', '.join(sorted(self.kinds))}")
        return kind

    def pin(self, genome: Genome) -> Genome:
        style = dict(genome.style)
        if style.get("kind") not in self.kinds:
            style["kind"] = self.default_kind
        return genome.clone(style=style)

    def compile(self, genome: Genome) -> Compiled:
        g = self.pin(genome)
        d = g.effective()
        kind = self.kind_of(g)
        build, default_register, _ = self.kinds[kind]
        register = g.register if g.register else default_register
        from .probe import note_of
        reg_note = note_of(*register)
        f = register_hz(register)
        layer_list, length, drive = build(d, f)
        params = {"layers": _rounded(layer_list), "length": round(length, 3),
                  "drive": drive, "reg_note": reg_note, "seed": 1}
        if g.style.get("dac"):          # played through a console's DAC
            params["dac"] = dict(g.style["dac"])
        patch = Patch(self.name, params)
        patch.inst["nna"] = "cut"
        patch.inst["gain"] = 128
        patch.bands = (1, 120, 120)
        notes = [f"{kind} tuned to {f:.0f} Hz: "
                 + ", ".join(l["t"] for l in layer_list)
                 + f", {length * 1000:.0f} ms"]
        return Compiled(self.name, g.name or kind, patch, {},
                        {"pitched": False, "release_expected": False,
                         "kind": kind}, notes)


def _rounded(layers):
    out = []
    for layer in layers:
        item = {}
        for k, v in layer.items():
            if isinstance(v, float):
                item[k] = round(v, 5)
            elif isinstance(v, (list, tuple)):
                item[k] = [round(x, 5) if isinstance(x, float) else
                           (list(x) if isinstance(x, tuple) else x)
                           for x in v]
            else:
                item[k] = v
        out.append(item)
    return out


class DrumFamily(LayerFamily):
    name = "drum"
    doc = ("percussion as layers of sine, noise and metal: kick, snare, hat, "
           "clap, tom, rim, cymbal, shaker, cowbell")
    default_register = ("A", 1)
    default_kind = "kick"
    genes = {"kind": sorted(KINDS)}
    supports = frozenset(("brightness", "attack", "body", "decay",
                          "metallicity", "bass_weight", "noise", "punch"))


def layer_sampler(params: dict, low: int, high: int):
    x = L.render(params["layers"], params["length"], L.RATE,
                 params.get("seed", 1), params.get("drive", 0.0))
    if params.get("dac"):
        from . import splice
        x = splice.crush(x, float(params["dac"]["rate"]),
                         int(params["dac"].get("bits", 8)))
    # the register note plays the data unshifted
    c5 = int(round(L.RATE * 2.0 ** ((61 - params["reg_note"]) / 12.0)))
    return spectral.Built(spectral.to_int16(x, 0.92), c5, None,
                          f"layers {low}-{high}")


register_sampler("drum", layer_sampler)
register_sampler("fx", layer_sampler)
register_family(DrumFamily())
