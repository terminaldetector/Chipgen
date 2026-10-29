"""The local half: a grammar, a client, and the loop that holds a model to
the rules without spending its context on them.

No model runs here — there is none in a test sandbox, and a test that
needs one is a test nobody runs. The protocol is exercised against a
fake OpenAI-compatible server instead, which is what Ollama, llama.cpp,
LM Studio and vLLM all look like from the outside.
"""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import support


def _good_score(chip, bars=1, bpm=140):
    import prompts
    rows = []
    while len(rows) < bars * 16:
        rows += prompts.EXAMPLES[chip]
    return "\n".join(prompts.template(chip, {"bpm": bpm})
                     + rows[:bars * 16]) + "\n"


# --------------------------------------------------------------------------
# The grammar
# --------------------------------------------------------------------------
def test_the_grammar_accepts_what_the_engine_plays():
    import grammar
    import prompts

    for chip in prompts.BRIEF_CHIPS:
        g = grammar.gbnf(chip, {"bpm": 140, "bars": 1})
        assert grammar.matches(g, _good_score(chip)), chip


def test_the_grammar_holds_the_row_count_exactly():
    """A small model under a grammar can neither stop early nor run on."""
    import grammar

    g = grammar.gbnf("RP2A03", {"bpm": 140, "bars": 1})
    score = _good_score("RP2A03")
    assert grammar.matches(g, score)
    lines = score.splitlines()
    assert not grammar.matches(g, "\n".join(lines[:-1]) + "\n")
    assert not grammar.matches(g, score + lines[-1] + "\n")


def _violations():
    """One score per rule the briefing says the grammar enforces."""
    good = {chip: _good_score(chip) for chip in ("YM2612", "RP2A03",
                                                 "YM3812")}

    def first_row(chip, row):
        lines = good[chip].splitlines()
        start = next(i for i, l in enumerate(lines)
                     if l.startswith("cols ")) + 1
        lines[start] = row
        return "\n".join(lines) + "\n"

    genesis = good["YM2612"]
    return {
        "fm_needs_inst": ("YM2612", genesis.replace(
            "inst fm1 ", "; inst fm1 ")),
        "psg_15_is_silent": ("YM2612", first_row(
            "YM2612", "A-2 A-4 C-4 E-3 C-5:15 ... ... kick")),
        "psg_floor": ("YM2612", first_row(
            "YM2612", "A-2 A-4 C-4 E-3 G-2 ... ... kick")),
        "dac_level": ("YM2612", genesis.replace("vol dac 55\n", "")),
        "fm5_vs_dac": ("YM2612", genesis.replace("cols fm0", "cols fm5")),
        "closed_lists": ("YM2612", first_row(
            "YM2612", "A-2 A-4 C-4 E-3 ... ... ... cowbell")),
        "chip_columns": ("RP2A03", good["RP2A03"].replace(
            "cols nes0", "cols psg0")),
        "nes_pulse_floor": ("RP2A03", first_row(
            "RP2A03", "G-1 C-5 A-2 ... kick")),
        "nes_triangle_floor": ("RP2A03", first_row(
            "RP2A03", "E-5 C-5 G-0 ... kick")),
        "nes_triangle_velocity": ("RP2A03", first_row(
            "RP2A03", "E-5 C-5 A-2:40 ... kick")),
        "nes_noise_cells": ("RP2A03", first_row(
            "RP2A03", "E-5 C-5 A-2 C-4 kick")),
        "note_format": ("YM3812", first_row(
            "YM3812", "Bb1 E-4 C-4 A-3 C-2 ...")),
        "cols_first": ("YM3812", first_row(
            "YM3812", "A-1 E-4 C-4 A-3 C-2")),
        "row_count": ("YM3812", good["YM3812"] + "... ... ... ... ... ...\n"),
    }


def test_the_grammar_forbids_every_rule_the_briefing_says_it_enforces():
    """grammar delivery leaves these rules out of the briefing. If the
    grammar does not actually hold one, the model is neither told nor
    stopped."""
    import grammar
    import prompts

    violations = _violations()
    missing = prompts.GRAMMAR_ENFORCED - set(violations)
    assert not missing, f"no violation case for {sorted(missing)}"
    for rule, (chip, score) in violations.items():
        g = grammar.gbnf(chip, {"bpm": 140, "bars": 1})
        assert not grammar.matches(g, score), \
            f"the {chip} grammar lets {rule!r} through"


