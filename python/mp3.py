"""mp3.py — deliver a render as MP3: about a tenth of the WAV, plays anywhere.

## Why MP3, and why its own encoder

A WAV is 176 KB a second — ten megabytes a minute — and handing one back
from a model's sandbox is often the most expensive thing in the whole
exchange. The format that is both much smaller and playable everywhere —
every browser, phone, operating system, chat client and car — is MP3.
Opus and Vorbis compress better and are not playable everywhere; FLAC is
lossless and only halves the size. MP3's patents expired in 2017.

And it has to be built in. A model's sandbox usually has no ffmpeg and
no network to fetch one; the one this was written in had neither. So
when `lame` or `ffmpeg` is on the PATH it is used — LAME is the better
encoder — and when neither is, this file is: an MPEG-1 Layer III
encoder in the standard library. The report says which one ran.

## What kind of encoder

Deliberately the simple kind: constant bitrate, long blocks, no
psychoacoustic model, one quantiser step per granule shared by both
channels, mid/side stereo when the channels are alike, and mono when the
mix is mono — which in this engine is common, because nothing pans
unless the score says so. It spends bits evenly rather than cleverly, so
at a given bitrate it is worse than LAME where LAME is good — attacks,
which LAME gives short blocks — and the measurements are in
BLIND_SPOTS.md rather than guessed.

Speed: the filterbank is most of the work. With numpy it runs as array
operations and a song encodes at about a fourteenth of its length; in
plain Python, about a sixth for a mono mix and a third for stereo. Both
paths compute the same numbers and write the same bytes.

The tables come from mp3_tables.py, generated from a public-domain
decoder by tools/derive_mp3_tables.py — nothing typed in from memory.
"""

import math
import operator
import os
import shutil
import struct
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import mp3_tables as T

#: MPEG-1 Layer III bitrates in kbps, by header index.
BITRATES = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256,
            320)
RATE_INDEX = {44100: 0, 48000: 1, 32000: 2}
#: The default. Chiptune is hard on a simple encoder — square waves put
#: energy right up to the top of the band. At 160 kbps the coding noise
#: on the three chips' example renders sits 29-32 dB under the signal
#: below 16 kHz (BLIND_SPOTS.md has the table, and why SNR is not the
#: whole story). 1.2 MB a minute against the WAV's 10.6.
DEFAULT_BITRATE = 160

GRANULE = 576
FRAME = 2 * GRANULE

_mul = operator.mul
_add = operator.add
_sub = operator.sub

