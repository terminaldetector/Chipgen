"""References, parallel listening, instruments and sketches for the
agentic loop (python/agentic/{features,references,listen,instrument,
sketches,guided}.py): the measuring scale, a reference kept with its
source checked against its audio, fragments cut on bars and matched in
loudness, observations that say what they rest on, an instrument moved
toward a sound goal, the musical ops changing only what they say, the
guard against copying a reference, and one guided round.

No reference here is anyone else's music: the modules and recordings are
made from this engine's own renders.
"""

import json
import math
import os
import shutil
import subprocess
import tempfile

import support  # noqa: F401  (puts python/ on the path)

from test_agentic import _ABEar, _Banks, _project


def _tmp():
    return tempfile.mkdtemp(prefix="chipgen_refs_")


def _composed(seed: int = 3):
    from agentic import compose as C, fixtures
    p = _project(fixtures.etude())
    C.continue_composition(p, hear=False, seed=seed)
    return p


# -- the measuring scale ------------------------------------------------------------
def test_the_measuring_scale_reads_known_signals():
    from agentic import features as F
    rate = 44100
    a = 10 ** (-20 / 20.0)
    sine = []
    for i in range(rate * 2):
        v = a * math.sin(2 * math.pi * 997 * i / rate)
        sine += [v, v]
    assert abs(F.loudness(sine)["lufs"] + 20.0) < 0.1
    st = F.stereo(sine)
    assert st["mono"] and st["correlation"] == 1.0
    # a click track: 128 BPM, a low thump on every fourth beat
    beat, first = 60 / 128.0, 0.37
    mono = [0.0] * (rate * 10)
    k = 0
    while first + k * beat < 9.5:
        s = int((first + k * beat) * rate)
        for j in range(int(0.06 * rate)):
            v = 0.3 * math.exp(-j / (0.015 * rate)) * \
                math.sin(2 * math.pi * 3000 * j / rate)
            if k % 4 == 0:
                v += 0.6 * math.exp(-j / (0.04 * rate)) * \
                    math.sin(2 * math.pi * 60 * j / rate)
            mono[s + j] += v
        k += 1
    g = F.beat_grid(mono)
    assert abs(g["bpm"] - 128.0) < 1.0, g
    assert abs(g["first_downbeat_s"] - first) < 0.03, g
    # a vibrato: 30 cents at 6 Hz after 200 ms on A-4
    ph, tone = 0.0, []
    for i in range(int(1.2 * rate)):
        t = i / rate
        dev = 0.0 if t < 0.2 else 30.0 * math.sin(2 * math.pi * 6 * (t - 0.2))
        ph += 2 * math.pi * 440.0 * 2 ** (dev / 1200) / rate
        tone.append(0.5 * math.sin(ph))
    prof = F.note_profile(tone, rate, [{"start": 0.0, "end": 1.15,
                                        "f0": 440.0}])
    assert 24 <= prof["vibrato_depth_cents"] <= 33, prof
    assert 5.4 <= prof["vibrato_rate_hz"] <= 6.6, prof
    assert 170 <= prof["vibrato_delay_ms"] <= 240, prof


# -- references ---------------------------------------------------------------------------
def _module_of(project, root):
    """The project's music as an Impulse Tracker module (this engine's
    own IT export): a module reference made of nobody else's music."""
    from agentic import capabilities as CAP
    out = CAP.export(project, "it", os.path.join(root, "ours.it"))
    return out["path"]


def test_a_module_reference_is_kept_with_its_source_checked_by_its_audio():
    from agentic import openmpt as O, references as REF
    if not O.available():
        support.skip("libopenmpt is not installed")
    src = _composed()
    root = _tmp()
    path = _module_of(src, root)
    p = _project(src.state)
    r = REF.add(p, path, tags=["rhythm", "space"], note="our own étude, "
                "played by libopenmpt", seconds=20)
    assert r["kind"] == "module" and r["engine"].startswith("libopenmpt")
    assert r["origin"]["sha256"] and r["origin"]["file"] == "ours.it"
    assert os.path.exists(p.path(r["files"]["audio"]))
    assert r["source_notes"] > 20 and r["voices"]
    assert r["grid"]["bars"] and r["grid"]["source"] in ("score", "audio")
    kinds = {c["kind"] for c in r["claims"]}
    assert "tempo" in kinds and "density" in kinds
    for c in r["claims"]:
        assert c["audio_check"]["verdict"] in (
            "confirmed", "not confirmed", "not measurable",
            "confirmed at half time", "confirmed at double time",
            "related, not confirmed")
    density = [c for c in r["claims"] if c["kind"] == "density"
               and c["audio_check"]["verdict"] != "not measurable"]
    assert any(c["audio_check"]["reading"]["recall"] >= 0.7
               for c in density), density
    card = REF.card(r)
    assert card["tags"] == ["rhythm", "space"] and card["claims"]
    for bad, code in ((lambda: REF.add(p, path, tags=["loudness"]),
                       "bad_tag"),
                      (lambda: REF.get(p, "ref9"), "no_reference"),
                      (lambda: REF.add(p, os.path.join(root, "x.ogg")),
                       "no_file")):
        try:
            bad()
            assert False, code
        except REF.AgenticError as error:
            assert error.code == code, error.to_dict()
    shutil.rmtree(root)
    shutil.rmtree(os.path.dirname(p.root))
    shutil.rmtree(os.path.dirname(src.root))


def test_a_recording_gets_its_grid_from_its_audio_and_says_so():
    from agentic import references as REF, render as R, timeline as T
    from agentic import state as S
    src = _composed()
    tl = T.build(S.content(src.state))
    rng = tl.range("all")
    root = _tmp()
    wav = os.path.join(root, "take.wav")
    REF._write_wav(wav, R.render_range(tl, rng).segment(
        rng["t0"], rng["t1"], mono=False))
    path = wav
    if shutil.which("lame"):
        path = os.path.join(root, "take.mp3")
        subprocess.run(["lame", "--silent", wav, path], check=True)
    p = _project(src.state)
    r = REF.add(p, path, tags=["rhythm"], note="a recording, no score")
    assert r["kind"] == "audio" and r["grid"]["source"] == "audio"
    assert "estimated" in r["grid"]["how"] and r.get("claims") is None
    ratio = r["grid"]["bpm"] / 140.0
    assert min(abs(ratio - x) for x in (1.0, 2.0, 0.5)) < 0.03, r["grid"]
    if path.endswith(".mp3"):
        assert "LAME" in r["engine"] or "lame" in r["engine"]
    shutil.rmtree(root)
    shutil.rmtree(os.path.dirname(p.root))
    shutil.rmtree(os.path.dirname(src.root))


def test_a_score_reference_plays_its_own_bank_and_leaves_ours_alone():
    import instruments
    from agentic import references as REF
    root = _tmp()
    bank = os.path.join(root, "ref_bank.json")
    patch = instruments.instrument_to_dict(instruments.get("square_lead"))
    patch["name"] = "reference_only_patch"
    with open(bank, "w") as handle:
        json.dump([patch], handle)
    score = os.path.join(root, "ref.trk")
    with open(score, "w") as handle:
        handle.write("bpm 120\nlpb 4\ninst fm0 reference_only_patch\n"
                     "vib fm0 40 6 0.1\ncols fm0 noise\n"
                     + ("A-4  w1\n...  ===\n...  w1\n...  ===\n"
                        "C-5  w1\n...  ===\n...  w1\n===  ===\n") * 4)
    p = _project(_composed().state)
    r = REF.add(p, score, bank=bank, tags=["modulation"], seconds=10)
    assert "reference_only_patch" not in instruments.BANK
    assert r["kind"] == "trk" and r["voices"]["fm0"]["vibrato_share"] > 0.5
    vib = [c for c in r["claims"] if c["kind"] == "vibrato"]
    assert vib and vib[0]["audio_check"]["verdict"] == "confirmed", vib
    assert 25 <= vib[0]["audio_check"]["reading"]["depth_cents"] <= 50
    shutil.rmtree(root)


# -- parallel listening ----------------------------------------------------------------------
def test_fragments_are_cut_on_bars_and_brought_to_one_loudness():
    from agentic import features as F, listen as LI, references as REF
    from agentic import openmpt as O
    if not O.available():
        support.skip("libopenmpt is not installed")
    src = _composed()
    root = _tmp()
    path = _module_of(src, root)
    p = _project(src.state)
    REF.add(p, path, tags=["rhythm"], seconds=20, check=False)
    ours = LI.of_project(p, "A", stems=True)
    theirs = LI.of_reference(p, "ref1", 1, 2)
    assert ours["bars"] == [1, 2] and theirs["bars"] == [1, 2]
    # each on its own grid: two bars of each, whatever their tempos
    assert abs(ours["seconds"] - 2 * 4 * 60.0 / 140.0) < 0.02
    bar = REF.get(p, "ref1")["grid"]["beat_s"] * 4
    assert abs(theirs["seconds"] - 2 * bar) < 0.1, (theirs["seconds"], bar)
    index = LI.write([ours, theirs], os.path.join(root, "out"), "cmp",
                     mp3=False)
    for f, e in zip((ours, theirs), index["fragments"]):
        scaled = [v * 10 ** (e["gain_db"] / 20.0) for v in f["samples"]]
        lufs = F.loudness(scaled)["lufs"]
        assert abs(lufs - (LI.TARGET_LUFS + index["loudness"][
            "common_reduction_db"])) < 0.2, (lufs, e)
    assert set(index["fragments"][0]["stems"]) >= {"lead", "bass"}
    a, b = index["fragments"][0]["in_reel"], index["fragments"][1]["in_reel"]
    assert abs(b[0] - a[1] - LI.GAP_S) < 0.01
    assert os.path.exists(index["reel"]["wav"])
    shutil.rmtree(root)


