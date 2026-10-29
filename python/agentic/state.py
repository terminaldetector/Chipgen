"""state.py — MusicalState: the piece and what is known about it, kept
outside any model's context window.

A model's context is the wrong place to keep a composition: it forgets,
it summarises, and a session that stops takes everything with it. So the
piece lives here, in a project folder, as JSON:

    identity     the request, the intent, the target, the constraints, and
                 what "finished" means
    structure    tempo, metre, key, the plan of sections with their
                 function and energy, the harmony per bar, the climax
    instruments  each voice's channel, patch and overrides, role, register
                 and how its timbre should develop
    sections     the music itself, as tracker rows (the engine's native,
                 editable notation) with the directives between them
    order        which section plays when
    motifs       the motif graph (motif.py)
    observations what was heard or measured, where, how sure, from whom
    decisions    every change tried: hypothesis, patch, expected and
                 measured effect, commit or rollback, cost

The music (structure, instruments, sections, order, columns) is the
*content*. Every change to it is a revision, written to revisions/ before
the state points at it, so a rollback is exact and a session that dies
mid-write resumes from the last whole revision. Revisions are never
rewritten: a rollback is a new revision whose content is an old one.

Nothing here composes, renders or listens. It keeps.
"""

import copy
import hashlib
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

FORMAT = "chipgen-musical-state/1"
REVISION_FORMAT = "chipgen-revision/1"

#: Columns a Mega Drive project may use, in the tracker's names.
MEGADRIVE_COLUMNS = ("fm0", "fm1", "fm2", "fm3", "fm4", "fm5",
                     "psg0", "psg1", "psg2", "noise", "dac")
#: The parts of the state that are the music, revisioned together.
CONTENT_KEYS = ("structure", "instruments", "sections", "order", "columns")
#: Rows are cut into bars of this many beats unless the structure says.
DEFAULT_BEATS_PER_BAR = 4
#: Ticks per row the project clock uses: divisible by 2, 3, 4, 6, 8, 12,
#: so delays and arpeggios land on whole ticks and no row is rounded.
TICKS_PER_ROW = 24


class AgenticError(ValueError):
    """A refusal with a short machine-readable code and the exact reason."""

    def __init__(self, code: str, message: str, **detail):
        super().__init__(message)
        self.code = code
        self.detail = detail

    def to_dict(self) -> dict:
        return {"error": self.code, "message": str(self), **self.detail}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def content_hash(content: dict) -> str:
    return hashlib.sha256(_canonical(content).encode("utf-8")).hexdigest()[:16]


def ticks_per_second(bpm: float, lpb: int) -> float:
    """The tick rate that makes a row exactly TICKS_PER_ROW ticks."""
    return bpm * lpb / 60.0 * TICKS_PER_ROW


#: the columns each machine has; a piece plays one of them
MACHINE_COLUMNS = {
    "megadrive": MEGADRIVE_COLUMNS,
    "nes": ("nes0", "nes1", "nes2", "nes3", "nes4"),
    "opl2": tuple(f"opl{i}" for i in range(9))}
#: the columns a new project gets on each machine: the Mega Drive's FM,
#: PSG, noise and DAC; the NES's two pulses, triangle and noise; six of
#: the OPL2's nine 2-operator channels
DEFAULT_COLUMNS = {
    "megadrive": ("fm0", "fm1", "fm2", "fm3", "psg0", "noise", "dac"),
    "nes": ("nes0", "nes1", "nes2", "nes3"),
    "opl2": ("opl0", "opl1", "opl2", "opl3", "opl4", "opl5")}


