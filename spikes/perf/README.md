# Network data spike

Step 0 of [the performance plan](../../docs/performance.md): measure where bytes go before
choosing what to change. The results are in that document. It is not product code.

## Run it

Run from the repository root with the project's venv. Nothing here touches your real
data directory or the built client in `src/sennet/web`.

```sh
# A throwaway daemon on a copy of the database, behind a byte-counting proxy.
export SENNET_HOME=~/.cache/sennet-perf    # any empty directory outside the repo
mkdir -p $SENNET_HOME
sqlite3 -readonly ~/.local/share/sennet/sennet.db ".backup $SENNET_HOME/sennet.db"
sed 's/port = 7710/port = 7720/' ~/.config/sennet/config.toml > $SENNET_HOME/config.toml
.venv/bin/python spikes/perf/pair.py > $SENNET_HOME/token
.venv/bin/sennet serve &
.venv/bin/python spikes/perf/tap.py 7721 7720 wire.jsonl &

node spikes/perf/sizes.mjs src/sennet/web                          # raw, gzip, brotli per file
.venv/bin/python spikes/perf/api.py http://127.0.0.1:7721 $SENNET_HOME/token
.venv/bin/python spikes/perf/events.py http://127.0.0.1:7721 $SENNET_HOME/token /some/scratch/dir wire.jsonl
node spikes/perf/load.mjs http://127.0.0.1:7720 $SENNET_HOME/token  # Slow 4G page loads

# The same loads with step 1 applied (no API behind it).
.venv/bin/python spikes/perf/static.py src/sennet/web 7740 &
node spikes/perf/load.mjs http://127.0.0.1:7740

# Voice audio on the same server stack, with real speech. The third argument adds the
# conversation runs, from spikes/sessions' recordings. Writes voice-wire.jsonl here.
.venv/bin/python spikes/perf/voice.py spikes/speech/results spikes/speech/.cache/librispeech \
    spikes/sessions/.cache/recordings

# Opus against PCM on the session recordings, through the server's turn-finding and
# Parakeet (steps 5 and 6). CPU only; about 15 minutes. Writes opus-eval.json here.
.venv/bin/python spikes/perf/opus_eval.py spikes/sessions/.cache/recordings ~/.local/share/sennet/models

# The JS bundle by package.
(cd frontend && npx vite build --outDir /some/scratch/build --sourcemap)
node spikes/perf/bundle.mjs /some/scratch/build/assets/index-*.js
```

`events.py` creates a read-only project for the scratch directory and runs one Codex
turn in it, which uses your subscription. While it runs, `ss -tinH '( sport = :7720 )'`
shows the server's TCP segment counts (`data_segs_out`).

| Script | Measures |
| --- | --- |
| `tap.py` | TCP proxy: bytes up and down per connection, after HTTP and WebSocket compression |
| `pair.py` | Pairs a device in the throwaway database and prints its cookie token |
| `sizes.mjs` | Each built file raw, gzipped and brotli-compressed |
| `api.py` | `/api/state` and every `/api/tasks/{id}`, as sent and gzipped |
| `events.py` | `/api/events` during one streamed reply: messages, deltas/s, wire bytes, coalescing |
| `load.mjs` | Chromium, Pixel 7, Slow 4G: cold load, reload and a new visit |
| `static.py` | The client served with gzip and cache headers, for comparison |
| `voice.py` | Downlink and push-to-talk uplink audio bytes on the wire, at 20, 40 and 60 ms uplink frames; with the session recordings, a conversation's uplink while talking, quiet and over a reply |
| `bundle.mjs` | The bundle's bytes by package, from its sourcemap |
| `opus_eval.py` | Words, turns and barge-ins with Opus at 20 and 60 ms against PCM and a dithered control, on real recordings |
