#!/usr/bin/env bash
# Install vLLM and the FP8 PII model on a GPU pod, for tools/gpu/start_worker.sh. Safe to run again.
#
# usage: bash tools/gpu/setup_vllm.sh
# Needs about 14 GB for the model and 9 GB for vLLM. The packages are hard-linked from uv's
# cache: on the pod's overlay disk a copy would need the space twice and fill the disk.
set -euo pipefail
env_dir="${TRANSFORMX_VLLM_ENV:-/workspace/vllm-env}"
model_dir="${TRANSFORMX_VLLM_MODEL:-/workspace/models/qwen3.5-9b-fp8}"
# Qwen3.5-9B quantized to FP8 by Red Hat for vLLM (Apache-2.0), pinned to the evaluated revision.
repo=RedHatAI/Qwen3.5-9B-FP8-dynamic
revision=790f0576d2d77dd5322aa0603a470bd9e3a3d1f6
export UV_CACHE_DIR="${UV_CACHE_DIR:-/workspace/uv-cache}" UV_LINK_MODE=hardlink

command -v uv >/dev/null || { echo "uv is required (https://docs.astral.sh/uv/)."; exit 1; }
if ! "$env_dir/bin/python" -c "import vllm" 2>/dev/null; then
  uv venv -q -p 3.12 "$env_dir"
  VIRTUAL_ENV="$env_dir" uv pip install -q "vllm==0.33.0"
fi
"$env_dir/bin/python" -c "import vllm, torch; print('vLLM', vllm.__version__, 'CUDA', torch.cuda.is_available())"

if [ ! -f "$model_dir/config.json" ]; then
  uvx --from "huggingface_hub[cli]" hf download "$repo" --revision "$revision" --local-dir "$model_dir"
fi
echo "Model in $model_dir. Restart the worker with tools/gpu/start_worker.sh to serve PII from vLLM."
