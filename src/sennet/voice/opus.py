"""Opus through the system's libopus, loaded with ctypes (docs/performance.md, step 5).

libopus (BSD-3-Clause) is not bundled: nearly every Linux desktop has it (PipeWire depends
on it), and on macOS it is `brew install opus`. Without it, voice stays on PCM.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
from functools import cache

import numpy as np

log = logging.getLogger(__name__)

APPLICATION_VOIP = 2048
APPLICATION_AUDIO = 2049
SIGNAL_VOICE = 3001
_SET_BITRATE, _GET_BITRATE = 4002, 4003
_SET_COMPLEXITY = 4010
_SET_DTX, _GET_DTX = 4016, 4017
_SET_SIGNAL = 4024
MAX_PACKET = 1500
MAX_FRAME_S = 0.12  # the longest packet libopus decodes
_FALLBACKS = ("libopus.so.0", "/opt/homebrew/lib/libopus.dylib", "/usr/local/lib/libopus.dylib")


class OpusError(RuntimeError):
    pass


@cache
def _lib() -> ctypes.CDLL | None:
    for name in (ctypes.util.find_library("opus"), *_FALLBACKS):
        if not name:
            continue
        try:
            lib = ctypes.CDLL(name)
        except OSError:
            continue
        lib.opus_get_version_string.restype = ctypes.c_char_p
        lib.opus_encoder_create.restype = ctypes.c_void_p
        lib.opus_encoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        lib.opus_encode_float.restype = ctypes.c_int32
        lib.opus_encode_float.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int,
                                          ctypes.c_char_p, ctypes.c_int32]
        lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
        lib.opus_decoder_create.restype = ctypes.c_void_p
        lib.opus_decoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        lib.opus_decode_float.restype = ctypes.c_int
        lib.opus_decode_float.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int32,
                                          ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.c_int]
        lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
        lib.opus_strerror.restype = ctypes.c_char_p
        # opus_encoder_ctl is variadic, so it has no argtypes; Encoder checks each setting took.
        return lib
    return None


def version() -> str | None:
    """libopus's version string, or None when it can't be loaded."""
    lib = _lib()
    return lib.opus_get_version_string().decode() if lib else None


@cache
def available() -> bool:
    """Whether libopus loads and works here: an encoder takes its settings and a packet comes back."""
    if _lib() is None:
        return False
    try:
        encoder = Encoder(16000, 24000, APPLICATION_VOIP, dtx=True)
        decoder = Decoder(16000)
        return len(decoder.decode(encoder.encode(np.zeros(320, np.float32)))) == 320
    except OpusError as error:
        log.warning("libopus is installed but not usable, so voice stays on PCM: %s", error)
        return False


def _check(lib: ctypes.CDLL, code: int, what: str) -> int:
    if code < 0:
        raise OpusError(f"{what}: {lib.opus_strerror(code).decode()}")
    return code


class Encoder:
    """Mono float32 audio → Opus packets. Frames must be 2.5, 5, 10, 20, 40 or 60 ms."""

    def __init__(self, rate: int, bitrate: int, application: int = APPLICATION_AUDIO, dtx: bool = False):
        lib = _lib()
        if lib is None:
            raise OpusError("libopus isn't installed")
        self._lib, self.rate = lib, rate
        error = ctypes.c_int()
        self._state = lib.opus_encoder_create(rate, 1, application, ctypes.byref(error))
        _check(lib, error.value, "opus_encoder_create")
        self._set(_SET_BITRATE, _GET_BITRATE, bitrate)
        self._set(_SET_DTX, _GET_DTX, int(dtx))
        _check(lib, lib.opus_encoder_ctl(ctypes.c_void_p(self._state), ctypes.c_int(_SET_SIGNAL),
                                         ctypes.c_int32(SIGNAL_VOICE)), "signal")
        _check(lib, lib.opus_encoder_ctl(ctypes.c_void_p(self._state), ctypes.c_int(_SET_COMPLEXITY),
                                         ctypes.c_int32(9)), "complexity")
        self._out = ctypes.create_string_buffer(MAX_PACKET)

    def _set(self, request: int, get: int, value: int):
        """Set, then read back: a variadic call that went wrong (some ABIs) shows up here, not as noise."""
        state = ctypes.c_void_p(self._state)
        _check(self._lib, self._lib.opus_encoder_ctl(state, ctypes.c_int(request), ctypes.c_int32(value)), "ctl")
        got = ctypes.c_int32()
        _check(self._lib, self._lib.opus_encoder_ctl(state, ctypes.c_int(get), ctypes.byref(got)), "ctl")
        if got.value != value:
            raise OpusError(f"opus_encoder_ctl({request}) set {got.value}, not {value}")

    def encode(self, frame: np.ndarray) -> bytes:
        """One frame → one packet. A packet of 2 bytes or less is DTX: it need not be sent."""
        pcm = np.ascontiguousarray(frame, dtype=np.float32)
        size = _check(self._lib, self._lib.opus_encode_float(
            self._state, pcm.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(pcm), self._out, MAX_PACKET),
            "opus_encode_float")
        return self._out.raw[:size]

    def __del__(self):
        if getattr(self, "_state", None):
            self._lib.opus_encoder_destroy(self._state)


class Decoder:
    """Opus packets → mono float32 at `rate`, whatever rate they were encoded at."""

    def __init__(self, rate: int):
        lib = _lib()
        if lib is None:
            raise OpusError("libopus isn't installed")
        self._lib, self.rate = lib, rate
        error = ctypes.c_int()
        self._state = lib.opus_decoder_create(rate, 1, ctypes.byref(error))
        _check(lib, error.value, "opus_decoder_create")
        self._pcm = np.zeros(int(rate * MAX_FRAME_S), np.float32)

    def decode(self, packet: bytes) -> np.ndarray:
        samples = _check(self._lib, self._lib.opus_decode_float(
            self._state, packet, len(packet), self._pcm.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            len(self._pcm), 0), "opus_decode_float")
        return self._pcm[:samples].copy()

    def __del__(self):
        if getattr(self, "_state", None):
            self._lib.opus_decoder_destroy(self._state)
