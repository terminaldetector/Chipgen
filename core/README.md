# core/ — the chip emulation cores

Two C sources, each built into its own shared library and driven from
Python through `ctypes`:

| source                  | library      | what it is                                            |
|-------------------------|--------------|-------------------------------------------------------|
| `ym3438.c` + `wrapper.c`| `libopn2.so` | Nuked-OPN2, cycle-accurate YM2612 (die-shot reverse engineering) |
| `psg.c`                 | `libpsg.so`  | Sega PSG / SN76489, register-level model written from spec |

The `.so` files are **not** tracked in git — they are platform-specific
build products. Build them with:

```bash
python3 python/build_cores.py
```

which finds `cc`/`gcc`/`clang`, produces the right extension for the host
platform (`.so` / `.dylib` / `.dll`) and verifies each library loads.
Anything importing `opn2` or `sn76489` also triggers this build
automatically on first use, so you rarely need to run it by hand.

If no compiler is available at all, the Python layer falls back to the
pure-Python cores in `python/fallback/` — see the honesty note there.

## Licensing

`ym3438.c` / `ym3438.h` are LGPL 2.1 (Nuked-OPN2, © Alexey Khokholov /
nukeykt) — see `NUKED_OPN2_LICENSE`. They are used as a separately-built
shared library loaded via `ctypes`, i.e. dynamic linking. `psg.c`,
`wrapper.c` and the entire Python layer were written for this project.
