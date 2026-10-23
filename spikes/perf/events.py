"""Record /api/events while an agent streams one reply, and count messages and bytes.

Usage: python events.py <base url> <token file> <project dir> <wire log>

Creates a read-only project for <project dir> (once) and a task asking for a long answer with
no tools, then records every event until the turn completes. Run through tap.py so the
wire log has the bytes after permessage-deflate.
"""
import asyncio
import json
import sys
import time

import httpx
import websockets

base, token, project_dir, wire_log = sys.argv[1], open(sys.argv[2]).read().strip(), sys.argv[3], sys.argv[4]
PROMPT = ("Don't run any commands or read any files. In about 500 words of plain prose, explain how "
          "TCP congestion control behaves on a lossy mobile link.")
WINDOWS_MS = (50, 100)


def project(client: httpx.Client) -> int:
    for p in client.get("/api/state").json()["projects"]:
        if p["path"] == project_dir:
            return p["id"]
    created = client.post("/api/projects", json={"path": project_dir, "name": "perf-spike"})
    created.raise_for_status()
    project_id = created.json()["id"]
    client.patch(f"/api/projects/{project_id}", json={"access": "read-only"}).raise_for_status()
    return project_id


async def main():
    headers = {"X-Sennet": "1"}
    client = httpx.Client(base_url=base, cookies={"sennet_device": token}, headers=headers, timeout=30)
    project_id = project(client)
    url = base.replace("http", "ws") + "/api/events"
    received = []
    async with websockets.connect(url, additional_headers={"Cookie": f"sennet_device={token}"},
                                  max_size=None) as socket:
        print("negotiated extensions:", socket.response.headers.get("sec-websocket-extensions"))
        task = client.post("/api/tasks", json={"project_id": project_id, "text": PROMPT})
        task.raise_for_status()
        task_id = task.json()["id"]
        started = time.time()
        while True:
            raw = await asyncio.wait_for(socket.recv(), timeout=300)
            event = json.loads(raw)
            received.append((time.time() - started, len(raw.encode()), event))
            if event.get("type") == "event" and event.get("task_id") == task_id \
                    and event.get("event", {}).get("kind") == "turn_completed":
                break
    await asyncio.sleep(0.5)  # let tap.py log the closed connection

    deltas = [(t, n, e) for t, n, e in received if e["type"] == "delta"]
    by_type = {}
    for _, n, e in received:
        count, size = by_type.get(e["type"], (0, 0))
        by_type[e["type"]] = (count + 1, size + n)
    print(f"task {task_id}: {len(received)} messages in {received[-1][0]:.1f} s")
    print("by type (count, bytes):", by_type)
    if deltas:
        span = deltas[-1][0] - deltas[0][0]
        text = sum(len(e.get("delta", e.get("text", ""))) for _, _, e in deltas)
        payload = sum(n for _, n, _ in deltas)
        print(f"deltas: {len(deltas)} over {span:.1f} s = {len(deltas) / max(span, 0.001):.1f}/s; "
              f"{payload} bytes of JSON for {text} characters of text ({payload / max(text, 1):.1f}x)")
        print("example delta:", json.dumps(deltas[len(deltas) // 2][2])[:300])
        for window in WINDOWS_MS:
            buckets = {}
            for t, _, e in deltas:
                buckets.setdefault((e.get("item_id"), int(t * 1000 // window)), []).append(e)
            print(f"coalesced every {window} ms: {len(buckets)} messages "
                  f"({len(buckets) / max(span, 0.001):.1f}/s)")
    total = sum(n for _, n, _ in received)
    wire = [json.loads(line) for line in open(wire_log)]
    socket_rows = [w for w in wire if w["request"] and w["request"].startswith("GET /api/events")]
    if socket_rows:
        down = socket_rows[-1]["down"]
        print(f"socket bytes: {total} as JSON, {down} on the wire ({down / total:.0%}), including the handshake")


asyncio.run(main())
