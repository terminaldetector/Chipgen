"""The acceptance tests T1-T10 of the hearing audit (22 October), run here.

Each audit test is mapped to the suite's tests that exercise it — no
second implementation — and run; the table says what passed, whether the
ear in it was a stand-in (an ear that decides from the samples it is sent,
tests/test_agentic_refs._ChoosingEar) or a live audio model, and what it
cost. T1 and T3 ask for a live multimodal model: with CHIPGEN_AUDIO_ENDPOINT
set (examples/agentic/references/check_listening.py) its probe check runs
too; without one they are reported as "stand-in only", never as done.

    python3 examples/agentic/acceptance.py [--out examples/agentic/acceptance]

Writes acceptance.json and acceptance.md to the folder.
"""

import argparse
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(ROOT, "tests"), os.path.join(ROOT, "python")]

#: audit test -> (what it asks, the suite's tests, whose ear)
PLAN = [
    ("T1", "a live model receives the WAV and hears a change of attack or "
           "vibrato; identical and swapped controls",
     ["test_agentic.test_an_ear_is_native_only_when_it_answers_from_the_audio",
      "test_agentic_refs.test_a_choice_is_obeyed_only_from_an_ear_that_"
      "passed_the_music_probes",
      "test_agentic_refs.test_an_ear_that_hears_drums_that_are_not_there_"
      "is_not_trusted"], "live model needed"),
    ("T2", "a heard complaint with no metric warning becomes an FM "
           "program change, not no_metric",
     ["test_agentic_refs.test_a_heard_instrument_problem_becomes_a_program_"
      "the_ear_picks",
      "test_agentic_refs.test_phases_the_model_heard_change_the_instrument_"
      "it_then_picks"], "stand-in"),
    ("T3", "among candidates the ear chooses by sound, or says uncertain",
     ["test_agentic_refs.test_the_ear_chooses_among_versions_it_is_sent_in_"
      "stereo",
      "test_agentic_refs.test_a_heard_gap_is_acted_on_only_when_the_ear_"
      "chooses_a_change"], "stand-in; live model needed"),
    ("T4", "stereo stays stereo (Mega Drive, IT); the NES is sent mono",
     ["test_agentic_refs.test_the_ear_chooses_among_versions_it_is_sent_in_"
      "stereo",
      "test_agentic_refs.test_the_ear_on_the_nes_is_sent_mono_and_chooses_"
      "the_change",
      "test_agentic_refs.test_an_it_change_is_chosen_by_the_ear_it_is_sent_"
      "in_stereo"], "stand-in"),
    ("T5", "the end of a long reference can be heard; VGM plays or is "
           "refused by name",
     ["test_agentic_refs.test_a_reference_is_kept_whole_and_says_when_it_"
      "was_cut",
      "test_agentic_refs.test_a_vgm_reference_plays_its_own_log_and_reads_"
      "its_notes",
      "test_agentic_refs.test_a_format_nothing_here_plays_is_refused_by_name",
      "test_agentic_refs.test_a_reference_is_cut_from_a_phrase_and_a_"
      "cadence_ends_on_one"], "no ear"),
    ("T6", "a chased window is marked approximate; what the ear hears is "
           "played from the top",
     ["test_agentic_refs.test_what_the_ear_hears_is_played_from_the_top",
      "test_agentic.test_a_range_render_with_the_state_chased_in_sounds_"
      "like_the_song"], "no ear"),
    ("T7", "an instrument phase improved without notes, tempo or chip "
           "limits moving; A/B matched and raw",
     ["test_agentic_refs.test_an_instrument_said_phase_by_phase_moves_"
      "those_phases_only"], "measured (no ear)"),
    ("T8", "the memory keeps the real judgement; a remembered change is "
           "rendered and checked again",
     ["test_agentic_refs.test_a_learning_record_keeps_what_the_model_heard",
      "test_agentic.test_memory_makes_a_similar_task_cheaper_and_is_"
      "checked_again"], "stand-in"),
    ("T9", "an IT module heard as played, from the top, no silent "
           "substitution",
     ["test_agentic_refs.test_an_it_modules_buried_channel_is_brought_out_"
      "in_its_header",
      "test_agentic_refs.test_what_the_module_loop_does_not_change_is_"
      "refused_by_name"], "measured; partial (header levels and pans)"),
    ("T10", "the cost is counted (calls, audio seconds sent, clips by "
            "hash) and the engine's own tests stay green",
     ["test_agentic_refs.test_the_audible_session_keeps_what_the_model_was_"
      "sent"], "stand-in; and the full suite"),
]


