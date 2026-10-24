#!/usr/bin/env bash
# Run manually inside a Linux RunPod pod, after its GPU/driver is confirmed.
# Official CUDA wheel indexes: https://www.paddlepaddle.org.cn/documentation/docs/en/install/pip/linux-pip.html
set -euo pipefail
phase="${1:-translation}"
case "$phase" in translation|pii|all) ;; *) echo 'Usage: bash setup_transformx.sh translation|pii|all'; exit 2;; esac
nvidia-smi
python3 --version
if command -v apt-get >/dev/null && [ "$(id -u)" = 0 ]; then
  needed=()
  command -v curl >/dev/null || needed+=(curl)
  command -v zstd >/dev/null || needed+=(zstd)
  python3 -c 'import ensurepip' >/dev/null 2>&1 || needed+=(python3-venv)
  ldconfig -p | grep 'libgthread-2.0.so.0' >/dev/null || needed+=(libglib2.0-0)
  ldconfig -p | grep 'libGL.so.1' >/dev/null || needed+=(libgl1)
  if [ "${#needed[@]}" -gt 0 ]; then
    # Some GPU hosts block the default Ubuntu HTTP mirrors.
    for sources in /etc/apt/sources.list /etc/apt/sources.list.d/ubuntu.sources; do
      if [ -f "$sources" ]; then
        sed -i 's|http://archive.ubuntu.com/ubuntu|https://archive.ubuntu.com/ubuntu|g;s|http://security.ubuntu.com/ubuntu|https://security.ubuntu.com/ubuntu|g' "$sources"
      fi
    done
    apt-get -o Acquire::Retries=1 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 update
    apt-get install -y "${needed[@]}"
  fi
fi
mkdir -p /workspace/ollama-models
if ! command -v ollama >/dev/null; then
  curl -fsSL https://ollama.com/install.sh -o /workspace/install-ollama.sh
  sh /workspace/install-ollama.sh
fi
if ! curl -fsS http://127.0.0.1:11434/api/version >/dev/null; then
  nohup env OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS=/workspace/ollama-models \
    ollama serve > /workspace/ollama.log 2>&1 &
  for attempt in {1..30}; do
    if curl -fsS http://127.0.0.1:11434/api/version >/dev/null; then break; fi
    sleep 1
  done
fi
curl -fsS http://127.0.0.1:11434/api/version
if [ "$phase" = translation ] || [ "$phase" = all ]; then ollama pull translategemma:12b; fi
if [ "$phase" = pii ] || [ "$phase" = all ]; then ollama pull qwen3.5:9b; fi
if [ ! -x /workspace/paddle-env/bin/python ]; then python3 -m venv /workspace/paddle-env; fi
/workspace/paddle-env/bin/python -m pip install --upgrade pip
cuda_wheel="${TRANSFORMX_PADDLE_CUDA:-}"
if [ -z "$cuda_wheel" ]; then
  capability="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader,nounits | head -n 1)"
  major="${capability%%.*}"
  if ! [[ "$major" =~ ^[0-9]+$ ]]; then echo 'Cannot determine GPU compute capability.'; exit 1; fi
  if [ "$major" -ge 10 ]; then cuda_wheel=cu129; else cuda_wheel=cu126; fi
fi
case "$cuda_wheel" in cu126|cu129|cu130) ;; *) echo 'Unsupported Paddle CUDA wheel index.'; exit 1;; esac
/workspace/paddle-env/bin/python -m pip install --no-compile paddlepaddle-gpu==3.3.0 -i "https://www.paddlepaddle.org.cn/packages/stable/$cuda_wheel/"
/workspace/paddle-env/bin/python -m pip install --no-compile paddleocr==3.7.0 'paddlex[ocr-core]==3.7.2' pymupdf==1.27.2.2
/workspace/paddle-env/bin/python -m pip check
/workspace/paddle-env/bin/python - <<'PY'
import json,paddle
import pymupdf,numpy,cv2,PIL
from paddlex.inference.pipelines.components.common.crop_image_regions import CropByPolys
from importlib.metadata import version
assert paddle.is_compiled_with_cuda(), 'CPU Paddle was installed; do not start dataset OCR.'
assert paddle.device.cuda.device_count()>0, 'No visible GPU.'
paddle.set_device('gpu:0')
assert paddle.ones([2,2]).place.is_gpu_place(), 'GPU tensor placement failed.'
x=paddle.ones([1,3,16,16]); kernel=paddle.ones([4,3,3,3])
convolution=paddle.nn.functional.conv2d(x,kernel)
values=convolution.numpy()
convolution_check={'expected':27.0,'minimum':float(values.min()),'maximum':float(values.max()),
                   'max_absolute_error':float(numpy.abs(values-27.0).max()),
                   'gpu_placement':convolution.place.is_gpu_place()}
print('GPU convolution check: '+json.dumps(convolution_check),flush=True)
assert convolution_check['gpu_placement'] and numpy.allclose(values,27.0,rtol=1e-5,atol=1e-5), 'GPU convolution failed: '+json.dumps(convolution_check)
report={'device':paddle.get_device(),'convolution_check':convolution_check,'versions':{n:version(n) for n in ('paddlepaddle-gpu','paddleocr','paddlex','pymupdf')}}
from paddleocr import TextDetection,TextRecognition
import hashlib
from pathlib import Path
report['models']={}
for model_type,name in [(TextDetection,'PP-OCRv5_server_det'),(TextRecognition,'en_PP-OCRv5_mobile_rec'),(TextRecognition,'PP-OCRv5_server_rec')]:
    model=model_type(model_name=name,device='gpu:0',enable_mkldnn=False,cpu_threads=10)
    files=Path.home()/'.paddlex/official_models'/name
    report['models'][name]={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files.glob('*.pdiparams')}
    del model
with open('/workspace/transformx-environment.json','w') as file: json.dump(report,file,indent=2)
print(json.dumps(report))
PY
ollama list
echo 'Setup finished. Keep Ollama bound to loopback and connect using an SSH tunnel.'
