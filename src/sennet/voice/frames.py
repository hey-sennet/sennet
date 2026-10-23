"""Binary frames on /api/voice (docs/voice-protocol.md §3): PCM, or Opus once a client asks for it."""
from __future__ import annotations

import struct

import numpy as np

from . import opus

VERSION = 1
AUDIO = 1                               # PCM16
OPUS = 2
HEADER = struct.Struct("<BBHI")  # version, kind, then segment and playback generation (down) or stream and position (up)
LENGTH = struct.Struct("<H")     # before each Opus packet in a downlink frame

INPUT_RATE = 16000
MAX_FRAME_SAMPLES = INPUT_RATE // 10    # 100 ms
CHUNK_S = 0.1                           # downlink audio per frame
OPUS_FRAME_S = 0.02                     # downlink Opus packets
DOWNLINK_BITRATE = 32000
MAX_GAP_S = 1.5                         # the most silence one uplink frame may stand for


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


class OpusUplink:
    """One client's Opus frames → 16 kHz float32, with the gaps DTX leaves filled with silence.

    The client sends nothing while its encoder is in DTX, and a header-only frame now and then to
    say how far the silence runs, so the server's timeline (voice activity, the end of a turn)
    keeps moving. A new stream number means a new encoder: the timeline carries on from there."""

    def __init__(self):
        self.stream: int | None = None
        self.decoder: opus.Decoder | None = None
        self.next = 0  # the client's position the timeline has reached

    def decode(self, data: bytes) -> np.ndarray:
        if len(data) < HEADER.size:
            raise BadFrame("Opus frames start with an 8-byte header")
        version, kind, stream, position = HEADER.unpack_from(data)
        if version != VERSION or kind != OPUS:
            raise BadFrame(f"expected an Opus frame, got version {version} kind {kind}")
        packet = data[HEADER.size:]
        if stream != self.stream:
            self.stream, self.decoder, self.next = stream, opus.Decoder(INPUT_RATE), position
        ahead = (position - self.next) & 0xFFFFFFFF
        gap = min(ahead, int(MAX_GAP_S * INPUT_RATE)) if ahead < 1 << 31 else 0  # behind: an overlap, not a gap
        parts = [np.zeros(gap, np.float32)] if gap else []
        end = position
        if packet:
            try:
                audio = self.decoder.decode(packet)
            except opus.OpusError as error:
                raise BadFrame(str(error)) from None
            parts.append(audio)
            end = position + len(audio)
        if ((end - self.next) & 0xFFFFFFFF) < 1 << 31:
            self.next = end & 0xFFFFFFFF
        return np.concatenate(parts) if parts else np.zeros(0, np.float32)


class OpusDownlink:
    """Speech → Opus frames of about 100 ms, each several 20 ms packets with a length before each.

    One encoder for the whole session: segments are back to back as far as Opus can tell, and
    frames dropped for a stale generation are like lost packets to the client's decoder."""

    def __init__(self, rate: int):
        self.rate = rate
        self.encoder = opus.Encoder(rate, DOWNLINK_BITRATE, opus.APPLICATION_AUDIO)
        self.step = int(rate * OPUS_FRAME_S)
        self.per_frame = round(CHUNK_S / OPUS_FRAME_S)

    def frames(self, audio: np.ndarray, generation: int, segment: int) -> list[bytes]:
        pcm = np.clip(audio, -1, 1).astype(np.float32)
        pcm = np.concatenate([pcm, np.zeros(-len(pcm) % self.step, np.float32)])
        packets = [self.encoder.encode(pcm[i:i + self.step]) for i in range(0, len(pcm), self.step)]
        header = HEADER.pack(VERSION, OPUS, segment & 0xFFFF, generation & 0xFFFFFFFF)
        return [header + b"".join(LENGTH.pack(len(p)) + p for p in packets[i:i + self.per_frame])
                for i in range(0, len(packets), self.per_frame)]


def opus_packets(frame: bytes) -> list[bytes]:
    """For tests and tools: the packets in a downlink Opus frame."""
    packets, at = [], HEADER.size
    while at < len(frame):
        (size,) = LENGTH.unpack_from(frame, at)
        packets.append(frame[at + LENGTH.size:at + LENGTH.size + size])
        at += LENGTH.size + size
    return packets
