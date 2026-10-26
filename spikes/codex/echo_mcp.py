"""A stdio MCP server with one tool, `look_up`, for tools_probe.py. Not product code."""
import json
import sys

TOOL = {"name": "look_up", "description": "Look up a word in the project glossary.",
        "inputSchema": {"type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"]},
        "annotations": {"readOnlyHint": True}}

for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method, result = message.get("method"), {}
    if method == "initialize":
        result = {"protocolVersion": message["params"].get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "glossary", "version": "0"}}
    elif method == "tools/list":
        result = {"tools": [TOOL]}
    elif method == "tools/call":
        word = (message["params"].get("arguments") or {}).get("word", "")
        result = {"content": [{"type": "text", "text": f"{word}: a word in the glossary."}]}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}) + "\n")
    sys.stdout.flush()