def test_every_rule_a_grammar_uses_is_defined_and_none_recurse():
    """llama.cpp rejects a grammar with an undefined rule at load time —
    after the user has started the server and sent the request."""
    import grammar
    import prompts

    for chip in prompts.BRIEF_CHIPS:
        grammar.to_regex(grammar.gbnf(chip, {"bars": 2}))  # raises if not


def test_the_grammar_offers_only_the_real_banks():
    import grammar
    import prompts
    import samples

    g = grammar.gbnf("YM2612")
    for name in prompts.instrument_names("fm") + samples.names():
        assert f'"{name}"' in g, name
    g = grammar.gbnf("YM3812")
    for name in prompts.instrument_names("opl"):
        assert f'"{name}"' in g, name


# --------------------------------------------------------------------------
# A fake chat-completions server
# --------------------------------------------------------------------------
class _Fake:
    """Answers each POST with the next scripted reply, records requests."""

    def __init__(self, replies, status=200):
        self.replies = list(replies)
        self.requests = []
        self.status = status
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length))
                fake.requests.append({"path": self.path, "body": body,
                                      "auth": self.headers.get(
                                          "Authorization")})
                if fake.status != 200:
                    self.send_response(fake.status)
                    self.end_headers()
                    self.wfile.write(b'{"error": "model not found"}')
                    return
                text = fake.replies.pop(0) if fake.replies else ""
                content = text if not isinstance(text, list) else text
                payload = {"choices": [{"message": {"role": "assistant",
                                                    "content": content}}]}
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        return False


