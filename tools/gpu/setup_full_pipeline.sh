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
uv python install 3.11
test -x /workspace/transformx-engine/bin/python || uv venv --python 3.11 /workspace/transformx-engine
test -x /workspace/transformx-babeldoc/bin/python || uv venv --python 3.11 /workspace/transformx-babeldoc
# Table extraction uses the server CPU; OCR and Ollama use its GPU.
uv pip install --python /workspace/transformx-engine/bin/python torch torchvision --index-url https://download.pytorch.org/whl/cpu
uv pip install --python /workspace/transformx-engine/bin/python -r requirements-deep-doc.txt 'docling-core==2.85.0' 'pymupdf==1.27.2.2' 'Pillow>=11,<13' 'python-docx>=1.1,<2' 'python-pptx>=1,<2'
uv pip install --python /workspace/transformx-babeldoc/bin/python -r requirements-transformation.txt 'pymupdf==1.28.2'
uv pip check --python /workspace/transformx-engine/bin/python
uv pip check --python /workspace/transformx-babeldoc/bin/python
/workspace/transformx-babeldoc/bin/python -c 'from bitstring import BitStream; from babeldoc.format.pdf.document_il.backend.pdf_creater import PDFCreater; from babeldoc.docvision.doclayout import OnnxModel'
test -x /workspace/paddle-env/bin/python || { echo 'Run the existing GPU Paddle setup first.'; exit 1; }
/workspace/paddle-env/bin/python -c 'import paddle; assert paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count()>0'
mkdir -p /workspace/transformx-cache/ocr /workspace/transformx-full-jobs
echo 'Full server pipeline dependencies installed. Ollama and GPU Paddle must already be configured.'
