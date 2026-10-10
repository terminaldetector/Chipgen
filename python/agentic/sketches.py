"""sketches.py — the agent's own drafts, kept, chosen or rejected with the
reason, and heard again later.

A finished piece remembers only the version that won. The drafts that lost
say as much about the piece: what was tried, why it was not kept. So every
draft is kept here:

    sketch      a version of the music the agent made to compare (another
                seed or energy for a section, an instrument tried on a
                voice), the whole content or a revision of the project
    rejected    a candidate a correction did not keep, with the chain that
                made it, the revision it started from and the reason it
                lost (a guard, a measurement, a listening model's
                preference)
    chosen      a sketch the agent picked as a direction: the comparison
                loop hears the piece against it, as it hears a reference

A sketch is not a revision of the project: choosing one does not change
the music. Its content is stored in sketches/<id>.json (or named by
revision), its record in state["sketches"].
"""

import json
import os
import time
from typing import List, Optional

from .state import AgenticError, CONTENT_KEYS, content as state_content

STATUSES = ("open", "chosen", "rejected")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def all_(project) -> List[dict]:
    return project.state.setdefault("sketches", [])


def get(project, sid: str) -> dict:
    for s in all_(project):
        if s["id"] == sid:
            return s
    raise AgenticError("no_sketch", f"no sketch {sid!r}; the project has "
                       f"{[s['id'] for s in all_(project)]}", sketch=sid)


def save(project, label: str, content: Optional[dict] = None,
         rev: Optional[int] = None, why: str = "",
         status: str = "open", chain=None, base_rev: Optional[int] = None,
         reason: str = "", measured: Optional[dict] = None,
         section: Optional[str] = None) -> str:
    """Keep a draft: a content (stored in sketches/) or a revision of the
    project. -> its id."""
    if status not in STATUSES:
        raise AgenticError("bad_status", f"status is one of {STATUSES}")
    if (content is None) == (rev is None):
        raise AgenticError("bad_sketch", "a sketch is a content or a "
                           "revision, one of the two")
    items = all_(project)
    sid = f"s{len(items) + 1}"
    record = {"id": sid, "label": label, "status": status, "why": why,
              "created": _now(), "base_rev": base_rev
              if base_rev is not None else project.head,
              "section": section}
    if chain is not None:
        record["chain"] = chain
    if reason:
        record["reason"] = reason
    if measured:
        record["measured"] = measured
    if rev is not None:
        project.revision(rev)               # refuses a revision not there
        record["rev"] = rev
    else:
        missing = [k for k in CONTENT_KEYS if k not in content]
        if missing:
            raise AgenticError("bad_sketch", f"a sketch's content needs "
                               f"{missing}")
        folder = project.path("sketches")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{sid}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({k: content[k] for k in CONTENT_KEYS}, handle,
                      ensure_ascii=False)
        record["file"] = os.path.relpath(path, project.root)
    items.append(record)
    project.save()
    return sid


def content_of(project, sid: str) -> dict:
    s = get(project, sid)
    if "rev" in s:
        return project.checkout(s["rev"])
    with open(project.path(s["file"]), encoding="utf-8") as handle:
        return json.load(handle)


def choose(project, sid: str, why: str = "") -> dict:
    s = get(project, sid)
    s["status"] = "chosen"
    s["chosen_because"] = why
    s["chosen_at"] = _now()
    project.save()
    return s


def reject(project, sid: str, reason: str) -> dict:
    if not reason:
        raise AgenticError("no_reason", "a rejected sketch keeps the reason "
                           "it lost")
    s = get(project, sid)
    s["status"] = "rejected"
    s["reason"] = reason
    project.save()
    return s


def chosen(project) -> List[dict]:
    return [s for s in all_(project) if s["status"] == "chosen"]


def head_as_sketch(project, label: str, why: str = "") -> str:
    """The project's current music kept as a sketch (by revision)."""
    return save(project, label, rev=project.head, why=why)


def section_variants(project, section_id: str, seeds=(), energies=(),
                     label: str = "") -> List[str]:
    """Other drafts of one planned section: the built-in composer run
    again with another seed (and, given, another energy), each kept as a
    sketch of the whole piece with that section replaced. The project's
    music is not changed. -> the sketch ids."""
    from . import compose as C
    from . import motif as M
    from . import timeline as T
    st = project.state
    plan = st["structure"]["plan"]
    index = next((i for i, e in enumerate(plan) if e["id"] == section_id),
                 None)
    if index is None:
        raise AgenticError("no_section", f"{section_id!r} is not in the "
                           f"plan", section=section_id)
    out = []
    variants = [(int(s), None) for s in seeds] + \
        [(None, float(e)) for e in energies]
    for seed, energy in variants:
        c = state_content(st)
        if energy is not None:
            c["structure"]["plan"][index] = dict(
                c["structure"]["plan"][index], energy=energy)
        g = M.graph(st)
        ctx = C.window(c, c["structure"]["plan"], index, g)
        use_seed = seed if seed is not None else \
            (st.get("composition") or {}).get("seed", 0)
        section, report = C.compose_section(
            c, ctx, st["structure"].get("key") or "C major", g, use_seed)
        c["sections"] = [s for s in c["sections"] if s["id"] != section_id] \
            + [section]
        if section_id not in c["order"]:
            c["order"].append(section_id)
        T.build(c)                       # it must parse and play
        tag = f"seed {seed}" if seed is not None else f"energy {energy:g}"
        out.append(save(project, label or f"{section_id} ({tag})", content=c,
                        why=f"another draft of {section_id}: {tag}",
                        section=section_id,
                        measured={"motif": report.get("motif"),
                                  "fitted_to_harmony": report.get("fitted")}))
    return out
