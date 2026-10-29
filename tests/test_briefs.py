"""Briefings per model family, and the budget they are held to.

The failure this round answers: models that do not read closely got a
briefing that assumed they would. Some were asked through the interface
for "chipgen's tracker notation" and shown none of it; the rest were
pointed at 12,000 tokens of documentation and kept the gist. Every test
here pins one of the things that went wrong.
"""

import os

import support

ROOT = support.ROOT


def test_a_family_name_picks_a_profile():
    import prompts

    assert prompts.profile_for("gpt") == "contract"
    assert prompts.profile_for("claude") == "reasoned"
    assert prompts.profile_for("grok") == "reasoned"
    assert prompts.profile_for("llama") == "compact"
    # The profile names themselves work, so a caller can be explicit.
    for name in prompts.PROFILES:
        assert prompts.profile_for(name) == name


def test_a_small_model_gets_the_compact_brief_whatever_its_family():
    """A small context window binds before house style does."""
    import prompts

    assert prompts.profile_for("qwen2.5:7b") == "compact"
    assert prompts.profile_for("some-gpt-like-3b") == "compact"
    assert prompts.profile_for("mistral-7b-instruct") == "compact"
    assert prompts.profile_for("llama-70b") == "compact", \
        "llama is a local family, and 70B is still a local model"


def test_an_unknown_model_gets_the_strictest_brief():
    """The failure is a model that does not read closely, and an unknown
    model has not shown that it does."""
    import prompts

    assert prompts.profile_for("totally-new-model") == "contract"
    assert prompts.profile_for(None) == "contract"
    assert prompts.profile_for("") == "contract"


def _all_briefs():
    import prompts
    for chip in prompts.BRIEF_CHIPS:
        for profile in prompts.PROFILES:
            for delivery in prompts.DELIVERY:
                yield chip, profile, delivery, prompts.brief(
                    chip, profile, {"prompt": "a test", "bars": 4},
                    delivery=delivery)


def test_every_brief_carries_the_notation():
    """compose() leaves the notation to START_HERE.md, so a model asked
    through the interface was told to write a format it was never
    shown. A brief has to stand on its own."""
    import prompts

    for chip, profile, delivery, text in _all_briefs():
        where = f"{chip}/{profile}/{delivery}"
        assert "cols " in text and "lpb 4" in text, where
        assert "`...`" in text and "`===`" in text, where
        for column in prompts.CARDS[chip]["cols"].split():
            assert column in text, f"{where} never names {column}"


def test_every_brief_closes_its_lists():
    """A model that is not told the list is complete fills it from what
    it knows about synthesisers: `inst fm0 piano`."""
    import prompts
    import samples

    for chip, profile, delivery, text in _all_briefs():
        where = f"{chip}/{profile}/{delivery}"
        kind = prompts.CARDS[chip]["instruments"]
        if kind:
            for name in prompts.instrument_names(kind):
                assert name in text, f"{where} does not list {name}"
            assert "the only ones" in text, where
        if chip != "YM3812":
            for name in samples.names():
                assert name in text, f"{where} does not list sample {name}"


def test_no_brief_offers_a_column_the_tracker_rejects():
    import prompts
    import tracker

    valid = set(tracker.valid_columns())
    for chip in prompts.BRIEF_CHIPS:
        for column in prompts.CARDS[chip]["cols"].split():
            assert column in valid, (chip, column)


def test_the_genesis_brief_never_offers_fm5():
    """The DAC takes FM channel 6: offering both invites the collision."""
    import prompts

    assert "fm5" not in prompts.CARDS["YM2612"]["cols"].split()


def test_the_example_in_every_brief_renders_with_no_warnings():
    """The one example a small model copies the shape of has to be one
    that works — no silent channel, no attenuator at 15, nothing below a
    floor."""
    import chipgen
    import prompts

    for chip in prompts.BRIEF_CHIPS:
        score = "\n".join(prompts.template(chip, {"bpm": 140})
                          + prompts.EXAMPLES[chip]) + "\n"
        result = chipgen.compose(score)
        assert result.peak > 0.05, f"{chip} example is near-silent"
        assert not result.warnings, f"{chip} example: {result.warnings}"


