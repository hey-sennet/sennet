"""A scripted stand-in for `codex app-server`, speaking the same stdio JSON-RPC.

Turn behaviour is driven by the user's text:
  run:<command>   ask to run a command, then run it if approved
  edit:<path>     ask to edit a file, then apply it if approved
  hang            keep working until interrupted
  crash           exit mid-turn
Anything else gets a short reply. Every call is appended to $FAKE_CODEX_LOG.
"""
import asyncio
import itertools
import json
import os
import sys

ids = itertools.count(1000)
threads = itertools.count(1)
waiting: dict[int, asyncio.Future] = {}
interrupted = asyncio.Event()
turn_ids = itertools.count(1)


def log(entry: dict):
    if path := os.environ.get("FAKE_CODEX_LOG"):
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")


def send(message: dict):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def notify(method: str, **params):
    send({"method": method, "params": params})


async def ask(method: str, **params) -> dict:
    request_id = next(ids)
    future = waiting[request_id] = asyncio.get_running_loop().create_future()
    send({"id": request_id, "method": method, "params": params})
    try:
        return await future
    finally:
        waiting.pop(request_id, None)


async def run_turn(thread_id: str, turn_id: str, text: str):
    notify("turn/started", threadId=thread_id, turn={"id": turn_id, "status": "inProgress"})
    status = "completed"
    if text.startswith("run:"):
        command = text[4:]
        response = await ask("item/commandExecution/requestApproval", kind="command", threadId=thread_id,
                             turnId=turn_id, itemId="cmd1", startedAtMs=0, environmentId=None,
                             command=f"/bin/bash -lc '{command}'", cwd="/tmp", reason=None,
                             commandActions=[{"type": "unknown", "command": command}])
        accepted = response["decision"] == "accept"
        item = {"type": "commandExecution", "id": "cmd1", "command": command, "cwd": "/tmp"}
        if accepted:
            notify("item/started", threadId=thread_id, turnId=turn_id, startedAtMs=0, item=item)
        notify("item/completed", threadId=thread_id, turnId=turn_id, completedAtMs=0,
               item={**item, "status": "completed" if accepted else "declined",
                     "exitCode": 0 if accepted else None, "durationMs": 5, "aggregatedOutput": "ok\n"})
        reply = "Ran it." if accepted else "Okay, I won't run it."
    elif text.startswith("edit:"):
        changes = [{"path": text[5:], "kind": {"type": "update", "move_path": None}, "diff": "-a\n+b\n"}]
        item = {"type": "fileChange", "id": "edit1", "changes": changes, "status": "inProgress"}
        notify("item/started", threadId=thread_id, turnId=turn_id, startedAtMs=0, item=item)
        response = await ask("item/fileChange/requestApproval", threadId=thread_id, turnId=turn_id,
                             itemId="edit1", startedAtMs=0, reason=None)
        accepted = response["decision"] == "accept"
        notify("item/completed", threadId=thread_id, turnId=turn_id, completedAtMs=0,
               item={**item, "status": "completed" if accepted else "declined"})
        reply = "Edited." if accepted else "Left it alone."
    elif text == "hang":
        await interrupted.wait()
        interrupted.clear()
        status, reply = "interrupted", None
    elif text == "crash":
        os._exit(1)
    else:
        reply = f"You said: {text}"
    if reply:
        for word in reply.split(" "):
            notify("item/agentMessage/delta", threadId=thread_id, turnId=turn_id, itemId="msg1", delta=word + " ")
        notify("item/completed", threadId=thread_id, turnId=turn_id, completedAtMs=0,
               item={"type": "agentMessage", "id": "msg1", "text": reply, "phase": None})
    notify("turn/completed", threadId=thread_id,
           turn={"id": turn_id, "items": [], "status": status, "error": None})


async def main():
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    turns = set()
    while line := await reader.readline():
        message = json.loads(line)
        if "method" not in message:
            waiting[message["id"]].set_result(message.get("result") or {})
            continue
        method, params, request_id = message["method"], message.get("params") or {}, message.get("id")
        log({"method": method, "params": params, "cwd": os.getcwd()})
        if request_id is None:
            continue
        if method == "initialize":
            result = {"userAgent": "fake", "codexHome": "/tmp", "platformFamily": "unix", "platformOs": "linux"}
        elif method == "account/read":
            result = {"account": {"type": "chatgpt", "email": None, "planType": "plus"}, "requiresOpenaiAuth": True}
        elif method == "model/list":
            result = {"data": [
                {"id": "gpt-fast", "model": "gpt-fast", "displayName": "GPT Fast", "isDefault": True, "hidden": False,
                 "defaultReasoningEffort": "low", "supportedReasoningEfforts": [
                     {"reasoningEffort": e, "description": ""} for e in ("low", "medium", "high")]},
                {"id": "gpt-deep", "model": "gpt-deep", "displayName": "GPT Deep", "isDefault": False, "hidden": False,
                 "defaultReasoningEffort": "high", "supportedReasoningEfforts": [
                     {"reasoningEffort": e, "description": ""} for e in ("medium", "high", "max")]},
                {"id": "gpt-old", "model": "gpt-old", "displayName": "Old", "isDefault": False, "hidden": True,
                 "defaultReasoningEffort": None, "supportedReasoningEfforts": []}],
                "nextCursor": None}
        elif method == "thread/start":
            result = {"thread": {"id": f"thread-{next(threads)}"}}
        elif method == "thread/resume":
            result = {"thread": {"id": params["threadId"]}}
        elif method == "turn/start":
            turn_id = f"turn-{next(turn_ids)}"
            result = {"turn": {"id": turn_id, "status": "inProgress", "items": [], "error": None}}
            send({"id": request_id, "result": result})
            task = asyncio.create_task(run_turn(params["threadId"], turn_id, params["input"][0]["text"]))
            turns.add(task)
            task.add_done_callback(turns.discard)
            continue
        elif method == "turn/interrupt":
            interrupted.set()
            result = {}
        else:
            send({"id": request_id, "error": {"code": -32601, "message": "unknown method"}})
            continue
        send({"id": request_id, "result": result})


asyncio.run(main())
