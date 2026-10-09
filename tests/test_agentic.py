"""The agentic loop (python/agentic/, python/agent.py): the spec's tests.

    A  a local correction found, located, patched, rendered, compared,
       guarded, committed — and undone exactly
    B  a theme and a variation that develops it (not a copy), the motif
       graph read back from the rows, the harmony read back from the notes
    C  composition in windows, interrupted and resumed, giving the same
       music; no note left hanging, no state left behind at a join
    D  the three ears, and no listening claimed where nothing listened
    E  what was learned makes a similar task cheaper, and is re-checked
    F  the backend's limits refused by name, never degraded in silence

plus the clock, the trace, the patches and the facade they rest on.
"""

import array
import io
import json
import os
import shutil
import tempfile
import wave

import support  # noqa: F401  (puts python/ on the path)


def _tmp():
    return tempfile.mkdtemp(prefix="chipgen_agentic_")


def _project(state):
    from agentic import state as S
    root = os.path.join(_tmp(), "p")
    return S.Project.create(root, state)


# -- state, clock, trace, patches ------------------------------------------------
def test_revisions_roll_back_exactly_and_an_interrupted_state_recovers():
    from agentic import fixtures, state as S
    p = _project(fixtures.masked_lead())
    c = S.content(p.state)
    c["sections"][0]["rows"][0][1] = "A-5:80"
    r1 = p.commit(c, {"kind": "test"})
    assert p.head == r1 == 1
    r2 = p.rollback(0, {"why": "test"})
    assert p.revision(r2)["hash"] == p.revision(0)["hash"]
    assert p.revision(r2)["parent"] == r1
    # a state pointing past the last whole revision is moved back to it
    os.remove(p._revision_path(r2))
    q = S.Project.open(p.root)
    assert q.head == r1 and q.state["recovered"]["from"] == r2
    # a format it does not read is refused, not guessed at
    st = json.load(open(os.path.join(p.root, "state.json")))
    st["format"] = "chipgen-musical-state/99"
    json.dump(st, open(os.path.join(p.root, "state.json"), "w"))
    try:
        S.Project.open(p.root)
        assert False, "an unknown format was read"
    except S.AgenticError as error:
        assert error.code == "unknown_format"
    shutil.rmtree(os.path.dirname(p.root))


def test_one_clock_from_rows_to_seconds_to_source_lines():
    from agentic import fixtures, state as S, timeline as T
    tl = T.build(S.content(fixtures.masked_lead()))
    assert len(tl.rows) == 32 and tl.rows_per_bar == 16
    row_s = 60.0 / 150 / 4
    assert abs(tl.rows[5]["t0"] - 5 * row_s) < 1e-9
    assert tl.rows[5]["tick"] == 5 * 24
    assert tl.range("bars 2")["row0"] == 16
    assert tl.range("rows 0-31")["row1"] == 32
    assert tl.range("t 0.5-1.0")["row0"] == tl.row_at(0.5)["index"]
    lead = [n for n in tl.notes if n["voice"] == "lead"]
    first = tl.rows[lead[0]["row"]]
    assert "E-5:80" in tl.text.splitlines()[first["line"] - 1]
    for spec in ("bars 9", "rows 40-50", "t 9.0-10.0"):
        try:
            tl.range(spec)
            assert False, spec
        except S.AgenticError as error:
            assert error.code == "out_of_piece"
    assert tl.range("bars 2-5")["clamped"]


def test_a_moment_of_audio_traces_back_to_its_note_and_register_writes():
    from agentic import fixtures, state as S, timeline as T, trace
    tl = T.build(S.content(fixtures.masked_lead()))
    lead = [n for n in tl.notes if n["voice"] == "lead"][2]
    out = trace.explain(tl, lead["start"], lead["start"] + 0.05, "lead")
    assert out["notes"][-1]["note"] == lead["name"]
    assert out["notes"][-1]["source"].split()[1].startswith(lead["name"])
    writes = out["register_writes"]
    assert any(w["what"] == "key" and w["value"] == "on" for w in writes)
    assert all(w["channel"] == "fm1" for w in writes)
    assert out["instruments"]["lead"]["patch"] == "square_lead"