def test_the_client_speaks_the_chat_completions_protocol():
    import llm

    with _Fake(["hello"]) as fake:
        endpoint = llm.Endpoint(fake.url, model="qwen2.5:7b",
                                api_key="secret")
        text = llm.chat(endpoint, [{"role": "user", "content": "hi"}])
    assert text == "hello"
    request = fake.requests[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["body"]["model"] == "qwen2.5:7b"
    assert request["body"]["messages"][0]["content"] == "hi"
    assert request["auth"] == "Bearer secret"


def test_the_grammar_goes_in_the_field_the_server_reads():
    import llm

    with _Fake(["x", "x", "x"]) as fake:
        for field, key in (("llama.cpp", "grammar"),
                           ("vllm", "guided_grammar"), (None, None)):
            endpoint = llm.Endpoint(fake.url, model="m",
                                    grammar_field=field)
            llm.chat(endpoint, [{"role": "user", "content": "x"}],
                     grammar="root ::= \"a\"")
    bodies = [r["body"] for r in fake.requests]
    assert bodies[0]["grammar"] == "root ::= \"a\""
    assert bodies[1]["guided_grammar"] == "root ::= \"a\""
    assert "grammar" not in bodies[2] and "guided_grammar" not in bodies[2]


def test_content_parts_are_joined():
    import llm

    with _Fake([[{"type": "text", "text": "a"},
                 {"type": "text", "text": "b"}]]) as fake:
        endpoint = llm.Endpoint(fake.url, model="m")
        assert llm.chat(endpoint, [{"role": "user", "content": "x"}]) == "ab"


def test_a_server_error_says_what_the_server_said():
    import llm

    with _Fake([], status=404) as fake:
        endpoint = llm.Endpoint(fake.url, model="nope")
        try:
            llm.chat(endpoint, [{"role": "user", "content": "x"}])
        except RuntimeError as error:
            assert "404" in str(error) and "model not found" in str(error)
        else:
            assert False, "a 404 was taken as an answer"


def test_an_unreachable_server_says_so():
    import llm

    endpoint = llm.Endpoint("http://127.0.0.1:9", model="m", timeout=5)
    try:
        llm.chat(endpoint, [{"role": "user", "content": "x"}])
    except RuntimeError as error:
        assert "cannot reach" in str(error)
    else:
        assert False


def test_no_model_named_is_an_error_before_any_request():
    import llm

    saved = os.environ.pop("CHIPGEN_MODEL", None)
    try:
        llm.Endpoint("http://127.0.0.1:9")
    except ValueError as error:
        assert "--model" in str(error)
    else:
        assert False
    finally:
        if saved is not None:
            os.environ["CHIPGEN_MODEL"] = saved


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------
BROKEN_NES = ("Sure! Here is a castle theme.\n```trk\nbpm 140\nlpb 4\n"
              "cols nes0 nes1 nes2 nes3 nes4\nE-5 C-5 A-2 C-4 kick\n```")


def test_the_loop_feeds_the_errors_back_and_stops_when_it_plays():
    import llm

    good = "```trk\n" + _good_score("RP2A03") + "```"
    with _Fake([BROKEN_NES, good]) as fake:
        endpoint = llm.Endpoint(fake.url, model="gpt-like")
        result = llm.generate("RP2A03", {"prompt": "castle", "bars": 1},
                              endpoint)
    assert result.ok and len(result.rounds) == 2, result.summary()
    second = fake.requests[1]["body"]["messages"]
    assert second[-2]["content"] == BROKEN_NES
    assert "nes3 takes a number" in second[-1]["content"]


def test_each_round_carries_only_the_last_attempt():
    """A small context window is not spent on a model's earlier mistakes."""
    import llm

    with _Fake([BROKEN_NES, BROKEN_NES, BROKEN_NES]) as fake:
        endpoint = llm.Endpoint(fake.url, model="gpt-like")
        result = llm.generate("RP2A03", {"bars": 1}, endpoint, rounds=3)
    assert not result.ok and len(result.rounds) == 3
    sizes = [len(r["body"]["messages"]) for r in fake.requests]
    assert sizes == [2, 4, 4], sizes


def test_the_family_picks_the_brief_and_the_brief_reaches_the_model():
    import llm
    import prompts

    with _Fake(["```trk\n" + _good_score("YM3812") + "```"]) as fake:
        endpoint = llm.Endpoint(fake.url, model="llama3.2:3b")
        result = llm.generate("YM3812", {"bars": 1}, endpoint)
    assert result.ok and result.profile == "compact"
    messages = fake.requests[0]["body"]["messages"]
    # Compact: the brief leads the first user turn, no system role.
    assert [m["role"] for m in messages] == ["user"]
    assert prompts.brief("YM3812", "compact", {"bars": 1}) in \
        messages[0]["content"]


def test_a_thinking_block_is_set_aside_before_extraction():
    """A draft fenced inside <think> would otherwise win extraction."""
    import reply

    text = ("<think>maybe\n```trk\nbpm 1\ncols fm0\nZZZ\n```\n</think>\n"
            "```trk\n" + _good_score("RP2A03") + "```")
    verdict = reply.check(text, chip="RP2A03")
    assert verdict.ok, verdict.report()
    assert any("think" in note for note in verdict.extracted)


def test_generate_from_the_command_line_writes_a_score_and_a_wav():
    good = "```trk\n" + _good_score("RP2A03") + "```"
    with _Fake([BROKEN_NES, good]) as fake, support.TempDir() as directory:
        score = os.path.join(directory, "castle.trk")
        wav = os.path.join(directory, "castle.wav")
        finished = subprocess.run(
            [sys.executable, os.path.join(support.ROOT, "python",
                                          "chipgen.py"),
             "--generate", "castle theme", "--chip-target", "RP2A03",
             "--endpoint", fake.url, "--model", "gpt-like", "--bars", "1",
             "--tracker", score, "-o", wav],
            capture_output=True, text=True, cwd=directory, timeout=300)
        assert finished.returncode == 0, finished.stdout + finished.stderr
        assert "round 2: PLAYABLE" in finished.stdout, finished.stdout
        assert os.path.getsize(wav) > 1000
        with open(score, encoding="utf-8") as handle:
            assert handle.read().startswith("bpm 140")


def test_the_cli_prints_a_brief_a_grammar_and_a_full_request():
    root = support.ROOT
    run = lambda *argv: subprocess.run(
        [sys.executable, os.path.join(root, "python", "chipgen.py"), *argv],
        capture_output=True, text=True, cwd=root, timeout=120)

    brief = run("--brief", "--chip-target", "RP2A03", "--family", "gpt",
                "--describe", "castle", "--bars", "2")
    assert brief.returncode == 0 and "exactly 32 rows" in brief.stdout

    gbnf = run("--grammar", "--chip-target", "YM3812", "--bars", "2")
    assert gbnf.returncode == 0 and gbnf.stdout.startswith("root ::=")

    # This path crashed with AttributeError: there was no --bpm flag.
    full = run("--prompt", "--chip-target", "RP2A03", "--describe",
               "castle", "--bpm", "144")
    assert full.returncode == 0, full.stderr[-400:]
    assert "castle" in full.stdout and "144 BPM" in full.stdout
