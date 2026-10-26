"""Drive one `codex app-server` through the background scenario and record it, for #3.

Starts a thread in PROJECT_DIR, asks Codex to start a long command in the background and
end its turn, asks something else while it runs, then listens for whatever comes after.
Approvals are accepted. Every line in and out goes to transcripts/background.jsonl.

    python probe.py PROJECT_DIR [--experimental] [--listen SECONDS]
"""
from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import threading
import time
from pathlib import Path

HERE = Path(__file__).parent


class Probe:
    def __init__(self, cwd: Path, experimental: bool, transcript: Path, command: list[str] = ["codex", "app-server"]):
        self.process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, text=True, bufsize=1)
        self.file = transcript.open("w")
        self.start = time.monotonic()
        self.ids = itertools.count(1)
        self.responses: dict[int, dict] = {}
        self.turns_completed = 0
        self.changed = threading.Condition()
        threading.Thread(target=self._read, daemon=True).start()
        self.request("initialize", {"clientInfo": {"name": "sennet-probe", "title": "Sennet probe", "version": "0"},
                                    "capabilities": {"experimentalApi": experimental, "requestAttestation": False}})
        self.write({"method": "initialized", "params": {}})

    def log(self, direction: str, message: dict):
        self.file.write(json.dumps({"t": round(time.monotonic() - self.start, 3), "dir": direction, **message}) + "\n")
        self.file.flush()

    def write(self, message: dict):
        self.log("in", message)
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method: str, params: dict, timeout: float = 60) -> dict:
        request_id = next(self.ids)
        self.write({"id": request_id, "method": method, "params": params})
        with self.changed:
            self.changed.wait_for(lambda: request_id in self.responses, timeout)
        return self.responses.get(request_id, {})

    def wait_turns(self, count: int, timeout: float):
        with self.changed:
            self.changed.wait_for(lambda: self.turns_completed >= count, timeout)

    def _read(self):
        for line in self.process.stdout:
            message = json.loads(line)
            self.log("out", message)
            if "method" in message and "id" in message:  # a request from Codex: approve it
                decision = {"decision": "accept"} if "Approval" in message["method"] else {}
                self.write({"id": message["id"], "result": decision})
            with self.changed:
                if "id" in message and "method" not in message:
                    self.responses[message["id"]] = message
                if message.get("method") == "turn/completed":
                    self.turns_completed += 1
                self.changed.notify_all()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("--experimental", action="store_true")
    parser.add_argument("--listen", type=float, default=90)
    args = parser.parse_args()
    (HERE / "transcripts").mkdir(exist_ok=True)
    name = "background-experimental" if args.experimental else "background"
    probe = Probe(args.project.resolve(), args.experimental, HERE / "transcripts" / f"{name}.jsonl")
    thread = probe.request("thread/start", {"cwd": str(args.project.resolve()), "approvalPolicy": "never",
                                            "sandbox": "workspace-write"})["result"]["thread"]["id"]

    def turn(text: str):
        probe.request("turn/start", {"threadId": thread, "input": [{"type": "text", "text": text, "text_elements": []}],
                                     "effort": "low"})

    turn("Start `python3 -c \"import time; time.sleep(25); print('finished')\"` as a background process, "
         "tell me you've started it, and end your turn without waiting for it. When it finishes, tell me what it printed.")
    probe.wait_turns(1, 120)
    turn("While that runs: what is 2 + 2? Reply with just the number.")
    probe.wait_turns(2, 120)
    time.sleep(args.listen)  # anything Codex does by itself once the process exits
    probe.process.terminate()


if __name__ == "__main__":
    main()
