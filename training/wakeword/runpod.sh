#!/usr/bin/env bash
# The whole run on a GPU pod: environment, data, training. Rerun it after any failure;
# every stage picks up where it stopped. Extra arguments go to train.py (e.g. `generate`).
#
#   nohup ./runpod.sh > /workspace/wakeword.log 2>&1 &
set -euo pipefail
cd "$(dirname "$0")"

WORKSPACE=${WORKSPACE:-/workspace}
DATA=${DATA:-$WORKSPACE/wakeword-data}
# Stop the pod when the run ends, done or failed, so it doesn't bill while idle. Stopping keeps
# /workspace; start the pod again (with zero GPUs is enough) to copy the model off.
# STOP_POD=0 keeps it running. The pod's id and API key live in /etc/rp_environment, which
# only interactive shells source.
[[ -f /etc/rp_environment ]] && source /etc/rp_environment
if [[ ${STOP_POD:-1} == 1 && -n ${RUNPOD_POD_ID:-} ]] && command -v runpodctl > /dev/null; then
    trap 'echo "Stopping pod $RUNPOD_POD_ID"; runpodctl stop pod "$RUNPOD_POD_ID"' EXIT
fi
# Caches on the volume, so a restarted pod doesn't download it all again.
export UV_CACHE_DIR=$WORKSPACE/.cache/uv
export UV_PYTHON_INSTALL_DIR=$WORKSPACE/.cache/uv-python
export HF_HOME=$WORKSPACE/.cache/huggingface

if ! command -v uv > /dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH=$HOME/.local/bin:$PATH
fi

nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
uv sync --frozen --python 3.12
uv run --frozen python fetch.py --data "$DATA"
uv run --frozen python train.py --data "$DATA" "$@"
echo "Model: $DATA/output/hey_sennet.onnx"
