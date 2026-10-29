"""MP3 delivery: the tables, the transforms, the bitstream, and a round trip.

The encoder was checked against two decoders that share nothing with it
(dr_mp3, and Chromium's). Neither exists in a test sandbox, so the
round trip here goes through a reference decoder written from the
standard's formulas — not from the encoder's code — at the bottom of
this file: header, side information, Huffman, dequantisation, mid/side,
alias reduction, IMDCT, frequency inversion and the synthesis
filterbank. It reads only what this encoder writes (long blocks, no
scalefactors, no bit reservoir) and asserts that it is exactly that.
"""

import json
import math
import operator
import os
import random
import subprocess
import sys

import support

PY = os.path.join(support.ROOT, "python")
SR = 44100


def _tone(seconds, parts, rate=SR):
    """sum of (frequency, amplitude) sines."""
    return [sum(a * math.sin(2 * math.pi * f * i / rate) for f, a in parts)
            for i in range(int(seconds * rate))]


def _snr(reference, decoded, delay, start=2000, end=None):
    end = end or len(reference) - 2000
    signal = noise = 0.0
    for i in range(start, end):
        s = reference[i]
        e = decoded[i + delay] - s
        signal += s * s
        noise += e * e
    return 10 * math.log10(signal / max(noise, 1e-30))


def _gain(reference, decoded, delay, start=2000, end=None):
    end = end or len(reference) - 2000
    a = sum(v * v for v in reference[start:end])
    b = sum(v * v for v in decoded[start + delay:end + delay])
    return 10 * math.log10(b / a)


# --------------------------------------------------------------------------
# The tables
# --------------------------------------------------------------------------
def _prefix_free_and_complete(codes, lengths):
    words = sorted(format(c, f"0{n}b") for c, n in zip(codes, lengths)
                   if n)
    for a, b in zip(words, words[1:]):
        assert not b.startswith(a), (a, b)
    longest = max(lengths)
    return sum(1 << (longest - n) for n in lengths if n) == 1 << longest


def test_every_huffman_table_is_prefix_free_and_complete():
    """A Kraft sum of exactly 1: no codeword wasted, none ambiguous."""
    import mp3_tables as T

    for table, (size, codes, lengths) in T.HUFFMAN.items():
        assert len(codes) == len(lengths) == size * size, table
        assert _prefix_free_and_complete(codes, lengths), table
    assert _prefix_free_and_complete(*T.COUNT1_A)
    assert T.COUNT1_B[1] == (4,) * 16


def test_the_tables_match_the_standard_where_it_is_easy_to_check():
    import mp3_tables as T

    # Table 1, as ISO 11172-3 prints it: 00 -> 1, 01 -> 001, 10 -> 01,
    # 11 -> 000.
    size, codes, lengths = T.HUFFMAN[1]
    assert [(codes[i], lengths[i]) for i in range(4)] == \
        [(1, 1), (1, 3), (1, 2), (0, 3)]
    assert T.LINBITS[16:24] == (1, 2, 3, 4, 6, 8, 10, 13)
    assert T.LINBITS[24:32] == (4, 5, 6, 7, 8, 9, 11, 13)
    assert 4 not in T.HUFFMAN and 14 not in T.HUFFMAN
    for rate in (32000, 44100, 48000):
        edges = T.SFB_LONG[rate]
        assert len(edges) == 23 and edges[0] == 0 and edges[-1] == 576
        assert all(a < b for a, b in zip(edges, edges[1:]))
    assert T.SFB_LONG[44100][:8] == [0, 4, 8, 12, 16, 20, 24, 30]


def test_the_window_is_the_standards_prototype():
    """Symmetric, on the 2^-16 grid, and C[256] = 1.144989014 / 32.

    The eight values at n = 16 mod 64 cannot be measured from a decoder
    — the synthesis cosine is zero there in every band — and an encoder
    built without them reconstructs at 15 dB. Symmetry is what fills
    them, and what this holds."""
    import mp3_tables as T

    p = T.WINDOW_P
    assert len(p) == 512 and p[0] == 0
    assert all(isinstance(v, int) for v in p)
    assert all(p[n] == p[512 - n] for n in range(1, 512))
    assert p[256] == 75038                      # 1.144989014 * 65536
    assert all(p[n] for n in range(16, 512, 64))


