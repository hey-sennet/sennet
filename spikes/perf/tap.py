"""A TCP proxy that counts the bytes on the wire for each connection.

Usage: python tap.py <listen port> <target port> <log.jsonl>

Each connection is logged when it closes: its first request line, bytes up and down, and
how long it was open. That is what crosses the network after any HTTP or WebSocket
compression, before TLS (which adds about 2 to 5% more).
"""
import asyncio
import json
import sys
import time


async def main(listen: int, target: int, log_path: str):
    log = open(log_path, "a", buffering=1)

    async def handle(client_reader, client_writer):
        upstream_reader, upstream_writer = await asyncio.open_connection("127.0.0.1", target)
        record = {"request": None, "up": 0, "down": 0, "opened": time.time()}

        async def pipe(reader, writer, key):
            try:
                while data := await reader.read(65536):
                    if key == "up" and record["request"] is None:
                        record["request"] = data.split(b"\r\n", 1)[0].decode(errors="replace")
                    record[key] += len(data)
                    writer.write(data)
                    await writer.drain()
            except ConnectionError:
                pass
            finally:
                writer.close()

        await asyncio.gather(pipe(client_reader, upstream_writer, "up"),
                             pipe(upstream_reader, client_writer, "down"))
        record["seconds"] = round(time.time() - record["opened"], 3)
        log.write(json.dumps(record) + "\n")

    server = await asyncio.start_server(handle, "127.0.0.1", listen)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]))
