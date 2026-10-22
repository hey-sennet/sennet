"""Print a transcript compactly: python summarize.py transcripts/NAME.jsonl"""
import json
import sys

for line in open(sys.argv[1]):
    e = json.loads(line)
    m, d = e["msg"], e["dir"]
    where = f'{e["t"]:>7} {e.get("pid", ""):>8} {d:<7}'
    if d == "out":
        t = m.get("type")
        if t == "stream_event":
            continue
        if t in ("assistant", "user"):
            content = m["message"]["content"]
            for block in content if isinstance(content, list) else [content]:
                if isinstance(block, dict) and block.get("type") == "thinking":
                    continue
                print(where, t, "parent=" + str(m.get("parent_tool_use_id")), json.dumps(block)[:400])
        elif t == "system" and m.get("subtype") in ("init", "status", "thinking_tokens"):
            if m.get("subtype") == "init":
                print(where, "init", m.get("session_id"), m.get("permissionMode"))
        elif t == "rate_limit_event":
            continue
        else:
            print(where, json.dumps(m)[:500])
    elif d == "mcp_in" and m["body"].get("method") != "tools/call":
        continue
    elif d == "mcp_out" and "tools" in json.dumps(m)[:60]:
        continue
    elif d != "argv":
        print(where, json.dumps(m)[:500])
