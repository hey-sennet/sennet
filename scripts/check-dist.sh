#!/usr/bin/env bash
# Check what a built wheel and sdist contain: the client, the notices, and nothing that shouldn't ship.
#   scripts/check-dist.sh dist
set -euo pipefail

dist=${1:-dist}
wheel=$(ls "$dist"/agent_talk-*.whl)
sdist=$(ls "$dist"/agent_talk-*.tar.gz)
fail() { echo "FAIL: $*"; exit 1; }

files=$(unzip -Z1 "$wheel")
for wanted in agent_talk/web/index.html agent_talk/web/licenses.txt agent_talk/web/notices.txt \
              agent_talk/speech/kokoro_vocab.json; do
  grep -qx "$wanted" <<< "$files" || fail "the wheel has no $wanted"
done
grep -q '\.dist-info/licenses/LICENSE$' <<< "$files" || fail "the wheel has no LICENSE"
grep -q '\.dist-info/licenses/NOTICES$' <<< "$files" || fail "the wheel has no NOTICES"
if grep -E '__pycache__|\.pyc$|\.onnx$|node_modules' <<< "$files"; then fail "the wheel has files it shouldn't"; fi
[ "$(wc -c < "$wheel")" -lt 10000000 ] || fail "the wheel is over 10 MB"

listed=$(tar tzf "$sdist")
grep -q '/src/agent_talk/web/index.html$' <<< "$listed" || fail "the sdist has no built client"
if grep -E 'node_modules|/spikes/|\.onnx$' <<< "$listed"; then fail "the sdist has files it shouldn't"; fi
[ "$(wc -c < "$sdist")" -lt 20000000 ] || fail "the sdist is over 20 MB"
echo "ok: $(basename "$wheel"), $(basename "$sdist")"
