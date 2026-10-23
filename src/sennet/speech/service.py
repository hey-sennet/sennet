"""The speech engines one daemon shares between its voice sessions.

Engines load in the background at startup. Until then, or when models or the
runtime are missing, the status says so and the daemon still serves typed use.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import models
from .base import RATE, Recognizer, Synthesizer, VoiceActivityModel
from .runtime import resolve_device
from .worker import FINAL, Worker

log = logging.getLogger(__name__)

ROLES = ("stt", "tts")  # what clients are told about; VAD counts as part of STT


class Speech:
    def __init__(self):
        self.vad: VoiceActivityModel | None = None
        self.stt: Recognizer | None = None
        self.tts: Synthesizer | None = None
        # Finalize segments at pauses while the user talks (HLD §6.2): a CPU full pass
        # over a long utterance is too slow to leave until release.
        self.incremental = False
        self.status = {role: "loading" for role in ROLES}
        self.detail = {role: "" for role in ROLES}
        self.workers = {name: Worker(name) for name in ("vad", "stt", "tts")}
        self.listeners: set[Callable[[], Any]] = set()

    @classmethod
    def with_engines(cls, vad: VoiceActivityModel | None, stt: Recognizer | None, tts: Synthesizer | None,
                     incremental: bool = False) -> Speech:
        """Engines that are already loaded, such as the test fakes."""
        speech = cls()
        speech.vad, speech.stt, speech.tts = vad, stt, tts
        speech.incremental = incremental
        speech._set("stt", "ready" if vad and stt else "unavailable", "" if vad and stt else "not configured")
        speech._set("tts", "ready" if tts else "unavailable", "" if tts else "not configured")
        return speech

    @classmethod
    def unavailable(cls, detail: str) -> Speech:
        speech = cls()
        for role in ROLES:
            speech._set(role, "unavailable", detail)
        return speech

    async def load(self, models_root: Path, device: str, threads: int, voice: str):
        """Load the real engines, each in its own worker thread."""
        try:
            device = await self.workers["stt"].run(resolve_device, device)
        except Exception as error:
            for role in ROLES:
                self._set(role, "unavailable", str(error))
            return
        self.incremental = device == "cpu"
        chosen = models.MODELS[device]
        missing = [m.id for m in chosen.values() if not models.present(models_root, m)]
        if missing:
            detail = f"models missing ({', '.join(missing)}); run `sennet setup`"
            for role in ROLES:
                self._set(role, "unavailable", detail)
            return

        from .stt import Parakeet
        from .tts import Kokoro
        from .vad import SileroVAD

        async def load_stt():
            self.vad = await self.workers["vad"].run(SileroVAD, models.path(models_root, chosen["vad"]))
            self.stt = await self.workers["stt"].run(Parakeet, models.path(models_root, chosen["stt"]), device, threads)
            # The first decode of each shape is slow (cuDNN tuning, allocation); take it now.
            await self.workers["stt"].run(self.stt.words, np.zeros(RATE, dtype=np.float32))

        async def load_tts():
            self.tts = await self.workers["tts"].run(Kokoro, models.path(models_root, chosen["tts"]),
                                                     device, threads, voice)
            await self.workers["tts"].run(self.tts.synthesize, "Ready.")

        async def load_role(role, loader):
            try:
                await loader()
            except Exception as error:
                log.exception("could not load %s", role)
                self._set(role, "unavailable", str(error))
            else:
                log.info("%s ready on %s", role, device)
                self._set(role, "ready", device)

        await asyncio.gather(load_role("stt", load_stt), load_role("tts", load_tts))

    async def run(self, worker: str, fn: Callable[..., Any], *args, priority: int = FINAL) -> Any:
        return await self.workers[worker].run(fn, *args, priority=priority)

    def snapshot(self) -> dict:
        return dict(self.status)

    def close(self):
        for worker in self.workers.values():
            worker.close()
        # Release the models (and on GPU, their memory) now rather than whenever the
        # last reference goes.
        self.vad = self.stt = self.tts = None

    def _set(self, role: str, status: str, detail: str):
        self.status[role], self.detail[role] = status, detail
        for listener in list(self.listeners):
            listener()
