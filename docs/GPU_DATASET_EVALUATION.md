# GPU dataset evaluation

Prepared order: translation first (TranslateGemma 12B), review its outputs, then
PII (Qwen3.5:9B). These are independent workflows over the same registered PDFs.
No pod is deployed, resumed or billed by the preparation or evaluation tools.

## Prepared local files

- `configs/gpu-evaluation.local.json`: six source paths, model names, local
  BabelDOC interpreter and endpoint. This local configuration is ignored by Git.
- `runs/dataset-evaluation/gpu-baseline-v1/dataset.json`: source hashes and page
  profiles. Sample04 comes from the project samples folder.
- `runs/dataset-evaluation/gpu-baseline-v1/annotations.json`: human review and
  PII annotation templates. Unreviewed documents have no accuracy score.
- `tools/gpu/setup_transformx.sh`: pod setup; run it manually only after deploying
  your GPU. CUDA 12.6 wheel installation follows the [official Paddle guide](https://www.paddlepaddle.org.cn/documentation/docs/zh/install/pip/linux-pip.html).

## When the new pod is available

1. Confirm its current SSH address, port and key. Do not reuse an old pod's
   connection details. Connect and run `nvidia-smi`.
2. Copy `tools/gpu/setup_transformx.sh` to `/workspace/setup_transformx.sh`.
   In the pod terminal run:

   ```bash
   bash /workspace/setup_transformx.sh translation
   ```

3. In a separate laptop PowerShell terminal, keep this tunnel running, replacing
   each placeholder with the new connection details:

   ```powershell
   ssh.exe -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -i "PRIVATE_KEY_PATH" -p SSH_PORT -L 127.0.0.1:11435:127.0.0.1:11434 root@GPU_IP
   ```

4. Edit `ocr_remote.host`, `ocr_remote.port` and `ocr_remote.key` in the local
   configuration. Ollama stays bound to loopback; OCR workers use SSH. The SSH
   host must already be trusted in your local known-hosts file.
5. From the project directory run:

   ```powershell
   python -m tools.evaluation.dataset preflight --phase translation
   python -m tools.evaluation.dataset run --phase translation
   ```

   On the pod, `ollama ps` should show GPU usage while translating. The runner
   records GPU-resident model evidence after each document and pauses if it
   cannot confirm at least 95 percent of the reported model allocation in VRAM.
   Downloads, cold model loading, cached OCR and
   inference timings are different measurements; do not compare them as one speed score.

## Review and evaluate translation

Outputs are indexed in `translation/index.json`, with JSON and CSV reports.
Review each source/output pair for meaning, fluency, completeness, names and
identifiers, table labels, layout, figures and untranslated regions. Enter four
0–5 scores and notes in `annotations.json`, then mark that target reviewed.
Human scores are not automatic metrics such as COMET or BLEU; no such scores
are invented without appropriate reference translations and metric setup.

```powershell
python -m tools.evaluation.dataset report --phase translation
```

Investigate failed documents too. A generated PDF is not an accepted translation.

## PII phase

Before running PII, annotate exact sensitive spans from the translation run's
`source-ir.json`: `region_id`, `start`, `end`, `value`, `category`. Confirm the
source hash. Use only configured category names. Review OCR omissions against
the actual source: span metrics measure detection on extracted text, not whether
OCR found all sensitive text in pixels. Empty spans count as negative ground
truth only when explicitly reviewed.

On the pod:

```bash
bash /workspace/setup_transformx.sh pii
```

On the laptop:

```powershell
python -m tools.evaluation.dataset preflight --phase pii
python -m tools.evaluation.dataset run --phase pii
python -m tools.evaluation.dataset report --phase pii
```

The PII report measures exact-span precision, recall and F1 only for reviewed
annotations. It also checks expected values in the output and records raster
content that still needs inspection. Manually check image pixels, hidden content
and redaction appearance; searchable-text checks alone do not prove removal.

## Reuse, retries and limitations

OCR evidence is shared between phases using rendered-page hashes, source
language, CPU/GPU mode and worker-code hashes. Evidence is validated against the
source before its cache pointer is published. Translation inference is fresh.
Explicit `--resume` preserves generated GPU-verified jobs and retries failures;
it never removes their run folders. Configuration/code changes require a new
`output_root`. Keep run folders and OCR evidence together for provenance.

DOCX/PPTX dataset evaluation remains deferred, as agreed. GPU setup and real
TranslateGemma inference must still be validated on the new pod. Long translation
regions, uncertain OCR, complex tables and heuristic figure crops can still
produce review items or failures; the report retains these instead of declaring
success. Stop the pod after testing and check retained storage charges separately.