def new_state(prompt: str, target: str = "megadrive", intent: str = "",
              bpm: float = 150.0, lpb: int = 4,
              beats_per_bar: int = DEFAULT_BEATS_PER_BAR, key: str = "",
              style: Optional[List[str]] = None,
              constraints: Optional[List[str]] = None,
              duration_s: Optional[float] = None,
              done_when: Optional[List[str]] = None,
              columns=None, project_id: str = None) -> dict:
    if target not in DEFAULT_COLUMNS:
        raise AgenticError(
            "unsupported_target",
            f"the agentic loop runs on the Mega Drive, the NES and the OPL2; "
            f"{target!r} is not wired to it (capabilities.py says what each "
            f"backend can do)", target=target)
    return {
        "format": FORMAT,
        "identity": {
            "id": project_id or "p-" + uuid.uuid4().hex[:8],
            "prompt": prompt, "intent": intent or prompt, "target": target,
            "style": list(style or []), "constraints": list(constraints or []),
            "duration_s": duration_s,
            "done_when": list(done_when or [])},
        "structure": {
            "bpm": float(bpm), "lpb": int(lpb),
            "ticks": ticks_per_second(bpm, lpb),
            "beats_per_bar": int(beats_per_bar), "key": key,
            "plan": [], "harmony": {}, "dynamics": [], "climax": None,
            "dependencies": [], "form": ""},
        "instruments": {},
        "sections": [],
        "order": [],
        "columns": list(columns or DEFAULT_COLUMNS[target]),
        "motifs": {"motifs": {}, "instances": [], "open": []},
        "observations": [],
        "decisions": [],
        "revisions": [],
        "head": 0,
        "created": _now(),
        "updated": _now(),
    }


def content(state: dict) -> dict:
    return {key: copy.deepcopy(state[key]) for key in CONTENT_KEYS}


def rows_per_bar(state_or_content: dict) -> int:
    s = state_or_content["structure"]
    return int(s["lpb"]) * int(s.get("beats_per_bar",
                                     DEFAULT_BEATS_PER_BAR))


# -- validation ---------------------------------------------------------------
def check_content(c: dict) -> List[dict]:
    """Structural problems, each a {code, message, where} the caller can
    act on. The tracker's own check (does it parse, does it play) is the
    timeline's job; this is about the shape of the state."""
    problems = []
    columns = c.get("columns") or []
    machine = next((m for m, cols in MACHINE_COLUMNS.items()
                    if columns and columns[0] in cols), "megadrive")
    allowed = MACHINE_COLUMNS[machine]
    for column in columns:
        if column not in allowed:
            problems.append({"code": "bad_column", "where": column,
                             "message": f"{column!r} is not a column of the "
                                        f"{machine} piece this is "
                                        f"({', '.join(allowed)}); one piece "
                                        f"plays one machine"})
    if len(set(columns)) != len(columns):
        problems.append({"code": "duplicate_column", "where": "columns",
                         "message": "a column appears twice"})
    ids = [s.get("id") for s in c.get("sections", [])]
    if len(set(ids)) != len(ids):
        problems.append({"code": "duplicate_section", "where": "sections",
                         "message": "two sections share an id"})
    for section in c.get("sections", []):
        for i, row in enumerate(section.get("rows", [])):
            if len(row) != len(columns):
                problems.append({
                    "code": "row_width", "where": f"{section['id']}:{i}",
                    "message": f"row {i} of {section['id']} has {len(row)} "
                               f"cells for {len(columns)} columns"})
                break
        for d in section.get("directives", []):
            if not 0 <= int(d.get("before_row", -1)) <= len(section["rows"]):
                problems.append({"code": "directive_row",
                                 "where": section["id"],
                                 "message": f"directive {d.get('text')!r} "
                                            f"is placed outside the section"})
    for name in c.get("order", []):
        if name not in ids:
            problems.append({"code": "unknown_section", "where": "order",
                             "message": f"order names {name!r}, which is "
                                        f"not a section"})
    used = {}
    for voice, ins in c.get("instruments", {}).items():
        channel = ins.get("channel")
        if channel not in columns:
            problems.append({"code": "voice_column", "where": voice,
                             "message": f"voice {voice!r} plays {channel!r}, "
                                        f"which the project has no column "
                                        f"for"})
        if channel in used:
            problems.append({"code": "channel_shared", "where": voice,
                             "message": f"voices {used[channel]!r} and "
                                        f"{voice!r} both claim {channel}"})
        used[channel] = voice
    if "fm5" in used and "dac" in used:
        problems.append({
            "code": "channel_conflict", "where": "instruments",
            "message": f"voices {used['fm5']!r} (fm5) and {used['dac']!r} "
                       f"(dac) share the YM2612's channel 6: the DAC "
                       f"replaces fm5's output whenever it plays "
                       f"(sanity.FM6_DAC_MASK_THRESHOLD measures it in a "
                       f"score). Give one of them another channel"})
    return problems


