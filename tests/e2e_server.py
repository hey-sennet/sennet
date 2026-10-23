"""Start Sennet with the fake agents for browser tests: python tests/e2e_server.py PORT HOME.

With SENNET_FAKE_SPEECH set, speech uses the test fakes and hears that text. The browsers
are paired already: SENNET_E2E_TOKEN is a device's token, which they hold as its cookie.
"""
import hashlib
import os
import shutil
import sys
from pathlib import Path

import uvicorn

from sennet.config import Paths, Settings
from sennet.db import Database
from sennet.server import create_app
from sennet.speech import Speech
from sennet.speech.fakes import FakeRecognizer, FakeSynthesizer, FakeTurn, FakeVAD

port, home = int(sys.argv[1]), Path(sys.argv[2])
shutil.rmtree(home, ignore_errors=True)
(home / "repo").mkdir(parents=True)
settings = Settings()
settings.agents.commands["codex"] = [sys.executable, str(Path(__file__).with_name("fake_codex.py"))]
settings.agents.commands["claude"] = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]
settings.server.port = port  # where the fake Claude asks its approvals
db = Database(Paths(home, home).database)
db.add_device("Playwright", hashlib.sha256(os.environ["SENNET_E2E_TOKEN"].encode()).hexdigest(), "the tests")
db.close()
speech = None
if heard := os.environ.get("SENNET_FAKE_SPEECH"):
    speech = Speech.with_engines(FakeVAD(), FakeRecognizer(heard), FakeSynthesizer(), turn=FakeTurn())
uvicorn.run(create_app(settings, Paths(home, home), speech=speech), host="127.0.0.1", port=port, log_level="warning")
