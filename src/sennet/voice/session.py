"""One voice session per /api/voice socket: turns in, speech out.

The rules are in docs/voice-protocol.md. Three tasks share the socket: the
receive loop (controls and uplink frames), the open turn's listener, and the
speaker. Sends are serialized with a lock. While a conversation is open
(conversation.py), it listens instead, and opens and ends turns itself.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from starlette.websockets import WebSocket, WebSocketState

from ..speech import RATE, Speech
from ..speech.stt import Caption
from .conversation import ECHO_WINDOW_S, Conversation
from .frames import INPUT_RATE, BadFrame, decode_uplink, encode_downlink
from .recognition import Recognition

if TYPE_CHECKING:
    from .hub import VoiceHub

log = logging.getLogger(__name__)

TURN_ID_MAX = 64
UPLINK_LIMIT_S = 2.0    # audio received but not yet taken by recognition
TURN_LIMIT_S = 300.0
PLAYBACK_TAIL_S = 0.4   # the client starts each run of audio a little late (playback.ts LEAD_S)


@dataclass(eq=False)  # each turn is itself: kept in a set while open
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
    start: int = 0                  # in a conversation: the absolute sample the turn starts at
    over_reply: bool = False        # in a conversation: it began while a reply was playing
    manual: bool = False            # in a conversation: the talk control holds it


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
    asked_at: float = 0.0           # when the chime was asked for


class VoiceSession:
    def __init__(self, websocket: WebSocket, speech: Speech, hub: VoiceHub):
        self.ws = websocket
        self.speech = speech
        self.hub = hub
        self.id = secrets.token_hex(8)
        self.generation = 0
        self.state = "idle"
        self.turn: Turn | None = None
        self.open_turns: set[Turn] = set()  # being heard or delivered; speech waits for them
        self.replies: asyncio.Queue[Reply] = asyncio.Queue()
        self.floor = asyncio.Event()  # set while no turn is open; speech waits for it
        self.floor.set()
        self.conversation: Conversation | None = None
        self.playing_until = 0.0      # when the client should finish playing what was sent
        self.playing_task: int | None = None  # whose reply that is
        self.spoken: deque[tuple[float, str]] = deque(maxlen=32)  # recent segments, for the echo check
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
            if self.conversation is not None:
                await self.conversation.stop()
                self.conversation = None
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
        elif kind == "end" and self.conversation is not None:
            await self.conversation.release(message.get("turn"))
        elif kind == "end":
            if self.turn and message.get("turn") == self.turn.id and not self.turn.ended:
                self.turn.ended = True
                self.turn.frames.put_nowait(None)
        elif kind == "cancel" and self.conversation is not None:
            await self.conversation.discard(message.get("turn"))
        elif kind == "cancel":
            if self.turn and message.get("turn") == self.turn.id:
                await self._discard_turn()
        elif kind == "stop":
            await self.interrupt()
        elif kind == "open_mic":
            await self._open_conversation(message)
        elif kind == "close_mic":
            await self.close_conversation("closed")
        elif kind == "target":
            if self.conversation is not None and (target := await self._target(message)) is not None:
                self.conversation.retarget(target)
        else:
            await self.error("bad_message", f"unknown message type {kind!r}")

    async def _target(self, message: dict, turn_id: str | None = None) -> dict | None:
        """The task, project, model and effort a message names, or None (and an error) if malformed."""
        task_id, project_id = message.get("task_id"), message.get("project_id")
        if any(v is not None and (not isinstance(v, int) or isinstance(v, bool)) for v in (task_id, project_id)):
            await self.error("bad_message", "task_id and project_id are integers or null", turn_id)
            return None
        model, effort = message.get("model"), message.get("effort")
        if any(v is not None and not isinstance(v, str) for v in (model, effort)):
            await self.error("bad_message", "model and effort are strings or null", turn_id)
            return None
        return {"task_id": task_id, "project_id": project_id, "model": model, "effort": effort}

    async def _start(self, message: dict):
        turn_id = message.get("turn")
        if not isinstance(turn_id, str) or not 0 < len(turn_id) <= TURN_ID_MAX:
            await self.error("bad_message", f"start needs a turn id of 1 to {TURN_ID_MAX} characters")
            return
        if (target := await self._target(message, turn_id)) is None:
            return
        if self.conversation is not None:
            await self.interrupt()  # talking stops any speech (voice-protocol §7)
            await self.conversation.hold(turn_id, target)
            return
        if self.turn is not None:
            await self.error("busy", "this device already has a turn open", turn_id)
            return
        if self.speech.status["stt"] != "ready":
            await self.error("stt_unavailable", self.speech.detail["stt"] or "speech recognition is loading", turn_id)
            return
        await self.interrupt()  # talking stops any speech (voice-protocol §7)
        turn = Turn(turn_id, **target)
        turn.job = asyncio.ensure_future(self._listen(turn))
        await self.begin(turn)

    # Conversations

    async def _open_conversation(self, message: dict):
        if (target := await self._target(message)) is None:
            return
        if self.conversation is not None:
            self.conversation.retarget(target)
            await self.send(type="mic", open=True)
            return
        if self.turn is not None:
            await self.error("busy", "this device already has a turn open")
            return
        for role, code in (("stt", "stt_unavailable"), ("turn", "turn_unavailable")):
            if self.speech.status[role] != "ready":
                await self.error(code, self.speech.detail[role] or "speech recognition is loading")
                return
        self.conversation = Conversation(self, target)
        await self.send(type="mic", open=True)
        if not self.open_turns and self.state == "idle":
            await self._set_state("open")

    async def close_conversation(self, reason: str):
        conversation, self.conversation = self.conversation, None
        if conversation is None:
            return
        await conversation.close()
        await self.send(type="mic", open=False, reason=reason)
        if self.state == "open":
            await self._set_state("idle")

    async def begin(self, turn: Turn, announce: bool = False):
        """A turn opens: speech waits for it. `announce` tells the client about a turn it didn't start."""
        self.turn = turn
        self.open_turns.add(turn)
        self.floor.clear()
        if announce:
            await self.send(type="turn", turn=turn.id)
        await self._set_state("listening", turn.id)

    async def set_state(self, state: str, turn: str | None = None):
        await self._set_state(state, turn)

    @property
    def playing(self) -> bool:
        """Whether the client is probably still playing a reply."""
        return time.monotonic() < self.playing_until

    def echoes(self, words: list[str]) -> bool:
        """Whether a transcript is mostly words Sennet said in the last few seconds."""
        recent = {w for at, text in self.spoken if time.monotonic() - at < ECHO_WINDOW_S
                  for w in re.findall(r"[\w']+", text.lower())}
        return bool(recent) and sum(w in recent for w in words) >= 0.6 * len(words)

    def reask(self):
        """Ask the current approval again: chime, and a fresh window to answer it."""
        if self.asking:
            self.asking.started = False
            self.ask([])

    async def _frame(self, data: bytes):
        if self.conversation is not None:
            try:
                audio = decode_uplink(data)
            except BadFrame as error:
                await self.error("bad_frame", str(error), self.turn.id if self.turn else None)
                return
            if not self.conversation.add(audio):
                turn = self.conversation.turn
                await self.error("uplink_behind", "recognition fell behind; what you were saying was dropped so no "
                                                  "words are lost silently. Try again.", turn.id if turn else None)
                if turn is not None:
                    await self.conversation.discard(turn.id)
            return
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
        if turn is None or turn not in self.open_turns:
            return
        self.open_turns.discard(turn)
        if self.turn is turn:
            self.turn = None
        if self.open_turns:
            return
        self.floor.set()
        await self._set_state("speaking" if not self.replies.empty() else self._resting)

    async def close_turn(self, turn: Turn | None):
        await self._close_turn(turn)

    @property
    def _resting(self) -> str:
        return "open" if self.conversation is not None else "idle"

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

    async def interrupt(self, barge_in: bool = False):
        """Stop speaking: a new generation. `barge_in` when it was the user talking over a reply."""
        self.generation += 1
        self.playing_until = 0.0
        await self.send(type="barge_in" if barge_in else "stopped", generation=self.generation)

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
                self.asking.asked_at = time.monotonic()
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
            if not self.open_turns and self.replies.empty():
                await self._set_state(self._resting)

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
                now = time.monotonic()
                self.playing_task = reply.task_id
                self.playing_until = max(self.playing_until - PLAYBACK_TAIL_S, now) + len(audio) / tts.rate \
                    + PLAYBACK_TAIL_S
                self.spoken.append((now, text))
                for frame in encode_downlink(audio, tts.rate, generation, index):
                    if self.generation != generation or reply.cancelled:
                        return
                    await self.send_bytes(frame)
            await self.send(type="speech_end", generation=generation)
        finally:
            if upcoming is not None:
                upcoming.cancel()
