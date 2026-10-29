"""director.py — the optional model in the loop, and what stands in for it.

The search does all of its own work: it renders, measures, validates and
scores hundreds of candidates without asking anyone anything. A model is
useful in exactly two places, and this module is those two places:

  * **directions** — "the pad is too bright and too still; push brightness
    down and motion up". A model reading a table of dials is good at this
    and the search is bad at it. It is asked once every few generations,
    never once per candidate, and what it returns is a handful of
    (axis, add|set) pairs that the mutation operator applies to a share of
    the offspring. A wrong answer costs a few wasted renders.
  * **rank** — the last look at the finalists, when there are six and a
    person (or a model) has to say which one is the bass for this song.

A Director is any object with `directions(summary)` and
`rank(finalists, summary)`. Three are here:

  * `HeuristicDirector` — no model; reads the gap between the best
    instruments and the target and pushes the other way. This is what runs
    when nothing else is configured, and what an `LLMDirector` falls back to.
  * `LLMDirector` — asks through any callable `ask(messages) -> text`, or an
    `llm.Endpoint` (Ollama, llama.cpp, LM Studio, vLLM, a cloud API). It
    never raises: a model that is down, slow, or answering in prose costs
    the run nothing but that answer.
  * `FileDirector` — directions written down beforehand, for the case where
    the "model" is a chat window: run `forge.py director-prompt`, paste the
    prompt, save the reply's JSON, run with `--director file:reply.json`.

Everything a model says is validated here before the search sees it: axes
the chip cannot express are dropped, steps are clipped, and the reply is
read tolerantly (fences, trailing commas, single quotes, prose around it).
"""

import json
import re
from typing import Callable, Dict, List, Optional

from .backends import get_backend
from .dna import AXES, AXIS_DOC, _canonical

#: the largest step a single direction may ask for
MAX_ADD = 0.5
#: how many directions one reply may carry
MAX_DIRECTIONS = 6


# --------------------------------------------------------------------------
# Reading what a model sends back
# --------------------------------------------------------------------------
def extract_json(text: str):
    """The first JSON value in a model's reply, or None.

    Models wrap the answer in a fence, or in a sentence, or leave a comma
    at the end of a list, or write 'single quotes'. None of that is worth a
    second request.
    """
    if not text:
        return None
    candidates = []
    for block in re.findall(r"```(?:json|JSON)?\s*(.*?)```", text, re.S):
        candidates.append(block)
    candidates.append(text)
    for chunk in candidates:
        for start in [i for i, c in enumerate(chunk) if c in "{["]:
            value = _balanced(chunk, start)
            if value is not None:
                return value
    return None


def _balanced(text: str, start: int):
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    quote = None
    for i in range(start, len(text)):
        c = text[i]
        if quote:
            if c == "\\":
                continue
            if c == quote:
                quote = None
            continue
        if c in "\"'":
            quote = c
        elif c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return _loads(text[start:i + 1])
    return None


def _loads(chunk: str):
    for attempt in (chunk, _tidy(chunk)):
        try:
            return json.loads(attempt)
        except ValueError:
            continue
    return None


def _tidy(chunk: str) -> str:
    fixed = re.sub(r",\s*([}\]])", r"\1", chunk)              # trailing commas
    fixed = re.sub(r"'([^']*)'", r'"\1"', fixed)              # 'single'
    fixed = re.sub(r"\+(\d)", r"\1", fixed)                   # +0.2
    return fixed


