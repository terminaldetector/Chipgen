"""genome.py — what the search holds: a DNA, how it is built, and its history.

A `Genome` is not an instrument. It is the *intent* (the DNA), the discrete
choices a compiler needs that the DNA cannot say (which waveform family,
which way to make a transient), and a small bias per axis that realisation
learns — "this DNA asked for 0.7 brightness and the player gave 0.55, so
compile as if it were asked for 0.85". The instrument comes out of
`compile`, and can always be thrown away and rebuilt from the genome.

Keeping the intent apart from the samples and envelopes is what lets the
same search run on a bass, a bell and a hi-hat, and what lets mixing work
on *sounds* instead of on numbers that mean different things in each.
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from .dna import DNA, DNAError


@dataclass
class Genome:
    family: str
    dna: DNA
    style: Dict[str, Any] = field(default_factory=dict)
    bias: Dict[str, float] = field(default_factory=dict)
    name: str = ""
    #: the note the probe plays, and the register the instrument is for
    register: Tuple[str, int] = ("A", 3)
    #: how it came to be: ["archetype:saw_lead", "mutate:brightness+0.12", ...]
    lineage: List[str] = field(default_factory=list)

    # -- views ----------------------------------------------------------
    def effective(self) -> DNA:
        """The DNA the compiler is actually given: intent plus learned bias."""
        if not self.bias:
            return self.dna
        return self.dna.shifted(self.bias)

    def key(self) -> str:
        """A short hash of everything that changes the compiled patch. Two
        genomes with the same key compile to the same instrument, which is
        what the probe cache relies on."""
        blob = json.dumps(
            {"b": self.family, "d": self.dna.to_dict(4),
             "s": self.style, "bias": {k: round(v, 4)
                                       for k, v in sorted(self.bias.items())
                                       if round(v, 4)},
             "r": list(self.register)},
            sort_keys=True)
        return hashlib.sha1(blob.encode()).hexdigest()[:12]

    def clone(self, **changes) -> "Genome":
        data = dict(family=self.family, dna=self.dna,
                    style=dict(self.style), bias=dict(self.bias),
                    name=self.name, register=self.register,
                    lineage=list(self.lineage))
        data.update(changes)
        return Genome(**data)

    # -- serialisation ----------------------------------------------------
    def to_dict(self) -> dict:
        return {"family": self.family, "name": self.name,
                "dna": self.dna.to_dict(), "style": dict(self.style),
                "bias": {k: round(v, 4) for k, v in sorted(self.bias.items())
                         if v},
                "register": list(self.register),
                "lineage": list(self.lineage)}

    @classmethod
    def from_dict(cls, data: dict) -> "Genome":
        try:
            return cls(family=data["family"], dna=DNA.from_dict(data["dna"]),
                       style=dict(data.get("style", {})),
                       bias={k: float(v) for k, v in
                             data.get("bias", {}).items()},
                       name=data.get("name", ""),
                       register=tuple(data.get("register", ("A", 3))),
                       lineage=list(data.get("lineage", [])))
        except KeyError as error:
            raise DNAError(f"a genome needs {error.args[0]!r}") from None


def slug(text: str) -> str:
    """A bank-safe name: lowercase letters, digits and underscores."""
    out = "".join(c if c.isalnum() else "_" for c in text.lower())
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_") or "instrument"
