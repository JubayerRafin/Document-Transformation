#!/usr/bin/env bash
# Install vLLM and the FP8 PII model on a GPU pod, for tools/gpu/start_worker.sh. Safe to run again.
#
# usage: bash tools/gpu/setup_vllm.sh
# Needs about 14 GB for the PII model, 24 GB for the translation model and 9 GB for vLLM. The packages are hard-linked from uv's
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
echo "PII model in $model_dir."

# TranslateGemma 12B for vLLM. Google's repository is gated (each user accepts the Gemma terms on
# Hugging Face), so this uses Infomaniak's ungated vLLM build, pinned, whose only changes are the chat
# template and a RoPE config key. Its weights and tokenizer must be byte-identical to Google's:
# checked below against the SHA-256 values Hugging Face publishes for google/translategemma-12b-it.
tg_dir="${TRANSFORMX_TRANSLATION_VLLM_MODEL:-/workspace/models/translategemma-12b}"
tg_repo=Infomaniak-AI/vllm-translategemma-12b-it
tg_revision=7b92526270082454328d51b5d839c7feae73b91c
if [ "${TRANSFORMX_SKIP_TRANSLATION_MODEL:-0}" != 1 ]; then
  if [ ! -f "$tg_dir/.verified" ]; then
    uvx --from "huggingface_hub[cli]" hf download "$tg_repo" --revision "$tg_revision" --local-dir "$tg_dir"
    "$env_dir/bin/python" - "$tg_dir" <<'PY'
import hashlib, json, sys, urllib.request
from pathlib import Path
folder = Path(sys.argv[1])
info = json.load(urllib.request.urlopen('https://huggingface.co/api/models/google/translategemma-12b-it?blobs=true', timeout=60))
expected = {s['rfilename']: (s.get('lfs') or {}).get('sha256') for s in info['siblings']
            if s['rfilename'].endswith(('.safetensors', 'tokenizer.json', 'tokenizer.model'))}
for name, sha in sorted(expected.items()):
    digest = hashlib.sha256()
    with open(folder / name, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 24), b''): digest.update(chunk)
    if digest.hexdigest() != sha: sys.exit(f'{name} differs from google/translategemma-12b-it; not using this model.')
(folder / '.verified').write_text('weights and tokenizer match google/translategemma-12b-it\n')
print('TranslateGemma weights verified against Google\'s published checksums.')
PY
  fi
  echo "Translation model in $tg_dir."
fi
# TranslateGemma 27B, the optional higher-quality model (TRANSFORMX_HIGH_QUALITY_SETUP=1; about 55 GB
# of disk, a GPU of 40 GB or more): the same pinned Infomaniak build, checked against Google's 27B.
hq_dir="${TRANSFORMX_HIGH_QUALITY_MODEL:-/workspace/models/translategemma-27b}"
if [ "${TRANSFORMX_HIGH_QUALITY_SETUP:-0}" = 1 ] && [ ! -f "$hq_dir/.verified" ]; then
  uvx --from "huggingface_hub[cli]" hf download Infomaniak-AI/vllm-translategemma-27b-it \
    --revision be47a43850d00bdf556df71fdff7f9740ded598e --local-dir "$hq_dir"
  "$env_dir/bin/python" - "$hq_dir" <<'PY'
import hashlib, json, sys, urllib.request
from pathlib import Path
folder = Path(sys.argv[1])
info = json.load(urllib.request.urlopen('https://huggingface.co/api/models/google/translategemma-27b-it?blobs=true', timeout=60))
expected = {s['rfilename']: (s.get('lfs') or {}).get('sha256') for s in info['siblings']
            if s['rfilename'].endswith(('.safetensors', 'tokenizer.json', 'tokenizer.model'))}
for name, sha in sorted(expected.items()):
    digest = hashlib.sha256()
    with open(folder / name, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 24), b''): digest.update(chunk)
    if digest.hexdigest() != sha: sys.exit(f'{name} differs from google/translategemma-27b-it; not using this model.')
(folder / '.verified').write_text('weights and tokenizer match google/translategemma-27b-it\n')
print('TranslateGemma 27B weights verified against Google\'s published checksums.')
PY
  echo "Higher-quality translation model in $hq_dir."
fi
echo "Restart the worker with tools/gpu/start_worker.sh to serve PII and translation from vLLM."
