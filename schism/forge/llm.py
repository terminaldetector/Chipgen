"""llm.py — ask a model one question, over the OpenAI chat protocol.

Ollama, llama.cpp's server, LM Studio and vLLM all serve the OpenAI chat
completions protocol on a local port, and so do most cloud providers, so one
small client reaches a local 7B model and a frontier one alike with nothing
installed: urllib and json are the whole dependency list.

The forge asks very little of a model (which way to push a few dials; which
of six finalists to keep), and `director.py` makes sure that a model which
is down, slow or answering in prose costs a run nothing. This module is
only the wire.
"""

import json
import os
import urllib.error
import urllib.request

DEFAULT_ENDPOINT = "http://localhost:11434/v1"


class Endpoint:
    """A chat-completions server and the model to ask on it."""

    def __init__(self, url: str = None, model: str = None,
                 api_key: str = None, timeout: float = 600.0):
        env = os.environ
        self.url = (url or env.get("SCHISM_ENDPOINT")
                    or env.get("CHIPGEN_ENDPOINT") or DEFAULT_ENDPOINT
                    ).rstrip("/")
        self.model = (model or env.get("SCHISM_MODEL")
                      or env.get("CHIPGEN_MODEL") or "")
        self.api_key = (api_key or env.get("SCHISM_API_KEY")
                        or env.get("CHIPGEN_API_KEY")
                        or env.get("OPENAI_API_KEY") or "")
        self.timeout = timeout
        if not self.model:
            raise ValueError("name the model to ask: --model, or "
                             "SCHISM_MODEL in the environment")

    def __repr__(self):
        return f"Endpoint({self.url!r}, model={self.model!r})"


def chat(endpoint: Endpoint, messages: list, temperature: float = 0.7,
         max_tokens: int = 600) -> str:
    """One chat completion. -> the reply text."""
    body = {"model": endpoint.model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens,
            "stream": False}
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
