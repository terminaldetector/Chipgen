"""openmpt.py — modules (IT, XM, S3M, MOD) played by libopenmpt, for
references: their audio, the row clock they were played on, the cells that
were played, and one channel at a time.

libopenmpt is a different codebase from this engine, written against real
files for twenty years: a module reference is heard as libopenmpt plays it,
not as this engine would imitate it. Optional: without the library a module
cannot be added as a reference, and `available()` says so.

What is read:

    render      stereo float frames and, row by row, the order, pattern,
                row and second it started (from libopenmpt's own play
                position, polled every 128 frames: a row's time is known
                to 2.9 ms)
    cells       the note, instrument, volume column and effect of each
                cell as the module's own format writes it (`H44` is a
                vibrato in an IT, `4xy` in an XM)
    channels    the same render with only some channels heard, through
                libopenmpt's interactive interface (the file is not
                touched). An S3M is silenced by channel volume instead of
                muting: Scream Tracker drops a muted channel's commands,
                tempo changes with them, and the song would move
    beat        rows per beat and per measure from an IT header (bytes
                0x1E and 0x1F, the pattern highlight its author set);
                other formats store none, and 4 and 16 are assumed — and
                said to be assumed
"""

import array
import ctypes
import ctypes.util
from typing import Dict, Iterable, List, Optional, Tuple

_CANDIDATES = ("libopenmpt.so.0", "libopenmpt.so", "libopenmpt.0.dylib",
               "libopenmpt.dylib", "libopenmpt-0.dll", "libopenmpt.dll")
COMMAND_NOTE, COMMAND_INSTRUMENT, COMMAND_VOLUMEEFFECT = 0, 1, 2
COMMAND_EFFECT, COMMAND_VOLUME, COMMAND_PARAMETER = 3, 4, 5
RENDER_INTERPOLATION = 3
NOTE_OFF, NOTE_CUT, NOTE_FADE = 255, 254, 253
#: Frames rendered between two looks at the play position.
CLOCK_FRAMES = 128


class OpenMPTError(RuntimeError):
    pass


def _load():
    found = ctypes.util.find_library("openmpt")
    for name in ((found,) if found else ()) + _CANDIDATES:
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    return None


_lib = _load()


def available() -> bool:
    return _lib is not None


def _bind():
    L = _lib
    P, I32, SZ, D, CP = (ctypes.c_void_p, ctypes.c_int32, ctypes.c_size_t,
                         ctypes.c_double, ctypes.c_char_p)
    L.openmpt_get_string.restype = P
    L.openmpt_get_string.argtypes = [CP]
    L.openmpt_free_string.argtypes = [P]
    L.openmpt_module_create_from_memory2.restype = P
    L.openmpt_module_create_from_memory2.argtypes = [
        CP, SZ, P, P, P, P, ctypes.POINTER(ctypes.c_int), P, P]
    L.openmpt_module_destroy.argtypes = [P]
    L.openmpt_module_get_duration_seconds.restype = D
    L.openmpt_module_get_duration_seconds.argtypes = [P]
    L.openmpt_module_get_position_seconds.restype = D
    L.openmpt_module_get_position_seconds.argtypes = [P]
    for name in ("channels", "orders", "patterns", "instruments", "samples"):
        fn = getattr(L, f"openmpt_module_get_num_{name}")
        fn.restype, fn.argtypes = I32, [P]
    for name in ("order", "pattern", "row", "speed"):
        fn = getattr(L, f"openmpt_module_get_current_{name}")
        fn.restype, fn.argtypes = I32, [P]
    L.openmpt_module_get_current_estimated_bpm.restype = D
    L.openmpt_module_get_current_estimated_bpm.argtypes = [P]
    L.openmpt_module_get_pattern_num_rows.restype = I32
    L.openmpt_module_get_pattern_num_rows.argtypes = [P, I32]
    L.openmpt_module_get_order_pattern.restype = I32
    L.openmpt_module_get_order_pattern.argtypes = [P, I32]
    L.openmpt_module_get_pattern_row_channel_command.restype = ctypes.c_uint8
    L.openmpt_module_get_pattern_row_channel_command.argtypes = [
        P, I32, I32, I32, ctypes.c_int]
    L.openmpt_module_format_pattern_row_channel_command.restype = P
    L.openmpt_module_format_pattern_row_channel_command.argtypes = [
        P, I32, I32, I32, ctypes.c_int]
    L.openmpt_module_get_metadata.restype = P
    L.openmpt_module_get_metadata.argtypes = [P, CP]
    L.openmpt_module_get_instrument_name.restype = P
    L.openmpt_module_get_instrument_name.argtypes = [P, I32]
    L.openmpt_module_get_sample_name.restype = P
    L.openmpt_module_get_sample_name.argtypes = [P, I32]
    L.openmpt_module_set_repeat_count.restype = ctypes.c_int
    L.openmpt_module_set_repeat_count.argtypes = [P, I32]
    L.openmpt_module_set_render_param.restype = ctypes.c_int
    L.openmpt_module_set_render_param.argtypes = [P, ctypes.c_int, I32]
    L.openmpt_module_read_interleaved_float_stereo.restype = SZ
    L.openmpt_module_read_interleaved_float_stereo.argtypes = [
        P, I32, SZ, ctypes.POINTER(ctypes.c_float)]
    L.openmpt_module_ext_create_from_memory.restype = P
    L.openmpt_module_ext_create_from_memory.argtypes = [
        CP, SZ, P, P, P, P, ctypes.POINTER(ctypes.c_int), P, P]
    L.openmpt_module_ext_get_module.restype = P
    L.openmpt_module_ext_get_module.argtypes = [P]
    L.openmpt_module_ext_get_interface.restype = ctypes.c_int
    L.openmpt_module_ext_get_interface.argtypes = [P, CP, P, SZ]
    L.openmpt_module_ext_destroy.argtypes = [P]