def test_an_observation_says_what_it_rests_on():
    from agentic import ear as EAR, listen as LI, references as REF
    from agentic import openmpt as O, patch as P, state as S
    from agentic import timeline as T
    if not O.available():
        support.skip("libopenmpt is not installed")
    src = _composed()
    # the reference: the same piece with its hats doubled — busier
    c = S.content(src.state)
    busy, _ = P.apply(c, {"op": "density", "voice": "hats", "range": "all",
                          "mode": "double"}, T.build(c))
    other = _project(src.state)
    other.commit(busy, {"why": "test"})
    root = _tmp()
    path = _module_of(other, root)
    p = _project(src.state)
    REF.add(p, path, tags=["rhythm"], seconds=20, check=False)
    frags = [LI.of_project(p, "A"), LI.of_reference(p, "ref1", 1, 2)]
    rep = LI.compare(p, frags, os.path.join(root, "a"), "a")
    assert not rep["listening"]["available"]
    assert "nothing listened" in rep["listening"]["why"]
    measured = [o for o in rep["observations"] if o["basis"] == "measured"]
    assert any(o["feature"] == "notes_per_beat" and o["theirs"] > o["ours"]
               for o in measured), rep["observations"]
    for o in rep["observations"]:
        assert o["basis"] in ("measured", "assumed")
        for word in ("heard", "listened", "sounds"):
            assert word not in o["statement"], o["statement"]
    # a verified model's observations come back as heard by it
    adapter = _ABEar("B")
    adapter.script = [{"fragment": 1, "t0": 0.2, "t1": 1.0,
                       "feature": "rhythm", "statement": "sparser pulse",
                       "voices": ["hats"], "confidence": 0.7}]
    ear = EAR.choose("hybrid", adapter, EAR.verify(adapter))
    rep = LI.compare(p, frags, os.path.join(root, "b"), "b", ear=ear)
    heard = [o for o in rep["observations"]
             if o["basis"] == "heard by model"]
    assert heard and heard[0]["voices"] == ["hats"]
    assert rep["listening"]["available"]
    shutil.rmtree(root)


# -- the musical ops and the copy guard --------------------------------------------------------
def test_the_musical_ops_change_only_what_they_say():
    from agentic import loop as L, patch as P, state as S, timeline as T
    p = _composed()
    c = S.content(p.state)
    tl = T.build(c)
    rng = tl.range("B")
    for spec in ({"op": "articulation", "voice": "lead", "range": "B",
                  "gate": 0.5},
                 {"op": "density", "voice": "hats", "range": "B",
                  "mode": "double"},
                 {"op": "density", "voice": "bass", "range": "B",
                  "mode": "thin"},
                 {"op": "rhythm", "voice": "hats", "range": "B",
                  "mode": "swing"},
                 {"op": "voicing", "voice": "pad", "range": "B"},
                 {"op": "echo", "voice": "lead", "range": "B"}):
        new, delta = P.apply(c, spec, tl)
        tl2 = T.build(new)
        a, b = L._onsets(tl, rng), L._onsets(tl2, tl2.range("B"))
        assert not L._notes_kept(a, b, L._allowed([spec])), spec
        # without the op's allowance the change is seen
        if spec["op"] in ("density", "echo"):
            assert L._notes_kept(a, b, {}), spec
        assert L._pitches(tl, rng, "lead") == L._pitches(tl2, tl2.range("B"),
                                                         "lead"), spec
    new, delta = P.apply(c, {"op": "echo", "voice": "lead", "range": "B",
                             "rows": 3}, tl)
    echo = new["instruments"][delta["new_voice"]]
    assert echo["role"] == "echo" and echo["pan"] == "R"
    assert echo["channel"] not in {i["channel"] for v, i in
                                   c["instruments"].items()}
    for spec, code in (({"op": "density", "voice": "lead", "range": "B",
                         "mode": "thin"}, "melodic_voice"),
                       ({"op": "pan", "voice": "hats", "side": "L"},
                        "no_pan"),
                       ({"op": "voicing", "voice": "bass", "range": "B"},
                        "not_a_pad")):
        try:
            P.apply(c, spec, tl)
            assert False, spec
        except S.AgenticError as error:
            assert error.code == code, error.to_dict()
    shutil.rmtree(os.path.dirname(p.root))


def test_a_change_that_brings_in_a_reference_figure_is_refused():
    import copy
    from agentic import guided as G, patch as P, references as REF
    from agentic import state as S, timeline as T
    p = _composed()
    c = S.content(p.state)
    tl = T.build(c)
    rng = tl.range("B")
    # a reference whose melody has a figure the piece does not
    figure = [(0, 64), (2, 71), (3, 69), (4, 68), (6, 66), (7, 64)]
    folder = p.path("references", "ref1")
    os.makedirs(folder)
    row_s = 60.0 / 120.0 / 4
    with open(os.path.join(folder, "notes.json"), "w") as handle:
        json.dump([{"voice": "ch3", "start": 10.0 + r * row_s,
                    "pitch": pitch} for r, pitch in figure], handle)
    p.state.setdefault("references", []).append(
        {"id": "ref1", "kind": "module", "tags": []})
    # rows that write that figure (transposed, at this tempo) into B
    copied = copy.deepcopy(c)
    k = copied["columns"].index("fm1")
    b = next(s for s in copied["sections"] if s["id"] == "B")
    for r in range(8):
        b["rows"][r][k] = "..."
    for r, pitch in figure:
        b["rows"][r][k] = P._name(pitch + 2)
    g = G.copy_guard(p, tl, T.build(copied), rng)
    assert not g["ok"] and "ref1 ch3" in g["detail"], g
    # a change to the hats brings in no melody
    new, _ = P.apply(c, {"op": "density", "voice": "hats", "range": "B",
                         "mode": "double"}, tl)
    assert G.copy_guard(p, tl, T.build(new), rng)["ok"]
    # nor does an echo of the piece's own lead: that figure was the piece's
    new, _ = P.apply(c, {"op": "echo", "voice": "lead", "range": "B"}, tl)
    assert G.copy_guard(p, tl, T.build(new), rng)["ok"]
    assert REF.notes(p, "ref1")
    shutil.rmtree(os.path.dirname(p.root))


# -- sketches ------------------------------------------------------------------------------------
def test_sketches_keep_drafts_and_rejections_with_their_reasons():
    from agentic import sketches as SK, state as S
    p = _composed()
    before = S.content_hash(S.content(p.state))
    ids = SK.section_variants(p, "A", seeds=[7]) + \
        SK.section_variants(p, "B", energies=[0.95])
    assert len(ids) == 2
    assert S.content_hash(S.content(p.state)) == before   # nothing changed
    for sid, section in zip(ids, ("A", "B")):
        mine = next(s for s in S.content(p.state)["sections"]
                    if s["id"] == section)
        theirs = next(s for s in SK.content_of(p, sid)["sections"]
                      if s["id"] == section)
        assert mine["rows"] != theirs["rows"], section
        assert SK.get(p, sid)["measured"]["same_as_piece"] is False
    SK.choose(p, ids[1], "busier")
    try:
        SK.reject(p, ids[0], "")
        assert False
    except S.AgenticError as error:
        assert error.code == "no_reason"
    SK.reject(p, ids[0], "the theme does not come back")
    reopened = S.Project.open(p.root)
    states = {s["id"]: s["status"] for s in reopened.state["sketches"]}
    assert states == {ids[0]: "rejected", ids[1]: "chosen"}
    assert SK.chosen(reopened)[0]["chosen_because"] == "busier"
    shutil.rmtree(os.path.dirname(p.root))


# -- an instrument and a guided round (slow: they render) ---------------------------------------
def test_an_instrument_moves_toward_a_sound_goal_or_stays():
    from agentic import fixtures, instrument as INS, state as S
    with _Banks():          # what it installs leaves with it
        p = _project(fixtures.masked_lead())
        rec = INS.design(p, "lead", {"vibrato_depth_cents": 30.0,
                                     "vibrato_rate_hz": 6.0,
                                     "vibrato_delay_ms": 0.0}, "all",
                         max_candidates=2)
        assert rec["outcome"] == "committed", rec["why"]
        kept = next(c for c in rec["candidates"] if c["id"] == rec["chosen"])
        assert kept["profile"]["vibrato_depth_cents"] >= 20.0
        assert all(g["ok"] for g in kept["guards"])
        assert rec["listening"]["available"] is False
        name = p.state["instruments"]["lead"]["patch"]
        assert name != "square_lead" and name == rec["patch_after"]
        banks = p.state["structure"]["banks"]
        assert any(b["path"].endswith(f"{name}.bank.json") for b in banks)
        rejected = [c for c in rec["candidates"] if c is not kept]
        assert all(c.get("sketch") for c in rejected if c.get("profile"))
        again = S.Project.open(p.root)          # the bank comes back with it
        assert again.state["instruments"]["lead"]["patch"] == name
        rev = again.rollback(0, {"why": "test"})
        assert again.revision(rev)["hash"] == again.revision(0)["hash"]
        shutil.rmtree(os.path.dirname(p.root))


def test_a_transcriptions_rows_that_miss_the_eighths_give_no_offbeat_reading():
    # A transcription's 52 ms rows against a 164 BPM beat: an eighth is 3.5
    # rows, so every other straight eighth sits half a row away, past the
    # tolerance; the share of onsets between the eighths would measure the
    # grid. On the beat the rows divide, and on a source's own rows, it is
    # a reading.
    from agentic import listen as LI
    ref = {"grid": {"source": "audio", "beat_s": 0.7296},
           "source_grid": {"row_s": 0.05208}}
    why = LI._rows_misfit(ref, 0.3648)
    assert why and "52 ms" in why, why
    assert LI._rows_misfit(ref, 0.7296) is None
    ref["grid"]["source"] = "score"
    assert LI._rows_misfit(ref, 0.3648) is None


def test_a_combination_that_broke_a_guard_is_tried_with_the_next_best():
    # Each family's best change put together can break a guard neither
    # broke alone; the round then takes each family's next best in turn.
    from agentic import instrument as INS
    compiled = INS._base_compiled("square_lead")
    goal = {"vibrato_depth_cents": 56.0, "vibrato_rate_hz": 7.5,
            "vibrato_share": 0.6, "brightness_body": 2.3}
    hyps = INS.hypotheses(compiled, {"brightness_body": 6.4}, goal, "lead",
                          "fm1", lengths=[0.2, 0.3, 0.6, 0.9])
    ids = [h["id"] for h in hyps]
    assert {"vib1", "vib2", "br1", "br2"} <= set(ids), ids
    # a made-up first round: vib2 then vib1 nearest on the vibrato's
    # readings, br2 then br1 on the body's brightness
    parts = {"vib1": 0.4, "vib2": 0.2, "vib3": 0.8, "vib4": 0.9,
             "br1": 2.0, "br2": 1.0}
    tried = []
    for h in hyps:
        part = parts.get(h["id"], 5.0)
        tried.append({"distance": {"parts": {
            k: part for k in INS._FAMILY_READINGS.get(h["family"], ())
            if k in goal}}, "guards": [{"ok": True}], "closed": 0.3})
    joint = INS.combine(compiled, goal, hyps, tried)
    assert joint["parts"] == ["br2", "vib2"], joint["parts"]
    assert INS.combine(compiled, goal, hyps, tried,
                       avoid={"vib2"})["parts"] == ["br2", "vib1"]
    assert INS.combine(compiled, goal, hyps, tried,
                       avoid={"br2"})["parts"] == ["br1", "vib2"]