def test_what_a_brief_shows_passes_what_the_checker_holds():
    """The briefing and the checker are one rule set or two that drift."""
    import prompts
    import reply

    for chip in prompts.BRIEF_CHIPS:
        score = "\n".join(prompts.template(chip, {"bpm": 140})
                          + prompts.EXAMPLES[chip]) + "\n"
        verdict = reply.check(score, chip=chip)
        assert verdict.ok, f"{chip}: {verdict.report()}"
        assert not verdict.warnings, f"{chip}: {verdict.report()}"


def test_every_rule_a_chip_names_exists():
    import prompts

    for chip, rules in prompts.CHIP_RULES.items():
        for rule in rules:
            assert rule in prompts.RULES, (chip, rule)
    used = {r for rules in prompts.CHIP_RULES.values() for r in rules}
    assert set(prompts.RULES) == used, \
        f"rules no chip teaches: {sorted(set(prompts.RULES) - used)}"


def test_every_silent_failure_the_engine_catches_is_taught():
    """A check a model is held to and was never told about is a trap."""
    import prompts
    import sanity

    taught = {r for rules in prompts.CHIP_RULES.values() for r in rules}
    # The detune ceiling is one half of the PSG range rule.
    covered_by = {"psg_detune": "psg_floor"}
    for rule in sanity.SILENT_RULES:
        assert covered_by.get(rule, rule) in taught, \
            f"the engine checks {rule!r} and no briefing teaches it"


def test_the_agent_brief_forbids_editing_the_engine():
    """The other half of the failure: handed an archive full of Python,
    a model that reaches for the code starts improving the engine."""
    import prompts

    for chip in prompts.BRIEF_CHIPS:
        for profile in prompts.PROFILES:
            text = prompts.brief(chip, profile, delivery="agent")
            assert "never edit" in text and "python/" in text
            assert "--check" in text


def test_an_agent_hands_back_an_mp3_and_a_chat_model_only_the_score():
    """The file handed back is often the most expensive thing in the
    exchange. An agent renders an MP3, a tenth of the WAV; a chat model
    renders nothing — the score is a few kilobytes and the user's side
    renders it."""
    import prompts

    for chip in prompts.BRIEF_CHIPS:
        agent = prompts.brief(chip, "gpt", delivery="agent")
        assert agent.endswith(prompts.HAND_BACK)
        assert agent.count("-o work/song.mp3") == 1
        assert ".mp3" not in prompts.brief(chip, "gpt")
    assert "-o work/song.mp3" in prompts.agents_md()


def test_the_chat_brief_asks_for_one_fenced_block():
    """Models fence their output however firmly they are told not to.
    Asking for the fence they will write anyway makes extraction exact."""
    import prompts

    text = prompts.brief("RP2A03", "gpt")
    assert "ONE fenced code block marked `trk`" in text


def test_the_strict_profiles_say_the_contract_twice():
    """First so it frames the work, last so it is the freshest thing."""
    import prompts

    for profile in ("contract", "compact"):
        text = prompts.brief("YM2612", profile)
        assert text.count(prompts.DELIVERY["chat"]) == 2, profile
    reasoned = prompts.brief("YM2612", "reasoned")
    assert reasoned.count(prompts.DELIVERY["chat"]) == 1


def test_grammar_delivery_does_not_teach_what_the_decoder_enforces():
    """A token spent on a rule the decoder makes unbreakable is a token
    the smallest models do not have."""
    import prompts

    for chip in prompts.BRIEF_CHIPS:
        text = prompts.brief(chip, "compact", delivery="grammar")
        for rule in prompts.GRAMMAR_ENFORCED:
            assert prompts.RULES[rule]["text"] not in text, (chip, rule)


