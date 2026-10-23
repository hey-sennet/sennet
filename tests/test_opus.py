"""Opus on /api/voice (docs/voice-protocol.md §3), through the system's libopus."""
import numpy as np
import pytest

from sennet.voice import frames, opus
from conftest import wait_for
from test_voice import Voice, client, session, speak, speech, voice  # noqa: F401  (fixtures)

pytestmark = pytest.mark.timeout(20)
needs_libopus = pytest.mark.skipif(not opus.available(), reason="libopus isn't installed")

RATE = 16000


def tone(seconds: float, rate: int = RATE, level: float = 0.2) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    return (level * np.sin(2 * np.pi * 300 * t)).astype(np.float32)


class Client:
    """What the browser sends: 20 ms packets, nothing during DTX, and a header now and then."""

    def __init__(self, stream: int = 1):
        self.encoder = opus.Encoder(RATE, 24000, opus.APPLICATION_VOIP, dtx=True)
        self.stream, self.position = stream, 0

    def frames(self, audio: np.ndarray) -> list[bytes]:
        sent = []
        for i in range(0, len(audio), 320):
            packet = self.encoder.encode(audio[i:i + 320])
            if len(packet) > 2:
                sent.append(frames.HEADER.pack(frames.VERSION, frames.OPUS, self.stream, self.position) + packet)
            self.position += 320
        return sent

    def silent_until_now(self) -> bytes:
        return frames.HEADER.pack(frames.VERSION, frames.OPUS, self.stream, self.position)


@needs_libopus
def test_a_packet_round_trips():
    encoder, decoder = opus.Encoder(RATE, 24000, opus.APPLICATION_VOIP), opus.Decoder(RATE)
    packets = [encoder.encode(chunk) for chunk in tone(0.2).reshape(-1, 320)]
    assert all(0 < len(p) < 200 for p in packets)
    decoded = np.concatenate([decoder.decode(p) for p in packets])
    assert len(decoded) == 3200
    assert np.sqrt(np.mean(decoded[1600:] ** 2)) == pytest.approx(0.2 / np.sqrt(2), rel=0.2)


@needs_libopus
def test_silence_goes_quiet_with_dtx():
    encoder = opus.Encoder(RATE, 24000, opus.APPLICATION_VOIP, dtx=True)
    sizes = [len(encoder.encode(np.zeros(320, np.float32))) for _ in range(50)]
    assert sizes[-1] <= 2


@needs_libopus
def test_uplink_fills_the_silence_between_packets():
    client, uplink = Client(), frames.OpusUplink()
    heard = [uplink.decode(f) for f in client.frames(tone(0.2))]
    assert sum(map(len, heard)) == 3200
    client.position += 8000  # half a second of DTX: no packets
    assert len(uplink.decode(client.silent_until_now())) == 8000
    after = [uplink.decode(f) for f in client.frames(tone(0.1))]
    assert sum(map(len, after)) == 1600


@needs_libopus
def test_a_gap_is_filled_by_the_next_packet_too():
    client, uplink = Client(), frames.OpusUplink()
    first, *_ = client.frames(tone(0.02))
    uplink.decode(first)
    client.position += 3200
    second, *_ = client.frames(tone(0.02))
    audio = uplink.decode(second)
    assert len(audio) == 3200 + 320
    assert not audio[:3200].any()


@needs_libopus
def test_a_new_stream_carries_on_without_a_gap():
    uplink = frames.OpusUplink()
    for f in Client(stream=1).frames(tone(0.1)):
        uplink.decode(f)
    restarted = Client(stream=2)
    restarted.position = 123  # a new encoder's positions start wherever it likes
    assert sum(len(uplink.decode(f)) for f in restarted.frames(tone(0.1))) == 1600


@needs_libopus
def test_one_frame_stands_for_at_most_a_little_silence():
    client, uplink = Client(), frames.OpusUplink()
    uplink.decode(client.silent_until_now())
    client.position += 10 * RATE
    assert len(uplink.decode(client.silent_until_now())) == frames.MAX_GAP_S * RATE


@needs_libopus
def test_uplink_rejects_what_isnt_opus():
    uplink = frames.OpusUplink()
    for data in (b"", b"\x00" * 7, frames.HEADER.pack(1, frames.AUDIO, 0, 0) + b"\x00" * 640):
        with pytest.raises(frames.BadFrame):
            uplink.decode(data)


