"""
tests/run_tests.py — the test suite, runnable with nothing installed.

    python3 tests/run_tests.py            everything
    python3 tests/run_tests.py vgm        only tests whose name contains "vgm"
    python3 tests/run_tests.py -v         print every test as it runs

pytest works too (`pytest tests/`) — the test functions are ordinary
`test_*` callables and the assertions are plain `assert`. But chipgen's
promise is that it runs in a sandbox with no network, and a test suite
that needed `pip install pytest` before it could tell you whether the
engine works would undercut that on the first day someone tried it.

## In a sandbox with a short time limit

The full suite renders a great deal of audio and takes minutes in pure
Python — longer than many sandboxes let one command run. Two ways round
that, both of which end in the same full result:

    python3 tests/run_tests.py --budget 100      run for 100 s, then stop
    python3 tests/run_tests.py --resume          and carry on from there

`--budget` stops between tests once the time is spent and saves where it
got to; `--resume` (with or without its own `--budget`) picks up from that
point, and the run that finishes the suite reports every result from
every piece and stamps it exactly as an uninterrupted run would.

    python3 tests/run_tests.py --quick           nine tenths of it, in a minute

`--quick` leaves out the tests that render a lot of audio — a tenth of
them, nine tenths of the time — and runs the rest: every module, every
chip. It says that it was quick, and it is never reported as the suite.
"""

import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
for path in (os.path.join(ROOT, "python"), HERE):
    if path not in sys.path:
        sys.path.insert(0, path)

MODULES = ["test_events", "test_tracker", "test_chips", "test_vgm",
           "test_render", "test_bridge", "test_sanity", "test_profile",
           "test_furnace", "test_it", "test_opl", "test_transcribe", "test_effects",
           "test_analysis", "test_selection", "test_musical", "test_nes",
           "test_fx", "test_livefm", "test_pcm", "test_patterns_notation", "test_ch3", "test_nes_score", "test_examples", "test_levels", "test_studio", "test_arrange", "test_briefs", "test_reply", "test_local", "test_mp3", "test_synthesis", "test_program"]


def _collect(pattern=""):
    cases = []
    for name in MODULES:
        module = __import__(name)
        for attribute in sorted(dir(module)):
            if not attribute.startswith("test_"):
                continue
            full = f"{name}.{attribute}"
            if pattern and pattern not in full:
                continue
            cases.append((full, getattr(module, attribute)))
    return cases


#: Where an interrupted run keeps its place: one file per kind of run, so
#: a filtered run started in the middle of a full one in pieces cannot
#: wipe the full one's place. Per-run, never committed.
def _state_path(pattern, quick):
    tag = "_quick" if quick else ""
    if pattern:
        tag += "_" + "".join(c if c.isalnum() else "_" for c in pattern)
    return os.path.join(HERE, f"run_state{tag}.json")


