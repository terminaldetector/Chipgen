"""derive_mp3_tables.py — rebuild python/mp3_tables.py from a public-domain
MP3 decoder, so every number the encoder uses has a checkable origin.

    python3 tools/derive_mp3_tables.py path/to/miniaudio.h

`miniaudio.h` (public domain or MIT-0, on PyPI as the `miniaudio` sdist)
carries dr_mp3, itself derived from minimp3 (CC0). An encoder needs the
standard's tables, and writing 2,000 Huffman codes and a 512-point window
from memory is how an encoder ends up producing files that decode to noise
in somebody's car. So nothing is typed in:

* **Huffman codes** — dr_mp3 stores each table as a packed decode tree.
  Walking the tree from the root enumerates every codeword. Each table
  comes out prefix-free and complete (its Kraft sum is exactly 1), table 1
  reproduces the standard's four codes, and the linbits are the standard's.

* **The analysis window** — dr_mp3's synthesis window is folded into its
  fast DCT, so it is not read off the source; it is *measured*. A probe
  compiled against the decoder feeds a unit impulse into each subband of
  the synthesis filterbank. The 32 responses fit
  h_k[n] = p[n] cos((2k+1)(n+16)pi/64) with a relative residual of 2e-14,
  which recovers the prototype p — except at the eight n = 16 mod 64,
  where that cosine is zero in every band and p[n] is invisible to the
  synthesis. Those come from the prototype's symmetry, p[n] = p[512 - n];
  the first encoder built without them reconstructed at 15 dB SNR, with
  them at the filterbank's design limit. dr_mp3 carries p at twice the
  standard's scale; rescaled, every value sits on the standard's 2^-16
  grid. The analysis window is C[n] = p[n] (-1)^(n // 64) / 32.

* **Scalefactor bands and the alias-reduction butterflies** are read from
  the decoder's own tables.

Needs gcc for the probe. Writes python/mp3_tables.py.
"""

import math
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "python", "mp3_tables.py")

PROBE = r"""
#define MA_NO_DEVICE_IO
#define MA_NO_THREADING
#define MA_IMPLEMENTATION
#define MA_DR_MP3_FLOAT_OUTPUT
#include "miniaudio.h"
#include <stdio.h>
#include <string.h>
int main(void) {
    for (int k = 0; k < 32; k++) {
        static float qmf[15*64], lins[18*64 + 15*64], grbuf[576 * 2], pcm[576 * 2];
        memset(qmf, 0, sizeof qmf);
        for (int g = 0; g < 2; g++) {
            memset(grbuf, 0, sizeof grbuf);
            if (g == 0) grbuf[k * 18] = 1.0f;
            memset(lins, 0, sizeof lins);
            ma_dr_mp3d_synth_granule(qmf, grbuf, 18, 1, pcm, lins);
            for (int i = 0; i < 576; i++) printf("%.9g ", pcm[i]);
        }
        printf("\n");
    }
    return 0;
}
"""


def _array(body, name):
    match = re.search(r"static const ma_(?:int16|uint8) " + re.escape(name)
                      + r"\[[^\]]*\](?:\[[^\]]*\])?\s*=\s*\{(.*?)\};", body, re.S)
    return [int(x) for x in re.findall(r"-?\d+", match.group(1))]


def window(header: str) -> list:
    """D[0..511] in units of 2^-16, measured off the synthesis filterbank."""
    with tempfile.TemporaryDirectory() as tmp:
        source = os.path.join(tmp, "probe.c")
        binary = os.path.join(tmp, "probe")
        with open(source, "w") as handle:
            handle.write(PROBE)
        subprocess.check_call(["gcc", "-O2", "-w", "-I", os.path.dirname(
            os.path.abspath(header)), "-o", binary, source, "-lm",
            "-lpthread", "-ldl"])
        rows = subprocess.check_output([binary], text=True).splitlines()
    responses = [[float(v) for v in row.split()] for row in rows]
    measured, residual, total = [], 0.0, 0.0
    for n in range(512):
        column = [responses[k][n] for k in range(32)]
        basis = [math.cos((2 * k + 1) * (n + 16) * math.pi / 64)
                 for k in range(32)]
        value = sum(c * b for c, b in zip(column, basis)) / \
            sum(b * b for b in basis)
        residual += sum((c - value * b) ** 2 for c, b in zip(column, basis))
        total += sum(c * c for c in column)
        measured.append(value)
    assert residual / total < 1e-10, f"synthesis model does not fit: {residual / total}"
    # Eight values are invisible to this measurement. Where n = 16 mod 64,
    # cos((2k+1)(n+16)pi/64) is zero for EVERY k, so no synthesis impulse
    # response carries p[n] — the fit returns 0 there, and the synthesis
    # never needs them. The analysis does: its cosine at those n is +-1.
    # The prototype of a PQMF is symmetric, p[n] = p[512 - n], and every
    # mirror of an invisible n is visible (512 - n = 48 mod 64), so the
    # gaps are filled from their mirrors and the result is checked whole.
    for n in range(16, 512, 64):
        measured[n] = measured[512 - n]
    worst_symmetry = max(abs(measured[n] - measured[512 - n])
                         for n in range(1, 512))
    assert worst_symmetry < 1e-6, f"prototype not symmetric: {worst_symmetry}"
    scale = 0.5        # dr_mp3 carries the window at twice the standard's
    units = [d * scale * 65536 for d in measured]
    worst = max(abs(u - round(u)) for u in units)
    assert worst < 0.05, f"window is off the 2^-16 grid by {worst}"
    return [int(round(u)) for u in units]


