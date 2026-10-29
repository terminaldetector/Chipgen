"""agentic — one loop for composing with feedback, on top of the engine.

    Intent -> Plan -> Compose -> Render -> Hear -> Diagnose -> Revise ->
    Remember -> Continue

The pieces are the engine's own (the tracker, the sequencer, voice
isolation, the phase readings, harmony, roles); this package keeps the
state they share and the order they run in:

    state       MusicalState: the piece as native tracker material plus
                its intent, structure, motifs, roles, observations and
                decisions, in revisions that can be rolled back
    timeline    one clock: seconds <-> rows <-> bars <-> section/order <->
                chip events, and which note of which voice sounds when
    render      a musical range rendered with the chip state chased in,
                stems, and a cache
    trace       audio time -> note -> voice -> instrument -> register writes
    ear         Tool (measured), Native (a model that verifiably takes
                audio) and Hybrid (both, cross-checked)
    diagnose    observations tied to voices, events and parameters, with
                hypotheses
    patch       minimal changes to the native material, with what was
                actually realised
    loop        diagnose -> patch -> A/B -> commit or roll back
    motif       a motif graph: themes, their transformations, where each
                is used
    compose     sliding musical context and continuation
    memory      verified causal decisions, for the next run
    capabilities  what each backend can do here, and how exactly
    fixtures    small pieces with known faults and an empty étude, for
                the tests and the examples

state.py owns the MusicalState schema (FORMAT, MIGRATIONS); every other
module reads it through a Project or a Timeline.

`python3 python/agent.py` is the agent's door to all of it
(bridge/AGENTIC.md).
"""