# --------------------------------------------------------------------------
# The transforms
# --------------------------------------------------------------------------
def test_the_analysis_filterbank_reconstructs_through_the_standard_synthesis():
    """Analysis here, synthesis from the standard's formulas: the
    filterbank's own design limit, ~84 dB, after its 481-sample delay."""
    import mp3

    random.seed(5)
    x = [random.uniform(-0.5, 0.5) for _ in range(576 * 8)]
    channel = mp3._Channel()
    synth = _Synthesis()
    out = []
    for g in range(8):
        for row in channel.subbands(x[g * 576:(g + 1) * 576]):
            out += synth.slot(row)
    assert _snr(x, out, 481, start=600, end=len(x) - 600) > 80


def test_the_folded_transforms_equal_their_definitions():
    """The encoder folds the matrixing to a quarter of its multiplies and
    the MDCT to half. Held here to the unfolded formulas."""
    import mp3

    random.seed(6)
    x = [random.uniform(-1, 1) for _ in range(576 * 3)]
    channel = mp3._Channel()
    fifo = [0.0] * 512
    previous = [[0.0] * 18 for _ in range(32)]
    window = mp3._C
    for g in range(3):
        block = x[g * 576:(g + 1) * 576]
        sb = []
        for slot in range(18):
            fifo = block[slot * 32:slot * 32 + 32][::-1] + fifo[:480]
            y = [sum(window[i + 64 * j] * fifo[i + 64 * j] for j in range(8))
                 for i in range(64)]
            sb.append([sum(math.cos((2 * k + 1) * (i - 16) * math.pi / 64)
                           * y[i] for i in range(64)) for k in range(32)])
        for slot in range(1, 18, 2):
            for band in range(1, 32, 2):
                sb[slot][band] = -sb[slot][band]
        expected = []
        for band in range(32):
            z = previous[band] + [sb[s][band] for s in range(18)]
            previous[band] = z[18:]
            expected += [sum(z[n] * math.sin(math.pi / 36 * (n + 0.5))
                             * math.cos(math.pi / 72 * (2 * n + 19)
                                        * (2 * k + 1))
                             for n in range(36)) / 9 for k in range(18)]
        for band in range(1, 32):
            for i in range(8):
                u, d = expected[band * 18 + i], expected[band * 18 - 1 - i]
                expected[band * 18 + i] = mp3._CS[i] * u + mp3._CA[i] * d
                expected[band * 18 - 1 - i] = -mp3._CA[i] * u + mp3._CS[i] * d
        got = channel.granule(block)
        assert max(abs(a - b) for a, b in zip(got, expected)) < 1e-12


def test_the_numpy_path_computes_the_same_lines():
    import mp3

    np = mp3._numpy()
    if np is None:
        support.skip("numpy is not installed")
    random.seed(7)
    x = [random.uniform(-1, 1) for _ in range(576 * 70)]   # > one block
    plain = mp3._Channel()
    for g, lines in enumerate(mp3._granules(x, 70)):
        expected = plain.granule(x[g * 576:(g + 1) * 576])
        assert max(abs(a - b) for a, b in zip(lines, expected)) < 1e-12, g


# --------------------------------------------------------------------------
# The bitstream, decoded
# --------------------------------------------------------------------------
def test_a_tone_comes_back_at_the_same_level_and_in_tune():
    import mp3

    x = _tone(0.6, [(440, 0.4), (1000, 0.2)])
    data = mp3.encode_pcm(x, None, SR, 160)
    frames, pcm = _decode(data)
    assert {f["mode"] for f in frames} == {3}           # mono
    assert {f["bitrate"] for f in frames} == {160}
    left = pcm[0]
    assert len(left) >= len(x) + 1057
    assert _best_delay(x, left) == 1057
    assert _snr(x, left, 1057) > 40
    assert abs(_gain(x, left, 1057)) < 0.05


def test_alike_channels_go_mid_side_and_unlike_ones_do_not():
    import mp3

    a = _tone(0.4, [(440, 0.4)])
    b = _tone(0.4, [(660, 0.4)])
    near = [0.9 * u + 0.1 * v for u, v in zip(a, b)]
    for right, ms in ((near, True), (b, False)):
        frames, (left_out, right_out) = _decode(mp3.encode_pcm(a, right, SR,
                                                               192))
        assert {f["mode"] for f in frames} == {1}       # joint stereo
        assert {bool(f["mode_ext"] & 2) for f in frames[1:-1]} == {ms}
        assert _snr(a, left_out, 1057) > 25
        assert _snr(right, right_out, 1057) > 25