#: The tests that render enough audio to take a second or more each,
#: measured on a pure-Python sandbox: 53 of the suite, and about
#: nine tenths of its time. `--quick` runs everything else — every module,
#: every chip, in well under a minute — and says it was the quick set.
#: test_studio checks that every name here is still a real test.
SLOW = {
    "test_analysis.test_envelope_times_separate_a_pad_from_a_stab",
    "test_analysis.test_pitch_is_accurate_across_the_chips_musical_range",
    "test_arrange.test_a_score_json_renders_instead_of_producing_silence",
    "test_arrange.test_the_whole_chain_from_midi_to_audio",
    "test_bridge.test_cold_unzip_bootstraps_and_renders",
    "test_briefs.test_the_example_in_every_brief_renders_with_no_warnings",
    "test_effects.test_a_slide_reaches_exactly_where_it_was_aimed_in_the_render",
    "test_effects.test_vibrato_actually_moves_the_pitch_in_the_render",
    "test_examples.test_every_example_renders_with_the_bank_it_needs",
    "test_fx.test_an_arpeggio_runs_on_every_voice_with_a_pitch",
    "test_levels.test_an_uncalibrated_bank_is_reported_against_the_bank_reference",
    "test_levels.test_recalibrating_a_bank_on_disk_matches_calibrating_at_import",
    "test_levels.test_the_built_in_bank_reports_no_calibration_problem",
    "test_levels.test_the_dominance_warning_fires_and_clears",
    "test_livefm.test_a_mid_note_operator_write_changes_the_timbre",
    "test_local.test_generate_from_the_command_line_writes_a_score_and_a_wav",
    "test_mp3.test_a_render_hands_back_a_tenth_of_the_wav",
    "test_mp3.test_the_local_server_offers_an_mp3_when_asked",
    "test_musical.test_a_voice_never_overlaps_itself",
    "test_musical.test_corpus_paths_returns_scores_and_not_banks_or_metadata",
    "test_musical.test_notes_carry_lengths_not_just_onsets",
    "test_musical.test_patterns_are_transposable",
    "test_musical.test_rhythm_and_contour_transfer_between_tracks_but_their_fusion_does_not",
    "test_musical.test_the_voice_called_bass_is_the_lowest_one",
    "test_nes.test_duty_cycle_changes_the_waveform_not_the_pitch",
    "test_nes.test_one_nes_voice_never_plays_two_notes_at_once",
    "test_nes.test_pitch_lands_where_the_timer_says_it_should",
    "test_nes.test_the_nes_corpus_loads_and_holds_only_nes_voices",
    "test_nes_score.test_every_nes_voice_survives_a_vgm_round_trip",
    "test_nes_score.test_sweep_up_raises_the_pitch",
    "test_nes_score.test_the_dmc_is_enabled_or_a_sample_is_exact_silence",
    "test_nes_score.test_the_duty_cycle_changes_the_harmonics_and_75_percent_equals_25",
    "test_nes_score.test_the_low_pulse_octave_is_audible_at_all",
    "test_opl.test_the_waveform_select_has_no_ym2612_equivalent_and_shifts_the_octave",
    "test_pcm.test_a_sample_plays_at_the_pitch_it_is_asked_for",
    "test_profile.test_auto_profile_falls_back_to_bars_without_markers",
    "test_profile.test_catches_a_note_that_leaked_into_a_quiet_section",
    "test_render.test_psg_no_longer_buries_the_fm_chip",
    "test_render.test_pure_python_cores_agree_with_the_native_ones",
    "test_sanity.test_compose_surfaces_sanity_warnings",
    "test_sanity.test_crowding_is_checked_on_every_chip_not_just_fm",
    "test_sanity.test_the_psg_floor_is_measured_not_assumed",
    "test_sanity.test_the_stereo_warning_is_only_for_scores_with_fm_in_them",
    "test_selection.test_characteristics_only_measures_what_it_does_not_already_have",
    "test_selection.test_measuring_an_imported_bank_leaves_the_built_in_cache_alone",
    "test_transcribe.test_a_corpus_records_what_it_rejected_and_why",
    "test_transcribe.test_a_transcription_parses_back_as_tracker_text",
    "test_transcribe.test_the_notes_a_score_played_come_back_out_of_its_vgm",
    "test_transcribe.test_the_tempo_of_a_known_score_is_recovered",
    "test_vgm.test_fast_lfo_drifts_between_direct_render_and_vgm_replay",
    "test_vgm.test_imported_bank_saves_loads_and_is_playable",
    "test_vgm.test_imported_patches_are_levelled_against_the_built_in_bank",
    "test_vgm.test_replay_alignment_is_bounded_and_does_not_accumulate",
    "test_program.test_a_run_keeps_its_budget_and_can_be_undone_exactly",
    "test_program.test_a_round_of_repeats_stops_the_loop",
    "test_synthesis.test_a_dial_turned_up_reads_higher_on_every_chip",
    "test_synthesis.test_a_director_steers_a_run_and_a_broken_one_cannot_stop_it",
    "test_synthesis.test_a_mix_follows_its_weights_and_a_tree_nests",
    "test_synthesis.test_detune_is_graded_on_every_chip_and_reads_zero_when_there_is_none",
    "test_synthesis.test_fitting_an_arrangement_lowers_its_cost_with_real_renders",
    "test_synthesis.test_the_probe_and_the_sequencer_agree_about_a_layered_instrument",
    "test_synthesis.test_the_search_is_deterministic_and_never_returns_an_invalid_instrument",
}


def _options(argv):
    verbose = "-v" in argv
    resume = "--resume" in argv
    quick = "--quick" in argv
    budget = None
    pattern = ""
    skip = False
    for index, arg in enumerate(argv):
        if skip:
            skip = False
            continue
        if arg == "--budget":
            try:
                budget = float(argv[index + 1])
            except (IndexError, ValueError):
                raise SystemExit("--budget wants a number of seconds, "
                                 "e.g. --budget 100")
            skip = True
        elif not arg.startswith("-") and not pattern:
            pattern = arg
    return verbose, resume, quick, budget, pattern


