"""Start Agent Talk with the fake Codex for browser tests: python tests/e2e_server.py PORT HOME.

With AGENT_TALK_FAKE_SPEECH set, speech uses the test fakes and hears that text.
"""
import os
import shutil
import sys
from pathlib import Path

import uvicorn

from agent_talk.config import Paths, Settings
from agent_talk.server import create_app
from agent_talk.speech import Speech
from agent_talk.speech.fakes import FakeRecognizer, FakeSynthesizer, FakeVAD

port, home = int(sys.argv[1]), Path(sys.argv[2])
shutil.rmtree(home, ignore_errors=True)
(home / "repo").mkdir(parents=True)
settings = Settings()
settings.agents.commands["codex"] = [sys.executable, str(Path(__file__).with_name("fake_codex.py"))]
speech = None
if heard := os.environ.get("AGENT_TALK_FAKE_SPEECH"):
    speech = Speech.with_engines(FakeVAD(), FakeRecognizer(heard), FakeSynthesizer())
uvicorn.run(create_app(settings, Paths(home, home), speech=speech), host="127.0.0.1", port=port, log_level="warning")
