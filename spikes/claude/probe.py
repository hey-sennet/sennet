"""Drive one `claude -p` stream-json process through a script of steps and record it.

The permission prompt tool is a minimal MCP server over HTTP on loopback, as the adapter
would serve it. Every line in and out, and every tool call, goes to the transcript.

    python probe.py SCENARIO PROJECT_DIR [--model haiku]
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
TOOL = {
    "name": "approve",
    "description": "Ask the user whether a tool call may run.",
    "inputSchema": {"type": "object", "properties": {
        "tool_name": {"type": "string"}, "input": {"type": "object"}, "tool_use_id": {"type": "string"}},
        "required": ["tool_name", "input"]},
}


class Recorder:
    def __init__(self, path: Path):
        self.file = path.open("w")
        self.lock = threading.Lock()
        self.start = time.monotonic()
        self.lines: list[dict] = []
        self.changed = threading.Condition(self.lock)

    def write(self, direction: str, message, pid: int | None = None):
        with self.lock:
            entry = {"t": round(time.monotonic() - self.start, 3), "dir": direction, "msg": message}
            if pid is not None:
                entry["pid"] = pid
            self.file.write(json.dumps(entry) + "\n")
            self.file.flush()
            self.lines.append(entry)
            self.changed.notify_all()

    def wait_for(self, predicate, timeout: float) -> dict | None:
        deadline = time.monotonic() + timeout
        seen = 0
        with self.lock:
            while True:
                for entry in self.lines[seen:]:
                    if predicate(entry):
                        return entry
                seen = len(self.lines)
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self.changed.wait(left)


def mcp_server(recorder: Recorder, policy):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(405)
            self.end_headers()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            recorder.write("mcp_in", {"path": self.path, "body": body})
            method, request_id = body.get("method"), body.get("id")
            if request_id is None:
                self.send_response(202)
                self.end_headers()
                return
            if method == "initialize":
                result = {"protocolVersion": body["params"].get("protocolVersion", "2025-06-18"),
                          "capabilities": {"tools": {}}, "serverInfo": {"name": "sennet", "version": "0"}}
            elif method == "tools/list":
                result = {"tools": [TOOL]}
            elif method == "tools/call":
                args = body["params"]["arguments"]
                decision = policy(args)
                result = {"content": [{"type": "text", "text": json.dumps(decision)}]}
            else:
                result = {}
            reply = json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}).encode()
            recorder.write("mcp_out", json.loads(reply))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class Claude:
    def __init__(self, recorder: Recorder, cwd: Path, port: int, model: str, session: str, resume: bool,
                 mode: str = "default", extra: list[str] = ()):
        server = {"type": "http", "url": f"http://127.0.0.1:{port}/mcp/secret"}
        if timeout := os.environ.get("MCP_SERVER_TIMEOUT"):
            server["timeout"] = int(timeout)  # ms; HTTP servers otherwise time out each request at 60 s
        mcp = {"mcpServers": {"sennet": server}}
        command = ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                   "--include-partial-messages", "--model", model, "--setting-sources", "user",
                   "--strict-mcp-config", "--mcp-config", json.dumps(mcp),
                   "--permission-prompt-tool", "mcp__sennet__approve", "--permission-mode", mode,
                   "--disallowedTools", "AskUserQuestion",
                   *(["--resume", session] if resume else ["--session-id", session]), *extra]
        recorder.write("argv", command)
        self.recorder = recorder
        self.process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, env={**os.environ})
        self.pid = self.process.pid
        self.results = 0
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._stderr, daemon=True).start()

    def _read(self):
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                message = {"unparsed": line}
            if message.get("type") == "stream_event" and message["event"].get("type") != "content_block_delta":
                message = {"type": "stream_event", "event": {"type": message["event"]["type"]}}  # keep it short
            self.recorder.write("out", message, self.pid)
        self.recorder.write("exit", self.process.wait(), self.pid)

    def _stderr(self):
        for line in self.process.stderr:
            self.recorder.write("stderr", line.rstrip(), self.pid)

    def send(self, text: str):
        message = {"type": "user", "message": {"role": "user", "content": text}}
        self.recorder.write("in", message)
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def control(self, request: dict):
        message = {"type": "control_request", "request_id": uuid.uuid4().hex, "request": request}
        self.recorder.write("in", message)
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def next_result(self, timeout=180):
        """This process's next `result` message, or its exit, or None after `timeout`."""
        count = 0

        def match(e):
            nonlocal count
            if e.get("pid") != self.pid:
                return False
            if e["dir"] == "exit":
                return True
            if e["dir"] == "out" and e["msg"].get("type") == "result":
                count += 1
                return count > self.results
            return False

        entry = self.recorder.wait_for(match, timeout)
        if entry and entry["dir"] == "out":
            self.results += 1
        return entry

    def close(self, timeout=30):
        self.process.stdin.close()
        try:
            self.process.wait(timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()


def allow(args):
    return {"behavior": "allow", "updatedInput": args["input"]}


def deny(args):
    return {"behavior": "deny", "message": "The user declined this."}


def delayed(seconds, then=allow):
    def policy(args):
        time.sleep(seconds)
        return then(args)
    return policy


SESSION = "22222222-2222-4222-8222-222222222222"


def run(scenario: str, cwd: Path, model: str):
    recorder = Recorder(HERE / "transcripts" / f"{scenario}.jsonl")
    policies = {"deny": deny, "slow_approval": delayed(int(os.environ.get("DELAY", "90")))}
    server = mcp_server(recorder, policies.get(scenario, allow))
    port = server.server_address[1]
    session = str(uuid.uuid4())

    def claude(**kw):
        return Claude(recorder, cwd, port, model, kw.pop("session", session), kw.pop("resume", False), **kw)

    if scenario == "command":
        c = claude()
        c.send("Run `ls -la` and then `false`, then tell me in one sentence what you saw.")
        c.next_result()
        c.close()
    elif scenario == "edit":
        c = claude()
        c.send("In app.py change the greeting to 'hello world'. Then create notes.txt containing 'done'. "
               "Then plan and track two todo items: 'check app' and 'check notes' and mark them done. Be brief.")
        c.next_result()
        c.close()
    elif scenario in ("deny", "slow_approval"):
        c = claude()
        c.send("Run `touch denied.txt` with the Bash tool, then tell me whether it worked.")
        c.next_result(timeout=300)
        c.close()
    elif scenario == "multiturn":
        c = claude()
        c.send("Remember the number 41. Reply with OK.")
        c.next_result()
        c.send("What number did I ask you to remember? Reply with just the number.")
        c.next_result()
        c.close()
        r = claude(resume=True, session=session)
        r.send("Add one to that number. Reply with just the number.")
        r.next_result()
        r.close()
    elif scenario in ("sigint", "control_interrupt"):
        c = claude()
        c.send("Run `python3 -c \"import time; time.sleep(40); print('finished')\"` with the Bash tool "
               "in the foreground, then say done.")
        recorder.wait_for(lambda e: e["dir"] == "mcp_in" and e["msg"]["body"].get("method") == "tools/call", 60) \
            or recorder.wait_for(lambda e: False, 8)
        time.sleep(3)
        if scenario == "sigint":
            recorder.write("signal", "SIGINT")
            c.process.send_signal(signal.SIGINT)
        else:
            c.control({"subtype": "interrupt"})
        c.next_result(timeout=60)
        time.sleep(2)
        if c.process.poll() is None:
            c.send("Reply with the word still-alive.")
            c.next_result(timeout=60)
        c.close()
        r = claude(resume=True, session=session)
        r.send("What was the last command you tried to run? Reply in one line.")
        r.next_result()
        r.close()
    elif scenario == "background":
        c = claude()
        c.send("Start `python3 -c \"import time; time.sleep(25); print('finished')\"` as a background command, "
               "tell me you've started it, and when it finishes tell me what it printed.")
        c.next_result()
        c.send("While that runs: what is 2 + 2? Reply with just the number.")
        c.next_result()
        c.next_result(timeout=120)  # the turn Claude starts itself when the command finishes
        time.sleep(5)
        c.close()
    elif scenario == "scoped_never":
        outside = Path.home() / ".cache" / "sennet-probe"
        outside.mkdir(parents=True, exist_ok=True)
        c = claude(mode="dontAsk", extra=["--allowedTools", f"Edit(/{cwd}/**)", f"Write(/{cwd}/**)"])
        c.send("Do these one at a time and report each result in one line: "
               "1. With the Write tool, create notes.txt containing 'hi'. "
               f"2. With the Write tool, create {outside}/written.txt containing 'hi'. "
               "3. With the Edit tool, change 'hi' to 'hello' in app.py. "
               "4. With Bash, run `touch inside.txt`.")
        c.next_result(timeout=300)
        c.close()
        recorder.write("outside_files", sorted(f.name for f in outside.iterdir()))
        recorder.write("inside_files", sorted(f.name for f in cwd.iterdir()))
    elif scenario in ("sandbox_auto", "sandbox_never"):
        outside = Path.home() / ".cache" / "sennet-probe"
        outside.mkdir(parents=True, exist_ok=True)
        sandbox = {"sandbox": {"enabled": True, "failIfUnavailable": True}}
        mode, extra = ("acceptEdits", []) if scenario == "sandbox_auto" else \
            ("dontAsk", ["--allowedTools", "Edit", "Write"])
        c = claude(mode=mode, extra=["--settings", json.dumps(sandbox), *extra])
        c.send("Do these one at a time and report each result in one line: "
               "1. With Bash, run `touch inside.txt`. "
               f"2. With Bash, run `touch {outside}/outside.txt`. "
               "3. With Bash, run `curl -sS -m 10 -o /dev/null -w '%{http_code}' https://example.com`. "
               "4. With the Write tool, create notes.txt containing 'hi'. "
               f"5. With the Write tool, create {outside}/written.txt containing 'hi'.")
        c.next_result(timeout=300)
        c.close()
        recorder.write("outside_files", sorted(f.name for f in outside.iterdir()))
    else:
        sys.exit(f"unknown scenario {scenario}")
    time.sleep(1)
    server.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario")
    parser.add_argument("cwd", type=Path)
    parser.add_argument("--model", default="haiku")
    args = parser.parse_args()
    run(args.scenario, args.cwd.resolve(), args.model)
