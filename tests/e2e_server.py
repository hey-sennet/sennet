"""Start Agent Talk with the fake agents for browser tests: python tests/e2e_server.py PORT HOME.

With AGENT_TALK_FAKE_SPEECH set, speech uses the test fakes and hears that text. The browsers
are paired already: AGENT_TALK_E2E_TOKEN is a device's token, which they hold as its cookie.
"""
import hashlib
import os
import shutil
import sys
from pathlib import Path

import uvicorn

from agent_talk.config import Paths, Settings
from agent_talk.db import Database
from agent_talk.server import create_app
from agent_talk.speech import Speech
from agent_talk.speech.fakes import FakeRecognizer, FakeSynthesizer, FakeVAD

port, home = int(sys.argv[1]), Path(sys.argv[2])
shutil.rmtree(home, ignore_errors=True)
(home / "repo").mkdir(parents=True)
settings = Settings()
settings.agents.commands["codex"] = [sys.executable, str(Path(__file__).with_name("fake_codex.py"))]
settings.agents.commands["claude"] = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]
settings.server.port = port  # where the fake Claude asks its approvals
db = Database(Paths(home, home).database)
db.add_device("Playwright", hashlib.sha256(os.environ["AGENT_TALK_E2E_TOKEN"].encode()).hexdigest(), "the tests")
db.close()
speech = None
if heard := os.environ.get("AGENT_TALK_FAKE_SPEECH"):
    speech = Speech.with_engines(FakeVAD(), FakeRecognizer(heard), FakeSynthesizer())
uvicorn.run(create_app(settings, Paths(home, home), speech=speech), host="127.0.0.1", port=port, log_level="warning")