def test_a_vibrato_goal_takes_the_references_share_not_its_delay():
    # A reference's vibrato delay is the length of its own held notes; the
    # share of notes that swing is what carries over, and the delay is
    # fitted to this part's notes from it.
    from agentic import guided as G, instrument as INS
    goal = {"vibrato_depth_cents": 56.1, "vibrato_share": 0.6,
            "vibrato_delay_ms": 800.0}
    why = G._complete_goal(goal, [{"vibrato_rate_hz": 7.67,
                                   "vibrato_delay_ms": 800.0}])
    assert goal == {"vibrato_depth_cents": 56.1, "vibrato_share": 0.6,
                    "vibrato_rate_hz": 7.67}, goal
    assert "800" in why
    lengths = [0.2, 0.3, 0.6, 0.9]
    delay = INS._delay_for_share(lengths, 0.5)
    assert delay == 450.0
    # half of the notes last 150 ms past it: those swing
    assert sum(1 for n in lengths
               if n - delay / 1000.0 >= 0.15 - 1e-9) == 2


def test_a_guided_round_keeps_what_moves_toward_the_reference():
    from agentic import guided as G, openmpt as O, patch as P
    from agentic import references as REF, state as S, timeline as T
    if not O.available():
        support.skip("libopenmpt is not installed")
    with _Banks():          # what it installs leaves with it
        src = _composed()
        c = S.content(src.state)
        busy, _ = P.apply(c, {"op": "density", "voice": "hats", "range": "all",
                              "mode": "double"}, T.build(c))
        other = _project(src.state)
        other.commit(busy, {"why": "test"})
        root = _tmp()
        path = _module_of(other, root)
        p = _project(src.state)
        REF.add(p, path, tags=["rhythm"], seconds=20, check=False)
        rep = G.improve_toward(p, "A", ["ref1"], folder=os.path.join(root, "g"),
                               max_gaps=1)
        rhythm = [w for w in rep["worked_on"] if w["dimension"] == "rhythm"]
        assert rhythm, rep["worked_on"]
        w = rhythm[0]
        assert w["outcome"] == "committed", w
        kept = next(h for h in w["hypotheses"] if h["id"] == w["chosen"])
        assert kept["after"] > kept["before"] and kept["chain"][0]["op"] in (
            "density", "rhythm")
        assert any(g["name"] == "no_reference_copy" and g["ok"]
                   for g in kept["guards"])
        assert os.path.exists(kept["ab_reel"])
        assert p.head == 1 and rep["listening"]["available"] is False
        shutil.rmtree(root)


def test_a_sketch_drafted_before_a_voice_came_keeps_its_cells_by_voice():
    # A sketch drafted before the piece gained a voice (an echo on a new
    # column) is still compared: its cells go to the piece's columns by
    # name, and the new voice is silent in it.
    from agentic import guided as G
    rows = [["C-4", "E-4", "kick"], ["...", "===", "..."]]
    assert G._in_columns(rows, ["fm0", "fm1", "dac"],
                         ["fm0", "fm1", "dac", "fm3"]) == [
        ["C-4", "E-4", "kick", "..."], ["...", "===", "...", "..."]]
    assert G._in_columns(rows, ["fm0", "fm1", "dac"],
                         ["fm1", "fm0", "dac"])[0] == ["E-4", "C-4", "kick"]


def test_a_new_section_is_heard_with_the_one_before_the_join_and_a_sketch():
    import json as _json
    from agentic import compose as C, fixtures, guided as G
    from agentic import sketches as SK
    p = _project(fixtures.etude())
    C.continue_composition(p, stop_after=1, seed=3, hear=False)   # A
    sid = SK.section_variants(p, "A2", energies=[0.95])[0]
    SK.choose(p, sid, "a busier A2")
    C.continue_composition(p, stop_after=1, hear=False)            # A2
    out = G.hear_continuation(p, "A2", sketch_ids=[sid])
    assert out["join"]["score"]["between"] == ["A", "A2"]
    assert "loudness_change_lufs" in out["join"]
    with open(out["index"]) as handle:
        kinds = [e["kind"] for e in _json.load(handle)["fragments"]]
    assert kinds == ["ours", "previous", "sketch"], kinds
    assert out["sketches"] == [{"id": sid, "label": SK.get(p, sid)["label"],
                                "compared": True}]
    assert out["listening"]["available"] is False
    assert os.path.exists(out["reel"]["wav"])
    for o in out["observations"]:
        assert o["basis"] in ("measured", "assumed"), o
    shutil.rmtree(os.path.dirname(p.root))


# -- the facade -------------------------------------------------------------------------------------
def test_the_facade_keeps_references_and_sketches_and_says_who_listened():
    from agent import Agent
    root = _tmp()
    a = Agent(root)
    a.call("create_musical_state", preset="etude", project_id="e")
    a.call("compose_range", project_id="e")
    r = a.call("check_listening")
    assert r["available"] is False and "consequence" in r
    r = a.call("add_reference", project_id="e", path="/no/such.mp3")
    assert r["error"] == "no_file"
    r = a.call("add_reference", project_id="e", path=__file__)
    assert r["error"] == "bad_reference"
    r = a.call("save_sketch", project_id="e", label="the first take")
    sid = r["sketch"]["id"]
    assert a.call("reject_sketch", project_id="e", sketch_id=sid,
                  why="")["error"] == "no_reason"
    r = a.call("list_sketches", project_id="e")
    assert r["sketches"][0]["status"] == "open"
    assert a.call("list_references", project_id="e")["references"] == []
    shutil.rmtree(root)


# -- hearing that decides: the router, the ear's choice, stereo, memory ---------------------------
class _ChoosingEar:
    """A stand-in for a verified audio model that decides from the audio
    it is sent. It answers the probes from the samples, reports a scripted
    observation about the piece, and when asked to choose among versions
    it reads each version's span (from the prompt's index) and picks the
    one with the fewest zero crossings for a goal that asks for less
    brightness (most otherwise) — or answers "uncertain" when told to.
    It records the channel count of every clip it was sent."""

    name = "test-choosing-ear"

    def __init__(self, observation=None, uncertain=False, pick=None):
        self.observation = observation
        self.uncertain = uncertain
        self.pick = pick
        self.channels = []
        self.choices = []

    def declares_audio(self):
        return True

    def listen(self, wav, prompt):
        import io as _io
        import re
        import wave as _wave
        from array import array
        with _wave.open(_io.BytesIO(wav)) as w:
            rate, ch = w.getframerate(), w.getnchannels()
            pcm = array("h", w.readframes(w.getnframes()))
        mono = [sum(pcm[i:i + ch]) / ch for i in range(0, len(pcm), ch)]
        if "Answer with one word" in prompt or "single digit" in prompt:
            from agentic import ear as EAR
            return EAR.answer_by_dsp(wav, prompt) or ""
        self.channels.append(ch)        # the music it was sent, not probes
        if "versions of the same passage" in prompt:
            spans = [(float(a), float(b)) for a, b in re.findall(
                r"^\d+\. ([\d.]+)-([\d.]+) s:", prompt, re.M)]
            if self.uncertain:
                return json.dumps({"choice": "uncertain", "confidence": 0.2,
                                   "statement": "cannot tell"})
            if self.pick is not None:
                self.choices.append({"pick": self.pick,
                                     "matched": "matched in loudness"
                                     in prompt})
                return json.dumps({"choice": self.pick + 1,
                                   "confidence": 0.8, "statement": "scripted"})

            def crossings(a, b):
                seg = mono[int(a * rate):int(b * rate)]
                return sum(1 for x, y in zip(seg, seg[1:])
                           if (x < 0) != (y < 0)) / max(1, len(seg))
            zc = [crossings(a, b) for a, b in spans]
            darker = "less" in prompt or "darker" in prompt or \
                "softer" in prompt
            pick = (min if darker else max)(range(len(zc)),
                                             key=lambda k: zc[k])
            self.choices.append({"crossings": zc, "pick": pick})
            return json.dumps({"choice": pick + 1, "confidence": 0.8,
                               "statement": "by ear", "ranking": [pick + 1]})
        if "fragments" in prompt or "Report only what you hear" in prompt:
            return json.dumps({"observations": [self.observation]
                               if self.observation else []})
        return "{}"


def _choosing(adapter, cache=None):
    from agentic import ear as EAR, render as R
    return EAR.choose("hybrid", adapter, EAR.verify(adapter),
                      cache or R.Cache())


def test_an_observation_goes_where_something_can_act_on_it():
    from agentic import fixtures, guided as G, state as S, timeline as T
    tl = T.build(S.content(fixtures.masked_lead()))
    heard = {"basis": "heard by model", "about_the_piece": True,
             "statement": "the lead's held notes are too bright"}
    r = G.route(dict(heard, category="body_brightness", direction="less",
                     voices=["lead"]), tl, "lead")
    assert r["route"] == "instrument" and r["voice"] == "lead"
    assert r["relative"] == {"brightness_body": 0.6}
    r = G.route(dict(heard, category="space", direction="more"), tl, "lead")
    assert r["route"] == "arrangement" and \
        r["feature"] == "stereo.side_over_mid_db" and r["sign"] == 1.0
    r = G.route(dict(heard, category="balance", direction="less",
                     voices=["pad"]), tl, "lead")
    assert r["chains"][0]["chain"][0] == {"op": "level", "voice": "pad",
                                          "range": "@", "db": -3.0}
    assert G.route(dict(heard, about_the_piece=False, category="space",
                        direction="more"), tl, "lead")["route"] is None
    assert G.route(dict(heard, category="other", direction="more"), tl,
                   "lead")["route"] is None
    measured = {"basis": "measured", "against_kind": "reference",
                "feature": "voice.vibrato_depth_cents", "theirs": 50.0}
    assert G.route(measured, tl, "lead") == {
        "route": "instrument", "voice": "lead",
        "goal": {"vibrato_depth_cents": 50.0}}
    assert G.route({"basis": "assumed"}, tl, "lead")["route"] is None


