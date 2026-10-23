#!/usr/bin/env bash
# Install a built wheel the way users do, in a throwaway uv tool directory, and check that it
# serves: the version, the app shell, the license files, the health check, and that the API wants a
# paired device. Release CI runs it on each built wheel (docs/m6-packaging.md).
#   scripts/smoke-test.sh dist/sennet-*.whl [extra]
set -euo pipefail

wheel=$(realpath "$1")
extra=${2:-cpu}
port=7797
work=$(mktemp -d "${SMOKE_DIR:-${TMPDIR:-/tmp}}/sennet-smoke.XXXXXX")
export UV_TOOL_DIR=$work/tools UV_TOOL_BIN_DIR=$work/bin SENNET_HOME=$work/home
server=
cleanup() {
  if [ -n "$server" ]; then kill "$server" 2>/dev/null || true; wait "$server" 2>/dev/null || true; fi
  rm -rf "$work"
}
trap cleanup EXIT

uv tool install --quiet "sennet[$extra] @ $wheel"
bin=$UV_TOOL_BIN_DIR/sennet
echo "installed: $("$bin" --version)"

mkdir -p "$SENNET_HOME"
printf '[server]\nport = %d\n\n[speech]\nenabled = false\n' "$port" > "$SENNET_HOME/config.toml"
"$bin" serve > "$work/serve.log" 2>&1 &
server=$!
for _ in $(seq 60); do
  curl -fs "http://127.0.0.1:$port/api/health" > /dev/null && break
  sleep 0.5
done

fail() { echo "FAIL: $*"; cat "$work/serve.log"; exit 1; }
status() { curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port$1"; }
health=$(curl -fs "http://127.0.0.1:$port/api/health") || fail "no answer on /api/health"
echo "health: $health"
grep -q "\"version\":\"$("$bin" --version)\"" <<< "$health" || fail "health reports another version"
curl -fs "http://127.0.0.1:$port/" | grep -q '<div id="root">' || fail "/ doesn't serve the app"
[ "$(status /licenses.txt)" = 200 ] || fail "/licenses.txt is missing"
[ "$(status /notices.txt)" = 200 ] || fail "/notices.txt is missing"
[ "$(status /api/state)" = 401 ] || fail "/api/state doesn't need pairing"
grep -q "No browser is paired yet" "$work/serve.log" || fail "serve didn't print a pairing link"
"$bin" doctor > "$work/doctor.log" || true  # agents aren't installed here; it must run, not pass
grep -q "^version:" "$work/doctor.log" || { cat "$work/doctor.log"; fail "doctor didn't run"; }
echo "ok"
