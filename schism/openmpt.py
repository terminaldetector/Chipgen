"""openmpt.py — libopenmpt, through ctypes, as the outside opinion.

The module writer here and the module reader here were written by the same
hands from the same document. They can agree with each other and both be
wrong. libopenmpt is a different codebase, written against real files for
twenty years, and it is what plays this project's output when Schism is not
at hand: so the tests ask *it* what it parsed, and the demos are rendered
*by it*.

Optional on purpose. Nothing in `schism` imports this except the tests,
the renderer and the CLI's `--wav`/`--mp3`; without the library they say
so and the rest works. Install it with `apt install libopenmpt0` (or
`openmpt123`), `brew install libopenmpt`, or from https://lib.openmpt.org.
"""

import array
import ctypes
import ctypes.util
import os
import wave

_CANDIDATES = ("libopenmpt.so.0", "libopenmpt.so", "libopenmpt.0.dylib",
               "libopenmpt.dylib", "libopenmpt-0.dll", "libopenmpt.dll")

COMMAND_NOTE, COMMAND_INSTRUMENT, COMMAND_VOLUMEEFFECT = 0, 1, 2
COMMAND_EFFECT, COMMAND_VOLUME, COMMAND_PARAMETER = 3, 4, 5
RENDER_INTERPOLATION = 3


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


def version() -> str:
    if _lib is None:
        return ""
    _lib.openmpt_get_string.restype = ctypes.c_void_p
    _lib.openmpt_get_string.argtypes = [ctypes.c_char_p]
    pointer = _lib.openmpt_get_string(b"library_version")
    text = ctypes.string_at(pointer).decode("ascii", "replace")
    _lib.openmpt_free_string.argtypes = [ctypes.c_void_p]
    _lib.openmpt_free_string(pointer)
    return text


