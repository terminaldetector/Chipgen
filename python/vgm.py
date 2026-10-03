"""
vgm.py — export a chipgen performance as a .vgm file.

A WAV is a recording of the chip. A VGM is the chip's *sheet music*: the
literal sequence of register writes and waits that produced the sound.
That difference matters more than it sounds:

  * a .vgm plays in foobar2000, Winamp, VGMPlay, in-browser players, and
    on real hardware through a MegaDrive flashcart;
  * DefleMask and Furnace import VGM, so a track a model composed here can
    be opened in a tracker and edited by a human;
  * it is a few kilobytes where the WAV is megabytes.

The recording mechanism is deliberately dumb, and that is the point: the
writer is attached as the *logger* on the same YM2612 and SN76489 objects
that render the audio, so the bytes in the file are the bytes that went to
the emulator. The .vgm and the .wav cannot drift apart, because there is
only one performance and both are views of it.

Format reference: VGM specification v1.71 (vgmrips.net).
"""

import gzip
import os
import struct

VGM_VERSION = 0x00000171
HEADER_SIZE = 0x100
DEFAULT_SAMPLE_RATE = 44100   # VGM waits are always counted in 44100 Hz samples

# --- command bytes ---------------------------------------------------------
CMD_PSG = 0x50            # 0x50 dd
CMD_YM2612_PORT0 = 0x52   # 0x52 aa dd
CMD_YM2612_PORT1 = 0x53   # 0x53 aa dd
CMD_WAIT_LONG = 0x61      # 0x61 nn nn  (16-bit sample count)
CMD_WAIT_735 = 0x62       # one NTSC frame
CMD_WAIT_882 = 0x63       # one PAL frame
CMD_END = 0x66
CMD_WAIT_SHORT = 0x70     # 0x70+n = wait n+1 samples, n = 0..15
CMD_DATA_BLOCK = 0x67     # 0x67 0x66 tt ssssssss <data>
CMD_DAC_WRITE_WAIT = 0x80 # 0x80+n = next PCM byte to 0x2A, then wait n
CMD_PCM_SEEK = 0xE0       # 0xE0 pppppppp = seek in the PCM bank
DATA_BLOCK_YM2612_PCM = 0x00

#: YM2612 register that eats DAC bytes. Writes to it are what get folded
#: into the PCM bank instead of being logged as ordinary register writes.
YM2612_DAC_REGISTER = 0x2A

#: Sega's SN76489 variant: 16-bit LFSR, taps at bits 0 and 3 (0x0009).
#: The stock TI part is 15-bit/0x0003 — writing that here makes every
#: player render the noise channel wrong.
SEGA_PSG_FEEDBACK = 0x0009
SEGA_PSG_SHIFT_WIDTH = 16


class GD3:
    """The metadata tag every VGM player shows in its playlist."""

    __slots__ = ("title", "title_jp", "game", "game_jp", "system", "system_jp",
                 "author", "author_jp", "date", "converter", "notes")

    def __init__(self, title="", game="", system="Sega Mega Drive / Genesis",
                 author="", date="", converter="chipgen", notes="",
                 title_jp="", game_jp="", system_jp="", author_jp=""):
        self.title = title
        self.title_jp = title_jp
        self.game = game
        self.game_jp = game_jp
        self.system = system
        self.system_jp = system_jp
        self.author = author
        self.author_jp = author_jp
        self.date = date
        self.converter = converter
        self.notes = notes

    def to_bytes(self) -> bytes:
        order = (self.title, self.title_jp, self.game, self.game_jp,
                 self.system, self.system_jp, self.author, self.author_jp,
                 self.date, self.converter, self.notes)
        body = b"".join((s or "").encode("utf-16-le") + b"\x00\x00" for s in order)
        return b"Gd3 " + struct.pack("<II", 0x00000100, len(body)) + body


