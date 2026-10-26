"""Whether agents write a <spoken> part on real work, and what makes Codex write one (issue #26).

spoken.py found Codex wrote none on explanations without tools. This sends requests that do
work and report it (a fix, a question about the code, a small feature) to a scratch project
reset before each turn, and tries Codex with firmer instructions and with a reminder sent with
each message. Not product code, and it never touches the running service. Each turn is a real
request on the subscription.

    .venv/bin/python spikes/tokens/work.py SCRATCH_DIR [--runs 2] [--arms claude,codex,codex-firm,codex-reminder] [--pause 30]
    .venv/bin/python spikes/tokens/work.py SCRATCH_DIR --summary

Writes one JSON line per turn, the reply included, to SCRATCH_DIR/work.jsonl.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import socket
import statistics
import subprocess
import sys
from pathlib import Path

import uvicorn

import spoken  # patches the adapters to record usage
from sennet.agents import codex, registry
from sennet.agents.base import AgentEvent, TurnPolicy
from sennet.agents.callbacks import CallbackServer
from sennet.voice import summary as voice_summary
from sennet.voice.speakable import for_speech, spoken_part, without_spoken

PROMPTS = [
    "The tests in this project fail. Find out why and fix it.",
    "How does the discount code handling in this project work, and are there cases it gets wrong?",
    "Add a function that totals an order including 20% tax, with a test, and run the tests.",
]
POLICY = TurnPolicy(mode="auto", access="workspace")

# The firmer wording tried for Codex, now part of its instructions (codex.SPOKEN_REMINDER).
FIRM = codex.SPOKEN_REMINDER

REMINDER = ("\n\n(Your final message is read aloud: if it's longer than three sentences, begin it "
            "with <spoken></spoken>.)")

FILES = {
    "cart.py": '''\
CODES = {"SAVE10": 0.10, "HALF": 0.50}


def subtotal(items):
    """items: (name, price, quantity) tuples."""
    return sum(price * quantity for _, price, quantity in items)


def discount(total, code):
    rate = CODES.get(code.upper(), 0)
    return round(total * rate, 2)


def total(items, code=None):
    amount = subtotal(items)
    if code:
        amount -= discount(amount, code)
        amount -= discount(amount, code)
    return round(amount, 2)
''',
    "test_cart.py": '''\
import unittest

from cart import discount, subtotal, total

ITEMS = [("tea", 4.0, 2), ("cake", 2.5, 1)]


class CartTest(unittest.TestCase):
    def test_subtotal(self):
        self.assertEqual(subtotal(ITEMS), 10.5)

    def test_discount(self):
        self.assertEqual(discount(10.0, "save10"), 1.0)

    def test_total_with_code(self):
        self.assertEqual(total(ITEMS, "SAVE10"), 9.45)


if __name__ == "__main__":
    unittest.main()
''',
    "README.md": "A shopping cart. Run the tests with `python3 -m unittest`.\n",
}


def reset(cwd: Path):
    if not (cwd / ".git").exists():
        cwd.mkdir(parents=True, exist_ok=True)
        for name, text in FILES.items():
            (cwd / name).write_text(text)
        git = ["git", "-C", str(cwd), "-c", "user.name=spike", "-c", "user.email=spike@example.com"]
        subprocess.run(["git", "init", "-q", str(cwd)], check=True)
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "commit", "-qm", "Cart"], check=True)
    subprocess.run(["git", "-C", str(cwd), "reset", "-q", "--hard"], check=True)
    subprocess.run(["git", "-C", str(cwd), "clean", "-qfdx"], check=True)


def use_arm(arm: str):
    base = spoken.INSTRUCTIONS["codex"].removesuffix(FIRM)
    codex.INSTRUCTIONS = base + (FIRM if arm == "codex-firm" else "")


class TurnFailed(Exception):
    pass


async def attempt(agent, cwd: Path, text: str, tries: int = 4) -> dict:
    """A turn, tried again after a pause when it fails: Codex answers 429 to turns in quick succession."""
    for n in range(tries):
        try:
            return await turn(agent, cwd, text)
        except TurnFailed as e:
            print(f"failed ({e}), try {n + 1} of {tries}", flush=True)
            if n == tries - 1:
                raise
            reset(cwd)
            await asyncio.sleep(120 * (n + 1))


async def turn(agent, cwd: Path, text: str) -> dict:
    spoken.usage.clear()
    done = asyncio.Event()
    messages: list[str] = []
    ended: dict = {}

    async def on_event(event: AgentEvent):
        if event.kind == "message":
            messages.append(event.data["text"])
        if event.kind == "turn_completed":
            ended.update(event.data)
            done.set()

    async def decline(_):
        return "decline"

    session = await agent.open(cwd, None, POLICY, on_event, decline)
    try:
        await session.send(text)
        await asyncio.wait_for(done.wait(), 900)
        await asyncio.sleep(0.5)
    finally:
        await session.close()
    if ended.get("status") != "completed":
        raise TurnFailed(ended.get("error") or ended.get("status"))
    reply = messages[-1] if messages else ""
    part = spoken_part(reply) or ""
    return {**spoken.usage, "messages": len(messages), "reply_chars": len(reply), "spoken_chars": len(part),
            "tag_elsewhere": not part and "<spoken>" in reply, "reply": reply}


def needs_spoken(reply: str) -> bool:
    """Long enough that, without a spoken part, the voice cuts it to its opening."""
    return not voice_summary.is_short(for_speech(without_spoken(reply)))


def summary(path: Path):
    rows: dict[str, list[dict]] = {}
    for line in path.read_text().splitlines():
        r = json.loads(line)
        if "error" in r:
            continue
        rows.setdefault(r["arm"], []).append(r)
    print(f"{'':16}{'n':>3}{'spoken':>8}{'long':>6}{'long+spoken':>13}{'reply ch':>10}{'spoken ch':>11}{'output':>8}")
    for arm, records in sorted(rows.items()):
        written = [r for r in records if r["spoken_chars"]]
        long = [r for r in records if needs_spoken(r.get("reply", ""))]
        def median(key, of):
            values = [r[key] for r in of if r.get(key) is not None]
            return f"{statistics.median(values):.0f}" if values else "—"
        print(f"{arm:16}{len(records):>3}{f'{len(written)}/{len(records)}':>8}{len(long):>6}"
              f"{f'{sum(1 for r in long if r in written)}/{len(long)}':>13}{median('reply_chars', records):>10}"
              f"{median('spoken_chars', written):>11}{median('output', records):>8}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scratch", type=Path)
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--arms", default="claude,codex,codex-firm,codex-reminder")
    parser.add_argument("--pause", type=float, default=0, help="seconds between turns")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    path = args.scratch / "work.jsonl"
    if args.summary:
        return summary(path)
    cwd = args.scratch / "project"
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    callbacks = CallbackServer(f"http://127.0.0.1:{port}")
    server = uvicorn.Server(uvicorn.Config(spoken.callback_app(callbacks), host="127.0.0.1", port=port,
                                           log_level="warning"))
    serving = asyncio.create_task(server.serve())
    agents = registry.build({}, callbacks)
    try:
        with path.open("a") as out:
            for run in range(args.runs):
                for index, prompt in enumerate(PROMPTS):
                    for arm in args.arms.split(","):  # interleaved, so drift hits every arm alike
                        reset(cwd)
                        use_arm(arm)
                        text = prompt + (REMINDER if arm == "codex-reminder" else "")
                        try:
                            record = await attempt(agents[arm.split("-")[0]], cwd, text)
                        except Exception as e:  # one failed turn shouldn't lose the rest
                            record = {"error": repr(e), "spoken_chars": 0, "reply_chars": 0}
                        record = {"arm": arm, "prompt": index, "run": run, **record}
                        out.write(json.dumps(record) + "\n")
                        out.flush()
                        print({k: v for k, v in record.items() if k != "reply"}, flush=True)
                        await asyncio.sleep(args.pause)
    finally:
        server.should_exit = True
        await serving
    summary(path)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