def _load_state(pattern, quick):
    try:
        with open(_state_path(pattern, quick), encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return None
    if state.get("pattern") != pattern or state.get("quick") != quick:
        return None
    return state


def _save_state(state):
    try:
        with open(_state_path(state["pattern"], state["quick"]), "w",
                  encoding="utf-8") as handle:
            json.dump(state, handle)
    except OSError:
        pass                     # a read-only checkout just cannot resume


def _forget_state(pattern, quick):
    try:
        os.remove(_state_path(pattern, quick))
    except OSError:
        pass


def main(argv):
    verbose, resume, quick, budget, pattern = _options(argv)

    cases = _collect(pattern)
    if quick:
        cases = [case for case in cases if case[0] not in SLOW]
    if not cases:
        print(f"no tests match {pattern!r}")
        return 1

    import support

    state = _load_state(pattern, quick) if resume else None
    if resume and state is None:
        print("nothing to resume — starting from the beginning")
    if state is None:
        _forget_state(pattern, quick)
        state = {"pattern": pattern, "quick": quick, "results": {},
                 "traces": {}, "seconds": 0.0}
    results, traces = state["results"], state["traces"]
    already = sum(1 for name, _ in cases if name in results)
    if already:
        print(f"resuming: {already} of {len(cases)} already run")

    started = time.time()
    for name, function in cases:
        if name in results:
            continue
        if budget is not None and time.time() - started >= budget:
            state["seconds"] += time.time() - started
            _save_state(state)
            done = sum(1 for n, _ in cases if n in results)
            failed = sum(1 for n, _ in cases if results.get(n) == "fail")
            print(f"\nstopped at the {budget:g} s budget: {done} of "
                  f"{len(cases)} run, {failed} failed so far.")
            print(f"continue with: python3 tests/run_tests.py "
                  f"{(pattern + ' ') if pattern else ''}"
                  f"{'--quick ' if quick else ''}--resume --budget "
                  f"{budget:g}")
            return 3
        try:
            function()
            results[name] = "ok"
            mark = "ok  "
        except support.Skipped as exc:
            results[name] = "skip"
            traces[name] = str(exc)
            mark = "skip"
        except Exception:
            results[name] = "fail"
            traces[name] = traceback.format_exc()
            mark = "FAIL"
        if verbose:
            print(f"  {mark} {name}")
        else:
            sys.stdout.write({"ok  ": ".", "skip": "s", "FAIL": "F"}[mark])
            sys.stdout.flush()
        if budget is not None or resume:
            _save_state(state)
    state["seconds"] += time.time() - started

    if not verbose:
        print()
    names = [name for name, _ in cases]
    failures = [name for name in names if results.get(name) == "fail"]
    skipped = [name for name in names if results.get(name) == "skip"]
    for name in failures:
        print(f"\n{'=' * 70}\nFAIL {name}\n{'-' * 70}\n{traces[name]}")
    for name in skipped:
        print(f"skipped: {name}: {traces[name]}")

    total = len(cases)
    passed = total - len(failures) - len(skipped)
    print(f"\n{passed} passed, "
          f"{len(failures)} failed"
          + (f", {len(skipped)} skipped" if skipped else "")
          + f"  ({total} total"
          + (", the quick set" if quick else "") + ")")

    # Stamp the result so studio.health() can report it. An interface
    # asking "are you healthy" must not trigger two minutes of rendering,
    # and shelling out to this runner to answer is exactly that.
    # Only a full run is stamped as the suite: a filtered one says nothing
    # about it, and stamping it would report 6/6 OK on a broken tree. The
    # quick set is stamped too, under its own name.
    if not pattern:
        _stamp(passed, len(failures), len(skipped), total,
               quick=quick, seconds=state["seconds"])
    _forget_state(pattern, quick)
    return 1 if failures else 0


def _stamp(passed, failed, skipped, total, quick=False, seconds=None):
    import datetime

    path = os.path.join(HERE, "last_quick.json" if quick else "last_run.json")
    record = {"passed": passed, "failed": failed, "skipped": skipped,
              "total": total, "scope": "quick" if quick else "full",
              "when": datetime.datetime.now(
                  datetime.timezone.utc).isoformat(timespec="seconds")}
    if seconds is not None:
        record["seconds"] = round(seconds)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=1)
    except OSError:
        pass                     # a read-only checkout is not a failure


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
