"""bank.py — instruments as files, and scenarios as files that make them.

A bank is JSON. It holds, for each instrument, the genome that made it,
the hardware patch it compiled to, what the probe read off it and why it
was kept — enough to install it, to explain it, and to seed the next run.
Nothing in a bank is code, so a model (or a person) can add a family of
sounds by writing a file, which is the extension point the bridge archive
already allows (`work/`, `--bank`).

A scenario is the other half: a JSON description of a run (which chip,
what to start from, what to aim for, how much to spend). Both formats are
versioned and validated with messages that name the field.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import archetypes, evolve, probe as probe_mod, score as score_mod
from .backends import get_backend
from .backends.base import Compiled
from .dna import AXES, DNA, DNAError, _canonical
from .genome import Genome, slug

BANK_FORMAT = "chipgen-forge-bank/1"
SCENARIO_FORMAT = "chipgen-forge-scenario/1"


class BankError(ValueError):
    """A bank or scenario file that cannot be used, with the field named."""


@dataclass
class Entry:
    name: str
    backend: str
    genome: Genome
    compiled: Compiled
    measured: Dict[str, Optional[float]]
    score: float = 0.0
    role: str = ""
    character: str = ""
    issues: List[str] = field(default_factory=list)
    level_db: float = 0.0

    def to_dict(self, backend=None) -> dict:
        backend = backend or get_backend(self.backend)
        return {"name": self.name, "backend": self.backend, "role": self.role,
                "character": self.character, "score": round(self.score, 4),
                "genome": self.genome.to_dict(),
                "compiled": backend.to_dict(self.compiled),
                "measured": {k: (None if v is None else round(v, 3))
                             for k, v in self.measured.items()},
                "issues": list(self.issues)}

    @classmethod
    def from_dict(cls, data: dict) -> "Entry":
        try:
            backend = get_backend(data["backend"])
            genome = Genome.from_dict(data["genome"])
            compiled = backend.from_dict(data["compiled"])
            compiled.name = data["name"]
            problems = backend.validate(compiled)
            if compiled.style.get("program"):
                from . import program as program_mod
                try:
                    program = program_mod.parse(compiled.style["program"])
                except program_mod.ProgramError as error:
                    raise BankError(f"{data['name']}: program: {error}") \
                        from None
                if backend.name == "ym2612":
                    problems += program_mod.check_fm(program, compiled.patch)
            if problems:
                raise BankError(
                    f"{data['name']} ({backend.name}) is not a legal "
                    f"instrument: {'; '.join(problems[:3])}"
                    + (f" (and {len(problems) - 3} more)"
                       if len(problems) > 3 else ""))
            return cls(data["name"], backend.name, genome, compiled,
                       dict(data.get("measured", {})),
                       float(data.get("score", 0.0)), data.get("role", ""),
                       data.get("character", ""), list(data.get("issues", [])))
        except KeyError as error:
            raise BankError(f"an instrument entry needs {error.args[0]!r}") \
                from None


class Bank:
    def __init__(self, name: str = "forge", entries=None, scenario=None):
        self.name = name
        self.entries: List[Entry] = list(entries or [])
        self.scenario = scenario

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
        backend = get_backend(cand.genome.backend)
        entry = Entry(
            name=slug(name), backend=backend.name, genome=cand.genome,
            compiled=cand.compiled, measured=dict(cand.measured),
            score=cand.score, role=role,
            character=cand.genome.dna.describe(threshold=0.22),
            issues=[str(i) for i in cand.result.issues])
        entry.genome = entry.genome.clone(name=entry.name)
        return self.add(entry)

    # -- files ---------------------------------------------------------------
    def to_dict(self) -> dict:
        return {"format": BANK_FORMAT, "name": self.name,
                "scenario": self.scenario,
                "instruments": [e.to_dict() for e in self.entries]}

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
        if data.get("format") != BANK_FORMAT:
            raise BankError(f"{path}: not a forge bank (format "
                            f"{data.get('format')!r}, want {BANK_FORMAT!r})")
        bank = cls(data.get("name", "forge"), scenario=data.get("scenario"))
        for raw in data.get("instruments", []):
            bank.entries.append(Entry.from_dict(raw))
        return bank

    # -- use ---------------------------------------------------------------------
    def install(self) -> List[str]:
        """Make every instrument selectable by name in this process."""
        from . import program as program_mod
        out = []
        for e in self.entries:
            # an instrument with a program installs the patch with the
            # program's settings in it (and on the NES, its macros)
            get_backend(e.backend).install(program_mod.prepare(e.compiled),
                                           e.character)
            out.append(e.name)
        return out

    def export_runtime(self, directory: str, stem: str = None) -> Dict[str, str]:
        """Write the files the rest of chipgen already reads: a YM2612 bank
        for `--bank`, an OPL2 bank for `--opl-bank`, an NES bank for
        `--nes-bank`. -> {chip: path}."""
        import instruments as instruments_mod
        import opl_import
        from . import nes_driver
        os.makedirs(directory, exist_ok=True)
        stem = stem or slug(self.name)
        written = {}
        from . import program as program_mod
        # the patch with a program's settings in it; a program's tracks,
        # vibrato and legato play only through --forge-bank
        fm = {e.name: program_mod.prepare(e.compiled).patch
              for e in self.entries if e.backend == "ym2612"}
        if fm:
            path = os.path.join(directory, f"{stem}.fm.json")
            instruments_mod.save_bank(path, fm)
            written["ym2612"] = path
        opl = {e.name: e.compiled.patch for e in self.entries
               if e.backend == "opl2"}
        if opl:
            path = os.path.join(directory, f"{stem}.opl.json")
            opl_import.save_bank(opl, path, calibrate=False)
            written["opl2"] = path
        nes = {e.name: program_mod.prepare(e.compiled).patch
               for e in self.entries if e.backend == "nes"}
        if nes:
            path = os.path.join(directory, f"{stem}.nes.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump([p.to_dict() for p in nes.values()], handle,
                          indent=1)
            written["nes"] = path
        return written

    def table(self) -> str:
        lines = [f"{'name':22s} {'chip':7s} {'score':>5s}  "
                 + " ".join(f"{a[:5]:>5s}" for a in AXES)]
        for e in self.entries:
            row = " ".join(("%5.2f" % e.measured[a]) if e.measured.get(a)
                           is not None else "    -" for a in AXES)
            lines.append(f"{e.name:22.22s} {e.backend:7s} {e.score:5.2f}  {row}")
        return "\n".join(lines)


def load_nes_bank(path: str) -> int:
    """Merge an NES bank file into nes_driver.BANK. -> how many."""
    from . import nes_driver
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    for item in data:
        nes_driver.add(nes_driver.NESInstrument.from_dict(item))
    return len(data)


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------
SCENARIO_KEYS = {
    "name", "format", "backend", "role", "seeds", "objective", "register",
    "generations", "offspring", "archive", "min_distance", "seed",
    "max_probes", "seconds", "locked", "keep", "prefix", "novelty", "mix",
    "realise_probes", "sigma", "note"}


def _validate_scenario(data: dict):
    if not isinstance(data, dict):
        raise BankError("a scenario is a JSON object")
    unknown = sorted(set(data) - SCENARIO_KEYS)
    if unknown:
        raise BankError(f"unknown scenario field(s): {', '.join(unknown)}; "
                        f"the fields are {', '.join(sorted(SCENARIO_KEYS))}")
    if "backend" not in data:
        raise BankError("a scenario needs `backend` (ym2612, opl2 or nes)")
    if data.get("format", SCENARIO_FORMAT) != SCENARIO_FORMAT:
        raise BankError(f"scenario format {data['format']!r}; this reads "
                        f"{SCENARIO_FORMAT!r}")
    try:
        get_backend(data["backend"])
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


def seeds_of(backend, scenario: dict, register=None) -> List[Genome]:
    """Turn the `seeds` list into genomes."""
    out = []
    specs = scenario.get("seeds") or []
    role = scenario.get("role")
    if not specs and role:
        specs = archetypes.for_role(role)
    if not specs:
        specs = ["saw_lead"] if backend.name != "nes" else ["square_lead"]
    for spec in specs:
        if isinstance(spec, str):
            spec = {"archetype": spec}
        if "bank" in spec:
            for entry in Bank.load(spec["bank"]).entries:
                if entry.backend == backend.name:
                    out.append(entry.genome.clone(name=""))
            continue
        if "patch" in spec:
            from . import mixing
            out.append(mixing.lift_named(backend, spec["patch"]))
            continue
        style = dict(spec.get("style", {}))
        reg = tuple(spec.get("register", register or ())) or None
        if "archetype" in spec:
            dna, a_reg, _, _ = archetypes.get(spec["archetype"])
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
            reg = reg or a_reg
            label = spec["archetype"]
        elif "dna" in spec:
            dna, label = DNA.from_dict(spec["dna"]), "dna"
            reg = reg or backend.default_register
        else:
            raise BankError(f"a seed needs archetype, dna, patch or bank: "
                            f"{spec!r}")
        out.append(Genome(backend.name, dna, style=style, name="",
                          register=reg or backend.default_register,
                          lineage=[f"seed:{label}"]))
    return out


def objective_of(backend, scenario: dict, seeds: List[Genome]):
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
    backend = get_backend(data["backend"])
    register = tuple(data["register"]) if data.get("register") else None
    seeds = seeds_of(backend, data, register)
    objective = objective_of(backend, data, seeds)
    forge = evolve.Forge(
        backend, objective, seed=int(data.get("seed", 0)),
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
    prefix = slug(data.get("prefix") or data.get("name") or backend.name)
    bank = Bank(data.get("name", prefix), scenario=data)
    for number, cand in enumerate(finalists[:keep], 1):
        bank.add_candidate(cand, f"{prefix}_{number:02d}",
                           role=data.get("role", ""))
    if data.get("mix"):
        from . import mixing
        for number, recipe in enumerate(data["mix"], 1):
            entry = mixing.run_recipe(backend, recipe, bank,
                                      name=f"{prefix}_mix{number:02d}",
                                      role=data.get("role", ""))
            bank.add(entry)
    return bank
