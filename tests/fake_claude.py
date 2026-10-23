"""A scripted stand-in for `claude -p` over stream-json, shaped like the M4 spike's transcripts.

Approvals go to the permission prompt tool in --mcp-config, over HTTP, as Claude Code's do.
Turn behaviour is driven by the user's text:
  run:<command>   ask to run a command, then run it if allowed
  edit:<path>     ask to write a file, then report it written if allowed
  bg:<command>    run a command in the background, end the turn, and report it in a turn of
                  its own once $FAKE_CLAUDE_BG_S seconds (default 0.3) have passed
  hang            keep working until SIGINT, which ends the turn and the process
  crash           exit mid-turn
Anything else gets a short reply. `auth status --json` reports a subscription login.
argv and every line in are appended to $FAKE_CLAUDE_LOG.
"""
import asyncio
import itertools
import json
import os
import signal
import sys
import urllib.request

args = sys.argv[1:]
messages = itertools.count(1)
tools = itertools.count(1)
interrupted = asyncio.Event()


def log(entry: dict):
    if path := os.environ.get("FAKE_CLAUDE_LOG"):
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")


def send(message: dict):
    sys.stdout.write(json.dumps({**message, "session_id": session_id}) + "\n")
    sys.stdout.flush()


def option(name: str) -> str | None:
    return args[args.index(name) + 1] if name in args else None


if args[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "pro"}))
    sys.exit(0)

log({"argv": args, "cwd": os.getcwd()})
session_id = option("--session-id") or option("--resume")
mcp_url = json.loads(option("--mcp-config"))["mcpServers"]["sennet"]["url"]
rpc_ids = itertools.count(1)


def rpc(method: str, params: dict | None = None) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": next(rpc_ids), "method": method, "params": params or {}}).encode()
    request = urllib.request.Request(mcp_url, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


async def approve(tool_name: str, data: dict, use_id: str) -> dict:
    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(None, rpc, "tools/call", {
        "name": "approve", "arguments": {"tool_name": tool_name, "input": data, "tool_use_id": use_id}})
    return json.loads(response["result"]["content"][0]["text"])


def text(reply: str):
    message_id = f"msg_{next(messages)}"
    send({"type": "stream_event", "parent_tool_use_id": None,
          "event": {"type": "message_start", "message": {"id": message_id}}})
    for word in reply.split(" "):
        send({"type": "stream_event", "parent_tool_use_id": None,
              "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": word + " "}}})
    send({"type": "assistant", "parent_tool_use_id": None,
          "message": {"id": message_id, "content": [{"type": "text", "text": reply}]}})


def tool_use(name: str, data: dict) -> str:
    use_id = f"toolu_{next(tools)}"
    send({"type": "assistant", "parent_tool_use_id": None,
          "message": {"id": f"msg_{next(messages)}", "content": [{"type": "tool_use", "id": use_id, "name": name,
                                                                   "input": data}]}})
    return use_id


def tool_result(use_id: str, content: str, is_error: bool = False):
    send({"type": "user", "parent_tool_use_id": None,
          "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": use_id, "content": content,
                                                   "is_error": is_error}]}})


def result(subtype: str = "success"):
    send({"type": "result", "subtype": subtype, "is_error": subtype != "success", "result": ""})


async def run_turn(user_text: str):
    send({"type": "system", "subtype": "init", "cwd": os.getcwd()})
    if user_text.startswith(("run:", "bg:")):
        command = user_text.split(":", 1)[1]
        background = user_text.startswith("bg:")
        data = {"command": command, "description": f"Run {command}"}
        if background:
            data["run_in_background"] = True
        use_id = tool_use("Bash", data)
        decision = await approve("Bash", data, use_id)
        if decision["behavior"] != "allow":
            tool_result(use_id, decision["message"], is_error=True)
            text("Okay, I won't run it.")
        elif background:
            send({"type": "system", "subtype": "background_tasks_changed",
                  "tasks": [{"task_id": "bg1", "task_type": "local_bash", "description": data["description"]}]})
            tool_result(use_id, "Command running in background with ID: bg1.")
            text("Started it in the background.")
            asyncio.get_running_loop().call_later(float(os.environ.get("FAKE_CLAUDE_BG_S", "0.3")),
                                                  lambda: asyncio.ensure_future(report(command)))
        else:
            tool_result(use_id, "ok")
            text("Ran it.")
    elif user_text.startswith("edit:"):
        data = {"file_path": os.path.join(os.getcwd(), user_text[5:]), "content": "b\n"}
        use_id = tool_use("Write", data)
        decision = await approve("Write", data, use_id)
        allowed = decision["behavior"] == "allow"
        tool_result(use_id, "File created successfully" if allowed else decision["message"], is_error=not allowed)
        text("Edited." if allowed else "Left it alone.")
    elif user_text == "hang":
        use_id = tool_use("Bash", {"command": "sleep 600"})
        await interrupted.wait()
        tool_result(use_id, "The user doesn't want to proceed with this tool use.", is_error=True)
        send({"type": "user", "parent_tool_use_id": None,
              "message": {"role": "user", "content": [{"type": "text", "text": "[Request interrupted by user]"}]}})
        result("error_during_execution")
        sys.exit(0)
    elif user_text == "crash":
        os._exit(1)
    else:
        text(f"You said: {user_text}")
    result()


async def report(command: str):
    send({"type": "system", "subtype": "background_tasks_changed", "tasks": []})
    await turns.put(f"report:{command}")  # Claude's own turn, after any turn in progress


turns: asyncio.Queue = asyncio.Queue()


async def run_turns():
    while True:
        item = await turns.get()
        if item.startswith("report:"):
            send({"type": "system", "subtype": "init", "cwd": os.getcwd()})
            text(f"The background command {item[7:]} finished.")
            result()
        else:
            await run_turn(item)


async def main():
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, interrupted.set)
    await loop.run_in_executor(None, rpc, "initialize", {"protocolVersion": "2025-11-25"})
    await loop.run_in_executor(None, rpc, "tools/list")
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    runner = asyncio.create_task(run_turns())
    while line := await reader.readline():
        message = json.loads(line)
        log({"in": message})
        await turns.put(message["message"]["content"])
    while not turns.empty():
        await asyncio.sleep(0.05)
    runner.cancel()


asyncio.run(main())