if _lib is not None:
    _bind()


def _string(pointer) -> str:
    if not pointer:
        return ""
    text = ctypes.string_at(pointer).decode("utf-8", "replace")
    _lib.openmpt_free_string(pointer)
    return text


def version() -> str:
    return _string(_lib.openmpt_get_string(b"library_version")) \
        if _lib is not None else ""


class _Interactive(ctypes.Structure):
    """libopenmpt_ext's "interactive" interface: a table of functions."""
    _fields_ = [(name, ctypes.c_void_p) for name in (
        "set_current_speed", "set_current_tempo", "set_tempo_factor",
        "get_tempo_factor", "set_pitch_factor", "get_pitch_factor",
        "set_global_volume", "get_global_volume", "set_channel_volume",
        "get_channel_volume", "set_channel_mute_status",
        "get_channel_mute_status", "set_instrument_mute_status",
        "get_instrument_mute_status", "play_note", "stop_note")]


def _note_name(value: int) -> str:
    names = ("C-", "C#", "D-", "D#", "E-", "F-", "F#", "G-", "G#", "A-",
             "A#", "B-")
    return f"{names[(value - 1) % 12]}{(value - 1) // 12}"


class Module:
    """A module as libopenmpt loaded it. `keep` (0-based channels) plays
    only those channels."""

    def __init__(self, data: bytes, keep: Optional[Iterable[int]] = None):
        if _lib is None:
            raise OpenMPTError("libopenmpt is not installed (apt install "
                               "libopenmpt0, or see https://lib.openmpt.org)")
        self.data = bytes(data)
        error = ctypes.c_int(0)
        self._ext = _lib.openmpt_module_ext_create_from_memory(
            self.data, len(self.data), None, None, None, None,
            ctypes.byref(error), None, None)
        if not self._ext:
            raise OpenMPTError(f"libopenmpt refused the module (error "
                               f"{error.value})")
        self._handle = _lib.openmpt_module_ext_get_module(self._ext)
        if keep is not None:
            self._keep(set(keep))

    def _keep(self, keep: set):
        table = _Interactive()
        if not _lib.openmpt_module_ext_get_interface(
                self._ext, b"interactive", ctypes.byref(table),
                ctypes.sizeof(table)):
            raise OpenMPTError("this libopenmpt has no interactive "
                               "interface: a module's channels cannot be "
                               "heard apart")
        P = ctypes.c_void_p
        if self.format == "s3m":
            silence = ctypes.CFUNCTYPE(ctypes.c_int, P, ctypes.c_int32,
                                       ctypes.c_double)(
                table.set_channel_volume)
            for ch in range(self.channels):
                silence(self._ext, ch, 1.0 if ch in keep else 0.0)
        else:
            silence = ctypes.CFUNCTYPE(ctypes.c_int, P, ctypes.c_int32,
                                       ctypes.c_int)(
                table.set_channel_mute_status)
            for ch in range(self.channels):
                silence(self._ext, ch, 0 if ch in keep else 1)

    def close(self):
        if getattr(self, "_ext", None):
            _lib.openmpt_module_ext_destroy(self._ext)
            self._ext = None
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # -- what it is -------------------------------------------------------------
    @property
    def format(self) -> str:
        d = self.data
        if d[:4] == b"IMPM":
            return "it"
        if d[:17] == b"Extended Module: ":
            return "xm"
        if len(d) > 0x30 and d[0x2C:0x30] == b"SCRM":
            return "s3m"
        return "mod"

    @property
    def channels(self) -> int:
        return _lib.openmpt_module_get_num_channels(self._handle)

    @property
    def duration(self) -> float:
        return _lib.openmpt_module_get_duration_seconds(self._handle)

    def metadata(self, key: str) -> str:
        return _string(_lib.openmpt_module_get_metadata(self._handle,
                                                        key.encode()))

    def instrument_name(self, index: int) -> str:
        return _string(_lib.openmpt_module_get_instrument_name(
            self._handle, index))

    def sample_name(self, index: int) -> str:
        return _string(_lib.openmpt_module_get_sample_name(self._handle,
                                                           index))

    def beat(self) -> Tuple[int, int, str]:
        """(rows per beat, rows per measure, where that came from)."""
        d = self.data
        if self.format == "it" and len(d) > 0x20 and d[0x1E] and d[0x1F]:
            return d[0x1E], d[0x1F], "the IT header's row highlight"
        return 4, 16, "assumed (the format stores no beat)"

    # -- cells --------------------------------------------------------------------
    def cell(self, pattern: int, row: int, channel: int) -> dict:
        h = self._handle
        get = _lib.openmpt_module_get_pattern_row_channel_command
        fmt = _lib.openmpt_module_format_pattern_row_channel_command
        note = get(h, pattern, row, channel, COMMAND_NOTE)

        def letter(which):
            text = _string(fmt(h, pattern, row, channel, which)).strip()
            return text if text.strip(".") else ""     # "." is no command
        out = {"note": note,
               "instrument": get(h, pattern, row, channel,
                                 COMMAND_INSTRUMENT),
               "volume": get(h, pattern, row, channel, COMMAND_VOLUME),
               "volcmd": letter(COMMAND_VOLUMEEFFECT),
               "effect": letter(COMMAND_EFFECT),
               "param": get(h, pattern, row, channel, COMMAND_PARAMETER)}
        if 1 <= note <= 120:
            out["name"] = _note_name(note)
        return out

    @staticmethod
    def commands(c: dict) -> List[str]:
        """A cell's commands as the format writes them: the effect with
        its parameter in hex (`H44`, `437`), the volume column's command
        with its value (`v40`, `h05`)."""
        out = []
        if c["effect"]:
            out.append(f"{c['effect']}{c['param']:02X}")
        if c["volcmd"]:
            out.append(f"{c['volcmd']}{c['volume']:02d}")
        return out

    # -- audio and the clock ----------------------------------------------------------
    def render(self, rate: int = 44100, seconds: Optional[float] = None,
               clock: bool = True, interpolation: int = 8):
        """-> (interleaved stereo array('f'), rows): each row the song
        played, in play order, as {order, pattern, row, t}."""
        h = self._handle
        _lib.openmpt_module_set_repeat_count(h, 0)
        _lib.openmpt_module_set_render_param(h, RENDER_INTERPOLATION,
                                             interpolation)
        limit = None if seconds is None else int(seconds * rate)
        out = array.array("f")
        rows: List[dict] = []
        chunk = CLOCK_FRAMES if clock else 8192
        buf = (ctypes.c_float * (chunk * 2))()
        done = 0
        last = None
        while limit is None or done < limit:
            if clock:
                here = (_lib.openmpt_module_get_current_order(h),
                        _lib.openmpt_module_get_current_pattern(h),
                        _lib.openmpt_module_get_current_row(h))
                if here != last:
                    rows.append({"order": here[0], "pattern": here[1],
                                 "row": here[2],
                                 "t": round(done / rate, 5),
                                 "speed": _lib.openmpt_module_get_current_speed(h),
                                 "bpm": round(_lib.
                                              openmpt_module_get_current_estimated_bpm(h), 3)})
                    last = here
            want = chunk if limit is None else min(chunk, limit - done)
            got = _lib.openmpt_module_read_interleaved_float_stereo(
                h, rate, want, buf)
            if not got:
                break
            out.extend(buf[:got * 2])
            done += got
        return out, rows

    def notes(self, rows: List[dict], rate_end: float) -> List[dict]:
        """The notes the rows played, per channel: start and end in
        seconds (a note ends at the channel's next note, cut or off),
        pitch (the engine's numbering: C-0 = 0, A-4 = 57, by the name the
        module writes — its samples set the true pitch), instrument, and
        the commands written on its rows while it sounded."""
        out: List[dict] = []
        open_: Dict[int, dict] = {}
        for k, r in enumerate(rows):
            for ch in range(self.channels):
                c = self.cell(r["pattern"], r["row"], ch)
                n = c["note"]
                written = self.commands(c)
                if n == 0:
                    if ch in open_ and written:
                        open_[ch]["effects"].extend(written)
                    continue
                prev = open_.pop(ch, None)
                if prev is not None:
                    prev["end"] = r["t"]
                    out.append(prev)
                if 1 <= n <= 120:
                    open_[ch] = {"voice": f"ch{ch + 1}", "channel": ch,
                                 "start": r["t"], "pitch": n - 1,
                                 "name": c["name"],
                                 "instrument": c["instrument"],
                                 "velocity": c["volume"]
                                 if c["volcmd"] == "v" else None,
                                 "row_index": k, "effects": written}
        for ch, prev in open_.items():
            prev["end"] = rate_end
            out.append(prev)
        out.sort(key=lambda n: (n["start"], n["channel"]))
        return out
