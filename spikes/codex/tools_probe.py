"""Record the items `codex app-server` sends for tool calls other than commands and edits, for #20.

Gives Codex one MCP server (echo_mcp.py) and live web search, and asks it to use both.
Every line in and out goes to transcripts/tools.jsonl.

    python tools_probe.py PROJECT_DIR
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from probe import HERE, Probe


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    args = parser.parse_args()
    (HERE / "transcripts").mkdir(exist_ok=True)
    server = HERE / "echo_mcp.py"
    command = ["codex", "app-server", "-c", f'mcp_servers.glossary.command="{sys.executable}"',
               "-c", f'mcp_servers.glossary.args=["{server}"]', "-c", 'web_search="live"']
    probe = Probe(args.project.resolve(), False, HERE / "transcripts" / "tools.jsonl", command)
    thread = probe.request("thread/start", {"cwd": str(args.project.resolve()), "approvalPolicy": "never",
                                            "sandbox": "read-only"})["result"]["thread"]["id"]
    probe.request("turn/start", {"threadId": thread, "effort": "low", "input": [{"type": "text", "text_elements": [],
        "text": "Use the glossary tool's look_up on the word 'sennet', then search the web for "
                "'Sennet board game', and reply with one sentence on each."}]})
    probe.wait_turns(1, 180)
    probe.process.terminate()


if __name__ == "__main__":
    main()
