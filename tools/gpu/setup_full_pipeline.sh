#!/usr/bin/env bash
# Run on the existing pod. Keep models and processing services private.
set -euo pipefail
cd "$(dirname "$0")/../.."
command -v uv >/dev/null || { echo 'Install uv on this pod first.'; exit 1; }
nvidia-smi
if ! command -v soffice >/dev/null; then
  apt-get -o Acquire::Retries=1 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libreoffice-writer libreoffice-impress fonts-noto-cjk fonts-dejavu-core
fi
# Bengali scans: PaddleOCR has no Bengali recognizer, Tesseract's LSTM model reads the line crops the
# Paddle detector finds (tools/ocr/tesseract_line.py; 50% -> 99% of characters, 2026-11-23).
# Arabic scans: Tesseract reads the digits PaddleOCR's Arabic model drops (dropped_digits).
if ! tesseract --list-langs 2>/dev/null | grep -qx ben || ! tesseract --list-langs 2>/dev/null | grep -qx ara; then
  apt-get -o Acquire::Retries=1 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-ben tesseract-ocr-ara
fi
uv python install 3.11
test -x /workspace/transformx-engine/bin/python || uv venv --python 3.11 /workspace/transformx-engine
test -x /workspace/transformx-babeldoc/bin/python || uv venv --python 3.11 /workspace/transformx-babeldoc
# Table extraction (Docling's layout and table models) runs on the GPU when there is one: on the CPU
# it took about 3.5 s per page, half of a translation job (2026-11-21). TRANSFORMX_TORCH_INDEX picks
# another PyTorch build (for example .../whl/cpu).
torch_index="${TRANSFORMX_TORCH_INDEX:-https://download.pytorch.org/whl/cpu}"
if [ -z "${TRANSFORMX_TORCH_INDEX:-}" ] && command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
  torch_index=https://download.pytorch.org/whl/cu128
fi
uv pip install --python /workspace/transformx-engine/bin/python torch torchvision --index-url "$torch_index"
uv pip install --python /workspace/transformx-engine/bin/python -r requirements-deep-doc.txt 'docling-core==2.85.0' 'pymupdf==1.27.2.2' 'Pillow>=11,<13' 'python-docx>=1.1,<2' 'python-pptx>=1,<2'
uv pip install --python /workspace/transformx-babeldoc/bin/python -r requirements-transformation.txt 'pymupdf==1.28.2'
uv pip check --python /workspace/transformx-engine/bin/python
# BabelDOC's page-layout model runs on the GPU with ONNX Runtime's CUDA build (BabelDOC's own "cuda"
# extra; transformation/pdf/layout_device.py). Both builds provide the same module, so the CPU build is
# removed; the dependency check then reports only that BabelDOC's default CPU requirement is absent.
if nvidia-smi >/dev/null 2>&1; then
  uv pip uninstall --python /workspace/transformx-babeldoc/bin/python onnxruntime 2>/dev/null || true
  uv pip install --python /workspace/transformx-babeldoc/bin/python 'onnxruntime-gpu[cuda,cudnn]==1.22.0'
  /workspace/transformx-babeldoc/bin/python -c "import onnxruntime as o; o.preload_dlls(); assert 'CUDAExecutionProvider' in o.get_available_providers(); print('Layout model: CUDA available')"
  report=$(uv pip check --python /workspace/transformx-babeldoc/bin/python 2>&1) || {
    echo "$report" | grep -q 'Found 1 incompatibility' && echo "$report" | grep -q 'requires `onnxruntime>=' \
      || { echo "$report"; exit 1; }
  }
else
  uv pip check --python /workspace/transformx-babeldoc/bin/python
fi
/workspace/transformx-babeldoc/bin/python -c 'from bitstring import BitStream; from babeldoc.format.pdf.document_il.backend.pdf_creater import PDFCreater; from babeldoc.docvision.doclayout import OnnxModel'
test -x /workspace/paddle-env/bin/python || { echo 'Run the existing GPU Paddle setup first.'; exit 1; }
/workspace/paddle-env/bin/python -c 'import paddle; assert paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count()>0'
mkdir -p /workspace/transformx-cache/ocr /workspace/transformx-full-jobs
echo 'Full server pipeline dependencies installed. Ollama and GPU Paddle must already be configured.'