def test_identical_channels_are_written_as_mono():
    import mp3

    x = _tone(0.2, [(330, 0.5)])
    frames, pcm = _decode(mp3.encode_pcm(x, list(x), SR, 128))
    assert {f["mode"] for f in frames} == {3} and len(pcm) == 1


def test_frames_are_exactly_as_long_as_their_headers_say():
    """CBR with the padding bit spreading 44.1 kHz's fractional frame
    length: every frame where the header puts it, at every bitrate."""
    import mp3

    x = _tone(0.3, [(220, 0.5), (3000, 0.1)])
    for kbps in (32, 96, 128, 160, 320):
        data = mp3.encode_pcm(x, None, SR, kbps)
        frames, _ = _decode(data)
        total = sum(f["length"] for f in frames)
        assert total == len(data) - frames[0]["offset"]
        expected = 144000 * kbps * len(frames) / SR
        assert abs(total - expected) < 1, kbps


def test_a_square_wave_at_full_scale_fits_the_smallest_bitrate():
    """Chiptune is the hard case — energy to the top of the band — and
    32 kbps is the tightest budget: nothing may overflow 13 bits, 4095
    bits a granule, or the frame."""
    import mp3

    x = [0.99 if (i // 50) % 2 else -0.99 for i in range(SR // 2)]
    frames, pcm = _decode(mp3.encode_pcm(x, None, SR, 32))
    assert len(frames) > 10
    assert _gain(x, pcm[0], 1057) > -6


def test_silence_is_valid_and_silent():
    import mp3

    frames, pcm = _decode(mp3.encode_pcm([0.0] * 5000, None, SR, 128))
    assert all(f["big_values"] == [0, 0] for f in frames)
    assert max(abs(v) for v in pcm[0]) == 0.0


def test_the_id3_tag_carries_title_and_artist_in_any_script():
    import mp3

    data = mp3.encode_pcm([0.0] * 2000, None, SR, 128, title="Тема",
                          artist="chiptune fan")
    assert data[:5] == b"ID3\x03\x00"
    size = 0
    for byte in data[6:10]:
        assert byte < 0x80
        size = size << 7 | byte
    tag = data[10:10 + size]
    assert data[10 + size] == 0xFF                      # first frame's sync
    assert b"TIT2" in tag and "Тема".encode("utf-16") in tag
    assert b"TPE1" in tag and b"\x00chiptune fan" in tag
    assert b"TSSE" in tag and b"chipgen" in tag


def test_bad_parameters_are_refused_with_the_legal_ones():
    import mp3

    for kwargs, words in (({"sample_rate": 22050}, "44100"),
                          ({"bitrate": 150}, "160")):
        try:
            mp3.encode_pcm([0.0] * 100, None, **kwargs)
        except ValueError as exc:
            assert words in str(exc)
        else:
            raise AssertionError(kwargs)
    with support.TempDir() as d:
        try:
            mp3.encode([0.0] * 100, SR, os.path.join(d, "x.mp3"),
                       encoder="sox")
        except ValueError as exc:
            assert "builtin" in str(exc)
        else:
            raise AssertionError("encoder='sox' accepted")


# --------------------------------------------------------------------------
# Delivery
# --------------------------------------------------------------------------
def _score(chip="YM2612", rows=32):
    import prompts
    lines = (prompts.EXAMPLES[chip] * (rows // len(prompts.EXAMPLES[chip])
                                        + 1))[:rows]
    return "\n".join(prompts.template(chip, {"bpm": 140}) + lines) + "\n"


def test_a_render_hands_back_a_tenth_of_the_wav():
    import chipgen

    with support.TempDir() as d:
        path = os.path.join(d, "song.mp3")
        result = chipgen.compose(_score(), mp3=path, normalize=0.89)
        report = result.mp3_report
        assert result.mp3_path == path and os.path.getsize(path) == \
            report["bytes"]
        assert report["ratio"] >= 8, report
        assert report["mode"] == "mono" and report["bitrate"] == 160
        assert "smaller than WAV" in result.summary()
        if report["encoder"] == "chipgen":
            frames, pcm = _decode(open(path, "rb").read())
            assert len(pcm[0]) >= len(result.audio)


def test_the_cli_writes_an_mp3_when_asked_for_one():
    with support.TempDir() as d:
        score = os.path.join(d, "song.trk")
        with open(score, "w") as handle:
            handle.write(_score(rows=16))
        out = subprocess.run(
            [sys.executable, os.path.join(PY, "chipgen.py"), score,
             "-o", os.path.join(d, "song.mp3"), "--bitrate", "128"],
            capture_output=True, text=True, timeout=300)
        assert out.returncode == 0, out.stderr
        assert "wrote " + os.path.join(d, "song.mp3") in out.stdout
        assert "smaller than WAV" in out.stdout
        assert sorted(os.listdir(d)) == ["song.mp3", "song.trk"]   # no WAV
        with open(os.path.join(d, "song.mp3"), "rb") as handle:
            assert handle.read(3) == b"ID3"


def test_a_rate_or_bitrate_mp3_cannot_carry_fails_before_the_render():
    """A usage error naming the legal values, at once — not a whole render
    and then a traceback."""
    for extra, words in ((["--rate", "22050"], "44100"),
                         (["--bitrate", "150"], "160"),
                         (["-o", "x.wav", "--bitrate", "128"], "-o song.mp3")):
        with support.TempDir() as d:
            score = os.path.join(d, "song.trk")
            with open(score, "w") as handle:
                handle.write(_score(rows=16))
            if "-o" not in extra:
                extra = ["-o", os.path.join(d, "x.mp3")] + extra
            out = subprocess.run(
                [sys.executable, os.path.join(PY, "chipgen.py"), score]
                + extra, capture_output=True, text=True, timeout=120,
                cwd=d)
            assert out.returncode == 2 and words in out.stderr, out.stderr
            assert "Traceback" not in out.stderr
            assert sorted(os.listdir(d)) == ["song.trk"]


def test_the_local_server_offers_an_mp3_when_asked():
    import serve

    plain = serve.render_score({"score": _score(rows=16)})
    assert "mp3" not in plain                   # WAV only, unless asked
    answer = serve.render_score({"score": _score(rows=16), "mp3": 128})
    token = answer["mp3"].rsplit("/", 1)[1]
    path = serve.RENDERS.resolve(token)
    assert token.endswith(".mp3") and serve._MIME[".mp3"] == "audio/mpeg"
    assert os.path.getsize(path) == answer["mp3_report"]["bytes"]
    assert answer["mp3_report"]["bitrate"] == 128
    assert answer["mp3_report"]["ratio"] >= 8


def _fake_tool(directory, name, body):
    path = os.path.join(directory, name)
    with open(path, "w") as handle:
        handle.write(f"#!{sys.executable}\nimport json, os, sys\n{body}\n")
    os.chmod(path, 0o755)


def test_lame_is_used_when_the_machine_has_it_and_skipped_when_it_fails():
    """A better encoder on the PATH goes first; one that fails, or writes
    something that is not an MP3, costs nothing but the attempt."""
    import audio
    import mp3

    if os.name == "nt":
        support.skip("the fake encoder is a POSIX script")
    buf = audio.zeros(4410, 2)
    saved = os.environ.get("PATH", "")
    with support.TempDir() as d:
        bin_dir = os.path.join(d, "bin")
        os.mkdir(bin_dir)
        os.environ["PATH"] = bin_dir         # nothing real: no ffmpeg either
        try:
            _fake_tool(bin_dir, "lame",
                       "json.dump(sys.argv[1:], open(os.path.join("
                       "os.path.dirname(sys.argv[0]), 'args'), 'w'))\n"
                       "assert open(sys.argv[-2], 'rb').read(4) == b'RIFF'\n"
                       "open(sys.argv[-1], 'wb').write(b'\\xff\\xfb\\x90\\x00'"
                       " + bytes(400))")
            out = os.path.join(d, "a.mp3")
            report = mp3.encode(buf, SR, out, bitrate=128, title="t")
            assert report["encoder"] == "lame"
            with open(os.path.join(bin_dir, "args")) as handle:
                args = json.load(handle)
            assert args[:6] == ["--quiet", "--cbr", "-b", "128", "-m", "m"]
            with open(out, "rb") as handle:
                data = handle.read()
            assert data[:3] == b"ID3" and data.endswith(bytes(400))

            _fake_tool(bin_dir, "lame", "sys.exit(1)")
            report = mp3.encode(buf, SR, out, bitrate=128)
            assert report["encoder"] == "chipgen"
            _decode(open(out, "rb").read())

            _fake_tool(bin_dir, "lame",
                       "open(sys.argv[-1], 'w').write('not an mp3')")
            assert mp3.encode(buf, SR, out)["encoder"] == "chipgen"
            assert mp3.encode(buf, SR, out, encoder="builtin")["encoder"] \
                == "chipgen"
        finally:
            os.environ["PATH"] = saved


# --------------------------------------------------------------------------
# A reference decoder, from the standard's formulas
# --------------------------------------------------------------------------
class _Reader:
    def __init__(self, data):
        self.value = int.from_bytes(data, "big")
        self.size = len(data) * 8
        self.pos = 0

    def read(self, count):
        if count == 0:
            return 0
        self.pos += count
        assert self.pos <= self.size, "read past the frame"
        return (self.value >> (self.size - self.pos)) & ((1 << count) - 1)


def _lookup(codes, lengths):
    return {(n, c): i for i, (c, n) in enumerate(zip(codes, lengths)) if n}


def _symbol(reader, table):
    code = length = 0
    while length < 20:
        code = code << 1 | reader.read(1)
        length += 1
        hit = table.get((length, code))
        if hit is not None:
            return hit
    raise AssertionError("no Huffman code matches")


class _Synthesis:
    """The standard's synthesis filterbank, one 32-sample slot at a time."""

    def __init__(self):
        import mp3_tables as T
        self.v = [0.0] * 1024
        self.d = [p * (-1) ** (n // 64) / 65536.0
                  for n, p in enumerate(T.WINDOW_P)]
        self.n = [[math.cos((16 + i) * (2 * k + 1) * math.pi / 64)
                   for k in range(32)] for i in range(64)]

    def slot(self, s):
        v = self.v
        v[64:] = v[:960]
        v[:64] = [sum(map(operator.mul, row, s)) for row in self.n]
        u = []
        for i in range(8):
            u += v[128 * i:128 * i + 32] + v[128 * i + 96:128 * i + 128]
        w = list(map(operator.mul, u, self.d))
        return [sum(w[j + 32 * i] for i in range(16)) for j in range(32)]


def _decode(data):
    """-> (frames, [pcm per channel]). Asserts the stream is what this
    encoder promises: MPEG-1 Layer III, no CRC, long blocks, no
    scalefactors, no reservoir, and every granule's bits used exactly."""
    import mp3_tables as T

    pos = 0
    if data[:3] == b"ID3":
        size = 0
        for byte in data[6:10]:
            size = size << 7 | byte
        pos = 10 + size
    big = {t: (size, _lookup(codes, lengths))
           for t, (size, codes, lengths) in T.HUFFMAN.items()}
    quad = (_lookup(*T.COUNT1_A), _lookup(*T.COUNT1_B))
    rates = {0: 44100, 1: 48000, 2: 32000}
    frames = []
    while pos < len(data):
        h = int.from_bytes(data[pos:pos + 4], "big")
        assert h >> 16 == 0xFFFB, hex(h)                # MPEG-1 L3, no CRC
        bitrate = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224,
                   256, 320)[h >> 12 & 15]
        rate = rates[h >> 10 & 3]
        length = 144000 * bitrate // rate + (h >> 9 & 1)
        frames.append({"offset": pos, "length": length, "bitrate": bitrate,
                       "rate": rate, "mode": h >> 6 & 3,
                       "mode_ext": h >> 4 & 3,
                       "data": data[pos + 4:pos + length]})
        pos += length
    assert pos == len(data), "the last frame is cut short"

    nch = 1 if frames[0]["mode"] == 3 else 2
    sfb = T.SFB_LONG[frames[0]["rate"]]
    overlap = [[[0.0] * 18 for _ in range(32)] for _ in range(nch)]
    synth = [_Synthesis() for _ in range(nch)]
    pcm = [[] for _ in range(nch)]
    for frame in frames:
        r = _Reader(frame["data"])
        assert r.read(9) == 0                           # main_data_begin
        r.read(5 if nch == 1 else 3)
        r.read(4 * nch)
        side = []
        for _ in range(2):
            for _ in range(nch):
                g = {"part23": r.read(12), "big": r.read(9),
                     "gain": r.read(8), "sfc": r.read(4),
                     "switch": r.read(1)}
                g["tables"] = [r.read(5) for _ in range(3)]
                g["r0"], g["r1"] = r.read(4), r.read(3)
                g["pre"], g["scale"], g["c1"] = r.read(1), r.read(1), \
                    r.read(1)
                assert g["sfc"] == 0 and g["switch"] == 0 and g["pre"] == 0
                assert g["big"] <= 288
                assert not set(g["tables"]) & {4, 14}
                side.append(g)
        frame["big_values"] = [g["big"] for g in side]
        for gr in range(2):
            lines = []
            for ch in range(nch):
                g = side[gr * nch + ch]
                start = r.pos
                ix = [0] * 576
                big_end = g["big"] * 2
                edges = (min(sfb[g["r0"] + 1], big_end),
                         min(sfb[g["r0"] + g["r1"] + 2], big_end))
                for i in range(0, big_end, 2):
                    t = g["tables"][0 if i < edges[0] else
                                    1 if i < edges[1] else 2]
                    if t == 0:
                        continue
                    size, table = big[t]
                    index = _symbol(r, table)
                    x, y = divmod(index, size)
                    out = []
                    for v in (x, y):
                        if T.LINBITS[t] and v == 15:
                            v += r.read(T.LINBITS[t])
                        if v and r.read(1):
                            v = -v
                        out.append(v)
                    ix[i], ix[i + 1] = out
                i = big_end
                while r.pos - start < g["part23"]:
                    index = _symbol(r, quad[g["c1"]])
                    for j, bit in enumerate((8, 4, 2, 1)):
                        v = 1 if index & bit else 0
                        if v and r.read(1):
                            v = -1
                        ix[i + j] = v
                    i += 4
                assert r.pos - start == g["part23"], "granule bits differ"
                step = 2.0 ** ((g["gain"] - 210) / 4.0)
                lines.append([math.copysign(abs(v) ** (4 / 3) * step, v)
                              for v in ix])
            if nch == 2 and frame["mode"] == 1 and frame["mode_ext"] & 2:
                m, s = lines
                root = math.sqrt(0.5)
                lines = [[(a + b) * root for a, b in zip(m, s)],
                         [(a - b) * root for a, b in zip(m, s)]]
            for ch in range(nch):
                pcm[ch] += _granule_out(lines[ch], overlap[ch], synth[ch])
    return frames, pcm


def _granule_out(xr, overlap, synth):
    import mp3_tables as T

    xr = list(xr)
    for band in range(1, 32):                           # alias reduction
        for i in range(8):
            u, d = xr[band * 18 + i], xr[band * 18 - 1 - i]
            xr[band * 18 + i] = u * T.ANTIALIAS_CS[i] - d * T.ANTIALIAS_CA[i]
            xr[band * 18 - 1 - i] = u * T.ANTIALIAS_CA[i] \
                + d * T.ANTIALIAS_CS[i]
    bands = []
    for band in range(32):                              # IMDCT, overlap-add
        x = xr[band * 18:band * 18 + 18]
        z = [sum(map(operator.mul, x, row)) * _SINE[i]
             for i, row in enumerate(_IMDCT)]
        out = [a + b for a, b in zip(z[:18], overlap[band])]
        overlap[band] = z[18:]
        if band % 2:                                    # frequency inversion
            out = [-v if s % 2 else v for s, v in enumerate(out)]
        bands.append(out)
    pcm = []
    for s in range(18):
        pcm += synth.slot([bands[k][s] for k in range(32)])
    return pcm


_IMDCT = [[math.cos(math.pi / 72 * (2 * i + 19) * (2 * k + 1))
           for k in range(18)] for i in range(36)]
_SINE = [math.sin(math.pi / 36 * (i + 0.5)) for i in range(36)]


def _best_delay(reference, decoded, around=1057, span=40):
    def score(delay):
        return sum(reference[i] * decoded[i + delay]
                   for i in range(2000, 6000))
    return max(range(around - span, around + span + 1), key=score)
