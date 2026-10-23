"""Sizes of the reconnect and task-open responses, as sent and if they were compressed.

Usage: python api.py <base url> <token file>
"""
import gzip
import json
import sys
import zlib

import httpx

base, token = sys.argv[1], open(sys.argv[2]).read().strip()
client = httpx.Client(base_url=base, cookies={"sennet_device": token}, headers={"Accept-Encoding": "gzip"})


def measure(path: str) -> dict:
    response = client.get(path)
    response.raise_for_status()
    body = response.content
    return {"path": path, "sent": int(response.headers.get("content-length", len(body))),
            "encoding": response.headers.get("content-encoding"), "gzip": len(gzip.compress(body, 6)),
            "json": response.json()}


def kb(n: int) -> str:
    return f"{n / 1024:7.1f}"


state = measure("/api/state")
tasks = state["json"]["tasks"]
print(f"tasks: {len(tasks)}, projects: {len(state['json']['projects'])}")
rows = [state] + [measure(f"/api/tasks/{t['id']}") for t in tasks]
rows.sort(key=lambda r: -r["sent"])

print(" sent KB  gzip KB  encoding  events  messages  path")
for r in rows[:8]:
    body = r["json"]
    print(f"{kb(r['sent'])}  {kb(r['gzip'])}  {str(r['encoding']):8}  {len(body.get('events', [])):6}  "
          f"{len(body.get('messages', [])):8}  {r['path']}")

task_rows = [r for r in rows if r["path"] != "/api/state"]
sizes = sorted(r["sent"] for r in task_rows)
print(f"task opens: median {kb(sizes[len(sizes) // 2])} KB, max {kb(sizes[-1])} KB, "
      f"total {kb(sum(sizes))} KB for all {len(sizes)}")

# What inside the biggest task takes the space.
biggest = task_rows[0]["json"]
share = {}
for event in biggest["events"]:
    share[event["kind"]] = share.get(event["kind"], 0) + len(json.dumps(event))
share["messages"] = len(json.dumps(biggest["messages"]))
print("biggest task by part (KB):", {k: round(v / 1024, 1) for k, v in sorted(share.items(), key=lambda kv: -kv[1])})
print("deflate on /api/state alone:", kb(len(zlib.compress(json.dumps(state["json"]).encode()))), "KB")