# -- the analysis filterbank -------------------------------------------------
#: The analysis window C[n] = p[n] (-1)^(n // 64) / 32. The sign flip is
#: the standard's: its matrixing cosine changes sign every 64 samples, and
#: the window carries the compensating sign so the effective filter is the
#: smooth prototype modulated by a cosine.
_C = tuple(p * (-1) ** (n // 64) / (32.0 * 65536.0)
           for n, p in enumerate(T.WINDOW_P))
#: The standard's matrixing is S[k] = sum_i cos((2k+1)(i-16) pi/64) y[i]
#: over 64 terms. The cosine is even about i = 16 and odd about i = 48
#: (where it is zero), so y folds to 32 terms a[m] with
#: S[k] = sum_m cos((2k+1) m pi/64) a[m]; and k -> 31-k only flips the
#: sign of the odd m, so S[k] = E[k] + O[k], S[31-k] = E[k] - O[k].
#: A quarter of the multiplies. The tests hold it to the 64-term form.
_EVEN = tuple(tuple(math.cos((2 * k + 1) * m * math.pi / 64)
                    for m in range(0, 32, 2)) for k in range(16))
_ODD = tuple(tuple(math.cos((2 * k + 1) * m * math.pi / 64)
                   for m in range(1, 32, 2)) for k in range(16))

# -- the MDCT, long blocks ---------------------------------------------------
#: The 36-point MDCT X[k] = sum_n w[n] x[n] cos(pi/72 (2n+19)(2k+1)) with
#: the sine window w, folded the usual way: quarters (a, b, c, d) of the
#: windowed block become (-c reversed - d, a - b reversed), and an 18-point
#: DCT-IV of that is the MDCT. Scaled so the standard decoder's unscaled
#: inverse reconstructs at unity — measured against dr_mp3, see the tests.
_MDCT_SCALE = 1.0 / 9.0
_SINE = tuple(math.sin(math.pi / 36 * (n + 0.5)) for n in range(36))
_DCT4 = tuple(tuple(_MDCT_SCALE * math.cos(math.pi / 18 * (n + 0.5) * (k + 0.5))
                    for n in range(18)) for k in range(18))

_CS = T.ANTIALIAS_CS
_CA = T.ANTIALIAS_CA


class _Channel:
    """One channel's filterbank and MDCT state."""

    __slots__ = ("fifo", "previous")

    def __init__(self):
        self.fifo = [0.0] * 512
        self.previous = [(0.0,) * 18] * 32

    def subbands(self, samples):
        """18 x 32 subband samples from 576 PCM samples."""
        out = []
        fifo = self.fifo
        for slot in range(18):
            # fifo[0] is the newest sample: the 32 new ones go in reversed.
            fifo[32:] = fifo[:480]
            fifo[:32] = samples[slot * 32 + 31::-1] if slot == 0 else \
                samples[slot * 32 + 31:slot * 32 - 1:-1]
            z = list(map(_mul, _C, fifo))
            y = list(map(_add,
                         map(_add, map(_add, z[0:64], z[64:128]),
                             map(_add, z[128:192], z[192:256])),
                         map(_add, map(_add, z[256:320], z[320:384]),
                             map(_add, z[384:448], z[448:512]))))
            a = [y[16]]
            a += map(_add, y[17:33], y[15::-1])
            a += map(_sub, y[33:48], y[63:48:-1])
            even, odd = a[0::2], a[1::2]
            e = [sum(map(_mul, row, even)) for row in _EVEN]
            o = [sum(map(_mul, row, odd)) for row in _ODD]
            row = list(map(_add, e, o))
            row += map(_sub, reversed(e), reversed(o))
            out.append(row)
        return out

    def granule(self, samples):
        """576 MDCT lines for one granule."""
        sb = self.subbands(samples)
        # Frequency inversion: odd subbands, odd time slots.
        for slot in range(1, 18, 2):
            row = sb[slot]
            row[1::2] = [-v for v in row[1::2]]
        xr = []
        previous = self.previous
        for band, current in enumerate(zip(*sb)):
            w = list(map(_mul, _SINE, previous[band] + current))
            previous[band] = current
            u = list(map(_sub, map(operator.neg, w[26:17:-1]), w[27:36]))
            u += map(_sub, w[0:9], w[17:8:-1])
            xr += [sum(map(_mul, row, u)) for row in _DCT4]
        # Alias reduction: the decoder's butterfly, inverted.
        for band in range(1, 32):
            base = band * 18
            for i in range(8):
                u = xr[base + i]
                d = xr[base - 1 - i]
                xr[base + i] = _CS[i] * u + _CA[i] * d
                xr[base - 1 - i] = -_CA[i] * u + _CS[i] * d
        return xr


def _numpy():
    try:
        import numpy
    except ImportError:
        return None
    return numpy


#: Granules per array pass in the numpy path: 64 granules of 18 slots of
#: 512 window products is 4.7 MB, whatever the length of the song.
_BLOCK = 64


def _granules(samples, count):
    """Each of `count` granules' 576 MDCT lines, in order, for one channel.

    The same arithmetic two ways: _Channel a granule at a time in plain
    Python, or with numpy, when there is numpy, a block of granules per
    array operation — the filterbank is most of an encode, and this is
    where numpy turns it from most into little. The tests hold the two to
    each other.
    """
    np = _numpy()
    if np is None:
        channel = _Channel()
        for g in range(count):
            yield channel.granule(samples[g * GRANULE:(g + 1) * GRANULE])
        return
    from numpy.lib.stride_tricks import as_strided
    signal = np.concatenate((np.zeros(480),
                             np.asarray(samples, dtype=np.float64)))
    window = np.array(_C)
    matrix = np.cos(np.outer(np.arange(32) * 2 + 1, np.arange(64) - 16)
                    * (math.pi / 64))
    n = np.arange(36)
    mdct = (_MDCT_SCALE * np.sin(math.pi / 36 * (n + 0.5))
            * np.cos(math.pi / 72 * np.outer(np.arange(18) * 2 + 1,
                                              n * 2 + 19)))
    up = [band * 18 + i for band in range(1, 32) for i in range(8)]
    down = [band * 18 - 1 - i for band in range(1, 32) for i in range(8)]
    cs, ca = np.tile(_CS, 31), np.tile(_CA, 31)
    previous = np.zeros((32, 18))
    step = signal.strides[0]
    for first in range(0, count, _BLOCK):
        size = min(_BLOCK, count - first)
        slots = size * 18
        # Row t is the 512 samples slot t's window covers, oldest first;
        # reversed, it is the standard's fifo with the newest at 0.
        segment = signal[first * GRANULE:first * GRANULE + slots * 32 + 480]
        rows = as_strided(segment, shape=(slots, 512),
                          strides=(32 * step, step))
        y = (rows[:, ::-1] * window).reshape(slots, 8, 64).sum(axis=1)
        sb = y @ matrix.T
        sb[1::2, 1::2] *= -1                 # frequency inversion
        sb = sb.reshape(size, 18, 32).transpose(0, 2, 1)
        before = np.concatenate((previous[None], sb[:-1]))
        previous = sb[-1].copy()
        xr = (np.concatenate((before, sb), axis=2) @ mdct.T).reshape(size,
                                                                     576)
        u, d = xr[:, up], xr[:, down]
        xr[:, up] = cs * u + ca * d
        xr[:, down] = cs * d - ca * u
        for row in xr:
            yield row.tolist()


# -- Huffman ------------------------------------------------------------------
def _families():
    """Candidate tables by the largest value a region holds."""
    small = {1: (1,), 2: (2, 3), 3: (5, 6), 4: (7, 8, 9), 5: (7, 8, 9),
             6: (10, 11, 12), 7: (10, 11, 12)}
    for v in range(8, 16):
        small[v] = (13, 15)
    return small


_SMALL = _families()


def _linbits_tables(largest):
    """The cheapest-to-encode linbits tables that can hold `largest`."""
    need = largest - 15
    out = []
    for family in (range(16, 24), range(24, 32)):
        for t in family:
            if need < (1 << T.LINBITS[t]):
                out.append(t)
                break
    return out


def _region(ix, start, stop):
    """-> (table, bits) for the big-value pairs in ix[start:stop], signs
    included: of the tables that can hold the largest value there, the
    one that codes these pairs in the fewest bits.

    Every candidate for a given largest value has the same row size, so
    the pairs are turned into table indexes once and each candidate is a
    sum over the same list.
    """
    if start >= stop:
        return 0, 0
    values = ix[start:stop]
    largest = max(values)
    if not largest:
        return 0, 0
    signs = len(values) - values.count(0)
    xs, ys = values[0::2], values[1::2]
    if largest < 16:
        candidates = _SMALL[largest]
        size = T.HUFFMAN[candidates[0]][0]
        index = [x * size + y for x, y in zip(xs, ys)]
        escapes = 0
    else:
        candidates = _linbits_tables(largest)
        index = [(x if x < 15 else 15) * 16 + (y if y < 15 else 15)
                 for x, y in zip(xs, ys)]
        escapes = len(values) - sum(map((15).__gt__, values))
    best = None
    for table in candidates:
        cost = sum(map(T.HUFFMAN[table][2].__getitem__, index)) \
            + escapes * T.LINBITS[table]
        if best is None or cost < best[1]:
            best = (table, cost)
    return best[0], best[1] + signs


#: region0_count / region1_count by how many scalefactor bands the big
#: values reach. Any split is legal — it only moves where a table changes —
#: and this is the familiar one: small regions low, where values are big.
_SUBDIVIDE = ((0, 0), (0, 0), (0, 0), (0, 0), (0, 0), (0, 1), (1, 1), (1, 1),
              (1, 2), (2, 2), (2, 3), (2, 3), (3, 4), (3, 4), (3, 4), (4, 5),
              (4, 5), (4, 6), (5, 6), (5, 6), (5, 7), (6, 7), (6, 7))


class _Plan:
    """How one granule-channel's quantised lines are coded."""

    __slots__ = ("big_values", "count1_end", "region0", "region1",
                 "tables", "count1_table", "bits", "ix")


def _plan(ix, sfb):
    """Partition, choose tables, count bits. ix: 576 non-negative ints."""
    # The zero tail, then the count1 run of 0s and 1s in fours below it.
    # rstrip does the scanning: a byte per line, non-zero where it counts.
    nonzero = len(bytes(map(bool, ix)).rstrip(b"\0"))
    count1_end = nonzero + (nonzero & 1)
    above_one = len(bytes(map((1).__lt__, ix[:count1_end])).rstrip(b"\0"))
    big_end = count1_end - (count1_end - above_one) // 4 * 4
    plan = _Plan()
    plan.ix = ix
    plan.big_values = big_end // 2
    plan.count1_end = count1_end

    bands = 0
    while bands < 22 and sfb[bands] < big_end:
        bands += 1
    r0, r1 = _SUBDIVIDE[bands]
    while r0 + r1 + 2 > 22:
        r1 -= 1
    plan.region0, plan.region1 = r0, r1
    edges = (0, min(sfb[r0 + 1], big_end), min(sfb[r0 + r1 + 2], big_end),
             big_end)
    tables = []
    bits = 0
    for start, stop in zip(edges, edges[1:]):
        table, cost = _region(ix, start, stop)
        tables.append(table)
        bits += cost
    plan.tables = tables

    quads = ix[big_end:count1_end]
    index = [v * 8 + w * 4 + x * 2 + y for v, w, x, y
             in zip(quads[0::4], quads[1::4], quads[2::4], quads[3::4])]
    a_bits = sum(map(T.COUNT1_A[1].__getitem__, index))
    b_bits = 4 * len(index)
    plan.count1_table = 0 if a_bits <= b_bits else 1
    plan.bits = bits + min(a_bits, b_bits) + sum(quads)   # + a sign per 1
    return plan


# -- the bitstream ------------------------------------------------------------
class _Bits:
    """MSB-first bit writer. One Python integer holds the frame: a frame is
    a few thousand bits, so shifting it is cheaper than a byte loop."""

    __slots__ = ("acc", "n")

    def __init__(self):
        self.acc = 0
        self.n = 0

    def put(self, value, count):
        self.acc = (self.acc << count) | (value & ((1 << count) - 1))
        self.n += count

    def flush(self):
        pad = -self.n % 8
        return (self.acc << pad).to_bytes((self.n + pad) // 8, "big")


def _write_huffman(bits, plan, sfb, negative):
    """The main data for one granule-channel. negative[i]: line i's sign.

    Each pair or quad goes out as one write: its code, then for each
    value the linbits escape if it has one and the sign if it is not 0.
    """
    ix = plan.ix
    put = bits.put
    big_end = plan.big_values * 2
    r0, r1 = plan.region0, plan.region1
    edges = (0, min(sfb[r0 + 1], big_end), min(sfb[r0 + r1 + 2], big_end),
             big_end)
    for (start, stop), table in zip(zip(edges, edges[1:]), plan.tables):
        if table == 0:
            continue
        size, codes, lengths = T.HUFFMAN[table]
        linbits = T.LINBITS[table]
        for i in range(start, stop, 2):
            x, y = ix[i], ix[i + 1]
            if linbits:
                cx = x if x < 15 else 15
                cy = y if y < 15 else 15
                index = cx * 16 + cy
            else:
                index = x * size + y
            value, count = codes[index], lengths[index]
            if x:
                if linbits and cx == 15:
                    value = (value << linbits) | (x - 15)
                    count += linbits
                value = (value << 1) | negative[i]
                count += 1
            if y:
                if linbits and cy == 15:
                    value = (value << linbits) | (y - 15)
                    count += linbits
                value = (value << 1) | negative[i + 1]
                count += 1
            put(value, count)
    codes, lengths = T.COUNT1_A if plan.count1_table == 0 else T.COUNT1_B
    for i in range(big_end, plan.count1_end, 4):
        v, w, x, y = ix[i], ix[i + 1], ix[i + 2], ix[i + 3]
        index = v * 8 + w * 4 + x * 2 + y
        value, count = codes[index], lengths[index]
        for at in range(i, i + 4):
            if ix[at]:
                value = (value << 1) | negative[at]
                count += 1
        put(value, count)


# -- quantisation -------------------------------------------------------------
#: The standard's rounding: nint(x - 0.0946), i.e. int(x + 0.4054).
_ROUND = 0.4054
_MAX_QUANT = 8191
_MAX_PART23 = 4095


def _quantise(xr075, gain):
    q = 2.0 ** (-0.1875 * (gain - 210))
    return [int(v * q + _ROUND) for v in xr075]


def _rate_loop(channels_075, budget, sfb, guess=None):
    """The finest shared step whose total bits fit `budget`. -> (gain, plans)

    One global_gain for every channel of the granule: with no
    psychoacoustic model, an equal noise floor in both channels is the
    right target, and under mid/side it lets the side channel — usually
    near silence in this engine — cost almost nothing on its own.

    The search starts at `guess`, the previous granule's step — music
    rarely moves more than a step or two from one granule to the next —
    and gallops away from it, so a steady passage costs two or three trial
    quantisations instead of the eight a plain bisection of 0..255 does.
    Steps too fine for the loudest line to fit 13 bits are never tried.
    """
    tried = {}

    def fits(gain):
        if gain not in tried:
            plans, total = [], 0
            for xr075 in channels_075:
                ix = _quantise(xr075, gain)
                if max(ix) > _MAX_QUANT:
                    break
                plan = _plan(ix, sfb)
                total += plan.bits
                if plan.bits > _MAX_PART23 or total > budget:
                    break
                plans.append(plan)
            tried[gain] = plans if len(plans) == len(channels_075) else None
        return tried[gain] is not None

    peak = max(max(x) for x in channels_075)
    low = 0
    if peak > 0:
        bound = 210 - math.log2((_MAX_QUANT + 1 - _ROUND) / peak) / 0.1875
        low = min(255, max(0, int(math.floor(bound))))
    gain = min(255, max(low, 210 if guess is None else guess))
    if fits(gain):
        hi, step = gain, 1
        while True:
            lo = hi - step
            if lo < low:
                lo = low - 1
                break
            if not fits(lo):
                break
            hi, step = lo, step * 2
    else:
        lo, step = gain, 1
        while True:
            hi = lo + step
            if hi >= 255:
                hi = 255
                break
            if fits(hi):
                break
            lo, step = hi, step * 2
    while hi - lo > 1:
        middle = (lo + hi) // 2
        if fits(middle):
            hi = middle
        else:
            lo = middle
    if fits(hi):
        return hi, tried[hi]
    # Nothing fits — only for input far beyond full scale. The coarsest
    # step, whatever it costs.
    return 255, [_plan(_quantise(x, 255), sfb) for x in channels_075]


# -- frames -------------------------------------------------------------------
def _side_info(bits, nch, granules):
    bits.put(0, 9)                          # main_data_begin: no reservoir
    bits.put(0, 5 if nch == 1 else 3)       # private bits
    for _ in range(nch):
        bits.put(0, 4)                      # scfsi
    for granule in granules:
        for gain, plan in granule:
            bits.put(plan.bits, 12)         # part2_3_length (no scalefactors)
            bits.put(plan.big_values, 9)
            bits.put(gain, 8)
            bits.put(0, 4)                  # scalefac_compress: none
            bits.put(0, 1)                  # window_switching_flag: long only
            for table in plan.tables:
                bits.put(table, 5)
            bits.put(plan.region0, 4)
            bits.put(plan.region1, 3)
            bits.put(0, 1)                  # preflag
            bits.put(0, 1)                  # scalefac_scale
            bits.put(plan.count1_table, 1)


def _id3(title: str, artist: str) -> bytes:
    """A minimal ID3v2.3 tag: title, artist, encoder."""
    def frame(name, text):
        try:
            payload = b"\x00" + text.encode("latin-1")
        except UnicodeEncodeError:
            payload = b"\x01" + text.encode("utf-16")
        return name.encode() + struct.pack(">I", len(payload)) + b"\x00\x00" \
            + payload
    body = b""
    if title:
        body += frame("TIT2", title)
    if artist:
        body += frame("TPE1", artist)
    body += frame("TSSE", "chipgen")
    size = len(body)
    syncsafe = bytes(((size >> s) & 0x7F) for s in (21, 14, 7, 0))
    return b"ID3\x03\x00\x00" + syncsafe + body


def check(sample_rate: int, bitrate: int = None):
    """Raise ValueError, naming the legal values, unless MPEG-1 Layer III
    can carry this rate and bitrate. Cheap: callers run it before a
    render, so a typo costs nothing rather than the whole render."""
    if sample_rate not in RATE_INDEX:
        raise ValueError(f"MP3 takes 32000, 44100 or 48000 Hz, not "
                         f"{sample_rate}")
    if bitrate is not None and bitrate not in BITRATES[1:]:
        raise ValueError(f"MP3 bitrates are "
                         f"{', '.join(map(str, BITRATES[1:]))} kbps, not "
                         f"{bitrate}")


def encode_pcm(left, right, sample_rate: int = 44100,
               bitrate: int = DEFAULT_BITRATE, title: str = "",
               artist: str = "", mono: bool = None) -> bytes:
    """Encode float PCM (lists, -1..1) to MP3 bytes.

    `right` None, or `mono` True, encodes one channel. With `mono` left at
    None, a stereo pair whose channels are identical is encoded as mono:
    the same file at half the side information, and every bit spent on
    the one signal there is.
    """
    check(sample_rate, bitrate)
    if right is not None and mono is None:
        mono = list(left) == list(right)
    if mono and right is not None:
        left = [(a + b) * 0.5 for a, b in zip(left, right)]
        right = None
    nch = 1 if right is None else 2
    sfb = T.SFB_LONG[sample_rate]
    side_bits = 136 if nch == 1 else 256

    # Pad to whole frames, plus one more so the filterbank and the MDCT
    # overlap flush the last real samples out.
    total = len(left)
    frames = (total + FRAME - 1) // FRAME + 1
    pad = frames * FRAME - total
    pcm = [list(left) + [0.0] * pad]
    if nch == 2:
        pcm.append(list(right) + [0.0] * pad)

    streams = [_granules(samples, frames * 2) for samples in pcm]
    guess = None
    out = bytearray(_id3(title, artist))
    per_frame = 144000 * bitrate
    remainder = 0
    for f in range(frames):
        remainder += per_frame % sample_rate
        padding = 0
        if remainder >= sample_rate:
            remainder -= sample_rate
            padding = 1
        frame_bytes = per_frame // sample_rate + padding
        budget = frame_bytes * 8 - 32 - side_bits

        lines = [[next(stream) for stream in streams]   # [granule][channel]
                 for _ in range(2)]

        ms = False
        if nch == 2:
            mid = side = 0.0
            for left_lines, right_lines in lines:
                for a, b in zip(left_lines, right_lines):
                    mid += (a + b) * (a + b)
                    side += (a - b) * (a - b)
            ms = side < 0.5 * mid
            if ms:
                root = math.sqrt(0.5)
                lines = [[[(a + b) * root for a, b in zip(l, r)],
                          [(a - b) * root for a, b in zip(l, r)]]
                         for l, r in lines]

        granules = []
        for g in range(2):
            share = budget // (2 - g)
            magnitudes = [[abs(v) ** 0.75 for v in xr] for xr in lines[g]]
            gain, plans = _rate_loop(magnitudes, share, sfb, guess)
            guess = gain
            granules.append([(gain, plan) for plan in plans])
            budget -= sum(plan.bits for plan in plans)

        bits = _Bits()
        mode = 3 if nch == 1 else 1          # mono, or joint stereo
        extension = 2 if ms else 0           # mid/side on, intensity off
        bits.put(0xFFFB, 16)                 # sync, MPEG-1, Layer III, no CRC
        bits.put(BITRATES.index(bitrate), 4)
        bits.put(RATE_INDEX[sample_rate], 2)
        bits.put(padding, 1)
        bits.put(0, 1)                       # private
        bits.put(mode, 2)
        bits.put(extension, 2)
        bits.put(0, 1)                       # copyright
        bits.put(1, 1)                       # original
        bits.put(0, 2)                       # emphasis
        _side_info(bits, nch, granules)
        for g, granule in enumerate(granules):
            for c, (_gain, plan) in enumerate(granule):
                _write_huffman(bits, plan, sfb,
                               [v < 0 for v in lines[g][c]])
        frame = bits.flush()
        out += frame + bytes(frame_bytes - len(frame))
    return bytes(out)


def _split(buf):
    """(left, right) float lists from the engine's buffer, numpy or not."""
    import audio
    if audio.is_fallback(buf):
        data, channels = buf.data, buf.channels
        if channels == 1:
            return list(data), None
        return list(data[0::channels]), list(data[1::channels])
    if getattr(buf, "ndim", 1) == 1:
        return buf.tolist(), None
    return buf[:, 0].tolist(), buf[:, 1].tolist()


#: What the report calls the encoder in this file.
BUILTIN = "chipgen"


def _external_commands(wav, out, bitrate, mono):
    """(name, argv) for each better encoder on the PATH, best first. No
    tags: the tag is this file's, the same whichever encoder ran."""
    lame = shutil.which("lame")
    if lame:
        yield "lame", [lame, "--quiet", "--cbr", "-b", str(bitrate),
                       "-m", "m" if mono else "j", wav, out]
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        yield "ffmpeg", [ffmpeg, "-nostdin", "-hide_banner",
                         "-loglevel", "error", "-y", "-i", wav,
                         "-codec:a", "libmp3lame", "-b:a", f"{bitrate}k",
                         "-ac", "1" if mono else "2",
                         "-id3v2_version", "0", out]


def _encode_external(buf, sample_rate, path, bitrate, mono, title, artist,
                     seconds):
    """LAME, alone or inside ffmpeg, when the machine has it. -> its name,
    or None when there is none or none of them worked.

    They are better encoders than this file — a psychoacoustic model,
    short blocks for attacks — so they go first. Each writes to scratch,
    and only a stream that starts like an MP3 is written into place: a
    missing, broken or hung tool costs seconds, never a half-written song.
    """
    import wavio
    with tempfile.TemporaryDirectory(prefix="chipgen-mp3-") as scratch:
        wav = os.path.join(scratch, "in.wav")
        out = os.path.join(scratch, "out.mp3")
        for name, command in _external_commands(wav, out, bitrate, mono):
            if not os.path.exists(wav):
                wavio.write(wav, buf, sample_rate)
            try:
                done = subprocess.run(command, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL,
                                      timeout=60 + 2 * seconds)
                with open(out, "rb") as handle:
                    data = handle.read()
            except (OSError, subprocess.SubprocessError):
                continue
            if done.returncode != 0 or len(data) < 4:
                continue
            if data[:3] != b"ID3":
                if data[0] != 0xFF or data[1] & 0xE0 != 0xE0:
                    continue
                data = _id3(title, artist) + data
            directory = os.path.dirname(os.path.abspath(path))
            os.makedirs(directory, exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(data)
            return name
    return None


def encode(buf, sample_rate: int, path: str, bitrate: int = None,
           title: str = "", artist: str = "", encoder: str = "auto") -> dict:
    """Encode an engine render to an MP3 file. -> what it wrote, measured.

    `encoder` "auto" uses LAME (or ffmpeg's LAME) when the PATH has it and
    this file's encoder when it does not; "builtin" always uses this
    file's. The report says which ran, and carries the size against the
    WAV it replaces, because that ratio is the reason this exists.
    """
    bitrate = bitrate or DEFAULT_BITRATE
    check(sample_rate, bitrate)
    if encoder not in ("auto", "builtin"):
        raise ValueError(f"encoder is 'auto' or 'builtin', not {encoder!r}")
    left, right = _split(buf)
    mono = right is None or left == right
    seconds = len(left) / float(sample_rate)
    used = None
    if encoder == "auto":
        used = _encode_external(buf, sample_rate, path, bitrate, mono, title,
                                artist, seconds)
    if used is None:
        data = encode_pcm(left, right, sample_rate, bitrate, title=title,
                          artist=artist, mono=mono)
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        used = BUILTIN
    size = os.path.getsize(path)
    wav_bytes = 44 + len(left) * 2 * (1 if right is None else 2)
    return {"path": path, "bytes": size, "bitrate": bitrate,
            "mode": "mono" if mono else "joint stereo", "encoder": used,
            "seconds": round(seconds, 2), "wav_bytes": wav_bytes,
            "ratio": round(wav_bytes / float(size), 1) if size else 0.0}