def clean_directions(raw, backend_name: str, notes: Optional[list] = None
                     ) -> List[dict]:
    """Validate what a model proposed -> [{"axis", "add"|"set"}]. Anything
    unusable is left out and said so in `notes`."""
    notes = notes if notes is not None else []
    supports = set(get_backend(backend_name).supports)
    if isinstance(raw, dict):
        raw = raw.get("directions", raw.get("moves", []))
    if not isinstance(raw, list):
        notes.append("directions was not a list")
        return []
    out, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            notes.append(f"ignored {item!r}: not an object")
            continue
        axis = _canonical(str(item.get("axis", "")))
        if axis is None:
            notes.append(f"ignored {item.get('axis')!r}: not an axis")
            continue
        if axis not in supports:
            notes.append(f"ignored {axis}: {backend_name} cannot express it")
            continue
        if axis in seen:
            notes.append(f"ignored a second direction for {axis}")
            continue
        try:
            if "set" in item:
                value = max(0.0, min(1.0, float(item["set"])))
                out.append({"axis": axis, "set": round(value, 3)})
            elif "add" in item:
                step = max(-MAX_ADD, min(MAX_ADD, float(item["add"])))
                if abs(step) < 0.01:
                    continue
                out.append({"axis": axis, "add": round(step, 3)})
            else:
                notes.append(f"ignored {axis}: needs add or set")
                continue
        except (TypeError, ValueError):
            notes.append(f"ignored {axis}: the amount is not a number")
            continue
        seen.add(axis)
        if len(out) >= MAX_DIRECTIONS:
            break
    return out


# --------------------------------------------------------------------------
# The numbers a Director looks at
# --------------------------------------------------------------------------
def signed_gaps(summary: dict, member: dict) -> Dict[str, float]:
    """target - measured for each axis the scenario scores; 0 inside a box."""
    objective = summary.get("objective", {})
    target = objective.get("target", {})
    box = objective.get("box", {})
    ignore = set(objective.get("ignore", ()))
    out = {}
    for axis, value in member.get("measured", {}).items():
        if value is None or axis in ignore or axis not in AXES:
            continue
        if axis in box:
            lo, hi = box[axis]
            out[axis] = lo - value if value < lo else hi - value \
                if value > hi else 0.0
        elif axis in target:
            out[axis] = target[axis] - value
    return out


class Director:
    """The protocol. Subclass or duck-type; both methods are optional in
    spirit: returning [] / the finalists unchanged is always legal."""

    def directions(self, summary: dict) -> List[dict]:
        return []

    def rank(self, finalists: list, summary: dict) -> list:
        return finalists


class HeuristicDirector(Director):
    """No model: push the best instruments toward the target.

    Looks at the three best members, takes the axes where they are furthest
    from what the scenario asked, and proposes closing most of each gap. When
    the best score has stopped moving it adds a push on the axis the archive
    varies least in, so the search is not stuck on one answer."""

    def __init__(self, step: float = 0.3, axes_per_round: int = 3):
        self.step = step
        self.axes_per_round = axes_per_round
        self._last_best = None
        self._flat = 0

    def directions(self, summary: dict) -> List[dict]:
        members = summary.get("members", [])
        if not members:
            return []
        weights = summary.get("objective", {}).get("weights", {})
        gaps: Dict[str, float] = {}
        for member in members[:3]:
            for axis, gap in signed_gaps(summary, member).items():
                gaps[axis] = gaps.get(axis, 0.0) + gap / min(3, len(members))
        ranked = sorted(gaps.items(), key=lambda kv: -abs(kv[1])
                        * weights.get(kv[0], 1.0))
        out = []
        for axis, gap in ranked[:self.axes_per_round]:
            if abs(gap) < 0.04:
                continue
            step = max(-self.step, min(self.step, gap * 0.8))
            out.append({"axis": axis, "add": round(step, 3)})
        best = members[0].get("score")
        if self._last_best is not None and best is not None \
                and best <= self._last_best + 1e-6:
            self._flat += 1
        else:
            self._flat = 0
        self._last_best = best
        if self._flat >= 2 and len(members) >= 3:
            spread = {a: max(m["measured"].get(a) or 0 for m in members)
                      - min(m["measured"].get(a) or 0 for m in members)
                      for a in AXES if a in members[0]["measured"]
                      and a not in {d["axis"] for d in out}}
            if spread:
                axis = min(spread, key=spread.get)
                out.append({"axis": axis, "add": 0.2 if self._flat % 2
                            else -0.2})
        return out


