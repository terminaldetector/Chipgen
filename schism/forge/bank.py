"""bank.py — instruments as files, and scenarios as files that make them.

A bank is JSON. It holds, for each instrument, the genome that made it, the
patch it compiled to (a recipe for its samples, its envelopes and switches:
never PCM), what the probe read off it and why it was kept — enough to
install it, to explain it, and to seed the next run. Nothing in a bank is
code, so a model (or a person) can add a family of sounds by writing a
file, and a bank of fifty instruments is a few hundred kilobytes.

A scenario is the other half: a JSON description of a run (which family,
what to start from, what to aim for, how much to spend). Both formats are
versioned and validated with messages that name the field.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import archetypes, evolve, score as score_mod
from .dna import AXES, DNA, DNAError, _canonical
from .family import Compiled, get_family
from .genome import Genome, slug

BANK_FORMAT = "schism-forge-bank/1"
SCENARIO_FORMAT = "schism-forge-scenario/1"


class BankError(ValueError):
    """A bank or scenario file that cannot be used, with the field named."""


@dataclass
class Entry:
    name: str
    family: str
    genome: Genome
    compiled: Compiled
    measured: Dict[str, Optional[float]]
    score: float = 0.0
    role: str = ""
    character: str = ""
    issues: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        family = get_family(self.family)
        return {"name": self.name, "family": self.family, "role": self.role,
                "character": self.character, "score": round(self.score, 4),
                "genome": self.genome.to_dict(),
                "compiled": family.to_dict(self.compiled),
                "measured": {k: (None if v is None else round(v, 3))
                             for k, v in self.measured.items()},
                "issues": list(self.issues)}

    @classmethod
    def from_dict(cls, data: dict) -> "Entry":
        try:
            family = get_family(data["family"])
            genome = Genome.from_dict(data["genome"])
            compiled = family.from_dict(data["compiled"])
            compiled.name = data["name"]
            problems = family.validate(compiled)
            if problems:
                raise BankError(
                    f"{data['name']} ({family.name}) is not a legal "
                    f"instrument: {'; '.join(problems[:3])}"
                    + (f" (and {len(problems) - 3} more)"
                       if len(problems) > 3 else ""))
            return cls(data["name"], family.name, genome, compiled,
                       dict(data.get("measured", {})),
                       float(data.get("score", 0.0)), data.get("role", ""),
                       data.get("character", ""), list(data.get("issues", [])))
        except KeyError as error:
            raise BankError(f"an instrument entry needs {error.args[0]!r}") \
                from None


class Bank:
    def __init__(self, name: str = "forge", entries=None, scenario=None,
                 kits=None):
        self.name = name
        self.entries: List[Entry] = list(entries or [])
        self.scenario = scenario
        #: [{"name", "style", "keys": [[key, instrument name], ...]}]
        self.kits: List[dict] = list(kits or [])

    def names(self):
        return [e.name for e in self.entries]

    def get(self, name: str) -> Entry:
        for e in self.entries:
            if e.name == name:
                return e
        raise KeyError(f"no instrument {name!r} in bank {self.name!r}; have: "
                       f"{', '.join(self.names()) or 'none'}")

    def add(self, entry: Entry) -> Entry:
        taken = set(self.names())
        base, n = entry.name, 2
        while entry.name in taken:
            entry.name = f"{base}_{n}"
            n += 1
        entry.compiled.name = entry.name
        self.entries.append(entry)
        return entry

    def add_candidate(self, cand: evolve.Candidate, name: str,
                      role: str = "") -> Entry:
        entry = Entry(
            name=slug(name), family=cand.genome.family, genome=cand.genome,
            compiled=cand.compiled, measured=dict(cand.measured),
            score=cand.score, role=role,
            character=cand.genome.dna.describe(threshold=0.22),
            issues=[str(i) for i in cand.result.issues])
        entry.genome = entry.genome.clone(name=entry.name)
        return self.add(entry)

    # -- files ---------------------------------------------------------------
    def to_dict(self) -> dict:
        data = {"format": BANK_FORMAT, "name": self.name,
                "scenario": self.scenario,
                "instruments": [e.to_dict() for e in self.entries]}
        if self.kits:
            data["kits"] = self.kits
        return data

    def save(self, path: str) -> str:
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=1)
        return path

    @classmethod
    def load(cls, path: str) -> "Bank":
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            raise BankError(f"no bank file at {path}") from None
        except json.JSONDecodeError as error:
            raise BankError(f"{path} is not JSON: {error}") from None
        if not isinstance(data, dict) or data.get("format") != BANK_FORMAT:
            raise BankError(f"{path}: not a forge bank (format "
                            f"{data.get('format') if isinstance(data, dict) else None!r}, "
                            f"want {BANK_FORMAT!r})")
        bank = cls(data.get("name", "forge"), scenario=data.get("scenario"))
        for raw in data.get("instruments", []):
            bank.entries.append(Entry.from_dict(raw))
        for kit in data.get("kits", []):
            names = {e.name for e in bank.entries}
            for key, member in kit.get("keys", []):
                if member not in names:
                    raise BankError(f"kit {kit.get('name')!r} puts "
                                    f"{member!r} on {key}, which is not an "
                                    f"instrument of the bank")
            bank.kits.append(kit)
        return bank

    def kit(self, name: str) -> dict:
        for kit in self.kits:
            if kit["name"] == name:
                return kit
        raise KeyError(f"no kit {name!r} in bank {self.name!r}; have: "
                       f"{', '.join(k['name'] for k in self.kits) or 'none'}")

    def table(self) -> str:
        lines = [f"{'name':24s} {'family':6s} {'score':>5s}  "
                 + " ".join(f"{a[:5]:>5s}" for a in AXES)]
        for e in self.entries:
            row = " ".join(("%5.2f" % e.measured[a]) if e.measured.get(a)
                           is not None else "    -" for a in AXES)
            lines.append(f"{e.name:24.24s} {e.family:6s} {e.score:5.2f}  {row}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------
SCENARIO_KEYS = {
    "name", "format", "family", "role", "seeds", "objective", "register",
    "generations", "offspring", "archive", "min_distance", "seed",
    "max_probes", "seconds", "locked", "keep", "prefix", "novelty", "mix",
    "realise_probes", "sigma", "note", "kind"}


def _validate_scenario(data: dict):
    if not isinstance(data, dict):
        raise BankError("a scenario is a JSON object")
    unknown = sorted(set(data) - SCENARIO_KEYS)
    if unknown:
        raise BankError(f"unknown scenario field(s): {', '.join(unknown)}; "
                        f"the fields are {', '.join(sorted(SCENARIO_KEYS))}")
    if "family" not in data:
        raise BankError("a scenario needs `family` (tone, modal, drum or fx)")
    if data.get("format", SCENARIO_FORMAT) != SCENARIO_FORMAT:
        raise BankError(f"scenario format {data['format']!r}; this reads "
                        f"{SCENARIO_FORMAT!r}")
    try:
        get_family(data["family"])
    except KeyError as error:
        raise BankError(str(error.args[0])) from None


def load_scenario(path_or_dict) -> dict:
    if isinstance(path_or_dict, dict):
        data = path_or_dict
    else:
        try:
            with open(path_or_dict, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            raise BankError(f"no scenario file at {path_or_dict}") from None
        except json.JSONDecodeError as error:
            raise BankError(f"{path_or_dict} is not JSON: {error}") from None
    _validate_scenario(data)
    return data


def seeds_of(family, scenario: dict, register=None) -> List[Genome]:
    """Turn the `seeds` list into genomes."""
    out = []
    specs = scenario.get("seeds") or []
    role = scenario.get("role")
    if not specs and role:
        specs = [n for n in archetypes.for_role(role)
                 if archetypes.get(n).family == family.name]
    if not specs:
        specs = [n for n in archetypes.for_family(family.name)][:1] \
            or ["saw_lead"]
    for spec in specs:
        if isinstance(spec, str):
            spec = {"archetype": spec}
        if "bank" in spec:
            for entry in Bank.load(spec["bank"]).entries:
                if entry.family == family.name:
                    out.append(entry.genome.clone(name=""))
            continue
        if "recipe" in spec:
            from . import mixing
            out.append(mixing.lift_recipe(spec["recipe"], family.name))
            continue
        style = dict(spec.get("style", {}))
        if scenario.get("kind") and "kind" not in style:
            style["kind"] = scenario["kind"]
        reg = tuple(spec.get("register", register or ())) or None
        if "archetype" in spec:
            arch = archetypes.get(spec["archetype"])
            if arch.family != family.name:
                raise BankError(
                    f"archetype {spec['archetype']!r} is a {arch.family} "
                    f"sound, and the scenario is for {family.name}")
            dna = arch.dna
            if spec.get("dna"):
                merged = dna.to_dict(6)
                for key, value in spec["dna"].items():
                    axis = _canonical(key)
                    if axis is None:
                        raise DNAError(
                            f"seed dna names {key!r}, which is not an axis; "
                            f"the axes are {', '.join(AXES)}")
                    merged[axis] = value
                dna = DNA.from_dict(merged)
            for key, value in arch.style.items():
                style.setdefault(key, value)
            reg = reg or arch.register
            label = spec["archetype"]
        elif "dna" in spec:
            dna, label = DNA.from_dict(spec["dna"]), "dna"
            reg = reg or family.default_register
        else:
            raise BankError(f"a seed needs archetype, dna, recipe or bank: "
                            f"{spec!r}")
        out.append(Genome(family.name, dna, style=style, name="",
                          register=reg or family.default_register,
                          lineage=[f"seed:{label}"]))
    return out


def objective_of(scenario: dict, seeds: List[Genome]):
    spec = scenario.get("objective")
    if spec:
        return score_mod.Objective.from_dict(spec)
    if seeds:
        from .dna import blend
        return score_mod.Objective(blend([(g.dna, 1.0) for g in seeds]))
    return score_mod.Objective()


def run_scenario(scenario, progress=None, director=None,
                 seconds: Optional[float] = None) -> Bank:
    """Run a scenario file (or dict) and return the bank it produced."""
    data = load_scenario(scenario)
    family = get_family(data["family"])
    register = tuple(data["register"]) if data.get("register") else None
    seeds = seeds_of(family, data, register)
    objective = objective_of(data, seeds)
    forge = evolve.Forge(
        family, objective, seed=int(data.get("seed", 0)),
        archive_size=int(data.get("archive", 12)),
        min_distance=float(data.get("min_distance", 0.10)),
        register=register, locked=data.get("locked", ()),
        realise_probes=int(data.get("realise_probes", 2)),
        w_novelty=float(data.get("novelty", 0.10)))
    forge.seed(seeds)
    forge.run(generations=int(data.get("generations", 6)),
              offspring=int(data.get("offspring", 8)),
              max_probes=data.get("max_probes"),
              seconds=seconds if seconds is not None else data.get("seconds"),
              director=director, progress=progress,
              sigma=float(data.get("sigma", 0.18)))
    keep = int(data.get("keep", 6))
    finalists = forge.finalize(top=max(keep, 1))
    if director is not None:
        try:
            finalists = director.rank(finalists, forge.summary()) or finalists
        except Exception:                      # a model must never end a run
            pass
    prefix = slug(data.get("prefix") or data.get("name") or family.name)
    bank = Bank(data.get("name", prefix), scenario=data)
    for number, cand in enumerate(finalists[:keep], 1):
        bank.add_candidate(cand, f"{prefix}_{number:02d}",
                           role=data.get("role", ""))
    if data.get("mix"):
        from . import mixing
        for number, recipe in enumerate(data["mix"], 1):
            entry = mixing.run_recipe(recipe, bank,
                                      name=f"{prefix}_mix{number:02d}",
                                      role=data.get("role", ""),
                                      family=family.name)
            bank.add(entry)
    return bank
