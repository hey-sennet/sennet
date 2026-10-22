"""Binary frames on /api/voice (docs/voice-protocol.md §3)."""
from __future__ import annotations

import struct

import numpy as np

VERSION = 1
AUDIO = 1
HEADER = struct.Struct("<BBHI")  # version, kind, segment, playback generation

INPUT_RATE = 16000
MAX_FRAME_SAMPLES = INPUT_RATE // 10    # 100 ms
CHUNK_S = 0.1                           # downlink audio per frame


class BadFrame(ValueError):
    pass


def decode_uplink(data: bytes) -> np.ndarray:
    """Raw 16 kHz mono PCM16 → float32. Rejects odd lengths, empty frames and frames over 100 ms.

    Short frames are fine: a client's last frame of a turn is usually a partial one."""
    if len(data) % 2:
        raise BadFrame("frames must hold whole 16-bit samples")
    samples = len(data) // 2
    if not 0 < samples <= MAX_FRAME_SAMPLES:
        raise BadFrame(f"frames must hold 1 to {MAX_FRAME_SAMPLES} samples (up to 100 ms)")
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768


def encode_downlink(audio: np.ndarray, rate: int, generation: int, segment: int) -> list[bytes]:
    """float32 audio → frames of about 100 ms, each with the 8-byte header."""
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    header = HEADER.pack(VERSION, AUDIO, segment & 0xFFFF, generation & 0xFFFFFFFF)
    step = int(rate * CHUNK_S)
    return [header + pcm[i:i + step].tobytes() for i in range(0, len(pcm), step)]


def decode_downlink(frame: bytes) -> tuple[int, int, int, np.ndarray]:
    """For tests and tools: (kind, segment, generation, int16 samples)."""
    version, kind, segment, generation = HEADER.unpack_from(frame)
    if version != VERSION:
        raise BadFrame(f"unknown frame version {version}")
    return kind, segment, generation, np.frombuffer(frame[HEADER.size:], dtype="<i2")