def test_a_range_render_with_the_state_chased_in_sounds_like_the_song():
    from agentic import render as R, state as S, timeline as T
    p, _ = _composed()
    tl = T.build(S.content(p.state))
    cache = R.Cache()
    # the third section, entered mid-piece: the pad's note and every
    # instrument come from the chase, not from a render from the top
    # the level envelope is the reading that says it sounds the same; the
    # waveform null depends on the patch (measured: pad -21 dB, but the
    # detuned square_lead -10 dB with its envelope 0.01 dB apart)
    for voices, mean_db in ((["pad"], 0.1), (["lead"], 0.1), (None, 0.6)):
        e = R.chase_error(tl, tl.range("B"), voices=voices, cache=cache)
        assert e["envelope"]["mean_db"] <= mean_db, (voices, e)
    shutil.rmtree(os.path.dirname(p.root))


def test_a_patch_reports_what_it_realised_and_refuses_what_the_chip_cannot():
    from agentic import fixtures, patch as P, state as S, timeline as T
    c = S.content(fixtures.masked_lead())
    tl = T.build(c)
    rng = tl.range("all")
    _new, d = P.level(c, "lead", rng, 6.0)
    # velocity 80 cannot rise 6 dB under the 127 ceiling
    assert d["realised_db"]["max"] < 6.0 and d["asked_db"] == 6.0
    for call, code in ((lambda: P.param(c, "lead", "op2.tl", delta=4),
                        "carrier_level"),
                       (lambda: P.transpose(c, "nope", rng, 12),
                        "no_voice")):
        try:
            call()
            assert False, code
        except S.AgenticError as error:
            assert error.code == code
    new, d = P.param(c, "bass", "op1.tl", delta=8, rng=tl.range("bars 2"))
    texts = [x["text"] for x in new["sections"][0]["directives"]]
    assert texts == ["op fm0 1 tl 20", "op fm0 1 tl 12"]
    assert new["instruments"] == c["instruments"]     # local, not global


# -- B: a theme and its development ----------------------------------------------
def _composed(stop_after=None, root=None, seed=7):
    from agentic import compose as C, fixtures, state as S
    root = root or os.path.join(_tmp(), "p")
    p = S.Project.create(root, fixtures.etude())
    r = C.continue_composition(p, seed=seed, hear=False,
                               stop_after=stop_after)
    return p, r


def test_motif_transformations_are_recognised_from_the_notes():
    from agentic import motif as M
    k = M.Key("A minor")
    m = M.make("m", "lead", [[0, 64, 3], [3, 62, 2], [5, 60, 3],
                             [8, 59, 2], [10, 60, 2]], "A minor")
    for tr in ([{"op": "shift", "degrees": 2}], [{"op": "invert"}],
               [{"op": "retrograde"}], [{"op": "augment"}],
               [{"op": "transpose", "semitones": 3}]):
        hit = M.find(m, M.apply(m["notes"], k, tr), k)[0]
        assert hit["exact"] and hit["transform"] == tr, (tr, hit)
    # one note changed (fitted to a chord) costs one note, not two steps
    varied = M.apply(m["notes"], k, [{"op": "shift", "degrees": 2}])
    varied[2][1] += 1
    hit = M.find(m, varied, k)[0]
    assert hit["notes_matched"] == 4 and not hit["exact"]


def test_a_variation_develops_the_theme_and_the_harmony_holds():
    from agentic import compose as C, loop as L, motif as M, state as S
    from agentic import timeline as T
    p, r = _composed()
    c = S.content(p.state)
    assert r["status"] == "complete"
    assert c["order"] == [e["id"] for e in c["structure"]["plan"]]
    tl = T.build(c)
    assert len(tl.rows) == sum(e["bars"] for e in c["structure"]["plan"]) \
        * tl.rows_per_bar
    g = p.state["motifs"]
    assert list(g["motifs"]) == ["m1"]
    lead = c["columns"].index("fm1")
    a, a2 = (next(s for s in c["sections"] if s["id"] == x)
             for x in ("A", "A2"))
    assert [row[lead] for row in a["rows"]] != \
        [row[lead] for row in a2["rows"]], "the variation copies the theme"
    found = M.analyse(c, g["motifs"], c["structure"]["key"])
    theme = [h for h in found if h["section"] == "A" and h["row"] == 0]
    assert theme and theme[0]["exact"] and theme[0]["transform"] == []
    said = next(i for i in g["instances"]
                if i["section"] == "A2" and i["row"] == 0)["transform"]
    heard = [h for h in found if h["section"] == "A2"]
    assert said and heard and heard[0]["transform"] and \
        any(t["op"] in ("shift", "invert", "retrograde", "transpose")
            for t in heard[0]["transform"])
    for sid in ("A", "A2", "B", "C"):
        h = C.harmony_check(c, sid)
        assert h["root_match"] >= 0.75 and h["bass_on_root"] == 1.0, h
    assert L._bass_above(tl, tl.range("all")) == 0
    for step in r["done"][1:]:
        assert step["transition"]["ok"], step["transition"]
        assert step["transition"]["state_left"] == []
    shutil.rmtree(os.path.dirname(p.root))