def huffman(body: str):
    tabs, tabindex = _array(body, "tabs"), _array(body, "tabindex")
    linbits = _array(body, "g_linbits")
    tab32, tab33 = _array(body, "tab32"), _array(body, "tab33")

    def walk(table):
        base, codes = tabindex[table], {}

        def level(offset, width, prefix, plen):
            for v in range(1 << width):
                leaf = tabs[base + offset + v]
                if leaf >= 0:
                    n = leaf >> 8
                    entry = ((prefix << n) | (v >> (width - n)), plen + n)
                    key = (leaf & 15, (leaf >> 4) & 15)
                    assert codes.get(key, entry) == entry
                    codes[key] = entry
                else:
                    level(-(leaf >> 3), leaf & 7, (prefix << width) | v,
                          plen + width)
        level(0, 5, 0, 0)
        return codes

    def count1(cb):
        codes = {}
        for v in range(16):
            leaf = cb[v]
            hops = [(leaf, v, 4)] if leaf & 8 else [
                (cb[(leaf >> 3) + e], (v << (leaf & 3)) | e, 4 + (leaf & 3))
                for e in range(1 << (leaf & 3))]
            for final, bits, width in hops:
                n = final & 7
                key = sum(((final >> (7 - s)) & 1) << (3 - s) for s in range(4))
                entry = (bits >> (width - n), n)
                assert codes.get(key, entry) == entry
                codes[key] = entry
        return [codes[i] for i in range(16)]

    tables = {}
    for t in range(32):
        if t in (0, 4, 14):
            continue
        codes = walk(t)
        size = max(max(k) for k in codes) + 1
        assert len(codes) == size * size
        assert abs(sum(2.0 ** -l for _, l in codes.values()) - 1) < 1e-12
        tables[t] = (size, [codes[(x, y)] for x in range(size)
                            for y in range(size)])
    assert tables[1][1] == [(1, 1), (1, 3), (1, 2), (0, 3)], "table 1 anchor"
    return tables, linbits, count1(tab32), count1(tab33)


def main(argv):
    header = argv[0] if argv else "miniaudio.h"
    text = open(header, encoding="utf-8", errors="replace").read()
    body = text[text.index("static void ma_dr_mp3_L3_huffman("):][:60000]
    tables, linbits, count1_a, count1_b = huffman(body)
    sfb_body = text[text.index("static const ma_uint8 g_scf_long"):][:2000]
    widths = _array(sfb_body, "g_scf_long")
    rows = [widths[i * 23:(i + 1) * 23] for i in range(8)]
    sfb = {}
    for rate, row in ((44100, rows[5]), (48000, rows[6]), (32000, rows[7])):
        edges = [0]
        for width in row[:22]:
            edges.append(edges[-1] + width)
        assert edges[-1] == 576
        sfb[rate] = edges
    aa_body = text[text.index("static void ma_dr_mp3_L3_antialias("):][:600]
    aa = [float(x) for x in re.findall(r"(\d\.\d+)f", aa_body)[:16]]
    measured_window = window(header)

    shared = {}       # tables 16-23 and 24-31 share one codebook each
    lines = ['"""mp3_tables.py — the MPEG-1 Layer III tables the encoder uses.',
             "",
             "GENERATED by tools/derive_mp3_tables.py from dr_mp3 (public domain /",
             "MIT-0, derived from minimp3, CC0). Do not edit by hand: every value",
             "here has a measured or walked origin, documented in that script.",
             '"""', "",
             "#: The filterbank prototype p[n], in units of 2^-16: symmetric,",
             "#: p[n] = p[512 - n]. The standard prints it with the sign of every",
             "#: other 64-sample block flipped, because its matrixing cosine",
             "#: flips sign every 64 samples: C[n] = p[n] * (-1)^(n // 64) / 32",
             "#: is the analysis window, and D[n] = 32 C[n] the synthesis one.",
             f"WINDOW_P = {tuple(measured_window)}", "",
             "#: Long-block scalefactor band edges, per sample rate.",
             f"SFB_LONG = {sfb!r}", "",
             "#: Alias-reduction butterflies: cs, ca (ca as the decoder uses it).",
             f"ANTIALIAS_CS = {tuple(aa[:8])}",
             f"ANTIALIAS_CA = {tuple(aa[8:16])}", "",
             "#: Big-value Huffman tables: number -> (size, codes, lengths),",
             "#: entry x * size + y. Tables 16-23 and 24-31 share a codebook.",
             f"LINBITS = {tuple(linbits)}",
             "HUFFMAN = {}"]
    for t, (size, entries) in sorted(tables.items()):
        codes = tuple(c for c, _ in entries)
        lengths = tuple(l for _, l in entries)
        key = (size, codes, lengths)
        if key in shared:
            lines.append(f"HUFFMAN[{t}] = HUFFMAN[{shared[key]}]")
        else:
            shared[key] = t
            lines.append(f"HUFFMAN[{t}] = ({size}, {codes}, {lengths})")
    lines += ["", "#: count1 tables A and B, entry v*8 + w*4 + x*2 + y.",
              f"COUNT1_A = ({tuple(c for c, _ in count1_a)}, "
              f"{tuple(l for _, l in count1_a)})",
              f"COUNT1_B = ({tuple(c for c, _ in count1_b)}, "
              f"{tuple(l for _, l in count1_b)})", ""]
    with open(OUT, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    print(f"wrote {os.path.relpath(OUT)}: {len(tables)} Huffman tables, "
          f"window of 512, {len(sfb)} sample rates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