def test_the_ear_chooses_among_versions_it_is_sent_in_stereo():
    from agentic import ear as EAR
    import math as _m
    rate = 8000

    def tone(f, sec=0.5):
        out = []
        for i in range(int(sec * rate)):
            v = 0.3 * _m.sin(2 * _m.pi * f * i / rate)
            out += [v, 0.5 * v]          # left of centre
        return out
    adapter = _ChoosingEar()
    ear = _choosing(adapter)
    v = EAR.ab_choose(ear, [("as it is", tone(2000)), ("bright", tone(3000)),
                            ("dark", tone(500))], "less bright, softer",
                      rate=rate)
    assert v["available"] and v["choice"] == 2, v
    assert v["clip"]["channels"] == 2 and adapter.channels[-1] == 2
    assert len(v["clip"]["versions"]) == 3
    # stereo kept: an anti-phase side signal survives (a mono mix would
    # cancel it to silence)
    wide = [x for i in range(4000) for x in (0.3 * _m.sin(i / 3.0),
                                             -0.3 * _m.sin(i / 3.0))]
    a = EAR.ab_clip(wide, wide, rate)[0]
    assert max(abs(x) for x in a) > 0.1
    assert max(abs(a[i] + a[i + 1]) for i in range(0, 2000, 2)) < 1e-6
    unsure = EAR.ab_choose(_choosing(_ChoosingEar(uncertain=True)),
                           [("a", tone(500)), ("b", tone(900))], "x",
                           rate=rate)
    assert unsure["choice"] is None and unsure["uncertain"]
    none = EAR.ab_choose(EAR.ToolEar(), [("a", tone(500))], "x")
    assert none["available"] is False and "choice" not in none


def test_a_mono_machine_is_named_and_sent_mono():
    from agentic import ear as EAR
    assert EAR.platform_of({"columns": ["fm0", "psg0", "noise"]}) == {
        "name": EAR.PLATFORMS[0][1], "stereo": True}
    nes = EAR.platform_of({"columns": ["nes0", "nes1", "nes2"]})
    assert nes["stereo"] is False and "NES" in nes["name"]
    assert EAR.platform_of({"columns": ["opl0"]})["stereo"] is False


def test_a_learning_record_keeps_what_the_model_heard():
    from agentic import memory as M
    heard = {"available": True, "model": "m", "basis": "heard by model",
             "choice": 2, "labels": ["as it is", "a", "b"],
             "confidence": 0.8, "statement": "b is clearer",
             "clip": {"channels": 2}}
    a = M._auditory({"outcome": "committed", "listening_verdict": heard,
                     "judged_by": "heard by model"})
    assert a["judgement"]["choice"] == 2 and a["judgement"]["model"] == "m"
    assert a["agrees_with_measurement"] is True
    assert "nothing listened" not in a["uncertainty"]
    none = M._auditory({"outcome": "committed", "listening_verdict":
                        {"available": False}})
    assert none["judgement"] is None and "nothing listened" in \
        none["uncertainty"]


def test_a_heard_gap_is_acted_on_only_when_the_ear_chooses_a_change():
    from agentic import fixtures, guided as G, render as R
    obs = {"basis": "heard by model", "about_the_piece": True,
           "category": "balance", "direction": "less", "voices": ["pad"],
           "statement": "the pad is too loud, softer please",
           "confidence": 0.7}
    for uncertain, outcome in ((True, "rolled_back"), (False, "committed")):
        p = _project(fixtures.masked_lead())
        cache = R.Cache(p.path("renders"))
        adapter = _ChoosingEar(uncertain=uncertain, pick=1)
        rv = G.revise_heard(p, "all", [obs], ear=_choosing(adapter, cache),
                            cache=cache)
        w = rv["worked_on"][0]
        assert w["outcome"] == outcome, w
        assert w["judged_by"] == "heard by model"
        assert adapter.channels and set(adapter.channels) == {2}
        if outcome == "committed":
            assert w["chosen"] == "level_pad_-3" and p.head == 1
            d = p.state["decisions"][-1]
            assert d["raised_by"] == "heard by model"
            assert d["listening_verdict"]["choice"] == 1
            # a balance change is heard at its rendered level
            assert adapter.choices[-1]["matched"] is False
            assert d["listening_verdict"]["clip"]["matched"] is False
        else:
            assert p.head == 0 and "acted on only when" in w["why"]
        shutil.rmtree(os.path.dirname(p.root))


def test_a_heard_instrument_problem_becomes_a_program_the_ear_picks():
    from agentic import fixtures, guided as G, render as R
    with _Banks():
        p = _project(fixtures.masked_lead())
        cache = R.Cache(p.path("renders"))
        adapter = _ChoosingEar()
        obs = {"basis": "heard by model", "about_the_piece": True,
               "category": "body_brightness", "direction": "less",
               "voices": ["lead"], "statement": "the lead is harsh: "
               "less bright in its held notes", "confidence": 0.7}
        rv = G.revise_heard(p, "all", [obs], ear=_choosing(adapter, cache),
                            cache=cache, instrument_budget=2)
        w = rv["worked_on"][0]
        assert w["dimension"] == "instrument" and w["goal"], w
        assert w["outcome"] == "committed", w["why"]
        assert w["chosen"].startswith("br"), w["chosen"]
        assert w["listening_verdict"]["choice"] >= 1
        assert adapter.choices and set(adapter.channels) == {2}
        assert p.state["instruments"]["lead"]["patch"] != "square_lead"
        shutil.rmtree(os.path.dirname(p.root))


def test_a_guided_round_works_first_on_what_the_ear_heard():
    from agentic import guided as G, openmpt as O, references as REF
    if not O.available():
        support.skip("libopenmpt is not installed")
    with _Banks():
        src = _composed()
        root = _tmp()
        path = _module_of(src, root)
        p = _project(src.state)
        REF.add(p, path, tags=["rhythm"], seconds=20, check=False)
        heard = {"fragment": 1, "t0": 0.0, "t1": 2.0, "feature": "space",
                 "category": "space", "direction": "more", "voices": [],
                 "statement": "the piece is narrow; wider", "confidence": 0.7}
        adapter = _ChoosingEar(observation=heard, pick=1)
        rep = G.improve_toward(p, "A", ["ref1"], folder=os.path.join(root,
                                                                     "g"),
                               max_gaps=1, ear=_choosing(adapter))
        w = rep["worked_on"][0]
        # what the ear heard is worked on first, through the same changes
        # and guards as a measured gap; here every candidate broke one, so
        # nothing reached the ear and the piece stayed
        assert w["basis"] == "heard by model" and w["dimension"] == "space"
        assert [h["id"] for h in w["hypotheses"]] == [
            "spread", "echo", "spread+echo"]
        assert all(h["moved"] > 0 for h in w["hypotheses"])
        if w["outcome"] == "committed":
            assert w["judged_by"] == "heard by model"
        else:
            assert w["judged_by"].startswith("guards"), w["judged_by"]
            assert p.head == 0
        assert rep["listening"]["available"] is True
        shutil.rmtree(root)


# -- references at their full length, VGM, tempo, probes, playback, raw levels ---------------------
def _vgm_of(project, root):
    """The project's music as a VGM (this engine's own register log): a
    VGM reference made of nobody else's music."""
    import chipgen
    from agentic import state as S, timeline as T
    path = os.path.join(root, "ours.vgm")
    chipgen.compose(T.build(S.content(project.state)).text, vgm=path)
    return path


def test_a_vgm_reference_plays_its_own_log_and_reads_its_notes():
    from agentic import references as REF
    with _Banks():
        src = _composed()
        root = _tmp()
        path = _vgm_of(src, root)
        p = _project(src.state)
        r = REF.add(p, path, tags=["rhythm"], check=False)
        assert r["kind"] == "vgm" and "VGM replayed" in r["engine"], r
        assert r["kept"]["why"] == "the whole of it" and not r["cut"]
        assert r["source_notes"] > 20 and r["voices"]
        assert os.path.exists(p.path(r["files"]["score"]))
        assert r["transcription"]["notes"] > 20
        voice = sorted(r["voices"])[0]
        stem = REF.stem(p, "ref1", voice, 2.0)
        assert len(stem) > 2 * 44100       # interleaved stereo, 2 s
        shutil.rmtree(root)


def test_a_format_nothing_here_plays_is_refused_by_name():
    from agentic import references as REF, state as S
    for name, word in (("x.nsf", "NSF"), ("x.fur", "Furnace"),
                       ("x.ftm", "FamiTracker")):
        try:
            REF.kind_of(name)
            assert False, name
        except S.AgenticError as error:
            assert error.code == "unsupported_reference" and \
                word in str(error), str(error)
    assert REF.kind_of("x.vgz") == "vgm"


def test_a_reference_is_kept_whole_and_says_when_it_was_cut():
    from agentic import openmpt as O, references as REF
    if not O.available():
        support.skip("libopenmpt is not installed")
    src = _composed()
    root = _tmp()
    path = _module_of(src, root)
    p = _project(src.state)
    whole = REF.add(p, path, check=False)
    assert whole["kept"]["why"] == "the whole of it" and not whole["cut"]
    assert abs(whole["seconds"] - whole["full_seconds"]) < 0.5
    cut = REF.add(p, path, seconds=4, check=False)
    assert cut["cut"] and "as asked" in cut["kept"]["why"]
    shutil.rmtree(root)


