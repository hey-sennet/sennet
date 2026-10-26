"""A scripted stand-in for `codex app-server`, speaking the same stdio JSON-RPC.

Turn behaviour is driven by the user's text:
  run:<command>   ask to run a command, then run it if approved
  bg:<seconds>    start a command and end the turn with it running; it finishes that much later
  edit:<path>     ask to edit a file, then apply it if approved
  look            call an MCP tool and search the web, shaped like spikes/codex/transcripts/tools.jsonl
  hang            keep working until interrupted
  crash           exit mid-turn
  limit           fail at the usage limit, which is then full
Anything else gets a short reply. Each reply reports its tokens, in two model responses,
and the limits. Every call is appended to $FAKE_CODEX_LOG.
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
limit_reached = False
background: set[asyncio.Task] = set()  # commands left running after their turn


def snapshot() -> dict:
    return {"limitId": "codex", "limitName": None, "planType": "plus", "rateLimitReachedType": None,
            "primary": {"usedPercent": 100 if limit_reached else 30, "windowDurationMins": 300, "resetsAt": 1792870493},
            "secondary": {"usedPercent": 10, "windowDurationMins": 10080, "resetsAt": 1793396758}}


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
    elif text.startswith("bg:"):
        item = {"type": "commandExecution", "id": f"bg{turn_id}", "command": "/bin/bash -lc 'sleep 25'",
                "cwd": "/tmp", "processId": "4242", "status": "inProgress"}
        notify("item/started", threadId=thread_id, turnId=turn_id, startedAtMs=0, item=item)

        async def finish():
            await asyncio.sleep(float(text[3:]))
            notify("item/completed", threadId=thread_id, turnId=turn_id, completedAtMs=0,
                   item={**item, "status": "completed", "exitCode": 0, "durationMs": 25000, "aggregatedOutput": ""})
        background.add(task := asyncio.ensure_future(finish()))
        task.add_done_callback(background.discard)
        reply = "Started it in the background."
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
    elif text == "look":
        call = {"type": "mcpToolCall", "id": "exec-mcp1", "server": "glossary", "tool": "look_up",
                "status": "inProgress", "arguments": {"word": "sennet"}, "appContext": None, "mcpAppUi": None,
                "pluginId": None, "readOnlyHint": True, "result": None, "error": None, "durationMs": None}
        notify("item/started", threadId=thread_id, turnId=turn_id, startedAtMs=0, item=call)
        notify("item/completed", threadId=thread_id, turnId=turn_id, completedAtMs=0, item={
            **call, "status": "completed", "durationMs": 3,
            "result": {"content": [{"type": "text", "text": "sennet: a word."}], "structuredContent": None, "_meta": None}})
        search = {"type": "webSearch", "id": "exec-web1", "query": "", "action": None, "results": None}
        notify("item/started", threadId=thread_id, turnId=turn_id, startedAtMs=0, item=search)
        notify("item/completed", threadId=thread_id, turnId=turn_id, completedAtMs=0, item={
            **search, "query": "Sennet board game",
            "action": {"type": "search", "query": "Sennet board game", "queries": None},
            "results": [{"type": "text_result", "domain": "example.com", "ref_id": "turn0search0", "title": "Senet",
                         "url": "https://example.com/senet"}]})
        reply = "Looked."
    elif text.startswith("two:"):
        # Two messages in one turn, a second apart, as an agent says what it's about to do and then does it.
        first, reply = text[4:].split("|")
        notify("item/completed", threadId=thread_id, turnId=turn_id, completedAtMs=0,
               item={"type": "agentMessage", "id": "msg0", "text": first, "phase": None})
        await asyncio.sleep(1.0)
    elif text == "hang":
        await interrupted.wait()
        interrupted.clear()
        status, reply = "interrupted", None
    elif text == "crash":
        os._exit(1)
    elif text == "limit":
        global limit_reached
        limit_reached = True
        error = {"message": "You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro) "
                            "or try again at 4:12 PM.", "codexErrorInfo": "usageLimitExceeded"}
        notify("error", threadId=thread_id, turnId=turn_id, willRetry=False, error=error)
        notify("turn/completed", threadId=thread_id, turn={"id": turn_id, "items": [], "status": "failed", "error": error})
        return
    elif text.startswith("title:"):
        # A first reply that starts with the task's title, as the instructions ask; it streams a word at a time.
        reply = f"<sennet-title>{text[6:]}</sennet-title>\n\nOn it, done."
    else:
        reply = f"You said: {text}"
        for _ in range(2):
            notify("thread/tokenUsage/updated", threadId=thread_id, turnId=turn_id, tokenUsage={
                "total": {"inputTokens": 99999, "cachedInputTokens": 0, "outputTokens": 0, "reasoningOutputTokens": 0,
                          "totalTokens": 99999},
                "last": {"inputTokens": 1000, "cachedInputTokens": 800, "outputTokens": 20,
                         "reasoningOutputTokens": 5, "totalTokens": 1020}})
        notify("account/rateLimits/updated", rateLimits=snapshot())
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
        elif method == "account/rateLimits/read":
            result = {"rateLimits": snapshot(), "rateLimitsByLimitId": {"codex": snapshot()}}
        elif method == "thread/start":
            result = {"thread": {"id": f"thread-{next(threads)}"}}
        elif method == "thread/resume":
            result = {"thread": {"id": params["threadId"]}}
        elif method == "turn/start":
            turn_id = f"turn-{next(turn_ids)}"
            result = {"turn": {"id": turn_id, "status": "inProgress", "items": [], "error": None}}
            send({"id": request_id, "result": result})
            text = " ".join(i["text"] for i in params["input"] if i["type"] == "text")
            task = asyncio.create_task(run_turn(params["threadId"], turn_id, text))
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