class VGMWriter:
    """Records register writes + elapsed time into VGM command bytes.

    Hook it up by hand:

        w = vgm.VGMWriter()
        ym = opn2.YM2612(logger=w.ym_logger)
        psg = sn76489.SN76489(logger=w.psg_logger)
        ...  # play; call w.advance(seconds) whenever time passes
        w.save("track.vgm")

    or just pass `vgm_path=` to Sequencer.render(), which does all of it.
    """

    def __init__(self, ym_clock: float = 7_670_453.57,
                 psg_clock: float = 3_579_545, gd3: GD3 = None,
                 pcm_blocks: bool = True):
        self.ym_clock = int(round(ym_clock))
        self.psg_clock = int(round(psg_clock))
        self.gd3 = gd3 or GD3()
        #: Route DAC bytes into a PCM data block (0x67) played back with
        #: 0x8n commands, the way ripped Genesis VGMs do it. One byte per
        #: sample instead of the three a plain "0x52 2A dd" write costs,
        #: and the combined write+wait usually absorbs the gap as well.
        #: Set False for the naive encoding if some player disagrees.
        self.pcm_blocks = pcm_blocks
        self._data = bytearray()
        self._pending = 0.0        # unflushed wait, in 44100 Hz samples
        self._total_samples = 0
        self._loop_offset = None   # index into _data
        self._loop_samples = 0
        self._writes = 0
        self._pcm = bytearray()    # the DAC sample bank
        self._pcm_run = False      # currently emitting a contiguous DAC run
        self._dac_due = False      # a PCM byte is waiting for its wait length

    # -- inputs ------------------------------------------------------------
    def ym_logger(self, port: int, addr: int, data: int):
        """Attach as YM2612(logger=...). `port` is the address port, 0 or 2."""
        if self.pcm_blocks and port < 2 and addr == YM2612_DAC_REGISTER:
            self._dac_byte(data & 0xFF)
            return
        self._settle()
        cmd = CMD_YM2612_PORT1 if port >= 2 else CMD_YM2612_PORT0
        self._data += bytes((cmd, addr & 0xFF, data & 0xFF))
        self._writes += 1

    def psg_logger(self, byte: int):
        """Attach as SN76489(logger=...)."""
        self._settle()
        self._data += bytes((CMD_PSG, byte & 0xFF))
        self._writes += 1

    def _dac_byte(self, value: int):
        """Append one PCM byte to the bank and queue its 0x8n command.

        The command is not emitted yet: 0x8n carries the wait that FOLLOWS
        the write, so it can only be encoded once we know how long that
        wait is. _flush_wait finishes the job.
        """
        self._flush_wait()   # NOT _settle: consecutive DAC bytes are one run
        if not self._pcm_run:
            # Seek explicitly at the start of every run. Costs five bytes per
            # drum hit and means a looping player restarts each run at the
            # right offset instead of walking off the end of the bank.
            self._data += bytes((CMD_PCM_SEEK,)) + struct.pack("<I", len(self._pcm))
            self._pcm_run = True
        self._pcm.append(value)
        self._dac_due = True
        self._writes += 1

    def _settle(self):
        """Flush any pending wait, and close an open DAC run."""
        self._flush_wait()
        self._pcm_run = False

    def advance(self, seconds: float):
        """Let `seconds` of music time pass."""
        if seconds > 0:
            self._pending += seconds * DEFAULT_SAMPLE_RATE

    def advance_samples(self, samples: float):
        if samples > 0:
            self._pending += samples

    def set_loop_point(self):
        """Mark here as the point a looping player jumps back to."""
        self._settle()
        self._loop_offset = len(self._data)
        self._loop_samples = self._total_samples

    # -- wait encoding -----------------------------------------------------
    def _flush_wait(self):
        """Emit the shortest encoding for the accumulated wait.

        Sub-sample remainders are carried, not dropped — a track made of
        thousands of short waits would otherwise drift audibly against the
        rendered WAV over a few minutes.
        """
        whole = int(self._pending)
        if whole <= 0:
            if self._dac_due:
                self._data.append(CMD_DAC_WRITE_WAIT)   # write, wait 0
                self._dac_due = False
            return
        self._pending -= whole
        self._total_samples += whole
        if self._dac_due:
            # Fold the front of the wait into the DAC command itself: at a
            # 16 kHz sample rate the gap between bytes is under three
            # 44.1 kHz samples, so it almost always vanishes entirely here.
            absorbed = min(whole, 15)
            self._data.append(CMD_DAC_WRITE_WAIT + absorbed)
            self._dac_due = False
            whole -= absorbed
        while whole > 0:
            if whole <= 16:
                self._data.append(CMD_WAIT_SHORT + (whole - 1))
                whole = 0
            elif whole == 735:
                self._data.append(CMD_WAIT_735)
                whole = 0
            elif whole == 882:
                self._data.append(CMD_WAIT_882)
                whole = 0
            else:
                chunk = min(whole, 0xFFFF)
                self._data += bytes((CMD_WAIT_LONG,)) + struct.pack("<H", chunk)
                whole -= chunk

    # -- output ------------------------------------------------------------
    def to_bytes(self) -> bytes:
        self._flush_wait()
        prefix = b""
        if self._pcm:
            prefix = (bytes((CMD_DATA_BLOCK, CMD_END, DATA_BLOCK_YM2612_PCM))
                      + struct.pack("<I", len(self._pcm)) + bytes(self._pcm))
        data = prefix + bytes(self._data) + bytes((CMD_END,))
        gd3 = self.gd3.to_bytes()

        gd3_absolute = HEADER_SIZE + len(data)
        eof_absolute = gd3_absolute + len(gd3)

        header = bytearray(b"\x00" * HEADER_SIZE)
        header[0x00:0x04] = b"Vgm "
        struct.pack_into("<I", header, 0x04, eof_absolute - 0x04)
        struct.pack_into("<I", header, 0x08, VGM_VERSION)
        struct.pack_into("<I", header, 0x0C, self.psg_clock)
        struct.pack_into("<I", header, 0x14, gd3_absolute - 0x14)
        struct.pack_into("<I", header, 0x18, self._total_samples)
        if self._loop_offset is not None:
            struct.pack_into("<I", header, 0x1C,
                             (HEADER_SIZE + len(prefix) + self._loop_offset) - 0x1C)
            struct.pack_into("<I", header, 0x20,
                             self._total_samples - self._loop_samples)
        struct.pack_into("<H", header, 0x28, SEGA_PSG_FEEDBACK)
        header[0x2A] = SEGA_PSG_SHIFT_WIDTH
        header[0x2B] = 0x00                       # SN76489 flags
        struct.pack_into("<I", header, 0x2C, self.ym_clock)
        struct.pack_into("<I", header, 0x34, HEADER_SIZE - 0x34)
        return bytes(header) + data + gd3

    def save(self, path: str, compress: bool = None) -> str:
        """Write the file. `.vgz` (or compress=True) gzips it, as players expect."""
        raw = self.to_bytes()
        if compress is None:
            compress = path.lower().endswith(".vgz")
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        if compress:
            # mtime=0 AND filename="" so the same music always produces the
            # same bytes. Without the empty filename, GzipFile copies the
            # output path into the gzip header, and two builds of one track
            # differ purely because they were written to different names.
            with open(path, "wb") as fh:
                with gzip.GzipFile(filename="", fileobj=fh, mode="wb",
                                   mtime=0) as gz:
                    gz.write(raw)
        else:
            with open(path, "wb") as fh:
                fh.write(raw)
        return path

    # -- introspection -----------------------------------------------------
    @property
    def register_writes(self) -> int:
        return self._writes

    @property
    def pcm_bytes(self) -> int:
        return len(self._pcm)

    @property
    def duration(self) -> float:
        return (self._total_samples + self._pending) / DEFAULT_SAMPLE_RATE

    def summary(self) -> str:
        pcm = f", {len(self._pcm)} PCM bytes" if self._pcm else ""
        return (f"{self._writes} register writes{pcm}, {self.duration:.2f}s, "
                f"{len(self.to_bytes())} bytes")