def test_a_tempo_change_in_a_recording_is_found_and_said():
    from agentic import listen as LI, references as REF
    rate = 44100
    stereo = []
    for bpm, seconds in ((120.0, 62.0), (150.0, 40.0)):
        beat = int(rate * 60.0 / bpm)
        n = int(seconds * rate)
        for i in range(n):
            k = i % beat
            v = 0.6 * (1.0 - k / 400.0) if k < 400 else 0.0
            stereo += [v, v]
    root = _tmp()
    wav = os.path.join(root, "clicks.wav")
    REF._write_wav(wav, stereo)
    p = _project(_composed().state)
    r = REF.add(p, wav, tags=["rhythm"], check=False)
    g = r["grid"]
    assert g["constant_tempo"] is False, g.get("windows")
    assert 50.0 <= g["uncertain_after_s"] <= 70.0, g["uncertain_after_s"]
    late = LI.of_reference(p, "ref1", len(g["bars"]) - 3, 2)
    assert "grid_uncertain" in late
    early = LI.of_reference(p, "ref1", 1, 2)
    assert "grid_uncertain" not in early
    shutil.rmtree(root)


class _HearsTonesOnly:
    """Hears tones (the basic probes, from the samples) but answers the
    music probes from the text: it may say which tone is higher, not
    choose among versions."""

    name = "test-tones-only"

    def declares_audio(self):
        return True

    def listen(self, wav, prompt):
        from agentic import ear as EAR
        if "How many" in prompt or "higher or lower" in prompt:
            return EAR.answer_by_dsp(wav, prompt)
        return "same" if "same or" in prompt else "first"


def test_a_choice_is_obeyed_only_from_an_ear_that_passed_the_music_probes():
    from agentic import ear as EAR
    from test_agentic import _DSPEar
    good = EAR.verify(_DSPEar())
    assert good["verified"] and good["music"]["passed"], good["music"]
    tones = EAR.verify(_HearsTonesOnly())
    assert tones["verified"] and not tones["music"]["passed"]
    assert not tones["music"]["probes"]["difference"]
    ear = EAR.choose("hybrid", _DSPEar(), good)
    assert EAR.trusted_to_choose(ear)["trusted"]
    weak = EAR.choose("hybrid", _HearsTonesOnly(), tones)
    t = EAR.trusted_to_choose(weak)
    assert not t["trusted"] and "difference" in t["why"]
    assert EAR.listening(weak)["trusted_to_choose"] is False


def test_what_the_ear_hears_is_played_from_the_top():
    from agentic import fixtures, listen as LI, render as R
    p = _project(fixtures.masked_lead())
    cache = R.Cache(p.path("renders"))
    chased = LI.of_project(p, "bars 2-2", cache=cache)
    full = LI.of_project(p, "bars 2-2", cache=cache, method="full")
    assert chased["playback"]["approximate"] is True
    assert full["playback"] == {"method": "full", "approximate": False,
                                "how": LI.PLAYBACK["full"]}
    root = _tmp()
    idx = LI.write([chased, full], root, "pb", mp3=False, raw=True)
    assert [e["playback"]["method"] for e in idx["fragments"]] == [
        "chase", "full"]
    assert os.path.exists(idx["reel_raw"]["wav"])
    assert idx["reel_raw"]["common_gain_db"] <= 0.0
    shutil.rmtree(root)
    shutil.rmtree(os.path.dirname(p.root))


def test_compose_range_can_hear_the_section_it_wrote():
    from agent import Agent
    root = _tmp()
    a = Agent(root)
    a.call("create_musical_state", preset="etude", project_id="e")
    r = a.call("compose_range", project_id="e", listen=True)
    assert r.get("composed"), r
    heard = r["heard"]
    assert heard["listening"]["available"] is False
    assert "revised" not in heard
    shutil.rmtree(root)


# -- the loop and the ear on the NES and the OPL2 ----------------------------------------------
def test_the_loop_hears_and_corrects_a_buried_lead_on_the_nes_and_the_opl2():
    from agentic import fixtures, loop as L, patch as P, render as R
    from agentic import state as S, timeline as T, ear as EAR
    for fixture, machine in ((fixtures.masked_lead_nes, "NES"),
                             (fixtures.masked_lead_opl, "OPL2")):
        p = _project(fixture())
        tl = T.build(S.content(p.state))
        assert EAR.platform_of(tl)["stereo"] is False
        assert machine in EAR.platform_of(tl)["name"]
        cache = R.Cache(p.path("renders"))
        err = R.chase_error(tl, tl.range("bars 2-2"), cache=cache)
        assert err["envelope"]["mean_db"] < 1.0, err
        h = EAR.ToolEar(cache).hear(tl, tl.range("all"))
        ids = [p.add_observation(o) for o in h["observations"]]
        buried = [i for i, o in zip(ids, h["observations"])
                  if o["kind"] == "buried" and o["voices"] == ["lead"]]
        assert buried, h["observations"]
        d = L.improve(p, buried[0], cache=cache, hearing=h, ab=False)
        assert d["outcome"] == "committed" and d["after"] > d["before"], d
        assert d["chain"][0]["op"] == "level"
        c = S.content(p.state)
        for op in ({"op": "pan", "voice": "pad", "side": "L"},
                   {"op": "echo", "voice": "lead", "range": "all",
                    "rows": 3, "db": -9.0}):
            try:
                P.apply(c, op, T.build(c))
                assert False, op
            except S.AgenticError as error:
                assert error.code in ("no_pan", "not_fm"), error.code
        shutil.rmtree(os.path.dirname(p.root))


def test_the_nes_triangle_has_no_level_and_says_so():
    from agentic import fixtures, patch as P, state as S, timeline as T
    c = S.content(fixtures.masked_lead_nes())
    try:
        P.apply(c, {"op": "level", "voice": "bass", "range": "all",
                    "db": -3.0}, T.build(c))
        assert False
    except S.AgenticError as error:
        assert error.code == "no_volume" and "triangle" in str(error)


def test_a_mono_machine_routes_no_stereo_change():
    from agentic import fixtures, guided as G, state as S, timeline as T
    tl = T.build(S.content(fixtures.masked_lead_nes()))
    r = G.route({"basis": "heard by model", "about_the_piece": True,
                 "category": "space", "direction": "more"}, tl, "lead")
    assert r["route"] is None and "mono" in r["why"]


def test_one_piece_plays_one_machine():
    from agentic import state as S
    st = S.new_state("x", target="nes")
    assert st["columns"] == ["nes0", "nes1", "nes2", "nes3"]
    c = S.content(st)
    c["columns"] = ["nes0", "fm1"]
    assert any(x["code"] == "bad_column" for x in S.check_content(c))


def test_a_commit_over_a_newer_state_is_refused():
    from agentic import fixtures, state as S
    p = _project(fixtures.masked_lead())
    other = S.Project.open(p.root)
    c = S.content(other.state)
    c["sections"][0]["rows"][0][1] = "A-5:80"
    other.commit(c, {"kind": "test"})
    c2 = S.content(p.state)
    c2["sections"][0]["rows"][1][1] = "B-5:80"
    try:
        p.commit(c2, {"kind": "test"})
        assert False
    except S.AgenticError as error:
        assert error.code == "stale_state"
    again = S.Project.open(p.root)
    assert again.head == 1 and again.checkout(1)["sections"][0]["rows"][0][1] \
        == "A-5:80"
    shutil.rmtree(os.path.dirname(p.root))


def test_a_passed_probe_check_is_kept_and_can_be_forgotten():
    from agent import Agent
    from test_agentic import _DSPEar

    class Counting(_DSPEar):
        calls = 0

        def listen(self, wav, prompt):
            Counting.calls += 1
            return super().listen(wav, prompt)
    root = _tmp()
    a = Agent(root, adapter=Counting())
    a.call("create_musical_state", preset="etude", project_id="e")
    v1 = a._cached_verification()
    first = Counting.calls
    assert v1["verified"] and first >= 8 and not v1.get("cached")
    b = Agent(root, adapter=Counting())          # a new process
    v2 = b._cached_verification()
    assert v2.get("cached") and Counting.calls == first
    b.forget_verification()
    b._cached_verification()
    assert Counting.calls > first
    shutil.rmtree(root)


def test_the_ear_on_the_nes_is_sent_mono_and_chooses_the_change():
    from agentic import fixtures, guided as G, render as R
    p = _project(fixtures.masked_lead_nes())
    cache = R.Cache(p.path("renders"))
    adapter = _ChoosingEar(pick=1)
    obs = {"basis": "heard by model", "about_the_piece": True,
           "category": "balance", "direction": "less", "voices": ["pad"],
           "statement": "the held pulse covers the lead; softer",
           "confidence": 0.7}
    rv = G.revise_heard(p, "all", [obs], ear=_choosing(adapter, cache),
                        cache=cache)
    w = rv["worked_on"][0]
    assert w["outcome"] == "committed" and w["judged_by"] == "heard by model"
    assert set(adapter.channels) == {1}           # the 2A03 is mono
    assert "NES" in rv["listening"]["model"] or rv["listening"]["available"]
    shutil.rmtree(os.path.dirname(p.root))


# -- P3: an instrument said phase by phase ---------------------------------------------------------
def test_a_phase_by_phase_statement_is_read_into_clauses():
    from agentic import intent as INT
    r = INT.parse("короткий укол, затем тёмный корпус, вибрато только на "
                  "длинной ноте")
    assert [(c["phase"], c["quality"], c["direction"]) for c in
            r["clauses"]] == [("attack", "sharpness", "more"),
                              ("body", "brightness", "less"),
                              ("held", "vibrato", "more")]
    assert r["clauses"][2]["only_long"] is True and not r["unparsed"]
    r = INT.parse("softer attack; brighter body; shorter tail")
    assert [(c["phase"], c["direction"]) for c in r["clauses"]] == [
        ("attack", "less"), ("body", "more"), ("release", "less")]
    # what it cannot place it says, it does not guess
    r = INT.parse("the lead sounds sad")
    assert r["clauses"] == [] and r["unparsed"] == ["the lead sounds sad"]
    # a model's clauses: only those with a phase, quality and direction
    r = INT.parse([{"phase": "attack", "quality": "brightness",
                    "direction": "more"},
                   {"phase": "attack", "quality": "vibrato",
                    "direction": "more"}])
    assert len(r["clauses"]) == 1 and len(r["unparsed"]) == 1


