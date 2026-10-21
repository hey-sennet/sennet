"""Start Agent Talk with the fake Codex for browser tests: python tests/e2e_server.py PORT HOME."""
import shutil
import sys
from pathlib import Path

import uvicorn

from agent_talk.config import Paths, Settings
from agent_talk.server import create_app

port, home = int(sys.argv[1]), Path(sys.argv[2])
shutil.rmtree(home, ignore_errors=True)
(home / "repo").mkdir(parents=True)
settings = Settings()
settings.agents.codex_command = [sys.executable, str(Path(__file__).with_name("fake_codex.py"))]
uvicorn.run(create_app(settings, Paths(home, home)), host="127.0.0.1", port=port, log_level="warning")