# -- C: windows, interruption, resume ----------------------------------------------
def test_an_interrupted_composition_resumes_to_the_same_music():
    from agentic import compose as C, state as S
    whole, _ = _composed()
    base = _tmp()
    root = os.path.join(base, "p")
    part, r = _composed(stop_after=2, root=root)
    assert r["status"] == "stopped" and r["next"] == 2
    assert r["remaining"] == ["B", "C"]
    del part                                    # the session ends
    # it died after the next revision was written, before the state was
    snapshot = open(os.path.join(root, "state.json")).read()
    p = S.Project.open(root)
    C.continue_composition(p, stop_after=1, hear=False)
    open(os.path.join(root, "state.json"), "w").write(snapshot)
    p = S.Project.open(root)
    assert p.state["composition"]["next"] == 2
    r = C.continue_composition(p, hear=False)
    assert r["status"] == "complete"
    a, b = S.content(whole.state), S.content(p.state)
    assert S.content_hash(a) == S.content_hash(b)
    assert C.hanging(b) == []
    last = b["sections"][-1]["rows"][-1]
    for k, col in enumerate(b["columns"]):
        if col.startswith(("fm", "psg")):
            assert last[k] in ("===",) or not last[k].startswith("..."), \
                (col, last[k])
    shutil.rmtree(base)
    shutil.rmtree(os.path.dirname(whole.root))


def test_a_correction_touches_the_section_not_the_bar_it_was_heard_with():
    from agentic import loop as L
    p, _ = _composed()
    heard = {"kind": "buried", "voices": ["lead"],
             "where": {"t0": 2.57, "t1": 6.86, "rows": [16, 63],
                       "bars": [2, 4], "sections": ["A", "A2"]},
             "statement": "lead sits 4.1 dB under the rest of the mix in its "
                          "own band", "evidence": {}, "basis": "measured",
             "confidence": 0.8, "severity": "high"}
    oid = p.add_observation(heard)
    scoped = p.observation(L.rescope(p, oid, "rows 32-63"))
    assert scoped["where"]["rows"] == [32, 63]
    assert scoped["where"]["sections"] == ["A2"]
    assert scoped["from_observation"] == oid
    from agentic import diagnose as D
    assert D._spec(scoped) == "rows 32-63"
    shutil.rmtree(os.path.dirname(p.root))


# -- D: the ears ---------------------------------------------------------------------
def _read_wav(data: bytes):
    with wave.open(io.BytesIO(data)) as w:
        rate = w.getframerate()
        pcm = array.array("h", w.readframes(w.getnframes()))
    return rate, pcm


class _DSPEar:
    """A stand-in for a model that takes audio. It answers the probes from
    the samples (it really reads the WAV: tones by their energy, pitch by
    zero crossings); for music it returns a scripted report, because the
    test is of the plumbing and the cross-check, not of a model."""

    name = "test-dsp-ear"

    def __init__(self, script=None):
        self.script = script or []

    def declares_audio(self):
        return True

    def listen(self, wav, prompt):
        rate, pcm = _read_wav(wav)
        hop = rate // 100
        loud = [max(abs(v) for v in pcm[i:i + hop]) > 2000
                for i in range(0, len(pcm) - hop, hop)]
        tones, spans, start = 0, [], None
        for i, on in enumerate(loud + [False]):
            if on and start is None:
                start = i
            if not on and start is not None:
                tones += 1
                spans.append((start * hop, i * hop))
                start = None
        if "How many" in prompt:
            return str(tones)
        if "higher or lower" in prompt:
            def zc(a, b):
                return sum(1 for j in range(a + 1, b)
                           if (pcm[j - 1] < 0) != (pcm[j] < 0)) / (b - a)
            return "higher" if zc(*spans[1]) > zc(*spans[0]) else "lower"
        return json.dumps({"observations": self.script})


class _TextOnly:
    name = "test-text-model"

    def declares_audio(self):
        return True             # it claims to hear

    def listen(self, wav, prompt):
        return "3" if "How many" in prompt else "higher"