def test_the_phases_not_named_are_held_and_a_spent_step_is_said():
    from agentic import intent as INT
    base = {"attack_rise_ms": 0.0, "brightness_attack": 6.8,
            "brightness_body": 6.8, "release_ms": 50.0,
            "vibrato_depth_cents": 0.0, "vibrato_rate_hz": None}
    it = INT.parse("a short sting, then a dark body, vibrato only on the "
                   "long notes")
    g = INT.to_goal(it, base, [0.2, 0.2, 0.8, 0.2, 0.8, 0.3])
    # the attack already rises in 0 ms: no step makes it sharper, so it is
    # held, and the reply says so
    assert "attack_rise_ms" not in g["goal"] and \
        "attack_rise_ms" in g["hold"]
    assert any("already" in n for n in g["notes"])
    assert g["goal"]["brightness_body"] < 6.8
    assert g["hold"]["brightness_attack"] == 1.7       # 25 % of 6.8
    # of the three notes the profile reads (0.25 s or longer), two are
    # long: two in three should swing
    assert g["goal"]["vibrato_share"] == 0.667
    assert g["goal"]["vibrato_depth_cents"] >= 25.0
    # no long note: no vibrato asked, and said
    g = INT.to_goal(it, base, [0.2, 0.3, 0.3])
    assert "vibrato_depth_cents" not in g["goal"]
    assert any("no note" in n for n in g["notes"])
    rep = INT.report(it, base, dict(base, brightness_body=4.0,
                                    brightness_attack=6.0),
                     {"brightness_body": 4.4}, {"brightness_attack": 1.7})
    assert rep["asked"][0]["moved_the_asked_way"] and rep["all_held"]
    rep = INT.report(it, base, dict(base, brightness_body=7.5,
                                    brightness_attack=3.0),
                     {"brightness_body": 4.4}, {"brightness_attack": 1.7})
    assert not rep["all_asked_moved"] and not rep["all_held"]


def test_a_model_that_heard_phases_routes_them_to_the_instrument():
    from agentic import ear as EAR, fixtures, guided as G, state as S, \
        timeline as T
    tl = T.build(S.content(fixtures.masked_lead()))
    phases = EAR.phases_of({"phases": [
        {"phase": "body", "quality": "brightness", "direction": "less"},
        {"phase": "held", "quality": "vibrato", "direction": "more",
         "only_long": True}, {"phase": "nowhere"}]})
    assert len(phases) == 2
    o = {"basis": "heard by model", "about_the_piece": True,
         "category": "other", "direction": None, "voices": ["lead"],
         "phases": phases, "statement": "dark body, vibrato when held"}
    r = G.route(o, tl, "lead")
    assert r["route"] == "instrument" and r["intent"]["clauses"] == phases
    assert G.route(dict(o, voices=["bass"], phases=phases), tl,
                   "lead")["voice"] == "bass"
    nes = T.build(S.content(fixtures.masked_lead_nes()))
    assert G.route(dict(o, voices=[]), nes, "lead")["route"] is None


def test_an_instrument_said_phase_by_phase_moves_those_phases_only():
    # T7 of the audit: the body darker and a vibrato on the long notes, the
    # attack and the release held; alone and in the mix, matched and raw
    from agent import Agent
    from test_agentic import _Banks
    with _Banks():
        root = _tmp()
        a = Agent(root)
        a.call("create_musical_state", preset="held_lead", project_id="h")
        r = a.call("design_instrument", project_id="h", voice="lead",
                   intent="a short sting, then a dark body, vibrato only "
                          "on the long notes", candidates=8)
        assert r["outcome"] == "committed", r.get("why") or r
        assert any("already" in n for n in r["notes"])     # the sting
        ph = r["phases"]
        assert ph["all_held"] and ph["all_asked_moved"], ph
        held = {h["reading"] for h in ph["held"]}
        assert {"attack_rise_ms", "brightness_attack",
                "release_ms"} <= held
        body = next(x for x in ph["asked"] if x["phase"] == "body")
        assert body["after"] < body["before"]
        share = next(x for x in ph["asked"]
                     if x["reading"] == "vibrato_share")
        assert 0.0 < share["after"] < 1.0      # the long notes, not all
        assert os.path.exists(r["ab_reel_raw"])
        assert r["ab_reel"] and os.path.exists(r["ab_reel"])
        shutil.rmtree(root)


def test_phases_the_model_heard_change_the_instrument_it_then_picks():
    from agentic import ear as EAR, fixtures, guided as G, render as R
    with _Banks():
        p = _project(fixtures.held_lead())
        cache = R.Cache(p.path("renders"))
        adapter = _ChoosingEar(pick=1)
        obs = {"basis": "heard by model", "about_the_piece": True,
               "category": "other", "direction": None, "voices": ["lead"],
               "phases": EAR.phases_of({"phases": [
                   {"phase": "held", "quality": "vibrato",
                    "direction": "more", "only_long": True}]}),
               "statement": "the long notes want a vibrato, the short ones "
                            "not", "confidence": 0.7}
        rv = G.revise_heard(p, "all", [obs], ear=_choosing(adapter, cache),
                            cache=cache, instrument_budget=3)
        w = rv["worked_on"][0]
        assert w["outcome"] == "committed", w["why"]
        assert w["listening_verdict"]["choice"] == 1     # the ear picked it
        assert w["phases"]["all_held"], w["phases"]
        share = next(x for x in w["phases"]["asked"]
                     if x["reading"] == "vibrato_share")
        assert 0.0 < share["after"] < 1.0
        shutil.rmtree(os.path.dirname(p.root))


