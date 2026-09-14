"""serve.py — the engine, behind a local HTTP door, for the interface.

    python3 python/serve.py            then open http://127.0.0.1:8765

## What this is for

The interface is a static page. It can render the whole reference — every
directive, every preset, the channel bus, the architecture — straight from
`studio.json` with no server at all, because those are facts and facts do
not need a process. What it cannot do alone is turn a score into sound:
that needs the emulation, which is Python.

So this is the narrow bridge. It serves the page, hands over the contract,
and renders scores. Nothing else. Every endpoint is a thing the interface
cannot do for itself, which is the test for whether one belongs here.

## Deliberately stdlib

`http.server` is not a production server and this is not a production
deployment — it binds to the loopback address and serves one person on
their own machine. Reaching for a framework would put a `pip install`
between someone and hearing their track, which is the thing this project
has spent its whole life avoiding.

## Bound to 127.0.0.1, on purpose

Not a configuration default to be overridden: renders write files and this
has no authentication. It is a tool on your own machine.
"""

import json
import os
import sys
import tempfile
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

#: Where the interface lives. Served straight off disk so editing a file
#: and reloading is the whole development cycle.
STATIC_ROOT = os.path.join(_ROOT, "studio")

DEFAULT_PORT = 8765

#: A score longer than this is refused rather than rendered. Not about
#: security — it is a local tool — but about a typo in a repeat count
#: turning into a render that never returns and looks like a hang.
MAX_SCORE_BYTES = 512 * 1024

#: Renders older than this are deleted when the next one arrives. They are
#: whole WAVs; a session of experimenting would otherwise fill a disk.
RENDER_TTL_SECONDS = 3600

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".wav": "audio/wav",
    ".vgm": "application/octet-stream",
    ".vgz": "application/octet-stream",
}


class _Renders:
    """Rendered files, kept on disk under one temporary directory."""

    def __init__(self):
        self.directory = tempfile.mkdtemp(prefix="chipgen-studio-")
        self.lock = threading.Lock()
        self.made = {}                 # token -> (path, created)

    def sweep(self):
        now = time.time()
        with self.lock:
            stale = [token for token, (_p, born) in self.made.items()
                     if now - born > RENDER_TTL_SECONDS]
            for token in stale:
                path, _born = self.made.pop(token)
                try:
                    os.remove(path)
                except OSError:
                    pass

    def path_for(self, suffix: str):
        token = uuid.uuid4().hex[:16] + suffix
        path = os.path.join(self.directory, token)
        with self.lock:
            self.made[token] = (path, time.time())
        return token, path

    def resolve(self, token: str):
        with self.lock:
            entry = self.made.get(token)
        return entry[0] if entry else None


RENDERS = _Renders()