def test_an_ear_is_native_only_when_it_answers_from_the_audio():
    from agentic import ear as EAR
    good = EAR.verify(_DSPEar())
    assert good["verified"], good
    liar = EAR.verify(_TextOnly())
    assert not liar["verified"]
    silent = EAR.verify(EAR.AudioAdapter())
    assert not silent["verified"] and "does not declare" in silent["why"]
    assert EAR.choose("auto").mode == "tool"
    assert EAR.choose("auto", _TextOnly(), liar).mode == "tool"
    assert EAR.choose("auto", _DSPEar(), good).mode == "hybrid"
    for mode, adapter, verification in (("native", None, None),
                                        ("hybrid", _TextOnly(), liar)):
        try:
            EAR.choose(mode, adapter, verification)
            assert False
        except EAR.AgenticError as error:
            assert error.code == "no_audio_input"
    assert "nothing was listened to" in EAR.TOOL_STATEMENT


def test_hybrid_checks_what_was_heard_against_what_was_measured():
    from agentic import ear as EAR, fixtures, render as R, state as S
    from agentic import timeline as T
    tl = T.build(S.content(fixtures.masked_lead()))
    script = [{"kind": "buried", "voices": ["lead"], "t0": 0.0, "t1": 3.2,
               "statement": "the lead is hard to follow",
               "confidence": 0.6},
              {"kind": "too_loud", "voices": ["pad"], "t0": 0.0, "t1": 3.2,
               "statement": "the pad is too loud", "confidence": 0.6}]
    adapter = _DSPEar(script)
    ear = EAR.choose("auto", adapter, EAR.verify(adapter), R.Cache())
    h = ear.hear(tl, tl.range("all"), intent="the lead must read clearly")
    tool = [o for o in h["observations"] if o["basis"] == "measured"]
    heard = [o for o in h["observations"] if "auditory" in o["basis"]]
    assert any(o["kind"] == "buried" for o in tool)
    verdicts = {o["kind"]: o["cross_check"]["verdict"] for o in heard}
    assert verdicts == {"buried": "confirmed", "too_loud": "not confirmed"}
    assert h["mode"] == "hybrid" and "test-dsp-ear" in h["statement"]
    for o in tool:
        for word in ("heard", "listened", "sounds"):
            assert word not in o["statement"], o["statement"]


# -- F: the backend's limits ----------------------------------------------------------
def test_the_backend_limits_are_refused_by_name():
    from agentic import capabilities as CAP, fixtures, state as S
    md = CAP.inspect("megadrive")
    assert md["agentic_loop"] == "Supported"
    assert md["actions"]["render_range"]["mode"] == "Emulated"
    assert md["actions"]["export.it"]["mode"] == "Approximate"
    assert md["ears"]["native"].startswith("unavailable")
    for target in ("nes", "opl2", "schism"):
        assert CAP.inspect(target)["agentic_loop"] == "Unsupported"
    refusals = [(lambda: S.new_state("x", target="nes"),
                 "unsupported_target"),
                (lambda: CAP.check_action("schism", "patch.level"),
                 "unsupported"),
                (lambda: CAP.check_action("megadrive", "export.mid"),
                 "unsupported")]
    p = _project(fixtures.masked_lead())
    c = S.content(p.state)
    c["instruments"]["pad"]["channel"] = "fm0"
    refusals.append((lambda: p.commit(c, {}), "bad_content"))
    c2 = S.content(p.state)
    c2["columns"] = ["fm0", "fm1", "fm5", "dac"]
    for s in c2["sections"]:
        s["rows"] = [row + ["..."] for row in s["rows"]]
    c2["instruments"]["pad"]["channel"] = "fm5"
    c2["instruments"]["drums"] = {"channel": "dac", "role": "drums"}
    refusals.append((lambda: p.commit(c2, {}), "bad_content"))
    refusals.append((lambda: CAP.export(p, "nsf"), "unsupported"))
    for call, code in refusals:
        try:
            call()
            assert False, code
        except S.AgenticError as error:
            assert error.code == code, (code, error.to_dict())
    problems = S.check_content(c2)
    assert any(x["code"] == "channel_conflict" for x in problems)
    assert p.head == 0                       # nothing was half-written
    out = CAP.export(p, "trk")
    assert out["mode"] == "Native" and os.path.getsize(out["path"]) > 100
    shutil.rmtree(os.path.dirname(p.root))