# -- P3: phrase anchors ------------------------------------------------------------------------
def _phrase_score():
    """Four phrases of four bars: three busy bars, then one that settles
    on a held note; each phrase on its own notes; a pad and a hit on each
    phrase's first downbeat, a drum on every downbeat."""
    sets = (("A-4", "C-5", "E-5", "D-5"), ("F-4", "A-4", "C-5", "G-4"),
            ("A-4", "C-5", "E-5", "D-5"), ("G-4", "B-4", "D-5", "E-5"))
    lines = ["bpm 150", "lpb 4", "inst fm0 square_lead",
             "inst fm1 slap_bass", "inst fm2 strings",
             "cols fm0 fm1 fm2 noise"]
    for notes in sets:
        for bar in range(4):
            for r in range(16):
                lead = "..."
                if bar < 3 and r % 2 == 0:
                    lead = notes[(r // 2 + bar) % 4]
                elif bar == 3:
                    lead = {0: notes[0], 2: notes[1], 4: notes[2],
                            15: "==="}.get(r, "...")
                bass = notes[0][0:2] + "3" if r == 0 else "..."
                pad = notes[1] if bar == 0 and r == 0 else (
                    "===" if bar == 0 and r == 8 else "...")
                drum = "w1" if r == 0 else ("===" if r == 2 else "...")
                lines.append(f"{lead}  {bass}  {pad}  {drum}")
    return "\n".join(lines) + "\n"


def _phrased_reference():
    from agentic import references as REF
    root = _tmp()
    path = os.path.join(root, "phrases.trk")
    with open(path, "w") as handle:
        handle.write(_phrase_score())
    p = _project(_composed().state)
    REF.add(p, path, tags=["rhythm"])
    return p, REF.all_(p)[0]["id"], root


def test_phrase_ends_are_found_in_a_scores_notes_and_in_its_audio():
    from agentic import anchors as A, references as REF
    p, rid, root = _phrased_reference()
    rec = A.of_reference(p, rid)
    assert rec["basis"] == "the source's notes"
    clear = {a["bar"] for a in rec["anchors"] if a["certainty"] == "clear"}
    assert {4, 8, 12} <= clear, rec["anchors"]
    assert all(a["parts"]["settling"] == 1.0 for a in rec["anchors"]
               if a["bar"] in (4, 8, 12))
    # the same from the audio alone (no notes to read)
    REF.get(p, rid).pop("anchors")
    keep = REF.notes
    REF.notes = lambda *a: []
    try:
        audio = A.of_reference(p, rid, refresh=True)
    finally:
        REF.notes = keep
    assert audio["basis"] == "the audio"
    assert {8, 12} <= {a["bar"] for a in audio["anchors"]}, audio
    shutil.rmtree(root)


def test_a_reference_is_cut_from_a_phrase_and_a_cadence_ends_on_one():
    from agentic import anchors as A, fixtures, listen as LI, \
        references as REF, state as S, timeline as T
    p, rid, root = _phrased_reference()
    s = A.phrase_span(p, rid, 4, "statement", near_bar=6)
    assert s["first_bar"] == 5 and s["aligned_by"] == "phrase anchors"
    s = A.phrase_span(p, rid, 2, "cadence", near_bar=3)
    assert (s["first_bar"] + 1) in (4, 8, 12) and \
        s["certainty"] == "clear", s
    # a person's marks replace the computed ones
    A.mark(p, rid, [6], note="the turn is at bar 6")
    s = A.phrase_span(p, rid, 2, "statement", near_bar=6)
    assert s["first_bar"] == 7 and s["basis"] == "manual"
    try:
        A.mark(p, rid, [99])
        assert False
    except S.AgenticError as error:
        assert error.code == "bad_anchor"
    # the piece's own sections carry their functions
    tl = T.build(S.content(fixtures.masked_lead()))
    assert A.function_of(tl, tl.range("A")) == "statement"
    assert A.of_piece(tl)["sections"][0]["function"] == "statement"
    # a region a person chose is kept as chosen
    REF.get(p, rid)["regions"][0]["chosen_by"] = "the user"
    tl = T.build(S.content(p.state))
    f = LI.reference_for(p, rid, tl, tl.range("all"), 2)
    assert f["aligned"]["aligned_by"] == "region"
    shutil.rmtree(root)


# -- P3: the loop on an Impulse Tracker module --------------------------------------------------
def _masked_it(root):
    from agentic import capabilities as CAP, fixtures
    p = _project(fixtures.masked_lead())
    out = CAP.export(p, "it", os.path.join(root, "masked.it"))
    shutil.rmtree(os.path.dirname(p.root))
    return out["path"]


def test_an_it_modules_buried_channel_is_brought_out_in_its_header():
    from agentic import itloop as IT, openmpt as O
    if not O.available():
        support.skip("libopenmpt is not installed")
    root = _tmp()
    path = _masked_it(root)
    with open(path, "rb") as handle:
        original = handle.read()
    rec = IT.correct_buried(path, 1, os.path.join(root, "out"))
    assert rec["playback"].startswith("full") and "none" in rec["chase"]
    assert rec["outcome"] == "committed", rec["why"]
    assert rec["after"]["band_margin_db"] - \
        rec["before"]["band_margin_db"] >= 2.5
    # the lead's volume was already 64: raising it is refused by name
    refused = [c for c in rec["candidates"] if "refused" in c["why"]]
    assert refused and all("outside 1-64" in c["why"] for c in refused)
    with open(rec["file"], "rb") as handle:
        after = handle.read()
    changed = [i for i, (a, b) in enumerate(zip(original, after)) if a != b]
    assert changed and all(0x80 <= i < 0xC0 for i in changed)  # volumes only
    with open(path, "rb") as handle:
        assert handle.read() == original                 # never rewritten
    assert os.path.exists(rec["ab_reel"]) and os.path.exists(rec["ab_reel_raw"])
    assert rec["judged_by"] == "measured"
    with O.Module(after) as m:                     # libopenmpt plays it
        assert m.channels == 3
    from agent import Agent
    r = Agent(os.path.join(root, "agent")).call("correct_module", path=path,
                                                channel=2)
    assert r["outcome"] == "committed" and r["chosen"] == rec["chosen"]
    assert Agent(root).call("correct_module", path="/no/such.it",
                            channel=1)["error"] == "no_file"
    shutil.rmtree(root)


def test_an_it_change_is_chosen_by_the_ear_it_is_sent_in_stereo():
    from agentic import itloop as IT, openmpt as O, render as R
    if not O.available():
        support.skip("libopenmpt is not installed")
    root = _tmp()
    path = _masked_it(root)
    adapter = _ChoosingEar(pick=1)
    rec = IT.correct_buried(path, 1, os.path.join(root, "out"),
                            ear=_choosing(adapter, R.Cache()))
    assert rec["judged_by"] == "heard by model", rec
    assert rec["listening_verdict"]["choice"] == 1 and adapter.choices
    assert set(adapter.channels) == {2}
    assert rec["outcome"] == "committed"
    shutil.rmtree(root)


def test_what_the_module_loop_does_not_change_is_refused_by_name():
    from agentic import capabilities as CAP, itloop as IT, openmpt as O, \
        state as S
    for op in ("envelope", "nna", "sample", "notes"):
        try:
            IT.refuse(op)
            assert False
        except S.AgenticError as error:
            assert error.code == "not_on_it" and op in str(error)
    try:
        IT.Piece(b"Extended Module: ...")
        assert False
    except S.AgenticError as error:
        assert error.code == "not_an_it"
    caps = CAP.inspect("schism")
    assert caps["agentic_loop"] == "Unsupported"
    assert caps["actions"]["module_loop"]["status"] == "Partial"
    if O.available():
        root = _tmp()
        piece = IT.Piece.open(_masked_it(root))
        surround = piece._with(IT.CHANNEL_PAN + 0, 100, "surround")
        try:
            IT.pan(surround, 0, 0.5)
            assert False
        except S.AgenticError as error:
            assert error.code == "pan_locked"
        shutil.rmtree(root)


# -- P4: what was sent to the ear, and an ear that hears what is not there -------------------------
class _Suggestible(_ChoosingEar):
    """Hears whatever the question names: right on every probe but the
    drums, which it always finds."""

    name = "test-suggestible-ear"

    def listen(self, wav, prompt):
        if "drums in this clip" in prompt:
            return "soft"
        return super().listen(wav, prompt)


def test_an_ear_that_hears_drums_that_are_not_there_is_not_trusted():
    from agentic import ear as EAR
    v = EAR.verify(_Suggestible())
    assert v["verified"]                  # it hears tones
    assert v["music"]["probes"]["absent_drums"] is False
    assert v["music"]["probes"]["present_drums"] is True
    ear = EAR.choose("native", _Suggestible(), v)
    t = EAR.trusted_to_choose(ear)
    assert not t["trusted"] and "absent_drums" in t["why"]
    # the positive control hears the difference
    good = EAR.verify(_ChoosingEar())
    assert good["music"]["passed"], good["music"]


def test_the_audible_session_keeps_what_the_model_was_sent():
    from agent import Agent
    from agentic import session as SESSION
    with _Banks():
        root = _tmp()
        adapter = _ChoosingEar(pick=1)
        a = Agent(root, adapter=adapter)
        a.call("create_musical_state", preset="masked_lead", project_id="m")
        empty = a.call("audible_session", project_id="m")
        assert empty["summary"]["calls"] == 0 and \
            not empty["summary"]["heard"]
        r = a.call("design_instrument", project_id="m", voice="lead",
                   goal={"vibrato_depth_cents": 30.0, "vibrato_rate_hz": 6.0,
                         "vibrato_delay_ms": 0.0}, candidates=2)
        assert r["listening_verdict"]["choice"] == 1, r
        s = a.call("audible_session", project_id="m")
        summ = s["summary"]
        assert summ["heard"] and summ["calls"] >= 1
        choose = summ["by_purpose"]["choose among versions"]
        assert choose["channels"] == [2] and choose["audio_seconds"] > 1.0
        assert summ["tokens"] == "not reported by the endpoint"
        p = a._project("m")
        sent = [e for e in SESSION.entries(p.root)
                if e["purpose"] == "choose among versions"]
        clip = os.path.join(p.root, "audible", "clips",
                            sent[-1]["clip"] + ".wav")
        assert os.path.exists(clip)
        assert SESSION.wav_facts(open(clip, "rb").read())["channels"] == 2
        last = s["decisions"][-1]
        assert last["clip"]["hash"] == sent[-1]["clip"]
        assert last["experiment"]["approval"] == "heard by model"
        assert last["action_plan"]["preserve"] == ["notes", "rhythm",
                                                   "harmony"]
        shutil.rmtree(root)


# -- P5: an IT instrument shaped phase by phase ----------------------------------------------------
def _held_it(root, instruments=True):
    from agentic import fixtures, timeline as T, state as S
    import it_export
    import tracker
    tl = T.build(S.content(fixtures.held_lead()))
    events, meta = tracker.loads(tl.text)
    data, _report = it_export.build(events, meta=meta, title="held",
                                    instruments=instruments)
    path = os.path.join(root, "held.it" if instruments else "held_smp.it")
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def test_a_sample_mode_module_gets_instruments_and_plays_the_same():
    from agentic import itloop as IT, itshape as ITS, openmpt as O
    if not O.available():
        support.skip("libopenmpt is not installed")
    root = _tmp()
    with open(_held_it(root, instruments=False), "rb") as handle:
        smp = handle.read()
    assert not ITS.layout(smp)["instrument_mode"]
    ins = ITS.with_instruments(smp)
    lay = ITS.layout(ins)
    assert lay["instrument_mode"] and lay["instruments"] == lay["samples"]
    a = IT.play(IT.Piece(smp))["samples"]
    b = IT.play(IT.Piece(ins))["samples"]
    assert len(a) == len(b)
    err = sum((x - y) ** 2 for x, y in zip(a[::5], b[::5]))
    ref = sum(x * x for x in a[::5]) or 1.0
    assert 10 * math.log10(err / ref + 1e-15) < -100.0    # the same audio
    assert ITS.with_instruments(ins) is ins                 # already there
    shutil.rmtree(root)


def test_an_it_channel_said_phase_by_phase_moves_those_phases_only():
    from agent import Agent
    from agentic import openmpt as O
    if not O.available():
        support.skip("libopenmpt is not installed")
    root = _tmp()
    path = _held_it(root)
    with open(path, "rb") as handle:
        original = handle.read()
    r = Agent(os.path.join(root, "a")).call(
        "shape_module", path=path, channel=2,
        intent="a darker body, vibrato only on the long notes")
    assert r["outcome"] == "committed", r
    ph = r["phases"]
    assert ph["all_held"], ph            # the attack's brightness, release
    body = next(x for x in ph["asked"] if x["reading"] == "brightness_body")
    assert body["after"] < body["before"] - 1.0
    share = next(x for x in ph["asked"] if x["reading"] == "vibrato_share")
    assert 0.0 < share["after"] < 1.0    # the long notes, not every note
    # a static filter darkens the attack too: refused by the hold
    ifc = [c for c in r["candidates"] if c["id"].startswith("ifc")]
    assert ifc and not any(c["accepted"] for c in ifc)
    with open(path, "rb") as handle:
        assert handle.read() == original
    assert os.path.exists(r["file"]) and os.path.exists(r["ab_reel_raw"])
    # brighter than the sample is refused by name
    r = Agent(os.path.join(root, "b")).call(
        "shape_module", path=path, channel=2, intent="a brighter body")
    assert r["outcome"] == "rolled_back"
    assert any("as bright as it was rendered" in c["why"]
               for c in r["candidates"])
    shutil.rmtree(root)


# -- P6: the ear is sent the phrase it heard, not the whole range again ------------------------------
def _four_times(st):
    st["order"] = ["A", "A", "A", "A"]
    return st


def test_the_window_an_ear_hears_is_the_phrase_around_the_observation():
    from agentic import anchors as A, fixtures, state as S, timeline as T
    tl = T.build(S.content(_four_times(fixtures.masked_lead())))
    w = A.window_for(tl, "all", {"where": {"bars": [5, 5]}})
    assert w["spec"] == "bars 5-6", w        # the third time A plays
    w = A.window_for(tl, "all", {"where": {"bars": [4, 5]}})
    assert w["spec"] == "bars 3-6", w        # across a join: both phrases
    assert A.window_for(tl, "all", {})["spec"] == "all"
    assert A.window_for(tl, "bars 7-8", {"where": {"bars": [1, 8]}})[
        "spec"] == "bars 7-8"


def test_a_heard_gap_is_chosen_about_over_its_phrase_only():
    from agentic import fixtures, guided as G, render as R
    p = _project(_four_times(fixtures.masked_lead()))
    cache = R.Cache(p.path("renders"))
    adapter = _ChoosingEar(pick=1)
    obs = {"basis": "heard by model", "about_the_piece": True,
           "category": "balance", "direction": "less", "voices": ["pad"],
           "where": {"bars": [5, 5]}, "confidence": 0.7,
           "statement": "the pad covers the lead in the third phrase"}
    rv = G.revise_heard(p, "all", [obs], ear=_choosing(adapter, cache),
                        cache=cache)
    w = rv["worked_on"][0]
    v = w["listening_verdict"]
    assert v["window"]["spec"] == "bars 5-6", v.get("window")
    # four versions of two bars (3.2 s each at 150 BPM) and their gaps,
    # not four of the whole eight bars
    assert v["audio_seconds"] < 4 * 3.2 + 4 * 1.0, v["audio_seconds"]
    shutil.rmtree(os.path.dirname(p.root))


def test_a_join_says_what_jumps_where_one_section_hands_over():
    import copy
    from agent import Agent
    from agentic import fixtures, joins as J, state as S, timeline as T
    tl = T.build(S.content(_four_times(fixtures.masked_lead())))
    joins = J.measure(tl)
    assert [j["bar"] for j in joins] == [3, 5, 7]
    assert all(not j["flags"] for j in joins)        # A into A, smooth
    st = fixtures.masked_lead()
    b = copy.deepcopy(st["sections"][0])
    b["id"] = "B"
    for row in b["rows"]:                            # the lead an octave up,
        if row[1] not in ("...", "==="):             # every row struck
            row[1] = row[1].replace("-5", "-6").replace("-4", "-5")
    st["sections"].append(b)
    st["order"] = ["A", "B"]
    st["structure"]["plan"].append({"id": "B", "function": "development",
                                    "bars": 2})
    j = J.measure(T.build(S.content(st)))[0]
    assert j["register_semitones"] >= 12
    assert any("jumps" in f for f in j["flags"]), j["flags"]
    root = _tmp()
    a = Agent(root)
    a.call("create_musical_state", preset="masked_lead", project_id="m")
    r = a.call("measure_joins", project_id="m")
    assert r["joins"] == [] and r["limits"]["gap_beats"] == 1.0
    shutil.rmtree(root)


# -- P7: a heard transition acted on --------------------------------------------------------------
def _abrupt_join():
    import copy
    from agentic import fixtures
    st = fixtures.masked_lead()
    b = copy.deepcopy(st["sections"][0])
    b["id"] = "B"
    for i, row in enumerate(b["rows"]):          # the bass on every row
        if i % 2 == 1:
            row[0] = row[0] if row[0] not in ("===", "...") else \
                st["sections"][0]["rows"][i - 1][0]
        if row[1] not in ("...", "==="):
            row[1] = row[1].replace(":80", ":120") if ":" in row[1] else \
                row[1]
    st["sections"].append(b)
    st["order"] = ["A", "B"]
    st["structure"]["plan"].append({"id": "B", "function": "development",
                                    "bars": 2})
    return st


def test_a_heard_abrupt_transition_is_eased_and_measured_at_the_join():
    from agentic import fixtures, guided as G, joins as J, render as R, \
        state as S, timeline as T
    st = _abrupt_join()
    j0 = J.measure(T.build(S.content(st)))[0]
    assert j0["bar"] == 3 and j0["density_ratio"] >= 1.3, j0
    tl1 = T.build(S.content(fixtures.masked_lead()))
    assert G.route({"basis": "heard by model", "about_the_piece": True,
                    "category": "transition", "direction": "less"}, tl1,
                   "lead")["route"] is None           # one section: no join
    p = _project(st)
    cache = R.Cache(p.path("renders"))
    adapter = _ChoosingEar(pick=1)
    obs = {"basis": "heard by model", "about_the_piece": True,
           "category": "transition", "direction": "less",
           "where": {"bars": [3, 3]}, "confidence": 0.7,
           "statement": "B comes in too abruptly"}
    rv = G.revise_heard(p, "all", [obs], ear=_choosing(adapter, cache),
                        cache=cache)
    w = rv["worked_on"][0]
    assert w["dimension"] == "transition", w
    assert w["outcome"] == "committed", w.get("why")
    kept = next(h for h in w["hypotheses"] if h["id"] == w["chosen"])
    assert kept["after"] < kept["before"], kept    # the join reads smoother
    window = w["listening_verdict"]["window"]["bars"]
    assert window[0] <= 2 and window[1] >= 3       # both sides of the join
    shutil.rmtree(os.path.dirname(p.root))


# -- P8: candidates side by side -------------------------------------------------------------------
def test_candidates_measured_side_by_side_give_the_same_record():
    import json
    from agentic import itloop as IT, openmpt as O, parallel as PAR
    assert PAR.run(abs, [-1, 2, -3], n=1) == ([1, 2, 3],
                                              "in order (one worker)")
    out, how = PAR.run(abs, [-1, 2, -3], n=3)
    assert out == [1, 2, 3]
    if not O.available():
        support.skip("libopenmpt is not installed")
    root = _tmp()
    path = _masked_it(root)
    keep = os.environ.get("CHIPGEN_WORKERS")
    recs = {}
    try:
        for w in ("1", "3"):
            os.environ["CHIPGEN_WORKERS"] = w
            recs[w] = IT.correct_buried(path, 1, os.path.join(root, w))
    finally:
        if keep is None:
            os.environ.pop("CHIPGEN_WORKERS", None)
        else:
            os.environ["CHIPGEN_WORKERS"] = keep
    assert recs["1"]["parallel"].startswith("in order")
    same = [json.dumps(r["candidates"], sort_keys=True) for r in
            recs.values()]
    assert same[0] == same[1]
    assert recs["1"]["chosen"] == recs["3"]["chosen"]
    shutil.rmtree(root)


# -- P10: a module that states its beat keeps its own grid --------------------------------------------
def test_a_module_that_states_its_beat_keeps_its_rows_when_the_audio_hears_dotted():
    from agentic import references as REF
    beat = 0.32
    rows = [{"t": round(i * beat / 4, 5), "row": i % 64} for i in range(256)]
    score = {"source": "score", "bpm": 60 / beat, "beat_s": beat,
             "rows_per_beat": 4, "rows_per_bar": 16, "beats_per_bar": 4,
             "beat_known": True, "how": "the IT header's row highlight",
             "bars": [round(i * beat * 4, 4) for i in range(16)]}
    keep = REF._audio_grid
    REF._audio_grid = lambda *a: {"bpm": 60 / (1.5 * beat),
                                  "beat_s": 1.5 * beat, "confidence": 0.95,
                                  "first_downbeat_s": 0.7}
    try:
        for kind, kept in (("module", True), ("trk", False)):
            record = {"kind": kind, "seconds": 20.0, "grid": dict(score)}
            REF._settle_grid(None, record, rows)
            if kept:
                assert record["grid"]["source"] == "score"
                assert record["grid"]["bars"] == score["bars"]
                assert record["grid"]["audio_disagrees"]["ratio"] == 1.5
                assert "rows are kept" in record["grid_check"]["verdict"]
            else:                      # a transcription's grid gives way
                assert record["grid"]["source"] == "audio"
    finally:
        REF._audio_grid = keep


# -- the live check's plumbing (no model here: a stand-in that cannot tell) --------------------------
class _NeverSure(_ChoosingEar):
    name = "test-never-sure"

    def listen(self, wav, prompt):
        if "versions of the same passage" in prompt:
            self.channels.append(2)
            # the versions' labels may not give the answer away
            index = prompt.split("\n")[1:5]
            assert not any(w in " ".join(index).lower()
                           for w in ("vibrato", "soft", "copy")), index
            return json.dumps({"choice": "uncertain", "confidence": 0.1,
                               "statement": "cannot tell"})
        return super().listen(wav, prompt)


def test_the_live_check_asks_four_questions_and_scores_them_honestly():
    from agentic import live as LIVE
    root = _tmp()
    rec = LIVE.run(_NeverSure(), os.path.join(root, "live"))
    assert rec["verified"] and len(rec["questions"]) == 4
    picks = {q["question"]: (q["picked"], q["right"])
             for q in rec["questions"]}
    # "uncertain" is right only where the versions are the same
    assert picks["T1 identity"] == ("uncertain", True)
    assert picks["T1 change"] == ("uncertain", False)
    assert rec["passed"] == {"T1": False, "T3": False}
    assert all(q["clip"] for q in rec["questions"])
    assert rec["session"]["by_purpose"]["choose among versions"][
        "calls"] == 4
    shutil.rmtree(root)


# -- audit of 27 October: recordings in other containers; timing against the grid ------------------
def test_an_aac_recording_is_decoded_by_ffmpeg_and_an_mp4_named_mp3_is_said():
    import shutil as _sh
    from agentic import references as REF, state as S
    if not _sh.which("ffmpeg"):
        support.skip("ffmpeg is not installed")
    root = _tmp()
    wav = os.path.join(root, "tone.wav")
    rate = 44100
    tone = []
    for i in range(rate * 3):
        v = 0.3 * math.sin(2 * math.pi * 220 * i / rate) * (1 if (i // 11025) % 2 == 0 else 0.2)
        tone += [v, v]
    REF._write_wav(wav, tone)
    m4a = os.path.join(root, "tone.m4a")
    subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-i", wav, "-c:a", "aac",
                    m4a], check=True)
    named_mp3 = os.path.join(root, "tone_really_aac.mp3")
    shutil.copy(m4a, named_mp3)
    p = _project(_composed().state)
    a = REF.add(p, m4a, tags=["timbre"])
    assert a["kind"] == "audio" and 2.5 < a["seconds"] < 3.5
    b = REF.add(p, named_mp3, tags=["timbre"])
    assert b["kind"] == "audio" and 2.5 < b["seconds"] < 3.5
    assert REF.kind_of("x.flac") == "audio"
    shutil.rmtree(root)


def test_timing_reads_a_swing_from_the_audio_and_keeps_a_straight_part_on_its_rows():
    from agentic import fixtures, patch as P, state as S, timeline as T, \
        timing as TM
    st = fixtures.masked_lead()
    tl = T.build(S.content(st))
    straight = TM.measure(tl, voices=["bass"])["voices"]["bass"]
    assert abs(straight["mean_ms"]) < 5 and straight["spread_ms"] < 5
    assert abs(straight["swing"] - 0.5) < 0.03
    c, _d = P.apply(S.content(st), {"op": "rhythm", "voice": "bass",
                                    "range": "all", "mode": "swing",
                                    "share": 0.5}, tl)
    m = TM.measure(T.build(c), voices=["bass"])
    swung = m["voices"]["bass"]
    assert 0.6 <= swung["swing"] <= 0.65            # half a row late: 0.625
    assert swung["written_ms"] > 20 and abs(swung["sounding_ms"]) < 5
    assert any("off-beats" in n for n in m["notes"])
