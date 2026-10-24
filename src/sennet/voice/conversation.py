"""An open-mic conversation on one voice socket (docs/m7-sessions.md).

While it is open the client streams audio continuously. Voice activity runs over all of
it, and the endpointer (endpoint.py) decides where turns start and end; each turn is
recognised and delivered as a push-to-talk turn is. Pressing the talk control still works:
it holds the open turn, or opens one, until it is let go.

Speech over a reply stops the reply (barge-in), and what is said over it is heard to the
end of the turn and sent like any other. A lone word or a "wait" only stops the reply.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING

import numpy as np

from ..speech import RATE
from ..speech.base import AudioBuffer
from ..speech.stt import Caption
from .endpoint import SPEECH, Endpointer
from .recognition import Recognition

if TYPE_CHECKING:
    from .session import Turn, VoiceSession

log = logging.getLogger(__name__)

PRE_ROLL_S = 0.3        # kept from before the speech that opened a turn
KEEP_S = 1.0            # audio kept between turns, for the pre-roll
UPLINK_LIMIT_S = 2.0    # audio received but not yet looked at
TURN_LIMIT_S = 300.0    # as for push-to-talk: a television never falls quiet
IDLE_S = 300.0          # a conversation closes after this long with nobody talking, Sennet included
ECHO_WINDOW_S = 20.0    # what was spoken this recently counts as a possible echo
FILLER = {"um", "uh", "erm", "er", "hmm", "mm", "mhm", "ah", "oh", "uh huh"}
HOLD = {"wait", "wait wait", "hold on", "hang on", "one sec", "one second", "just a sec", "just a second",
        "wait a sec", "wait a second", "wait a minute", "hold on a sec", "hang on a sec", "shh", "sorry"}
WORD = re.compile(r"[\w']+")


class Conversation:
    def __init__(self, session: VoiceSession, target: dict):
        self.session = session
        self.speech = session.speech
        self.target = target          # task_id, project_id, model, effort for the next turn
        self.frames: asyncio.Queue[np.ndarray | None] = asyncio.Queue()
        self.queued = 0
        self.audio = AudioBuffer()
        self.base = 0                 # the absolute sample at audio[0]
        self.vad = self.speech.vad.stream()
        self.vad_done = 0             # absolute samples the VAD has seen
        self.endpoint = Endpointer()
        self.turn: Turn | None = None
        self.recognition: Recognition | None = None
        self.fed = 0                  # absolute samples given to the recognition
        self.final: asyncio.Future | None = None
        self.concluding: set[asyncio.Task] = set()
        self.count = 0
        self.last_active = time.monotonic()
        self.job = asyncio.ensure_future(self._run())
        self.job.add_done_callback(self._stopped)
        log.info("conversation opened on session %s", session.id)

    def _stopped(self, job: asyncio.Task):
        """The listening loop ended. If it failed, say so, rather than leave a mic that hears nothing."""
        if job.cancelled() or job.exception() is None:
            return
        log.error("conversation on session %s failed", self.session.id, exc_info=job.exception())
        asyncio.ensure_future(self.session.close_conversation("error"))

    # From the session

    def add(self, chunk: np.ndarray) -> bool:
        """Queue uplink audio; False when recognition has fallen too far behind."""
        if (self.queued + len(chunk)) / RATE > UPLINK_LIMIT_S:
            return False
        self.queued += len(chunk)
        self.frames.put_nowait(chunk)
        return True

    async def hold(self, turn_id: str, target: dict):
        """The talk control was pressed: hold the open turn, or open one, until `release`."""
        self.target = target
        if self.turn is None:
            self.endpoint.hold()
            await self._open(self.now - int(PRE_ROLL_S * RATE), over_reply=False, turn_id=turn_id)
        else:
            if self.endpoint.state == "ending":
                await self._reopen()
            self.endpoint.hold()
        self.turn.manual = True
        await self.session.send(type="turn", turn=self.turn.id, manual=True)

    async def release(self, turn_id: str):
        if self.turn is not None and self.turn.id == turn_id:
            if self.endpoint.state == "turn":
                await self._end()
            self.endpoint.release()
            self._settle()

    async def discard(self, turn_id: str):
        if self.turn is not None and self.turn.id == turn_id:
            await self._drop()

    def retarget(self, target: dict):
        self.target = target

    async def close(self):
        """Stop listening. A turn still being heard is dropped; one being transcribed finishes."""
        self.job.cancel()
        await asyncio.gather(self.job, return_exceptions=True)
        if self.turn is not None and self.endpoint.state == "ending":
            self.endpoint.reset()
            self._settle()
        elif self.turn is not None:
            await self._drop()

    async def stop(self):
        """The socket closed: drop everything."""
        await self.close()
        for job in list(self.concluding):
            job.cancel()
        await asyncio.gather(*self.concluding, return_exceptions=True)

    # The listening loop

    @property
    def now(self) -> int:
        return self.base + len(self.audio)

    async def _run(self):
        while True:
            try:
                chunk = await asyncio.wait_for(self.frames.get(), timeout=1.0)
            except TimeoutError:
                chunk = None
            if chunk is not None:
                self.queued -= len(chunk)
                self.audio.append(chunk)
                await self._detect()
                await self._feed()
                self._trim()
                if self.turn is not None and self.now - self.turn.start > TURN_LIMIT_S * RATE:
                    await self.session.error("too_long", f"turns are limited to {TURN_LIMIT_S / 60:.0f} minutes",
                                             self.turn.id)
                    await self._drop()
            if self.session.playing:
                self.last_active = time.monotonic()  # listening to a reply isn't idle
            if self.turn is None and not self.concluding and time.monotonic() - self.last_active > IDLE_S:
                log.info("conversation on session %s closed after %.0f s without a turn", self.session.id, IDLE_S)
                asyncio.ensure_future(self.session.close_conversation("idle"))
                return

    async def _detect(self):
        size = self.vad.chunk
        end = self.vad_done + (self.now - self.vad_done) // size * size
        if end <= self.vad_done:
            return
        audio = self._view(self.vad_done, end)
        probabilities = await self.speech.run("vad", _detect, self.vad, audio, size)
        for probability in probabilities:
            self.vad_done += size
            over_reply = self.session.playing
            event = self.endpoint.frame(probability >= SPEECH, over_reply)
            if event == "start":
                begin = self.vad_done - self.endpoint.start_back * size - int(PRE_ROLL_S * RATE)
                await self._open(begin, over_reply)
            elif event == "check":
                pause = self.endpoint.pause
                await self._feed()
                score = await self.speech.run("turn", self.speech.turn, self._view(self.turn.start, self.vad_done))
                if self.endpoint.checked(score, pause) == "end":
                    await self._end()
            elif event == "end":
                await self._end()
            elif event == "resume":
                await self._reopen()
            elif event == "settled":
                self._settle()

    async def _feed(self):
        if self.recognition is not None and self.endpoint.state == "turn" and self.fed < self.now:
            await self.recognition.add(self._view(self.fed, self.now))
            self.fed = self.now

    # Turns

    async def _open(self, begin: int, over_reply: bool, turn_id: str | None = None):
        from .session import Turn

        self.count += 1
        turn = Turn(turn_id or f"s{self.count}", self.target.get("task_id"), self.target.get("project_id"),
                    self.target.get("model"), self.target.get("effort"))
        turn.start = max(begin, self.base)
        turn.over_reply = over_reply
        log.info("conversation turn %s opened%s", turn.id,
                 f", over a reply with {self.session.playing_until - time.monotonic():.1f} s left: barge-in"
                 if over_reply else " (no reply playing)" if turn_id is None else ", held by the talk control")
        self.turn = turn
        self.last_active = time.monotonic()
        if over_reply:
            # Stop the reply, and the rest of its turn: an agent often says more than one thing.
            self.session.hub.hush(self.session, self.session.playing_task)
            await self.session.interrupt(barge_in=True)
        self._listen(turn)
        await self.session.begin(turn, announce=turn_id is None)

    def _listen(self, turn: Turn):
        async def caption(caption: Caption):
            if self.turn is turn:
                await self.session.send(type="caption", turn=turn.id, committed=caption.committed,
                                        provisional=caption.provisional)

        self.recognition = Recognition(self.speech, caption)
        self.fed = turn.start

    async def _end(self):
        """The endpointer or the user ended the turn: run the final pass while the grace runs."""
        await self._feed()
        await self.recognition.stop()
        self.final = asyncio.ensure_future(self.recognition.finish())
        await self.session.set_state("transcribing", self.turn.id)

    async def _reopen(self):
        """Speech came back within the grace: the same turn carries on, from its start."""
        self.final.cancel()
        self.recognition.cancel()
        await asyncio.gather(self.final, return_exceptions=True)
        self.final = None
        self._listen(self.turn)
        await self._feed()
        await self.session.set_state("listening", self.turn.id)

    def _settle(self):
        turn, final = self.turn, self.final
        self.turn = self.recognition = self.final = None
        self.last_active = time.monotonic()
        job = asyncio.ensure_future(self._conclude(turn, final))
        self.concluding.add(job)
        job.add_done_callback(self.concluding.discard)

    async def _drop(self):
        turn = self.turn
        if self.final is not None:
            self.final.cancel()
        if self.recognition is not None:
            self.recognition.cancel()
        self.turn = self.recognition = self.final = None
        self.endpoint.reset()
        await self.session.close_turn(turn)

    async def _conclude(self, turn: Turn, final: asyncio.Future):
        try:
            text = await final
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.exception("recognition failed")
            await self.session.error("stt_unavailable", f"recognition failed: {error}", turn.id)
            await self.session.close_turn(turn)
            return
        result = await self.deliver(turn, text)
        log.info("conversation turn %s: %s", turn.id, result["outcome"])
        await self.session.send(type="final", turn=turn.id, text=text, **result)
        await self.session.close_turn(turn)

    async def deliver(self, turn: Turn, text: str) -> dict:
        """The checks a conversation adds before a transcript is acted on (m7-sessions, Barge-in)."""
        session = self.session
        words = [w.lower() for w in WORD.findall(text)]
        if not words or " ".join(words) in FILLER:
            return {"outcome": "empty"}
        if turn.over_reply and session.echoes(words):
            return {"outcome": "empty"}  # Sennet heard itself
        if turn.over_reply and not turn.manual \
                and (len(words) < 2 or " ".join(words) in HOLD):
            return {"outcome": "interrupted"}  # it only stopped the reply
        return await session.hub.deliver(session, turn, text)

    # Audio kept

    def _view(self, start: int, end: int) -> np.ndarray:
        return self.audio.view(start - self.base, end - self.base)

    def _trim(self):
        """Keep the open turn's audio, and otherwise only enough for the next pre-roll."""
        keep_from = self.turn.start if self.turn is not None else self.now - int(KEEP_S * RATE)
        keep_from = min(keep_from, self.vad_done)
        if keep_from - self.base < 4 * RATE:
            return
        kept = AudioBuffer()
        kept.append(self._view(keep_from, self.now))
        self.audio, self.base = kept, keep_from


def _detect(vad, audio: np.ndarray, size: int) -> list[float]:
    return [vad(audio[i:i + size]) for i in range(0, len(audio), size)]