def _prototypes():
    L = _lib
    P, I32, SZ, D, CP = (ctypes.c_void_p, ctypes.c_int32, ctypes.c_size_t,
                         ctypes.c_double, ctypes.c_char_p)
    L.openmpt_module_create_from_memory2.restype = P
    L.openmpt_module_create_from_memory2.argtypes = [
        CP, SZ, P, P, P, P, ctypes.POINTER(ctypes.c_int), P, P]
    L.openmpt_module_destroy.argtypes = [P]
    L.openmpt_module_get_duration_seconds.restype = D
    L.openmpt_module_get_duration_seconds.argtypes = [P]
    for name in ("channels", "orders", "patterns", "instruments", "samples"):
        fn = getattr(L, f"openmpt_module_get_num_{name}")
        fn.restype, fn.argtypes = I32, [P]
    L.openmpt_module_get_pattern_num_rows.restype = I32
    L.openmpt_module_get_pattern_num_rows.argtypes = [P, I32]
    L.openmpt_module_get_order_pattern.restype = I32
    L.openmpt_module_get_order_pattern.argtypes = [P, I32]
    L.openmpt_module_format_pattern_row_channel.restype = P
    L.openmpt_module_format_pattern_row_channel.argtypes = [
        P, I32, I32, I32, SZ, ctypes.c_int]
    L.openmpt_module_format_pattern_row_channel_command.restype = P
    L.openmpt_module_format_pattern_row_channel_command.argtypes = [
        P, I32, I32, I32, ctypes.c_int]
    L.openmpt_module_get_pattern_row_channel_command.restype = ctypes.c_uint8
    L.openmpt_module_get_pattern_row_channel_command.argtypes = [
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
    L.openmpt_free_string.argtypes = [P]


if _lib is not None:
    _prototypes()


def _string(pointer) -> str:
    if not pointer:
        return ""
    text = ctypes.string_at(pointer).decode("utf-8", "replace")
    _lib.openmpt_free_string(pointer)
    return text


class Song:
    """A module as libopenmpt loaded it."""

    def __init__(self, data: bytes):
        if _lib is None:
            raise OpenMPTError("libopenmpt is not installed (apt install "
                               "libopenmpt0, or see https://lib.openmpt.org)")
        self._data = bytes(data)           # libopenmpt copies, but be safe
        error = ctypes.c_int(0)
        self._handle = _lib.openmpt_module_create_from_memory2(
            self._data, len(self._data), None, None, None, None,
            ctypes.byref(error), None, None)
        if not self._handle:
            raise OpenMPTError(f"libopenmpt refused the module (error "
                               f"{error.value})")

    def close(self):
        if self._handle:
            _lib.openmpt_module_destroy(self._handle)
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

    # -- structure ----------------------------------------------------------
    @property
    def duration(self) -> float:
        return _lib.openmpt_module_get_duration_seconds(self._handle)

    @property
    def channels(self) -> int:
        return _lib.openmpt_module_get_num_channels(self._handle)

    @property
    def orders(self) -> int:
        return _lib.openmpt_module_get_num_orders(self._handle)

    @property
    def patterns(self) -> int:
        return _lib.openmpt_module_get_num_patterns(self._handle)

    @property
    def instruments(self) -> int:
        return _lib.openmpt_module_get_num_instruments(self._handle)

    @property
    def samples(self) -> int:
        return _lib.openmpt_module_get_num_samples(self._handle)

    def pattern_rows(self, pattern: int) -> int:
        return _lib.openmpt_module_get_pattern_num_rows(self._handle, pattern)

    def order_pattern(self, order: int) -> int:
        return _lib.openmpt_module_get_order_pattern(self._handle, order)

    def metadata(self, key: str) -> str:
        return _string(_lib.openmpt_module_get_metadata(self._handle,
                                                        key.encode()))

    def instrument_name(self, index: int) -> str:
        return _string(_lib.openmpt_module_get_instrument_name(self._handle,
                                                               index))

    def sample_name(self, index: int) -> str:
        return _string(_lib.openmpt_module_get_sample_name(self._handle,
                                                           index))

    def cell(self, pattern: int, row: int, channel: int) -> str:
        """The cell as libopenmpt shows it: `C-5 01 v64 H44`."""
        return _string(_lib.openmpt_module_format_pattern_row_channel(
            self._handle, pattern, row, channel, 0, 1))

    def command(self, pattern: int, row: int, channel: int, which: int) -> int:
        return _lib.openmpt_module_get_pattern_row_channel_command(
            self._handle, pattern, row, channel, which)

    # -- audio --------------------------------------------------------------
    def render(self, sample_rate: int = 44100, seconds: float = None,
               interpolation: int = 8):
        """Stereo float frames, interleaved, as an `array('f')`.

        Plays the song once (`seconds` caps it). `interpolation` is the
        resampler's tap count (1, 2, 4 or 8); the default of 8 is
        libopenmpt's own.
        """
        _lib.openmpt_module_set_repeat_count(self._handle, 0)
        _lib.openmpt_module_set_render_param(self._handle, RENDER_INTERPOLATION,
                                             interpolation)
        limit = None if seconds is None else int(seconds * sample_rate)
        out = array.array("f")
        chunk = 8192
        buf = (ctypes.c_float * (chunk * 2))()
        done = 0
        while limit is None or done < limit:
            want = chunk if limit is None else min(chunk, limit - done)
            got = _lib.openmpt_module_read_interleaved_float_stereo(
                self._handle, sample_rate, want, buf)
            if not got:
                break
            out.extend(buf[:got * 2])
            done += got
        return out


def peak(frames) -> float:
    return max((abs(v) for v in frames), default=0.0)


def write_wav(frames, path: str, sample_rate: int = 44100) -> str:
    """16-bit stereo WAV from interleaved floats, clipped (not scaled)."""
    pcm = array.array("h", (max(-32768, min(32767, int(v * 32767.0)))
                            for v in frames))
    import sys
    if sys.byteorder == "big":
        pcm.byteswap()
    with wave.open(path, "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())
    return path


def split(frames):
    """(left, right) as lists."""
    return list(frames[0::2]), list(frames[1::2])