def _run(name: str) -> dict:
    import support
    module, fn = name.split(".", 1)
    started = time.time()
    try:
        getattr(__import__(module), fn)()
        result = "passed"
        why = None
    except support.Skipped as error:
        result, why = "skipped", str(error)
    except Exception as error:               # a failure is a result
        result = "failed"
        why = f"{type(error).__name__}: {error}"[:300]
        traceback.print_exc()
    return {"test": name, "result": result, "why": why,
            "seconds": round(time.time() - started, 1)}


def _live_check(out: str) -> dict:
    """The tone and music probes, then T1 and T3 asked of the model itself
    (agentic/live.py), when CHIPGEN_AUDIO_ENDPOINT names one."""
    from agentic import ear as EAR
    adapter = EAR.adapter_from_env()
    if adapter is None:
        return {"ran": False, "why": "CHIPGEN_AUDIO_ENDPOINT is not set: no "
                "live audio model here"}
    from agentic import live as LIVE
    v = EAR.verify(adapter)
    rec = LIVE.run(adapter, os.path.join(out, "live"), verification=v)
    return {"ran": True, "adapter": adapter.name,
            "verified": v.get("verified"), "music": v.get("music"),
            "why": v.get("why"), "trusted": rec.get("trusted"),
            "passed": rec.get("passed"), "questions": rec.get("questions"),
            "session": rec.get("session")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "acceptance"))
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    rows = []
    for tid, asks, tests, ear in PLAN:
        runs = [_run(t) for t in tests]
        ok = all(r["result"] == "passed" for r in runs)
        rows.append({"id": tid, "asks": asks, "ear": ear,
                     "result": "passed" if ok and runs else
                     ("not run here" if not runs else "failed"),
                     "runs": runs,
                     "seconds": round(sum(r["seconds"] for r in runs), 1)})
        print(f"{tid}: {rows[-1]['result']} ({rows[-1]['seconds']} s)",
              flush=True)
    live = _live_check(a.out)
    for r in rows:
        if r["id"] not in ("T1", "T3"):
            continue
        if r["result"] == "passed":
            r["result"] = "stand-in tests passed"
        if not live["ran"]:
            r["live"] = f"with a live model: not done ({live['why']})"
        elif not live.get("verified"):
            r["live"] = f"the live model failed the tone probes: {live['why']}"
        else:
            ok = (live.get("passed") or {}).get(r["id"])
            asked = [q for q in live.get("questions") or []
                     if q["question"].startswith(r["id"])]
            r["live"] = (f"live model {live['adapter']}: "
                         f"{'PASSED' if ok else 'FAILED'} — " + "; ".join(
                             f"{q['question']}: picked {q['picked']} "
                             f"({'right' if q['right'] else 'wrong'})"
                             for q in asked) +
                         ("" if (live.get("trusted") or {}).get("trusted")
                          else " (not trusted to choose: "
                               f"{(live.get('trusted') or {}).get('why')})"))
    seconds = (live.get("session") or {}).get("audio_seconds", 0)
    rows[-1]["note"] = ("run python3 tests/run_tests.py for the engine; "
                        f"audio seconds sent to a live model: {seconds}; "
                        "renders and runtime are in each record's cost")
    report = {"date": time.strftime("%Y-%m-%d"), "live_model": live,
              "tests": rows}
    with open(os.path.join(a.out, "acceptance.json"), "w",
              encoding="utf-8") as handle:
        json.dump(report, handle, indent=1)
    lines = ["# Acceptance T1-T10 (hearing audit)", "",
             f"Live model: {'yes' if live['ran'] else 'no'} — "
             f"{live.get('why') or live.get('adapter')}", "",
             "| test | asks | ear | result | s |", "|---|---|---|---|---|"]
    for r in rows:
        res = r["result"] + (f"; {r['live']}" if r.get("live") else "")
        lines.append(f"| {r['id']} | {r['asks']} | {r['ear']} | {res} | "
                     f"{r['seconds']} |")
    with open(os.path.join(a.out, "acceptance.md"), "w",
              encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return 0 if all(r["result"] != "failed" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