def test_the_facade_answers_with_data_and_refuses_with_a_reason():
    from agent import Agent
    root = _tmp()
    a = Agent(root)
    r = a.call("create_musical_state", preset="etude", project_id="e1")
    assert r["project_id"] == "e1" and r["card"]["plan"][0]["id"] == "A"
    r = a.call("compose_range", project_id="e1")
    assert r["composed"] == ["A"]
    r = a.call("compose_range", project_id="e1", section="X",
               rows=[["C-3", "..."]])
    assert r["error"] == "bad_content"
    r = a.call("inspect_musical_state", project_id="e1", scope="motifs")
    assert "m1" in r["motifs"]
    assert a.call("no_such_thing")["error"] == "unknown_operation"
    assert a.call("hear_range", project_id="e1", mode="native")["error"] \
        == "no_audio_input"
    r = a.call("rollback_patch", project_id="e1", revision_id=0)
    assert r["content_equal_to_target"]
    calls = open(os.path.join(root, "e1", "calls.jsonl")).read()
    assert "compose_range" in calls and "approx_tokens" in calls
    shutil.rmtree(root)


# -- A and E: the correction loop and its memory -------------------------------------
def _heard(project):
    from agentic import ear as EAR, render as R, state as S, timeline as T
    tl = T.build(S.content(project.state))
    cache = R.Cache(project.path("renders"))
    h = EAR.ToolEar(cache).hear(tl, tl.range("all"))
    for o in h["observations"]:
        project.add_observation(o)
    return h, cache


def test_a_local_correction_is_measured_guarded_committed_and_undone():
    from agentic import fixtures, loop as L, state as S, timeline as T
    p = _project(fixtures.masked_lead())
    h, cache = _heard(p)
    buried = [o for o in p.state["observations"] if o["kind"] == "buried"]
    assert buried and buried[0]["voices"] == ["lead"]
    # a "fix" that moves the melody is refused by the guards
    tl = T.build(S.content(p.state))
    search = L.Search(tl, buried[0], h, cache)
    bad = search.candidate(tl.content, [], {
        "id": "x", "patch": {"op": "transpose", "voice": "lead",
                             "range": "all", "semitones": 12}})
    assert not bad["accepted"] and "melody_kept" in bad["why"]
    d = L.improve(p, buried[0]["id"], cache=cache, hearing=h)
    assert d["outcome"] == "committed" and d["goal_met"], d["stopped"]
    assert d["gain_db"] > 3.0
    assert all(g["ok"] for g in d["guards"])
    assert d["cost"]["candidates"] <= 6 and d["cost"]["model_tokens"] == 0
    ab = d["ab"]
    assert os.path.exists(ab["files"]["A"]["wav"])
    assert abs(ab["original_levels"]["A"]["rms_db"] + ab["gain_applied_db"]
               ["A"] - ab["original_levels"]["B"]["rms_db"]
               - ab["gain_applied_db"]["B"]) < 0.01
    tl1 = T.build(S.content(p.state))
    assert L._onsets(tl, tl.range("all")) == L._onsets(tl1, tl1.range("all"))
    rev = p.rollback(0, {"why": "test"})
    assert p.revision(rev)["hash"] == p.revision(0)["hash"]
    shutil.rmtree(os.path.dirname(p.root))


def test_memory_makes_a_similar_task_cheaper_and_is_checked_again():
    from agentic import fixtures, loop as L, memory as MEM
    base = _tmp()
    mem = MEM.Memory(os.path.join(base, "memory.jsonl"))
    runs = {}
    for name, fixture, memory in (("learn", fixtures.masked_lead, mem),
                                  ("without", fixtures.masked_lead_b, None),
                                  ("with", fixtures.masked_lead_b, mem)):
        from agentic import state as S
        p = S.Project.create(os.path.join(base, name), fixture())
        h, cache = _heard(p)
        oid = next(o["id"] for o in p.state["observations"]
                   if o["kind"] == "buried")
        runs[name] = L.improve(p, oid, cache=cache, memory=memory,
                               hearing=h, ab=False)
    record = mem.records()[0]
    for field in ("task", "context", "source", "initial", "change",
                  "expected", "result", "auditory", "decision",
                  "applies_to", "cost", "versions"):
        assert field in record, field
    assert record["auditory"]["judgement"] is None
    w, m = runs["without"], runs["with"]
    assert w["outcome"] == m["outcome"] == "committed"
    assert w["goal_met"] and m["goal_met"]
    assert m["memory"]["used"] and m["memory"]["used"][0]["similarity"] >= 0.5
    first = m["rounds"][0]["candidates"][0]
    assert first["kind"] == "remembered" and first["accepted"]
    assert all(g["ok"] for g in first["guards"])          # re-checked here
    assert m["cost"]["candidates"] < w["cost"]["candidates"]
    assert m["cost"]["renders"] < w["cost"]["renders"]
    shutil.rmtree(base)