def read_header(path_or_bytes) -> dict:
    """Parse a VGM header back out. Used by the tests, and handy for
    checking that a player's complaint is the file's fault or its own."""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        raw = bytes(path_or_bytes)
    else:
        with open(path_or_bytes, "rb") as fh:
            raw = fh.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
    if raw[:4] != b"Vgm ":
        raise ValueError("not a VGM file (bad magic)")
    version = struct.unpack_from("<I", raw, 0x08)[0]
    data_offset = struct.unpack_from("<I", raw, 0x34)[0]
    loop_offset = struct.unpack_from("<I", raw, 0x1C)[0]
    return {
        "version": f"{version >> 8:x}.{version & 0xFF:02x}",
        "eof_offset": struct.unpack_from("<I", raw, 0x04)[0] + 0x04,
        "file_size": len(raw),
        "psg_clock": struct.unpack_from("<I", raw, 0x0C)[0],
        "ym2612_clock": struct.unpack_from("<I", raw, 0x2C)[0],
        "total_samples": struct.unpack_from("<I", raw, 0x18)[0],
        "duration": struct.unpack_from("<I", raw, 0x18)[0] / DEFAULT_SAMPLE_RATE,
        "gd3_offset": struct.unpack_from("<I", raw, 0x14)[0] + 0x14,
        "data_offset": (data_offset + 0x34) if data_offset else 0x40,
        "loop_offset": (loop_offset + 0x1C) if loop_offset else 0,
        "psg_feedback": struct.unpack_from("<H", raw, 0x28)[0],
        "psg_shift_width": raw[0x2A],
    }
