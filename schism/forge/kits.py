"""kits.py — a drum kit that holds together.

A kit is not a list of good drums; it is drums that leave room for each
other. Two kicks that are both good are one kick too many; a snare and a
clap on the same band, a hat that sits where the snare's noise does, a tom
that is the kick again — each pair measures fine alone and muddies the
groove together.

`forge_kit` makes one in two steps. Each drum is searched on its own, from an
archetype moved by the style (a tight kit falls faster, an 808 kit is long and
low and its hats ring, a lo-fi kit is dark and noisy), and the few best of
each are kept. Then the kit is chosen from them by the same measure the
arrangement model uses for parts: a drum is added to the kit if it is good
and if its spectrum, set against the drums already chosen, shares little of
the same bands. The result is a bank of drums, one name for the kit, and the
keys they sit on.
"""

import math
from typing import Dict, List, Optional

from . import archetypes, evolve, score as score_mod
from .arrangement import BAND_WEIGHT, BANDS, _SEMI_TO_BARK
from .bank import Bank
from .family import get_family
from .genome import Genome, slug

#: role -> (archetype, key). The keys follow General MIDI's layout, one
#: octave down (an IT instrument's C-2 is note 25).
ROLES = [
    ("kick", "kick", "C-2"), ("snare", "snare", "D-2"),
    ("clap", "clap", "D#2"), ("hat", "hat", "F#2"),
    ("open_hat", "open_hat", "A#2"), ("tom_low", "tom", "F-2"),
    ("tom_high", "tom", "A-2"), ("rim", "rim", "C#2"),
    ("cymbal", "cymbal", "C#3"), ("shaker", "shaker", "G#2"),
    ("cowbell", "cowbell", "G#3"),
]
#: tom pitches, as registers
TOM_REGISTERS = {"tom_low": ("F", 2), "tom_high": ("C", 3)}

#: style -> {role or "all": {axis: delta}}
STYLES: Dict[str, Dict[str, Dict[str, float]]] = {
    "tight": {"all": {"decay": -0.12, "punch": +0.1},
              "hat": {"decay": -0.05}},
    "808": {"all": {},
            "kick": {"decay": +0.25, "bass_weight": +0.05, "brightness": -0.05,
                     "punch": -0.2},
            "snare": {"noise": -0.2, "metallicity": +0.15, "decay": +0.05},
            "hat": {"metallicity": +0.2, "brightness": +0.05},
            "clap": {"decay": +0.1}, "tom_low": {"decay": +0.15},
            "tom_high": {"decay": +0.1}, "cowbell": {"decay": +0.1}},
    "dark": {"all": {"brightness": -0.2}, "hat": {"brightness": -0.1},
             "kick": {"bass_weight": +0.03}},
    "bright": {"all": {"brightness": +0.12, "punch": +0.1}},
    "lofi": {"all": {"brightness": -0.25, "noise": +0.15, "decay": +0.05},
             "kick": {"punch": -0.2}},
    "big": {"all": {"decay": +0.12, "brightness": -0.1},
            "kick": {"bass_weight": +0.04, "decay": +0.1}},
}


def style_names():
    return sorted(STYLES)


def _spectrum_bands(measured_semitones, register_note: int, probe_note: int):
    """24 Bark bands of a drum's spectrum (the probe's semitone bands,
    already at the pitch the drum plays)."""
    out = [0.0] * BANDS
    for s, v in enumerate(measured_semitones):
        out[_SEMI_TO_BARK[s]] += v
    return out


def spectral_overlap(a, b) -> float:
    """Cosine similarity of two Bark-band vectors, audibility-weighted."""
    num = sum(math.sqrt(x * y) * w for x, y, w in zip(a, b, BAND_WEIGHT))
    ta, tb = sum(a), sum(b)
    return min(1.0, num / math.sqrt(ta * tb)) if ta > 0 and tb > 0 else 0.0


class KitMember:
    def __init__(self, role, key, entry, bands):
        self.role, self.key, self.entry, self.bands = role, key, entry, bands


def forge_kit(name: str = "kit", style: str = "tight", seed: int = 0,
              roles: Optional[List[str]] = None, generations: int = 2,
              offspring: int = 5, keep: int = 3, overlap_weight: float = 0.5,
              progress=None) -> Bank:
    """-> a Bank of drums and one kit (bank.kits) that names them."""
    if style not in STYLES:
        raise KeyError(f"no kit style {style!r}; have {', '.join(STYLES)}")
    wanted = [r for r in ROLES if roles is None or r[0] in roles]
    if not wanted:
        raise ValueError(f"no such roles; the roles are "
                         f"{', '.join(r[0] for r in ROLES)}")
    family = get_family("drum")
    moves = STYLES[style]
    pool: Dict[str, List[evolve.Candidate]] = {}
    for index, (role, arch_name, key) in enumerate(wanted):
        arch = archetypes.get(arch_name)
        deltas = dict(moves.get("all", {}))
        deltas.update(moves.get(role, {}))
        dna = arch.dna.shifted(deltas)
        register = TOM_REGISTERS.get(role, arch.register)
        genome = Genome("drum", dna, style=dict(arch.style), name=role,
                        register=register, lineage=[f"kit:{style}:{role}"])
        forge = evolve.Forge(family, score_mod.Objective(dna),
                             seed=seed * 101 + index, archive_size=6,
                             min_distance=0.06, register=register,
                             realise_probes=2)
        forge.seed([genome])
        forge.run(generations=generations, offspring=offspring, sigma=0.12)
        pool[role] = forge.finalize(top=keep)
        if progress:
            progress(f"{role}: {len(pool[role])} candidates, best "
                     f"{pool[role][0].score:.2f}" if pool[role] else role)
    chosen: List[KitMember] = []
    bank = Bank(name, scenario={"kit": style, "seed": seed})
    for role, arch_name, key in wanted:
        best, best_value = None, -1e9
        for cand in pool[role]:
            bands = _spectrum_bands(cand.result.measurement.semitones, 0, 0)
            worst = max((spectral_overlap(bands, m.bands) for m in chosen
                         if m.role.split("_")[0] != role.split("_")[0]),
                        default=0.0)
            value = cand.score - overlap_weight * worst
            if value > best_value:
                best, best_value, best_bands = cand, value, bands
        entry = bank.add_candidate(best, f"{slug(name)}_{role}", role="drums")
        chosen.append(KitMember(role, key, entry, best_bands))
    bank.kits.append({"name": slug(name), "style": style,
                      "keys": [[m.key, m.entry.name] for m in chosen]})
    return bank