class FileDirector(Director):
    """Directions written down beforehand. The file is one of:

        [{"axis": "brightness", "add": 0.2}, ...]          every generation
        {"directions": [...]}                              every generation
        {"generations": [[...], [...], ...]}               one list each,
                                                           the last repeats

    plus, optionally, {"order": ["name", ...]} to rank the finalists."""

    def __init__(self, path_or_data):
        if isinstance(path_or_data, str):
            with open(path_or_data, encoding="utf-8") as handle:
                data = extract_json(handle.read())
        else:
            data = path_or_data
        if data is None:
            raise ValueError("the director file holds no JSON")
        self.order = []
        if isinstance(data, dict):
            self.order = list(data.get("order", []))
            if "generations" in data:
                self.plan = list(data["generations"])
            else:
                self.plan = [data.get("directions", [])]
        elif isinstance(data, list):
            self.plan = [data]
        else:
            raise ValueError("a director file is a list of directions or an "
                             "object with `directions`/`generations`")
        self.notes: List[str] = []
        self._calls = 0

    def directions(self, summary: dict) -> List[dict]:
        if not self.plan:
            return []
        raw = self.plan[min(self._calls, len(self.plan) - 1)]
        self._calls += 1
        return clean_directions(raw, summary["backend"], self.notes)

    def rank(self, finalists: list, summary: dict) -> list:
        return _reorder(finalists, self.order)


def _reorder(finalists: list, order: List[str]) -> list:
    by_name = {c.genome.name: c for c in finalists}
    first = [by_name[n] for n in order if n in by_name]
    rest = [c for c in finalists if c not in first]
    return first + rest


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------
def _axis_lines(axes) -> str:
    return "\n".join(f"  {a}: 0 = {AXIS_DOC[a][0]}; 1 = {AXIS_DOC[a][1]}"
                     for a in axes)


def directions_prompt(summary: dict) -> List[dict]:
    """The messages for one steering question: the target, the archive as a
    table of dials, and the shape of the answer. Short on purpose; a 7B
    model reads it as easily as a large one."""
    backend = get_backend(summary["backend"])
    axes = [a for a in AXES if a in backend.supports]
    objective = summary.get("objective", {})
    target = objective.get("target", {})
    box = objective.get("box", {})
    want = []
    for axis in axes:
        if axis in box:
            want.append(f"{axis} {box[axis][0]:.2f}-{box[axis][1]:.2f}")
        elif axis in target and axis not in objective.get("ignore", ()):
            want.append(f"{axis} {target[axis]:.2f}")
    rows = []
    for member in summary.get("members", [])[:8]:
        cells = " ".join(
            f"{a[:5]}={member['measured'][a]:.2f}" for a in axes
            if member["measured"].get(a) is not None)
        rows.append(f"  {member['name']} score {member['score']:.2f} | {cells}")
    system = (
        "You steer a search for a new instrument on the "
        f"{backend.chip}. The search changes a few dials at random, renders "
        "the sound and measures it; you only say which way to push. Every "
        "dial runs from 0 to 1:\n" + _axis_lines(axes) + "\n"
        "Reply with JSON only, no prose: "
        '{"directions": [{"axis": "brightness", "add": 0.2}, '
        '{"axis": "attack", "set": 0.0}], "why": "one sentence"}. '
        f"At most {MAX_DIRECTIONS} directions; `add` is between "
        f"-{MAX_ADD} and {MAX_ADD}; `set` is between 0 and 1.")
    user = (f"Wanted: {'; '.join(want) or 'no target, find something new'}.\n"
            f"Generation {summary.get('generation', 0)}. The best so far, "
            f"as measured:\n" + "\n".join(rows) +
            "\nWhich dials should move, and which way?")
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def rank_prompt(finalists: list, summary: dict, purpose: str = "") -> List[dict]:
    backend = get_backend(summary["backend"])
    axes = [a for a in AXES if a in backend.supports]
    rows = []
    for c in finalists:
        cells = " ".join(f"{a[:5]}={c.measured[a]:.2f}" for a in axes
                         if c.measured.get(a) is not None)
        flags = ", ".join(str(i) for i in c.result.issues) or "clean"
        rows.append(f"  {c.genome.name} score {c.score:.2f} | {cells} | "
                    f"{flags}")
    system = ("You choose between finished instrument candidates for the "
              f"{backend.chip}. The dials run 0..1:\n" + _axis_lines(axes)
              + '\nReply with JSON only: {"order": ["best name", "next", '
              '...], "why": "one sentence"}. Use the names exactly as '
              "listed; leave out any you would not use.")
    user = (f"Purpose: {purpose or 'a good, usable instrument'}.\n"
            "Candidates:\n" + "\n".join(rows) + "\nBest first?")
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


