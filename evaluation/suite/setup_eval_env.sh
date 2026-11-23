#!/usr/bin/env bash
# The scoring tools of evaluation/suite on a GPU server, apart from the product's environments:
# unbabel-comet (COMET-22, COMET-QE) and sacrebleu (chrF++) in /workspace/eval-env, the models in
# /workspace/models, and Google's MetricX code in /workspace/metricx. Models are ungated:
# Unbabel/wmt22-comet-da and wmt20-comet-qe-da (Apache-2.0), google/metricx-24-hybrid-xl-v2p6-bfloat16
# with the google/mt5-xl tokenizer (Apache-2.0). COMET-KiWi needs a Hugging Face login that accepted
# Unbabel's terms and is not installed here.
set -euo pipefail
export HF_HOME="${HF_HOME:-/workspace/hf-cache}"
test -x /workspace/eval-env/bin/python || uv venv -q -p 3.11 /workspace/eval-env
uv pip install -q --python /workspace/eval-env/bin/python 'unbabel-comet==2.2.7' sacrebleu sentencepiece huggingface_hub
hf=/workspace/eval-env/bin/hf
test -d /workspace/models/wmt22-comet-da || "$hf" download Unbabel/wmt22-comet-da --local-dir /workspace/models/wmt22-comet-da
test -d /workspace/models/wmt20-comet-qe-da || "$hf" download Unbabel/wmt20-comet-qe-da --local-dir /workspace/models/wmt20-comet-qe-da
test -d /workspace/models/metricx-24-xl-bf16 || "$hf" download google/metricx-24-hybrid-xl-v2p6-bfloat16 --local-dir /workspace/models/metricx-24-xl-bf16
test -d /workspace/models/mt5-xl-tok || "$hf" download google/mt5-xl --include '*.json' 'spiece.model' --local-dir /workspace/models/mt5-xl-tok
test -d /workspace/metricx || git clone -q https://github.com/google-research/metricx.git /workspace/metricx
echo 'Evaluation tools ready.'