def check_patches(c: dict) -> List[dict]:
    """FM voices whose patch no installed bank has (the built-in one, or a
    bank the project loaded): the score would not play."""
    import instruments
    out = []
    for voice, ins in c.get("instruments", {}).items():
        patch = ins.get("patch")
        if patch and ins.get("channel", "").startswith("fm") and \
                patch not in instruments.BANK:
            out.append({"code": "unknown_patch", "where": voice,
                        "message": f"{voice} plays {patch!r}, which no "
                                   f"installed bank has; load the bank "
                                   f"that has it (load_bank)"})
    return out


# -- the project folder ---------------------------------------------------------
class Project:
    """A MusicalState on disk: state.json, revisions/, renders/, out/."""

    def __init__(self, root: str, state: dict):
        self.root = root
        self.state = state

    # creation and loading
    @classmethod
    def create(cls, root: str, state: dict) -> "Project":
        if os.path.exists(os.path.join(root, "state.json")):
            raise AgenticError("project_exists",
                               f"{root} already holds a project; open it "
                               f"instead", root=root)
        for sub in ("revisions", "renders", "out"):
            os.makedirs(os.path.join(root, sub), exist_ok=True)
        # its own copy: two projects made from one state must not share
        # it (a commit to one would change the other's music); and its own
        # history: another project's revisions are files in that project's
        # folder, and numbering on from them would leave this one's first
        # commit at a revision the folder never had
        state = copy.deepcopy(state)
        state["revisions"] = []
        project = cls(root, state)
        project._write_revision(0, None, {"kind": "create"})
        project.save()
        return project

    @classmethod
    def open(cls, root: str) -> "Project":
        path = os.path.join(root, "state.json")
        if not os.path.exists(path):
            raise AgenticError("no_project", f"no state.json in {root}",
                               root=root)
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
        state = migrate(state)
        project = cls(root, state)
        project._repair()
        from . import banks
        banks.install(root, project.state["structure"])
        return project

    def _repair(self):
        """After an interruption: the state must point at a revision that
        exists and matches. A revision file written but not yet pointed at
        is left on disk (it is harmless); a state pointing past the last
        whole revision is moved back to it."""
        head = self.state["head"]
        path = self._revision_path(head)
        if not os.path.exists(path):
            whole = sorted(r["rev"] for r in self.state["revisions"]
                           if os.path.exists(self._revision_path(r["rev"])))
            if not whole:
                raise AgenticError("no_revision", "the project has no "
                                   "readable revision", root=self.root)
            self.checkout_into_state(whole[-1])
            self.state["recovered"] = {"from": head, "to": whole[-1],
                                       "at": _now()}
            self.save()

    # paths
    def _revision_path(self, rev: int) -> str:
        return os.path.join(self.root, "revisions", f"r{rev:04d}.json")

    def path(self, *parts) -> str:
        return os.path.join(self.root, *parts)

    # one writer at a time, and never over a newer state
    def _locked(self):
        """The project's lock (a file lock on .lock), re-entrant within
        this object: commit, rollback and save hold it while they check
        that no other process changed the project since it was opened."""
        project = self

        class _Lock:
            def __enter__(self):
                depth = getattr(project, "_lock_depth", 0)
                if depth == 0:
                    project._lock_file = open(project.path(".lock"), "a")
                    try:
                        import fcntl
                        fcntl.flock(project._lock_file, fcntl.LOCK_EX)
                    except ImportError:     # no flock here: one process
                        pass
                project._lock_depth = depth + 1
                return self

            def __exit__(self, *exc):
                project._lock_depth -= 1
                if project._lock_depth == 0:
                    project._lock_file.close()
                return False
        return _Lock()

    def _check_fresh(self):
        """Refuse to write over revisions another process made after this
        one read the project: its music would be lost."""
        path = os.path.join(self.root, "state.json")
        if not os.path.exists(path):
            return
        with open(path, encoding="utf-8") as handle:
            disk = json.load(handle)
        theirs = len(disk.get("revisions", []))
        if theirs > len(self.state.get("revisions", [])):
            raise AgenticError(
                "stale_state", f"another process committed to this project "
                f"(revision {disk.get('head')}) after it was opened here; "
                f"open it again and redo the change on that",
                head_on_disk=disk.get("head"), head_here=self.state["head"])

    # saving
    def save(self):
        with self._locked():
            self._check_fresh()
            self._save()

    def _save(self):
        self.state["updated"] = _now()
        tmp = os.path.join(self.root, "state.json.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self.state, handle, indent=1, ensure_ascii=False)
        os.replace(tmp, os.path.join(self.root, "state.json"))

    def _write_revision(self, rev: int, parent: Optional[int], why: dict):
        body = {"format": REVISION_FORMAT, "rev": rev, "parent": parent,
                "content": content(self.state),
                "hash": content_hash(content(self.state)), "why": why,
                "at": _now()}
        tmp = self._revision_path(rev) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(body, handle, indent=1, ensure_ascii=False)
        os.replace(tmp, self._revision_path(rev))
        self.state["revisions"].append({"rev": rev, "parent": parent,
                                        "hash": body["hash"], "why": why,
                                        "at": body["at"]})
        self.state["head"] = rev

    # reading revisions
    def revision(self, rev: int) -> dict:
        path = self._revision_path(rev)
        if not os.path.exists(path):
            raise AgenticError("no_revision", f"revision {rev} does not "
                               f"exist", rev=rev)
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def checkout(self, rev: int) -> dict:
        """The content of a revision (a copy; the state is not moved)."""
        return self.revision(rev)["content"]

    def checkout_into_state(self, rev: int):
        for key, value in self.checkout(rev).items():
            self.state[key] = value
        self.state["head"] = rev

    @property
    def head(self) -> int:
        return self.state["head"]

    # changing the music
    def commit(self, new_content: dict, why: dict) -> int:
        """Make `new_content` the next revision. Refuses content whose
        shape is wrong; the caller checks that it plays (timeline)."""
        problems = check_content(new_content) + check_patches(new_content)
        if problems:
            raise AgenticError("bad_content", problems[0]["message"],
                               problems=problems)
        with self._locked():
            self._check_fresh()
            parent = self.state["head"]
            for key in CONTENT_KEYS:
                self.state[key] = copy.deepcopy(new_content[key])
            rev = max(r["rev"] for r in self.state["revisions"]) + 1
            self._write_revision(rev, parent, why)
            self._save()
        return rev

    def rollback(self, to_rev: int, why: dict) -> int:
        """A new revision whose content is `to_rev`'s, exactly."""
        target = self.checkout(to_rev)
        why = dict(why, kind="rollback", to=to_rev)
        with self._locked():
            self._check_fresh()
            parent = self.state["head"]
            for key in CONTENT_KEYS:
                self.state[key] = copy.deepcopy(target[key])
            rev = max(r["rev"] for r in self.state["revisions"]) + 1
            self._write_revision(rev, parent, why)
            self._save()
        return rev

    # knowledge about the music
    def _next_id(self, prefix: str, items: list) -> str:
        return f"{prefix}{len(items) + 1}"

    def add_observation(self, obs: dict) -> str:
        items = self.state["observations"]
        obs = dict(obs)
        obs.setdefault("id", self._next_id("o", items))
        obs.setdefault("rev", self.head)
        obs.setdefault("at", _now())
        items.append(obs)
        return obs["id"]

    def observation(self, oid: str) -> dict:
        for obs in self.state["observations"]:
            if obs["id"] == oid:
                return obs
        raise AgenticError("no_observation", f"no observation {oid!r}",
                           id=oid)

    def add_decision(self, decision: dict) -> str:
        items = self.state["decisions"]
        decision = dict(decision)
        decision.setdefault("id", self._next_id("d", items))
        decision.setdefault("at", _now())
        items.append(decision)
        return decision["id"]

    def decision(self, did: str) -> dict:
        for d in self.state["decisions"]:
            if d["id"] == did:
                return d
        raise AgenticError("no_decision", f"no decision {did!r}", id=did)

    def section(self, sid: str) -> dict:
        for s in self.state["sections"]:
            if s["id"] == sid:
                return s
        raise AgenticError("no_section", f"no section {sid!r}; have "
                           f"{[s['id'] for s in self.state['sections']]}",
                           id=sid)


# -- schema versions ---------------------------------------------------------
#: format -> function that turns it into the next one. Empty today; a
#: future format adds its step here, and an unknown format is refused
#: rather than read as if it were this one.
MIGRATIONS: Dict[str, Any] = {}


def migrate(state: dict) -> dict:
    seen = set()
    while state.get("format") != FORMAT:
        fmt = state.get("format")
        if fmt in seen or fmt not in MIGRATIONS:
            raise AgenticError(
                "unknown_format", f"state format {fmt!r} is not one this "
                f"engine reads (it reads {FORMAT}); nothing was changed",
                format=fmt)
        seen.add(fmt)
        state = MIGRATIONS[fmt](state)
    return state