def render_score(payload: dict) -> dict:
    """Render a score and report everything the interface shows about it.

    Returns the warnings too, and that is the point of rendering through
    the same path the CLI uses rather than a shortcut: a score that
    renders but trips the arrangement checks should say so in the
    interface exactly as it does in the terminal.
    """
    import chipgen

    score = payload.get("score") or ""
    if not score.strip():
        return {"error": "empty score"}
    if len(score.encode("utf-8")) > MAX_SCORE_BYTES:
        return {"error": f"score is larger than {MAX_SCORE_BYTES // 1024} KB"}

    RENDERS.sweep()
    wav_token, wav_path = RENDERS.path_for(".wav")
    vgm_token, vgm_path = RENDERS.path_for(".vgm")

    bank = payload.get("bank") or None
    started = time.time()
    result = chipgen.compose(
        score, wav=wav_path, vgm=vgm_path,
        chip_type=payload.get("chip") or "ym2612",
        bank=bank, opl_bank=payload.get("opl_bank") or None,
        normalize=payload.get("peak", 0.89) or None,
        quiet=True)

    out = {
        "ok": True,
        "duration": result.duration,
        "peak": result.peak,
        "events": len(result.events),
        "sample_rate": result.sample_rate,
        "warnings": list(result.warnings),
        "wav": f"/render/{wav_token}",
        "vgm": f"/render/{vgm_token}",
        "seconds_to_render": round(time.time() - started, 2),
    }
    if payload.get("profile"):
        import profile as profile_mod
        stats = result.profile()
        out["profile"] = [
            {"label": s.label, "start": s.start, "end": s.end,
             "rms": s.rms, "peak": s.peak}
            for s in (stats or [])]
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "chipgen-studio"

    # -- plumbing ----------------------------------------------------------
    def log_message(self, fmt, *args):
        # One line per request, on stderr, without the default's noise.
        sys.stderr.write(f"  {self.command} {self.path} — {fmt % args}\n")

    def _send(self, code, body, content_type="application/json; charset=utf-8",
              extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # The page and the API are same-origin, so no CORS is needed and
        # none is given: a header that lets any page talk to this is not
        # something to add speculatively.
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, code, message):
        self._send(code, {"error": message})

    # -- routes ------------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            path = "/index.html"

        if path == "/api/manifest":
            import studio
            return self._send(200, studio.manifest())

        if path == "/api/health":
            import studio
            return self._send(200, studio.health())

        if path.startswith("/render/"):
            token = path[len("/render/"):]
            resolved = RENDERS.resolve(token)
            if not resolved or not os.path.exists(resolved):
                return self._error(404, "no such render (it may have expired)")
            with open(resolved, "rb") as handle:
                body = handle.read()
            suffix = os.path.splitext(resolved)[1]
            return self._send(
                200, body, _MIME.get(suffix, "application/octet-stream"),
                {"Content-Disposition":
                 f'attachment; filename="chipgen{suffix}"'})

        return self._static(path)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/api/render":
            return self._error(404, f"no route {path}")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._error(400, "bad Content-Length")
        if length <= 0:
            return self._error(400, "empty request")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as error:
            return self._error(400, f"body is not JSON: {error}")

        try:
            return self._send(200, render_score(payload))
        except Exception as error:
            # A tracker error is the normal way to be wrong here — a typo
            # in a score — so it comes back as a message the interface can
            # show beside the line, not as a 500 with a stack trace.
            name = type(error).__name__
            if name in ("TrackerError", "KeyError", "ValueError"):
                return self._send(200, {"ok": False, "error": str(error),
                                        "kind": name})
            traceback.print_exc()
            return self._error(500, f"{name}: {error}")

    def _static(self, path):
        if ".." in path or not path.startswith("/"):
            return self._error(400, "bad path")
        resolved = os.path.normpath(os.path.join(STATIC_ROOT, path[1:]))
        if not resolved.startswith(STATIC_ROOT) or not os.path.isfile(resolved):
            return self._error(404, f"no such file {path}")
        with open(resolved, "rb") as handle:
            body = handle.read()
        suffix = os.path.splitext(resolved)[1]
        self._send(200, body, _MIME.get(suffix, "application/octet-stream"))


def serve(port: int = DEFAULT_PORT, host: str = "127.0.0.1"):
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"chipgen studio on http://{host}:{port}")
    print(f"  interface: {STATIC_ROOT}")
    print(f"  renders:   {RENDERS.directory}")
    print("  ctrl-c to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


def main(argv):
    import argparse
    parser = argparse.ArgumentParser(description="serve the chipgen studio")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1",
                        help="loopback by default and that is the intent — "
                             "this has no authentication and writes files")
    parser.add_argument("--dump", metavar="PATH",
                        help="write studio.json and exit, so the interface "
                             "works from file:// with no server at all")
    args = parser.parse_args(argv)

    if args.dump:
        import studio
        with open(args.dump, "w", encoding="utf-8") as handle:
            json.dump(studio.manifest(), handle, ensure_ascii=False, indent=1)
        print(f"wrote {args.dump}")
        return 0

    serve(args.port, args.host)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
