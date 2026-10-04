# Examples

Render any of these with:

    python3 python/chipgen.py examples/NAME.trk -o out.wav --vgm out.vgm

**Three of them need a bank passed**, because they name patches imported
from one rather than patches in the built-in bank. Without the flag you
get a `KeyError` listing the names that do exist — informative once you
read it, but the score itself does not say it needs one:

    python3 python/chipgen.py examples/neon_transit.trk -o out.wav \
        --bank examples/neon_transit_bank.json

    python3 python/chipgen.py examples/dos_transit.trk -o out.wav \
        --opl-bank examples/opl_furnace_bank.json

    python3 python/chipgen.py examples/forge_demo.trk -o out.mp3 \
        --forge-bank python/synthesis/banks/starter.bank.json

| score | what it is for | needs |
|---|---|---|
| `everything.trk` | all four chips and every layer at once — effect column, live register writes on both FM chips, CH3 special mode, patterns and an order, samples, and all four NES voices | — |
| `nes.trk` | the NES on its own: two pulses, triangle, noise and DMC, with duty and sweep changes mid-pattern | — |
| `form_patterns.trk` | `pattern` and `order` — writing form as blocks instead of one sheet of rows | — |
| `neon_transit.trk` | a full-length Genesis track, 64 s | `--bank examples/neon_transit_bank.json` |
| `dos_transit.trk` | a full-length OPL2 track, 53 s | `--opl-bank examples/opl_furnace_bank.json` |
| `forge_demo.trk` | instruments made by `forge.py` on all three chips: FM bass, lead and pad (the lead and the pad with a detune layer), an OPL2 pad, NES pulse, triangle and a noise channel that is a kick, a snare and a hat in turn; 8 bars, 17 s | `--forge-bank python/synthesis/banks/starter.bank.json` |

All six ship inside the bridge archive, with the two instrument banks
(and the forge starter bank, under `python/synthesis/banks/`) — the first
five cost 11.8 KB compressed, less than this file describing them.

`programs/make.py` is not a score: it takes a bass and a lead from the
forge's starter bank, finds one targeted change for each with the bounded
loop (`forge.py improve`: "darker sustain" for the bass, "legato" for the
lead), and writes the A/B of each part alone, a short etude before and after
(MP3, VGZ and tracker text), the run records and the readings. About a
minute; see `bridge/SYNTHESIS.md`, "Programs".

`tests/test_examples.py` renders all six on every test run, with the
banks above, and fails if any of them stops parsing or comes out silent.
If you add a score here that needs a bank, add it to `BANKS` in that file
too — otherwise the test renders it without one and the example breaks
for the next reader rather than for you.