# --------------------------------------------------------------------------
# The model
# --------------------------------------------------------------------------
class LLMDirector(Director):
    """Ask a model. Never raises; always has an answer.

    `ask(messages) -> str` is anything that turns chat messages into text;
    `endpoint=` builds one from an `llm.Endpoint`. `every` is how many
    generations share one answer, `max_calls` caps the whole run; between
    calls the last good directions are reused, and when a call fails or
    comes back unusable the heuristic answers instead.
    """

    def __init__(self, ask: Optional[Callable] = None, endpoint=None,
                 every: int = 2, max_calls: int = 12, purpose: str = "",
                 temperature: float = 0.4, fallback: Optional[Director] = None,
                 max_tokens: int = 600):
        if ask is None:
            if endpoint is None:
                raise ValueError("an LLMDirector needs ask= or endpoint=")
            import llm

            def ask(messages, _e=endpoint, _t=temperature, _m=max_tokens):
                return llm.chat(_e, messages, temperature=_t, max_tokens=_m)
        self.ask = ask
        self.every = max(1, every)
        self.max_calls = max_calls
        self.purpose = purpose
        self.fallback = fallback or HeuristicDirector()
        self.calls = 0
        self.errors: List[str] = []
        self.notes: List[str] = []
        self.log: List[dict] = []
        self._asked_at = None
        self._last: List[dict] = []

    def directions(self, summary: dict) -> List[dict]:
        generation = summary.get("generation", 0)
        due = self._asked_at is None \
            or generation - self._asked_at >= self.every
        if not due:
            return self._last
        if self.calls >= self.max_calls:
            return self._fallback(summary, "call budget spent")
        self.calls += 1
        self._asked_at = generation
        messages = directions_prompt(summary)
        try:
            reply = self.ask(messages)
        except Exception as error:               # a model must never end a run
            self.errors.append(str(error))
            return self._fallback(summary, f"model unavailable: {error}")
        raw = extract_json(reply)
        notes: List[str] = []
        found = clean_directions(raw, summary["backend"], notes) \
            if raw is not None else []
        self.notes += notes
        self.log.append({"kind": "directions", "generation": generation,
                         "reply": reply, "accepted": found, "notes": notes})
        if not found:
            return self._fallback(summary, "no usable directions in the reply")
        self._last = found
        return found

    def _fallback(self, summary: dict, why: str) -> List[dict]:
        self.notes.append(why)
        self._last = self.fallback.directions(summary)
        return self._last

    def rank(self, finalists: list, summary: dict) -> list:
        if len(finalists) < 2 or self.calls >= self.max_calls + 1:
            return finalists
        self.calls += 1
        try:
            reply = self.ask(rank_prompt(finalists, summary, self.purpose))
        except Exception as error:
            self.errors.append(str(error))
            return finalists
        data = extract_json(reply)
        order = data.get("order", []) if isinstance(data, dict) \
            else data if isinstance(data, list) else []
        order = [str(n) for n in order]
        known = {c.genome.name for c in finalists}
        used = [n for n in order if n in known]
        self.log.append({"kind": "rank", "reply": reply, "order": used,
                         "ignored": [n for n in order if n not in known]})
        return _reorder(finalists, used) if used else finalists


def make_director(spec: Optional[str], endpoint=None, purpose: str = ""):
    """The director a command line names: none | heuristic | file:PATH |
    llm (needs an endpoint). -> Director or None."""
    if not spec or spec == "none":
        return None
    if spec == "heuristic":
        return HeuristicDirector()
    if spec.startswith("file:"):
        return FileDirector(spec[5:])
    if spec == "llm":
        if endpoint is None:
            raise ValueError("--director llm needs --endpoint/--model (or "
                             "CHIPGEN_ENDPOINT and CHIPGEN_MODEL)")
        return LLMDirector(endpoint=endpoint, purpose=purpose)
    raise ValueError(f"unknown director {spec!r}; use none, heuristic, "
                     f"file:PATH or llm")