def test_the_brief_is_a_small_fraction_of_the_archive_path():
    """The point of the round, as a number: the archive path is START_HERE
    plus CORE, about 12,000 tokens before a note is written."""
    import prompts

    archive = 0
    for name in ("START_HERE.md", os.path.join("bridge", "CORE.md")):
        with open(os.path.join(ROOT, name), encoding="utf-8") as handle:
            archive += prompts.estimate_tokens(handle.read())
    largest = max(prompts.estimate_tokens(text)
                  for _, _, _, text in _all_briefs())
    assert largest * 10 < archive, (largest, archive)


def test_briefs_stay_inside_their_budget():
    """A ratchet, kept just above the real sizes: a briefing that creeps
    back towards the archive path loses the reason it exists."""
    import prompts

    ceilings = {"chat": 1000, "agent": 1100, "grammar": 650}
    for chip, profile, delivery, text in _all_briefs():
        tokens = prompts.estimate_tokens(text)
        assert tokens <= ceilings[delivery], \
            f"{chip}/{profile}/{delivery} is ~{tokens} tokens"


def test_the_brief_uses_the_request():
    import prompts

    text = prompts.brief("YM3812", "contract",
                         {"prompt": "rainy jazz bar", "bpm": 96, "bars": 2,
                          "key": "F minor"})
    assert "rainy jazz bar" in text and "F minor" in text
    assert "bpm 96" in text and "exactly 32 rows" in text


def test_the_template_cast_comes_from_measurement():
    """A header that assigned `bass` to the bass part because of the word
    would be the thing this project keeps finding wrong."""
    import prompts
    import selection

    palette = selection.palette()
    header = "\n".join(prompts.template("YM2612"))
    assert f"inst fm0 {palette['bass']['name']}" in header
    assert f"inst fm1 {palette['lead']['name']}" in header


def test_the_existing_briefings_did_not_move():
    """compose() is rebuilt by the interface in JavaScript; changing it
    here without changing app.js splits the two paths."""
    import prompts

    text = prompts.compose({"chip": "RP2A03", "prompt": "x"})
    assert text.startswith("Write an original chiptune score for the RP2A03")
    assert "Hardware that will bite you" in text


def test_the_interface_template_fills_to_the_same_brief():
    """The static interface has no Python. It gets the brief with blanks
    and fills them by string replacement — and has to end up with what
    brief() builds, or the two paths teach different things again."""
    import prompts

    request = {"prompt": "a haunted castle", "bpm": 132, "bars": 6}
    for chip in prompts.BRIEF_CHIPS:
        for profile in prompts.PROFILES:
            filled = (prompts.brief_template(chip, profile)
                      .replace("{prompt}", request["prompt"])
                      .replace("{bpm}", str(request["bpm"]))
                      .replace("{bars}", str(request["bars"]))
                      .replace("{rows}", str(request["bars"] * 16)))
            assert filled == prompts.brief(chip, profile, request), \
                (chip, profile)


def test_the_contract_carries_every_brief_and_the_family_table():
    import prompts
    import studio

    section = studio.manifest()["prompts"]
    for chip in prompts.BRIEF_CHIPS:
        assert set(section["briefs"][chip]) == set(prompts.PROFILES)
    assert section["families"]["gpt"] == "contract"
    assert section["unknown_family"] == "contract"


def test_the_agent_brief_and_agents_md_forbid_the_same_directories():
    """Two lists of what not to touch is one list that drifts. The agent
    brief first left out tests/, which the checksums cover."""
    import integrity
    import prompts

    agents = prompts.agents_md()
    brief = prompts.brief("RP2A03", "contract", delivery="agent")
    protected = {prefix.rstrip("/") for prefix in integrity.COVERED_PREFIXES}
    protected.add("bridge")
    for directory in protected:
        assert f"`{directory}/`" in agents, directory
        assert f"`{directory}/`" in brief, directory
