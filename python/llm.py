"""llm.py — ask a model for a score, and keep asking until it plays.

## One protocol, most of the models there are

Ollama, llama.cpp's server, LM Studio and vLLM all serve the OpenAI chat
completions protocol on a local port — and so do most cloud providers.
So one small client here reaches a local 7B model and a frontier one
alike, with nothing installed: urllib and json are the whole dependency
list. Point `--endpoint` at the server and name the `--model`.

## Why a loop

A briefing tells a model the rules; the engine is what holds them. So
the model writes, reply.check() reads it, and if it is not playable the
model gets back a short numbered list of what is wrong and where. The
conversation is rebuilt every round from the briefing, the last attempt
and its feedback — not the whole history — so a small context window is
not spent on the model's earlier mistakes.

With a llama.cpp server the grammar from grammar.py goes along too, and
then format errors are not corrected but impossible.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

#: Where a local server usually listens. Ollama's, because it is the one
#: most people already have running; `--endpoint` changes it.
DEFAULT_ENDPOINT = "http://localhost:11434/v1"

#: Request field that carries a GBNF grammar, per server family. Ollama
#: and the cloud providers ignore an unknown field, so sending it to the
#: wrong server costs nothing; not sending it to llama.cpp costs the
#: guarantee.
GRAMMAR_FIELDS = {"llama.cpp": "grammar", "vllm": "guided_grammar"}


class Endpoint:
    """A chat-completions server and the model to ask on it."""

    def __init__(self, url: str = None, model: str = None,
                 api_key: str = None, grammar_field: str = None,
                 timeout: float = 600.0):
        self.url = (url or os.environ.get("CHIPGEN_ENDPOINT")
                    or DEFAULT_ENDPOINT).rstrip("/")
        self.model = model or os.environ.get("CHIPGEN_MODEL") or ""
        self.api_key = (api_key or os.environ.get("CHIPGEN_API_KEY")
                        or os.environ.get("OPENAI_API_KEY") or "")
        self.grammar_field = GRAMMAR_FIELDS.get(grammar_field, grammar_field)
        self.timeout = timeout
        if not self.model:
            raise ValueError("name the model to ask: --model, or "
                             "CHIPGEN_MODEL in the environment")

    def __repr__(self):
        return f"Endpoint({self.url!r}, model={self.model!r})"


def chat(endpoint: Endpoint, messages: list, temperature: float = 0.7,
         max_tokens: int = 4096, grammar: str = None) -> str:
    """One chat completion. -> the reply text."""
    body = {"model": endpoint.model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens,
            "stream": False}
    if grammar and endpoint.grammar_field:
        body[endpoint.grammar_field] = grammar
    headers = {"Content-Type": "application/json"}
    if endpoint.api_key:
        headers["Authorization"] = f"Bearer {endpoint.api_key}"
    request = urllib.request.Request(
        f"{endpoint.url}/chat/completions",
        data=json.dumps(body).encode("utf-8"), headers=headers,
        method="POST")
    try:
        with urllib.request.urlopen(request,
                                    timeout=endpoint.timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"{endpoint.url} answered {error.code}: "
                           f"{detail}") from None
    except urllib.error.URLError as error:
        raise RuntimeError(f"cannot reach {endpoint.url}: {error.reason}. "
                           f"Is the server running?") from None
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"{endpoint.url} sent no message: "
                           f"{json.dumps(data)[:300]}") from None
    if isinstance(content, list):          # content parts
        content = "".join(part.get("text", "") for part in content
                          if isinstance(part, dict))
    return content or ""


class Generation:
    """What one generate() run produced, round by round."""

    def __init__(self, chip, model, profile, delivery):
        self.chip = chip
        self.model = model
        self.profile = profile
        self.delivery = delivery
        self.rounds = []            # [(reply text, Verdict, seconds)]
        self.brief_tokens = 0

    @property
    def verdict(self):
        return self.rounds[-1][1] if self.rounds else None

    @property
    def ok(self) -> bool:
        return bool(self.verdict and self.verdict.ok)

    @property
    def score(self) -> str:
        return self.verdict.score if self.verdict else ""

    def summary(self) -> str:
        lines = [f"{self.model} on {self.chip}: profile {self.profile}, "
                 f"{self.delivery} delivery, brief ~{self.brief_tokens} "
                 f"tokens"]
        for number, (_, verdict, seconds) in enumerate(self.rounds, 1):
            state = "PLAYABLE" if verdict.ok else \
                f"{len(verdict.errors)} error" \
                f"{'s' if len(verdict.errors) != 1 else ''}"
            rows = verdict.stats.get("rows", 0)
            lines.append(f"  round {number}: {state}, {rows} rows, "
                         f"{seconds:.1f}s"
                         + (f" — first: [{verdict.errors[0].rule}]"
                            if verdict.errors else ""))
        return "\n".join(lines)

    def to_json(self) -> dict:
        return {"chip": self.chip, "model": self.model,
                "profile": self.profile, "delivery": self.delivery,
                "brief_tokens": self.brief_tokens, "ok": self.ok,
                "rounds": [{"reply": text, "verdict": verdict.to_json(),
                            "seconds": round(seconds, 2)}
                           for text, verdict, seconds in self.rounds]}


def messages_for(brief_text: str, profile: str, last_reply: str = None,
                 feedback: str = None) -> list:
    """The conversation for one round, rebuilt from scratch.

    The briefing goes in the system role, except for the compact profile:
    several small models' chat templates have no system role, or one the
    model barely attends to, so there it leads the first user turn.
    """
    ask = "Write the score now."
    if profile == "compact":
        out = [{"role": "user", "content": f"{brief_text}\n\n{ask}"}]
    else:
        out = [{"role": "system", "content": brief_text},
               {"role": "user", "content": ask}]
    if last_reply is not None:
        out += [{"role": "assistant", "content": last_reply},
                {"role": "user", "content": feedback}]
    return out


def generate(chip: str, request: dict, endpoint: Endpoint,
             family: str = None, rounds: int = 3, delivery: str = "chat",
             temperature: float = 0.7, log=None, ask=None) -> Generation:
    """Brief, ask, check, feed back — until playable or out of rounds.

    `family` picks the briefing (a family, model name or profile; the
    model name is used when it is not given). `ask` replaces the network
    call — `ask(messages, grammar) -> text` — which is how the tests run
    this without a server.
    """
    import grammar as grammar_mod
    import prompts
    import reply

    profile = prompts.profile_for(family or endpoint.model)
    brief_text = prompts.brief(chip, profile, request, delivery=delivery)
    gbnf = grammar_mod.gbnf(chip, request) if delivery == "grammar" else None
    result = Generation(chip, endpoint.model, profile, delivery)
    result.brief_tokens = prompts.estimate_tokens(brief_text)
    ask = ask or (lambda messages, g: chat(endpoint, messages, temperature,
                                           grammar=g))

    last_reply = feedback = None
    for number in range(1, max(1, rounds) + 1):
        messages = messages_for(brief_text, profile, last_reply, feedback)
        started = time.time()
        text = ask(messages, gbnf)
        verdict = reply.check(text, chip=chip, request=request)
        result.rounds.append((text, verdict, time.time() - started))
        if log:
            log(f"round {number}: " + ("PLAYABLE" if verdict.ok else
                                       f"{len(verdict.errors)} error(s)"))
        if verdict.ok:
            break
        last_reply, feedback = text, verdict.feedback(delivery)
    return result
