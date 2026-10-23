"""One voice session per /api/voice socket: push-to-talk turns in, speech out.

The rules are in docs/voice-protocol.md. Three tasks share the socket: the
receive loop (controls and uplink frames), the open turn's listener, and the
speaker. Sends are serialized with a lock.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from starlette.websockets import WebSocket, WebSocketState

from ..speech import RATE, Speech
from ..speech.stt import Caption
from .frames import INPUT_RATE, BadFrame, decode_uplink, encode_downlink
from .recognition import Recognition

if TYPE_CHECKING:
    from .hub import VoiceHub

log = logging.getLogger(__name__)

TURN_ID_MAX = 64
UPLINK_LIMIT_S = 2.0    # audio received but not yet taken by recognition
TURN_LIMIT_S = 300.0


@dataclass
class Turn:
    id: str
    task_id: int | None
    project_id: int | None
    model: str | None = None        # for a task started in `project_id`
    effort: str | None = None
    frames: asyncio.Queue = field(default_factory=asyncio.Queue)  # float32 chunks, then None
    queued: int = 0                 # samples waiting in `frames`
    total: int = 0
    ended: bool = False             # `end` received; no more frames
    job: asyncio.Task | None = None


@dataclass
class Reply:
    segments: list[str]
    generation: int                 # replies from before a stop or start are dropped
    task_id: int | None = None
    approval_id: int | None = None  # set on an approval question, which is never dropped
    cancelled: bool = False         # the approval was answered elsewhere
    cue: bool = False               # the client chimes before it: nothing was asked for


@dataclass
class Asking:
    """The approval this session is asking about (voice-protocol §8)."""

    approval: dict
    project: str
    started: bool = False           # the question began playing: the next turn answers it
    confirming: bool = False        # a destructive request got its yes; waiting for "confirm"
    reply: Reply | None = None


class VoiceSession:
    def __init__(self, websocket: WebSocket, speech: Speech, hub: VoiceHub):
        self.ws = websocket
        self.speech = speech
        self.hub = hub
        self.id = secrets.token_hex(8)
        self.generation = 0
        self.state = "idle"
        self.turn: Turn | None = None
        self.replies: asyncio.Queue[Reply] = asyncio.Queue()
        self.floor = asyncio.Event()  # set while no turn is open; speech waits for it
        self.floor.set()
        self.send_lock = asyncio.Lock()
        self.ignored_frames = 0
        self.approvals: dict[int, tuple[dict, str]] = {}  # waiting to be asked: id → (approval, project)
        self.asking: Asking | None = None

    # Socket loop

    async def run(self):
        output_rate = self.speech.tts.rate if self.speech.tts else 24000
        await self.send(type="ready", session=self.id, generation=self.generation,
                        input={"rate": INPUT_RATE, "format": "pcm_s16le", "channels": 1},
                        output={"rate": output_rate, "format": "pcm_s16le", "channels": 1},
                        speech=self.hub.status())
        speaker = asyncio.ensure_future(self._speaker())
        try:
            while True:
                message = await self.ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    await self._frame(message["bytes"])
                elif message.get("text") is not None:
                    await self._control(message["text"])
        finally:
            speaker.cancel()
            jobs = [speaker]
            if self.turn and self.turn.job:
                jobs.append(self.turn.job)
                self.turn.job.cancel()
            self.turn = None
            await asyncio.gather(*jobs, return_exceptions=True)
            if self.ignored_frames:
                log.debug("voice session %s ignored %d frames outside a turn", self.id, self.ignored_frames)

    async def send(self, **message):
        async with self.send_lock:
            if self.ws.application_state == WebSocketState.CONNECTED:
                await self.ws.send_text(json.dumps(message))

    async def send_bytes(self, data: bytes):
        async with self.send_lock:
            if self.ws.application_state == WebSocketState.CONNECTED:
                await self.ws.send_bytes(data)

    async def error(self, code: str, message: str, turn: str | None = None):
        await self.send(type="voice_error", turn=turn, code=code, message=message)

    # Client → server

    async def _control(self, text: str):
        try:
            message = json.loads(text)
            kind = message["type"]
        except (ValueError, KeyError, TypeError):
            await self.error("bad_message", "control messages are JSON objects with a type")
            return
        if kind == "start":
            await self._start(message)
        elif kind == "end":
            if self.turn and message.get("turn") == self.turn.id and not self.turn.ended:
                self.turn.ended = True
                self.turn.frames.put_nowait(None)
        elif kind == "cancel":
            if self.turn and message.get("turn") == self.turn.id:
                await self._discard_turn()
        elif kind == "stop":
            await self.interrupt()
        else:
            await self.error("bad_message", f"unknown message type {kind!r}")

    async def _start(self, message: dict):
        turn_id = message.get("turn")
        if not isinstance(turn_id, str) or not 0 < len(turn_id) <= TURN_ID_MAX:
            await self.error("bad_message", f"start needs a turn id of 1 to {TURN_ID_MAX} characters")
            return
        task_id, project_id = message.get("task_id"), message.get("project_id")
        if any(v is not None and (not isinstance(v, int) or isinstance(v, bool)) for v in (task_id, project_id)):
            await self.error("bad_message", "task_id and project_id are integers or null", turn_id)
            return
        model, effort = message.get("model"), message.get("effort")
        if any(v is not None and not isinstance(v, str) for v in (model, effort)):
            await self.error("bad_message", "model and effort are strings or null", turn_id)
            return
        if self.turn is not None:
            await self.error("busy", "this device already has a turn open", turn_id)
            return
        if self.speech.status["stt"] != "ready":
            await self.error("stt_unavailable", self.speech.detail["stt"] or "speech recognition is loading", turn_id)
            return
        await self.interrupt()  # talking stops any speech (voice-protocol §7)
        self.turn = Turn(turn_id, task_id, project_id, model, effort)
        self.floor.clear()
        self.turn.job = asyncio.ensure_future(self._listen(self.turn))
        await self._set_state("listening", turn_id)

    async def _frame(self, data: bytes):
        turn = self.turn
        if turn is None or turn.ended:
            self.ignored_frames += 1
            return
        try:
            audio = decode_uplink(data)
        except BadFrame as error:
            await self._fail(turn, "bad_frame", str(error))
            return
        if (turn.queued + len(audio)) / RATE > UPLINK_LIMIT_S:
            await self._fail(turn, "uplink_behind", "recognition fell behind; the turn was dropped so no words are lost "
                                                    "silently. Try again.")
            return
        if (turn.total + len(audio)) / RATE > TURN_LIMIT_S:
            await self._fail(turn, "too_long", f"turns are limited to {TURN_LIMIT_S / 60:.0f} minutes")
            return
        turn.queued += len(audio)
        turn.total += len(audio)
        turn.frames.put_nowait(audio)

    # Turns

    async def _listen(self, turn: Turn):
        async def caption(caption: Caption):
            await self.send(type="caption", turn=turn.id, committed=caption.committed,
                            provisional=caption.provisional)

        recognition = Recognition(self.speech, caption)
        try:
            while (chunk := await turn.frames.get()) is not None:
                turn.queued -= len(chunk)
                await recognition.add(chunk)
            await recognition.stop()
            await self._set_state("transcribing", turn.id)
            text = await recognition.finish()
        except asyncio.CancelledError:
            recognition.cancel()
            raise
        except Exception as error:
            recognition.cancel()
            log.exception("recognition failed")
            await self.error("stt_unavailable", f"recognition failed: {error}", turn.id)
            await self._close_turn(turn)
            return
        result = await self.hub.deliver(self, turn, text)
        await self.send(type="final", turn=turn.id, text=text, **result)
        await self._close_turn(turn)

    async def _fail(self, turn: Turn, code: str, message: str):
        await self.error(code, message, turn.id)
        await self._discard_turn()

    async def _discard_turn(self):
        turn = self.turn
        if turn and turn.job and turn.job is not asyncio.current_task():
            turn.job.cancel()
        await self._close_turn(turn)

    async def _close_turn(self, turn: Turn | None):
        if turn is None or self.turn is not turn:
            return
        self.turn = None
        self.floor.set()
        await self._set_state("speaking" if not self.replies.empty() else "idle")

    async def _set_state(self, state: str, turn: str | None = None):
        if state == self.state:
            return
        self.state = state
        await self.send(type="voice_state", state=state, turn=turn)

    # Speech out

    def speak(self, segments: list[str], task_id: int | None = None, approval_id: int | None = None,
              cue: bool = False):
        """Queue a reply, spoken once no turn is open, after any reply before it. With `cue`, the
        client chimes first."""
        segments = [s for s in segments if s.strip()]
        if segments:
            self.replies.put_nowait(Reply(segments, self.generation, task_id, approval_id, cue=cue))

    async def interrupt(self):
        self.generation += 1
        await self.send(type="stopped", generation=self.generation)

    # Approvals

    def offer(self, approval: dict, project: str):
        """An approval for a task this session owns. Asked oldest first, one at a time."""
        if approval["id"] not in self.approvals and not (self.asking and self.asking.approval["id"] == approval["id"]):
            self.approvals[approval["id"]] = (approval, project)
            self._ask_next()

    def withdraw(self, approval_id: int):
        """The approval was answered, here or elsewhere: stop asking about it."""
        self.approvals.pop(approval_id, None)
        if self.asking and self.asking.approval["id"] == approval_id:
            if self.asking.reply:
                self.asking.reply.cancelled = True
            self.asking = None
            self._ask_next()

    def ask(self, segments: list[str]):
        """Say something as part of the current approval's question."""
        asking = self.asking
        reply = Reply(segments, self.generation, asking.approval["task_id"], asking.approval["id"])
        if asking.reply:
            asking.reply.cancelled = True
        asking.reply = reply
        self.replies.put_nowait(reply)

    def _ask_next(self):
        if self.asking is None and self.approvals:
            approval, project = self.approvals.pop(min(self.approvals))
            self.asking = Asking(approval, project)
            self.ask([])  # the question itself is the client's chime; it takes its turn like speech

    async def _speaker(self):
        while True:
            reply = await self.replies.get()
            await self.floor.wait()
            if reply.cancelled:
                continue
            if reply.approval_id is not None:
                reply.generation = self.generation  # a question waits out a stop or a turn; it is never dropped
            elif reply.generation != self.generation:
                continue  # queued before a stop or a new turn
            if not reply.segments:
                self.asking.started = True  # nothing to say: from here the next turn answers it
                await self.send(type="asking", approval_id=reply.approval_id)
                continue
            if self.speech.status["tts"] != "ready":
                await self.error("tts_unavailable", self.speech.detail["tts"] or "speech output is loading")
                continue
            await self._set_state("speaking")
            try:
                await self._play(reply)
            except Exception:
                log.exception("speech failed")
                await self.error("tts_unavailable", "speech output failed")
            if self.asking and reply is self.asking.reply and not self.asking.started and not reply.cancelled:
                self.replies.put_nowait(reply)  # interrupted before a word went out: ask again afterwards
            if self.turn is None and self.replies.empty():
                await self._set_state("idle")

    async def _play(self, reply: Reply):
        generation, tts = reply.generation, self.speech.tts

        def synthesize(text: str) -> asyncio.Future:
            return asyncio.ensure_future(self.speech.run("tts", tts.synthesize, text))

        upcoming = synthesize(reply.segments[0])
        try:
            for index, text in enumerate(reply.segments):
                audio = await upcoming
                # Synthesize one segment ahead of what is being sent, and no further.
                upcoming = synthesize(reply.segments[index + 1]) if index + 1 < len(reply.segments) else None
                if self.generation != generation or reply.cancelled:
                    return
                if self.asking and reply is self.asking.reply:
                    self.asking.started = True  # once the user may have heard it, the next turn answers it
                cue = {"cue": True} if reply.cue and index == 0 else {}
                await self.send(type="speech", generation=generation, segment=index, text=text,
                                task_id=reply.task_id, approval_id=reply.approval_id, **cue)
                for frame in encode_downlink(audio, tts.rate, generation, index):
                    if self.generation != generation or reply.cancelled:
                        return
                    await self.send_bytes(frame)
            await self.send(type="speech_end", generation=generation)
        finally:
            if upcoming is not None:
                upcoming.cancel()
