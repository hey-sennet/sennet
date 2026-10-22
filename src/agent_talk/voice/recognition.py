"""One turn's recognition: voice activity, live captions and the final transcript.

Rules from HLD §6.2. Captions re-decode a sliding window about every 250 ms and
are a preview only. The final transcript is always a full pass: one pass over the
whole turn on GPU, or, on CPU, one pass per segment, where each segment that ends
at a pause is decoded in the background while the user keeps talking.
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

import numpy as np

from ..speech import RATE, Speech
from ..speech.base import AudioBuffer
from ..speech.stt import Caption, StreamingTranscriber
from ..speech.worker import CAPTION, FINAL, SEGMENT

SPEECH_PROBABILITY = 0.5
PAUSE_S = 0.5             # silence that ends a segment
MIN_SEGMENT_S = 1.0       # speech needed before a segment may end
FULL_PASS_LIMIT_S = 60.0  # longer audio is decoded in segments even on GPU
CAPTION_PERIOD_S = 0.25
CAPTION_HEADROOM = 1.5    # a caption decode may take at most 2/3 of the period


def caption_period(decode_s: float) -> float:
    """Slow decodes stretch the period, so weak CPUs update less often instead of falling behind."""
    return max(CAPTION_PERIOD_S, decode_s * CAPTION_HEADROOM)


class Recognition:
    def __init__(self, speech: Speech, on_caption: Callable[[Caption], Awaitable[None]]):
        self.speech = speech
        self.on_caption = on_caption
        self.audio = AudioBuffer()
        self.vad = speech.vad.stream()
        self.vad_done = 0          # samples the VAD has seen
        self.heard = False         # any speech at all in the turn
        self.speech_since_cut = 0  # samples of speech in the current segment
        self.silence = 0           # samples of silence since the last speech
        self.cuts = [0]            # segment boundaries, in samples
        self.segments: list[asyncio.Future] = []   # background full passes, one per closed segment
        self.captions = StreamingTranscriber(speech.stt)
        self.captioned_at = 0      # audio length at the last caption
        self.last_caption: tuple[str, str] | None = None
        self.caption_job = asyncio.ensure_future(self._caption_loop())

    async def add(self, chunk: np.ndarray):
        self.audio.append(chunk)
        size = self.vad.chunk
        end = self.vad_done + (len(self.audio) - self.vad_done) // size * size
        if end > self.vad_done:
            probabilities = await self.speech.run("vad", _detect, self.vad, self.audio.view(self.vad_done, end), size)
            for probability in probabilities:
                self.vad_done += size
                self._mark(probability >= SPEECH_PROBABILITY, size)

    async def finish(self) -> str:
        """The final transcript, or "" when the turn held no speech."""
        await self.stop()
        if not self.heard:
            return ""
        end = len(self.audio)
        if not self.speech.incremental and end / RATE <= FULL_PASS_LIMIT_S:
            return await self._decode(0, end, FINAL)
        if not self.speech.incremental:
            self._cut_long(end)
            for start, stop in zip(self.cuts, self.cuts[1:]):
                self.segments.append(asyncio.ensure_future(self._decode(start, stop, FINAL)))
        last = self._decode(self.cuts[-1], end, FINAL) if end - self.cuts[-1] > 0 else _done("")
        texts = await asyncio.gather(*self.segments, last)
        return " ".join(t for t in texts if t).strip()

    async def stop(self):
        """Stop captions and any background passes not yet needed (cancel, or the socket closed)."""
        self.caption_job.cancel()
        await asyncio.gather(self.caption_job, return_exceptions=True)

    def cancel(self):
        self.caption_job.cancel()
        for segment in self.segments:
            segment.cancel()

    # Voice activity and segments

    def _mark(self, speech: bool, size: int):
        if speech:
            self.heard = True
            self.speech_since_cut += size
            self.silence = 0
            return
        self.silence += size
        if (self.speech.incremental and self.silence >= PAUSE_S * RATE
                and self.speech_since_cut >= MIN_SEGMENT_S * RATE):
            # Cut in the middle of the pause, so neither side loses a word's tail.
            cut = self.vad_done - self.silence // 2
            start = self.cuts[-1]
            self.cuts.append(cut)
            self.segments.append(asyncio.ensure_future(self._decode(start, cut, SEGMENT)))
            self.speech_since_cut = 0

    def _cut_long(self, end: int):
        """Split audio that is too long for one pass into pieces under the limit."""
        limit = int(FULL_PASS_LIMIT_S * RATE)
        while end - self.cuts[-1] > limit:
            self.cuts.append(self.cuts[-1] + limit)

    async def _decode(self, start: int, end: int, priority: int) -> str:
        return await self.speech.run("stt", self.speech.stt.text, self.audio.view(start, end), priority=priority)

    # Captions

    async def _caption_loop(self):
        period = CAPTION_PERIOD_S
        while True:
            await asyncio.sleep(period)
            if len(self.audio) == self.captioned_at:
                continue
            self.captioned_at = len(self.audio)
            started = time.perf_counter()
            caption = await self.speech.run("stt", self.captions.tick, self.audio.view(), priority=CAPTION)
            decode_s = time.perf_counter() - started
            period = max(caption_period(decode_s) - decode_s, 0.0)
            text = (caption.committed, caption.provisional)
            if text != self.last_caption and any(text):
                self.last_caption = text
                await self.on_caption(caption)


def _detect(vad, audio: np.ndarray, size: int) -> list[float]:
    return [vad(audio[i:i + size]) for i in range(0, len(audio), size)]


def _done(value) -> asyncio.Future:
    future = asyncio.get_running_loop().create_future()
    future.set_result(value)
    return future
