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

import numpy as np
from starlette.websockets import WebSocket, WebSocketState

from ..speech import RATE, Speech
from ..speech.stt import Caption
from .conversation import ECHO_WINDOW_S, Conversation
from . import opus
from .frames import INPUT_RATE, BadFrame, OpusDownlink, OpusUplink, decode_uplink, encode_downlink
from .recognition import Recognition
from .speakable import pause_after

if TYPE_CHECKING:
    from .hub import VoiceHub

log = logging.getLogger(__name__)

TURN_ID_MAX = 64
UPLINK_LIMIT_S = 2.0    # audio received but not yet taken by recognition
TURN_LIMIT_S = 300.0
PCM, OPUS = "pcm_s16le", "opus"
PLAYBACK_TAIL_S = 0.4   # the client starts each run of audio a little late (playback.ts LEAD_S)
# A client reports `drained` when it has played everything. One that doesn't (a page from an
# older build) counts as done this long after the audio sent should have finished.
DRAIN_GRACE_S = 3.0


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
    id: int                         # the session's count of replies: `speech`, `played` and `drained` name it
    segments: list[str]
    generation: int                 # replies from before a stop or start are dropped
    task_id: int | None = None
    approval_id: int | None = None  # set on an approval question, which is never dropped
    cancelled: bool = False         # the approval was answered elsewhere
    cue: bool = False               # the client chimes before it: nothing was asked for
    # Its trace, for the line logged when it ends (`_finished`).
    queued_at: float = field(default_factory=time.monotonic)
    waited: float = 0.0             # for an open turn to close
    heard_at: float | None = None   # the device started playing it
    sent: int = 0                   # segments sent
    played: int = 0                 # segments the device started
    audio_s: float = 0.0            # of speech sent
    done: bool = False              # its last segment has been sent


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
        self.replies_made = 0
        self.sending = False          # a reply's segments are going out
        self.last_sent: tuple[int, int] | None = None  # (reply, segment) last sent, until the client drains it
        self.playing_until = 0.0      # the estimate of when the client finishes it: only a cap (DRAIN_GRACE_S)
        self.playing_task: int | None = None  # whose reply that is
        self.settle_timer: asyncio.TimerHandle | None = None  # the cap, for a client that never drains
        self.spoken: deque[tuple[float, str]] = deque(maxlen=32)  # recent segments, for the echo check
        self.send_lock = asyncio.Lock()
        self.ignored_frames = 0
        self.approvals: dict[int, tuple[dict, str]] = {}  # waiting to be asked: id → (approval, project)
        self.asking: Asking | None = None
        self.uplink: OpusUplink | None = None      # set once the client asks for Opus (`codecs`)
        self.downlink: OpusDownlink | None = None
        self.opus_down = False
        self.sent: dict[tuple[int, int], float] = {}  # (reply, segment) → when it went out, for `played`
        self.unfinished: dict[int, Reply] = {}  # sent, not yet heard to its end or stopped

    # Socket loop

    async def run(self):
        output_rate = self.speech.tts.rate if self.speech.tts else 24000
        await self.send(type="ready", session=self.id, generation=self.generation,
                        input={"rate": INPUT_RATE, "format": "pcm_s16le", "channels": 1},
                        output={"rate": output_rate, "format": "pcm_s16le", "channels": 1},
                        codecs=[PCM, OPUS] if opus.available() else [PCM], speech=self.hub.status())
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
            if self.settle_timer:
                self.settle_timer.cancel()
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
        elif kind == "codecs":
            await self._codecs(message)
        elif kind == "played":
            self._played(message)
        elif kind == "drained":
            await self._drained(message)
        elif kind == "audio":
            log.info("voice %s: the device's audio is %s%s", self.id[:4], message.get("state"),
                     " (page hidden)" if message.get("hidden") else "")
        elif kind == "target":
            if self.conversation is not None and (target := await self._target(message)) is not None:
                self.conversation.retarget(target)
        else:
            await self.error("bad_message", f"unknown message type {kind!r}")

    def _played(self, message: dict):
        """The client started playing a segment."""
        key = (message.get("reply"), message.get("segment"))
        sent = self.sent.pop(key, None)
        delay = f", {time.monotonic() - sent:.1f} s after it was sent" if sent is not None else ""
        log.debug("voice %s: device playing reply %s segment %s%s", self.id[:4], *key, delay)
        if (reply := self.unfinished.get(key[0])) is None:
            return
        reply.played += 1
        if reply.heard_at is None:
            reply.heard_at = time.monotonic()
        for earlier in [r for i, r in self.unfinished.items() if i < reply.id]:
            self._finished(earlier, "heard")  # played to its end, since this one followed it

    async def _drained(self, message: dict):
        """The client ran out of audio, having last played (reply, segment). Unless more was sent
        since, the reply has been heard to its end: from here nothing is playing."""
        key = (message.get("reply"), message.get("segment"))
        if key != self.last_sent:
            return  # more was sent after it, or it is from before a stop
        if (reply := self.unfinished.get(key[0])) is not None and not reply.done:
            return  # ran dry mid-reply, before its next segment was sent
        self.last_sent = None
        if reply is not None:
            self._finished(reply, "heard")
        await self._settle()

    def _finished(self, reply: Reply, how: str):
        """The one line a reply logs at info: how it ended, and where its time went."""
        self.unfinished.pop(reply.id, None)
        parts = [f"{reply.sent} segment{'' if reply.sent == 1 else 's'}, {reply.audio_s:.1f} s of speech"]
        if how == "stopped":
            parts[0] = f"after {reply.played} of {reply.sent} segments"
        if reply.waited >= 0.05:
            parts.append(f"waited {reply.waited:.1f} s for a turn to end")
        if reply.heard_at is not None:
            parts.append(f"first heard {reply.heard_at - reply.queued_at:.1f} s after it was queued")
        log.info("voice %s: reply %d (task %s) %s: %s", self.id[:4], reply.id, reply.task_id, how, ", ".join(parts))

    async def _settle(self):
        """Speech is over once nothing is queued, sending or still playing on the client."""
        if self.last_sent is not None and not self.playing:
            # Past the cap with no `drained`: count it as done, but say the device never did.
            self.last_sent = None
            for reply in list(self.unfinished.values()):
                self._finished(reply, "sent, with no report from the device")
        if self.state == "speaking" and not self.open_turns and self.replies.empty() and not self.playing:
            await self._set_state(self._resting)

    async def _codecs(self, message: dict):
        """The client picks PCM or Opus for each direction; it applies to the frames after this."""
        uplink, downlink = message.get("uplink", PCM), message.get("downlink", PCM)
        offered = (PCM, OPUS) if opus.available() else (PCM,)
        if uplink not in offered or downlink not in offered:
            await self.error("bad_message", f"codecs must be one of {', '.join(offered)}")
            return
        self.uplink = OpusUplink() if uplink == OPUS else None
        self.opus_down = downlink == OPUS
        self.downlink = None

    def _decode(self, data: bytes):
        return self.uplink.decode(data) if self.uplink is not None else decode_uplink(data)

    def _encode(self, audio, rate: int, generation: int, segment: int) -> list[bytes]:
        if not self.opus_down:
            return encode_downlink(audio, rate, generation, segment)
        if self.downlink is None or self.downlink.rate != rate:
            self.downlink = OpusDownlink(rate)
        return self.downlink.frames(audio, generation, segment)

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
        # Saying and transcribing a request takes longer than starting an agent: start it now.
        self.hub.tasks.prewarm(turn.task_id, turn.project_id, turn.model, turn.effort)
        if announce:
            await self.send(type="turn", turn=turn.id)
        await self._set_state("listening", turn.id)

    async def set_state(self, state: str, turn: str | None = None):
        await self._set_state(state, turn)

    @property
    def playing(self) -> bool:
        """Whether a reply is going out or still playing: until the client reports `drained` for
        the last segment sent, or past the estimate of its end by DRAIN_GRACE_S if it never does."""
        if self.sending:
            return True
        return self.last_sent is not None and time.monotonic() < self.playing_until + DRAIN_GRACE_S

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
                audio = self._decode(data)
            except BadFrame as error:
                await self.error("bad_frame", str(error), self.turn.id if self.turn else None)
                return
            if not len(audio):
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
            audio = self._decode(data)
        except BadFrame as error:
            await self._fail(turn, "bad_frame", str(error))
            return
        if not len(audio):
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
            self.replies_made += 1
            log.debug("voice %s: reply %d for task %s queued, %d segment%s: %.60r", self.id[:4], self.replies_made,
                     task_id, len(segments), "" if len(segments) == 1 else "s", " ".join(segments))
            self.replies.put_nowait(Reply(self.replies_made, segments, self.generation, task_id, approval_id,
                                          cue=cue))

    async def interrupt(self, barge_in: bool = False):
        """Stop speaking: a new generation. `barge_in` when it was the user talking over a reply."""
        self.generation += 1
        self.playing_until = 0.0
        self.last_sent = None  # the client silences what it has at once
        for reply in list(self.unfinished.values()):
            self._finished(reply, "stopped")
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
        log.info("voice %s: reply %d, approval %s for task %s, queued%s", self.id[:4], self.replies_made + 1,
                 asking.approval["id"], asking.approval["task_id"],
                 f": {' '.join(segments)!r:.60}" if segments else " (the chime)")
        self.replies_made += 1
        reply = Reply(self.replies_made, segments, self.generation, asking.approval["task_id"], asking.approval["id"])
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
            if not self.floor.is_set():
                log.debug("voice %s: reply %d waits for the open turn", self.id[:4], reply.id)
                waiting = time.monotonic()
                await self.floor.wait()
                reply.waited = time.monotonic() - waiting
            if reply.cancelled:
                continue
            if reply.approval_id is not None:
                reply.generation = self.generation  # a question waits out a stop or a turn; it is never dropped
            elif reply.generation != self.generation:
                log.debug("voice %s: reply %d dropped, queued before a stop or a turn", self.id[:4], reply.id)
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
            self.sending = True
            try:
                await self._play(reply)
            except Exception:
                log.exception("speech failed")
                await self.error("tts_unavailable", "speech output failed")
            finally:
                self.sending = False
            if self.asking and reply is self.asking.reply and not self.asking.started and not reply.cancelled:
                self.replies.put_nowait(reply)  # interrupted before a word went out: ask again afterwards
            if self.playing:
                # The client is still playing it: `drained` settles the state, or the cap does.
                if self.settle_timer:
                    self.settle_timer.cancel()
                delay = self.playing_until + DRAIN_GRACE_S - time.monotonic()
                self.settle_timer = asyncio.get_running_loop().call_later(
                    max(delay, 0) + 0.05, lambda: asyncio.ensure_future(self._settle()))
            await self._settle()

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
                if upcoming is not None and (pause := pause_after(text)):
                    audio = np.concatenate([audio, np.zeros(int(pause * tts.rate), audio.dtype)])
                if self.asking and reply is self.asking.reply:
                    self.asking.started = True  # once the user may have heard it, the next turn answers it
                cue = {"cue": True} if reply.cue and index == 0 else {}
                await self.send(type="speech", reply=reply.id, generation=generation, segment=index, text=text,
                                task_id=reply.task_id, approval_id=reply.approval_id, **cue)
                now = time.monotonic()
                log.debug("voice %s: sending reply %d segment %d (task %s)", self.id[:4], reply.id, index,
                          reply.task_id)
                reply.sent += 1
                reply.audio_s += len(audio) / tts.rate
                self.unfinished[reply.id] = reply
                if len(self.sent) > 64:
                    self.sent.clear()  # a client that never reports `played`
                self.sent[(reply.id, index)] = now
                self.last_sent = (reply.id, index)
                self.playing_task = reply.task_id
                self.playing_until = max(self.playing_until - PLAYBACK_TAIL_S, now) + len(audio) / tts.rate \
                    + PLAYBACK_TAIL_S
                self.spoken.append((now, text))
                for frame in self._encode(audio, tts.rate, generation, index):
                    if self.generation != generation or reply.cancelled:
                        return
                    await self.send_bytes(frame)
            reply.done = True
            await self.send(type="speech_end", reply=reply.id, generation=generation)
        finally:
            if upcoming is not None:
                upcoming.cancel()