@needs_libopus
def test_downlink_packs_100_ms_per_frame():
    downlink = frames.OpusDownlink(24000)
    sent = downlink.frames(tone(0.45, 24000), generation=3, segment=1)
    assert len(sent) == 5  # 23 packets of 20 ms, five to a frame
    assert {frames.HEADER.unpack_from(f) for f in sent} == {(frames.VERSION, frames.OPUS, 1, 3)}
    packets = [p for f in sent for p in frames.opus_packets(f)]
    assert len(packets) == 23
    decoder = opus.Decoder(24000)
    assert sum(len(decoder.decode(p)) for p in packets) == 23 * 480


# Over the socket

@needs_libopus
def test_ready_offers_opus(voice):
    assert voice.ready["codecs"] == ["pcm_s16le", "opus"]


@needs_libopus
def test_a_turn_in_opus_is_heard(client, voice, project):
    voice.send(type="codecs", uplink="opus", downlink="pcm_s16le")
    voice.send(type="start", turn="t1", project_id=project["id"])
    for frame in Client().frames(tone(1.0)):
        voice.ws.send_bytes(frame)
    voice.send(type="end", turn="t1")
    final = voice.next("final")
    assert (final["text"], final["outcome"]) == ("run the tests in backend", "dispatched")
    assert wait_for(client, final["task_id"], lambda t: t["messages"])["messages"][0]["text"] == final["text"]


@needs_libopus
def test_pcm_frames_are_refused_once_opus_is_chosen(voice, project):
    voice.send(type="codecs", uplink="opus")
    voice.send(type="start", turn="t1", project_id=project["id"])
    voice.ws.send_bytes(b"\x00" * 640)
    assert voice.next("voice_error")["code"] == "bad_frame"


@needs_libopus
def test_an_unknown_codec_is_a_bad_message(voice):
    voice.send(type="codecs", uplink="mp3")
    assert voice.next("voice_error")["code"] == "bad_message"


@needs_libopus
def test_a_conversation_hears_the_end_of_a_turn_through_dtx(voice, speech, project):
    # Nothing is sent while the client's encoder is in DTX, only how far the silence runs: the turn
    # must still end, which needs the timeline to move.
    voice.send(type="codecs", uplink="opus")
    voice.send(type="open_mic", project_id=project["id"])
    assert voice.next("mic")["open"]
    client = Client()
    for frame in client.frames(tone(1.0)):
        voice.ws.send_bytes(frame)
    for _ in range(15):
        client.position += 1600
        voice.ws.send_bytes(client.silent_until_now())
    final = voice.next("final")
    assert (final["turn"], final["outcome"]) == ("s1", "dispatched")


@needs_libopus
def test_a_reply_is_spoken_in_opus(client, voice, speech):
    speech.tts.gate.set()
    voice.send(type="codecs", uplink="pcm_s16le", downlink="opus")
    voice.send(type="stop")
    voice.next("stopped")  # a round trip: the codecs are in place
    speak(client, "All tests pass.", "Two files changed.")
    voice.next("speech_end")
    headers = [frames.HEADER.unpack_from(f) for f in voice.audio]
    assert {(kind, generation) for _, kind, _, generation in headers} == {(frames.OPUS, 1)}
    assert [segment for _, _, segment, _ in headers] == [0, 0, 0, 1, 1, 1]
    decoder = opus.Decoder(24000)
    audio = np.concatenate([decoder.decode(p) for f in voice.audio for p in frames.opus_packets(f)])
    assert len(audio) == 2 * int(24000 * 0.3)
    assert session(client).downlink is not None


def test_without_libopus_the_socket_stays_on_pcm(client, monkeypatch, project):
    monkeypatch.setattr(opus, "available", lambda: False)
    with client.websocket_connect("/api/voice") as ws:
        voice = Voice(ws)
        assert voice.ready["codecs"] == ["pcm_s16le"]
        voice.send(type="codecs", uplink="opus", downlink="opus")
        assert voice.next("voice_error")["code"] == "bad_message"
        voice.talk(project_id=project["id"])
        assert voice.next("final")["outcome"] == "dispatched"
