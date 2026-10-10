"""fixtures.py — small pieces with known faults, for the tests and the
examples. Each is built here, from rows, so what is wrong with it is known
before anything listens: a test can check that the loop finds that and
fixes that, and nothing else.
"""

from . import state as S

_LEAD = ["E-5", "...", "...", "D-5", "...", "C-5", "...", "...",
         "B-4", "...", "C-5", "...", "D-5", "...", "...", "...",
         "C-5", "...", "...", "B-4", "...", "A-4", "...", "...",
         "G-4", "...", "A-4", "...", "B-4", "...", "...", "==="]


def masked_lead(prompt: str = "a lead over bass and pad; the lead must "
                              "read clearly") -> dict:
    """Two bars of FM: a lead (fm1, square_lead, velocity 80) whose
    register a bright bass (fm0, slap_bass, played an octave too high, in
    the lead's octave) and a loud pad (fm2, strings) fill. The fault is
    one of arrangement and level, not of notes: nothing about the melody
    or the harmony needs to change to fix it."""
    st = S.new_state(prompt, bpm=150, lpb=4, key="A minor",
                     columns=["fm0", "fm1", "fm2"],
                     constraints=["keep the melody and the harmony"])
    st["instruments"] = {
        "bass": {"channel": "fm0", "patch": "slap_bass", "role": "bass",
                 "register": [36, 52]},
        "lead": {"channel": "fm1", "patch": "square_lead", "role": "lead",
                 "register": [55, 76]},
        "pad": {"channel": "fm2", "patch": "strings", "role": "pad",
                "register": [48, 72]}}
    roots = ["A-4", "A-4", "F-4", "G-4"]
    rows = []
    for i in range(32):
        beat = i // 8
        bass = roots[beat] if i % 2 == 0 else "==="
        lead = _LEAD[i]
        if lead not in ("...", "==="):
            lead = f"{lead}:80"
        pad = {0: "C-5", 16: "A-4"}.get(i, "...")
        rows.append([bass, lead, pad])
    st["sections"] = [{"id": "A", "name": "phrase", "rows": rows,
                       "directives": [], "harmony": ["Am", "Am", "F", "G"],
                       "intent": {"function": "statement", "energy": 0.6}}]
    st["order"] = ["A"]
    st["structure"]["plan"] = [{"id": "A", "function": "statement",
                                "bars": 2, "energy": 0.6}]
    return st


_LEAD_B = ["A-5", "...", "G-5", "...", "F-5", "...", "...", "E-5",
           "...", "D-5", "...", "...", "E-5", "...", "F-5", "...",
           "E-5", "...", "...", "D-5", "...", "C-5", "...", "...",
           "D-5", "...", "...", "...", "...", "...", "...", "==="]


def masked_lead_b(prompt: str = "a lead over bass and pad in D minor; "
                                "the lead must read clearly") -> dict:
    """The same kind of fault as `masked_lead` in other material: D minor,
    160 BPM, another melody, another bass patch. A test of memory uses it
    as the similar task: what was learned on the first should apply here
    by role, and must be rendered and checked again before it is kept."""
    st = S.new_state(prompt, bpm=160, lpb=4, key="D minor",
                     columns=["fm0", "fm1", "fm2"],
                     constraints=["keep the melody and the harmony"])
    st["instruments"] = {
        "bass": {"channel": "fm0", "patch": "bass", "role": "bass",
                 "register": [33, 57]},
        "lead": {"channel": "fm1", "patch": "square_lead", "role": "lead",
                 "register": [60, 81]},
        "pad": {"channel": "fm2", "patch": "strings", "role": "pad",
                "register": [55, 74]}}
    roots = ["D-5", "D-5", "A#4", "C-5"]
    rows = []
    for i in range(32):
        beat = i // 8
        bass = roots[beat] if i % 2 == 0 else "==="
        lead = _LEAD_B[i]
        if lead not in ("...", "==="):
            lead = f"{lead}:84"
        pad = {0: "F-5", 16: "D-5"}.get(i, "...")
        rows.append([bass, lead, pad])
    st["sections"] = [{"id": "A", "name": "phrase", "rows": rows,
                       "directives": [], "harmony": ["Dm", "Dm", "A#", "C"],
                       "intent": {"function": "statement", "energy": 0.6}}]
    st["order"] = ["A"]
    st["structure"]["plan"] = [{"id": "A", "function": "statement",
                                "bars": 2, "energy": 0.6}]
    return st


def etude(prompt: str = "a short étude in A minor: a theme, its "
                        "variation, a development and a cadence",
          plan=None) -> dict:
    """An empty piece with an ensemble and a plan, for the composer to
    write section by section: bass, lead and pad on FM, hats on the noise
    channel, kick and snare on the DAC. Each section is two bars."""
    st = S.new_state(prompt, bpm=140, lpb=4, key="A minor",
                     columns=["fm0", "fm1", "fm2", "noise", "dac"],
                     constraints=["develop the theme, do not copy it"])
    st["instruments"] = {
        "bass": {"channel": "fm0", "patch": "slap_bass", "role": "bass",
                 "register": [33, 52]},
        "lead": {"channel": "fm1", "patch": "square_lead", "role": "lead",
                 "register": [57, 79]},
        "pad": {"channel": "fm2", "patch": "strings", "role": "pad",
                "register": [52, 67]},
        "hats": {"channel": "noise", "role": "hats"},
        "drums": {"channel": "dac", "role": "drums"}}
    st["structure"]["plan"] = plan or [
        {"id": "A", "function": "statement", "bars": 2, "energy": 0.6,
         "harmony": ["Am", "F", "C", "G"]},
        {"id": "A2", "function": "variation", "bars": 2, "energy": 0.7,
         "harmony": ["Am", "F", "C", "G"]},
        {"id": "B", "function": "development", "bars": 2, "energy": 0.8,
         "harmony": ["Dm", "Em", "F", "G"]},
        {"id": "C", "function": "cadence", "bars": 2, "energy": 0.5,
         "harmony": ["F", "G", "E", "Am"]}]
    st["structure"]["form"] = "A A' B C"
    return st
