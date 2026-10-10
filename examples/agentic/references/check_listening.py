"""Is an audio model listening here? The check, written down.

    python3 examples/agentic/references/check_listening.py [OUT.json]

1. The configured audio model, if any (CHIPGEN_AUDIO_ENDPOINT and
   CHIPGEN_AUDIO_MODEL: an OpenAI-compatible endpoint that takes an
   `input_audio` part), put through the probes (ear.verify): how many
   tones, which of two is higher — answers that are in the audio only.
   Without one, the boundary: every observation is measured or assumed,
   and nothing is reported as heard.
2. The probes themselves, checked on two stand-ins that are not models: one
   that reads the WAV it is sent (it answers from the samples) and one that
   only claims to take audio (it answers from the text). The probes must
   pass the first and fail the second; if they could not tell them apart,
   a "verified" model would mean nothing.
"""

import array
import io
import json
import os
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "python"))

from agent import Agent                     # noqa: E402
from agentic import ear as EAR              # noqa: E402


class ReadsTheWav:
    """Answers the probes from the samples: tones by their energy, pitch by
    zero crossings. Not a model — the probes' positive control."""

    name = "stand-in that reads the WAV"

    def declares_audio(self):
        return True

    def listen(self, wav, prompt):
        with wave.open(io.BytesIO(wav)) as w:
            rate = w.getframerate()
            pcm = array.array("h", w.readframes(w.getnframes()))
        hop = rate // 100
        loud = [max(abs(v) for v in pcm[i:i + hop]) > 2000
                for i in range(0, len(pcm) - hop, hop)]
        spans, start = [], None
        for i, on in enumerate(loud + [False]):
            if on and start is None:
                start = i
            if not on and start is not None:
                spans.append((start * hop, i * hop))
                start = None
        if "How many" in prompt:
            return str(len(spans))

        def crossings(a, b):
            return sum(1 for j in range(a + 1, b)
                       if (pcm[j - 1] < 0) != (pcm[j] < 0)) / (b - a)
        return "higher" if crossings(*spans[1]) > crossings(*spans[0]) \
            else "lower"


class OnlyClaims:
    """Says it takes audio and answers from the question's text. The
    probes' negative control."""

    name = "stand-in that only claims to hear"

    def declares_audio(self):
        return True

    def listen(self, wav, prompt):
        return "3" if "How many" in prompt else "higher"


def _adapter():
    url = os.environ.get("CHIPGEN_AUDIO_ENDPOINT")
    if not url:
        return None
    import llm
    endpoint = llm.Endpoint(url=url,
                            model=os.environ.get("CHIPGEN_AUDIO_MODEL"))
    return EAR.OpenAIAudioAdapter(endpoint, declared=True)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    out = argv[0] if argv else os.path.join(
        ROOT, "examples", "agentic", "_work", "audio_model_check.json")
    adapter = _adapter()
    agent = Agent(os.path.join(os.path.dirname(os.path.abspath(out)),
                               "check_projects"), adapter=adapter)
    report = {"configured": agent.call("check_listening")}
    controls = {}
    for stand_in, should in ((ReadsTheWav(), True), (OnlyClaims(), False)):
        v = EAR.verify(stand_in)
        controls[stand_in.name] = {
            "verified": v["verified"], "should_be": should,
            "answers": v["answers"]}
    report["probe_controls"] = controls
    report["probes_tell_them_apart"] = all(
        c["verified"] == c["should_be"] for c in controls.values())
    report["what_follows"] = (
        "an audio model answered the probes: its observations are reported "
        "as heard by model, apart from the measurements"
        if report["configured"].get("available") else
        "no audio model is configured here: every observation in the run "
        "is measured or assumed and says so; no change is reported as an "
        "auditory improvement, only as a measured one")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1, ensure_ascii=False)
    print(json.dumps({k: report[k] for k in ("probes_tell_them_apart",
                                             "what_follows")}, indent=1))
    return 0 if report["probes_tell_them_apart"] else 1


if __name__ == "__main__":
    sys.exit(main())
