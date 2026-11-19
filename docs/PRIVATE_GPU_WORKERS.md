# Private GPU servers for every processing user

## Enable this update

1. In the existing project's Supabase SQL Editor, run the complete file
   `supabase/migrations/202610220002_private_workers.sql` once. Do not rerun the
   initial migration. This adds worker capability metadata, explicit assignment
   and replacement claim/revoke functions without deleting jobs or documents.
   Then run `supabase/migrations/202611120001_job_recovery.sql` once, which
   recovers jobs left running by a stopped worker (see CLOUD_SETUP.md).
2. From `cloud/`, run `npm run deploy`. The script rebuilds and tests the app.
3. Hard refresh the deployed site. Sign in, open **GPU servers**, register a
   server and download its private configuration. Customer, mentor and admin
   accounts can register their own workers. Viewer accounts remain read-only.

Registration names describe a server; they do not select a provider or install
software. The worker agent works with RunPod, a university allocation or a private
compatible NVIDIA server that permits outbound HTTPS. No incoming port or public
IP is required. Workers are private; cross-account sharing is not enabled.

## Run the server agent

Clone the repository to `/workspace/transformx-app` on the server and run the
three setup scripts once (about 80 GB of disk; most of the time is model and
package downloads):

```bash
bash tools/gpu/setup_transformx.sh pii      # CUDA PaddleOCR and Ollama
bash tools/gpu/setup_full_pipeline.sh       # Deep Doc Extractor, BabelDOC, LibreOffice
bash tools/gpu/setup_vllm.sh                # vLLM, Qwen 3.5 9B FP8, TranslateGemma 12B
```

Transfer `gpu.worker.local.json` privately to `configs/` on the server, then:

```bash
bash tools/gpu/start_worker.sh
```

After updating the code, restart with `bash tools/gpu/restart_worker.sh` (it waits for a running
job, then restarts the worker and its pre-loaded job processes so no job mixes old and new code).

The start script launches the PII model server (port 8000) and the translation
model server (port 8010) on loopback, gives each a fixed amount of GPU memory,
and starts the agent with the matching environment. On cards under 40 GB the two
models take turns on the GPU using vLLM sleep mode
(`transformation/llm/gpu_share.py`); on larger cards both stay loaded. The
script is safe to rerun and leaves running services alone. To load new code,
stop the agent process and run the script again; it also restarts the
pre-loaded engine processes.

The downloaded configuration intentionally contains no laptop Python paths; the
start script points the agent at the server's environments. Alternatively use
the CLI registration command documented in CLOUD_SETUP.md; registration also
works for customer accounts.

For Slurm/PBS clusters, obtain the institution-approved GPU allocation first and
run the agent within that allocated job. It must not run on the login node. The
agent does not submit scheduler allocations or circumvent university policies.
If compute nodes cannot reach HTTPS endpoints, involve the cluster administrator.

## Job routing and readiness

For separate server environments, trusted `engine_profile` settings support
`engine_python` (main extraction interpreter), `python` (BabelDOC), `ocr_python`
(CUDA PaddleOCR), and `ocr_device: "gpu:0"`. Readiness probes the OCR interpreter
without a shell, and jobs launch with `engine_python`. These settings are never
browser upload options. All environments and the agent remain on the server.
Retain local server job directories for evaluation; a heartbeat alone does not
prove GPU execution.

The cloud engine adapter consumes `engine_options.ocr_cache_mode` locally:
`reuse` selects a server cache root (`engine_profile.ocr_cache_root` or the
worker job root's `shared-ocr` directory), and `fresh` disables reuse. This
transport policy must not be forwarded as a keyword to the PDF renderer. The
SSH adapter continues to resolve its own cache policy on the destination host.

Worker HTTP requests identify as `TransformX-Worker/1.0`. The default urllib
identity received Cloudflare HTTP 403/error 1010 on a verified pod, while the
explicit application identity succeeded. Keep worker credentials and lease
checks unchanged; do not disable edge security or log credential headers.

The upload form requires an enabled worker owned by the signed-in user. Its UUID
is bound into the idempotency request hash, so changing the server under the same
request key cannot silently redirect work. The database claims only queued jobs
with an exact matching assigned worker and owner. Administrators do not get a
cross-account compute exception. No unassigned job is silently routed.

The agent reports allocated NVIDIA GPU visibility, CUDA Paddle availability and
the models its model servers (vLLM or Ollama) provide. The database checks readiness, a recent heartbeat and
operation compatibility when claiming a job. Translation-only servers cannot
claim combined/PII jobs. Prerequisite checks do not prove GPU model placement:
actual inference evidence remains a per-run concern. Existing engine behavior is
unchanged by this routing update.

Offline jobs wait for their assigned server. Existing queued jobs from before
this migration have no assignment and will not run: cancel and resubmit them with
a server selected. Existing running jobs retain their original worker/lease.
Revocation is owner-scoped and atomically refuses an actively running worker;
revoking credentials is not a mechanism for stopping GPU processes or billing.

Worker secrets are shown once, downloaded only by the registering user, stored
hashed in PostgreSQL and excluded from worker-list responses. This implementation
does not allow editing or displaying another user's worker credential. Discard
the local browser download when server transfer is complete. Do not commit it.

## Validation scope

Local API tests cover customer registration, owner-only server discovery,
selection rejection, viewer restrictions, credential hashing, and conditional
revocation. Mocked worker tests cover CUDA/model readiness without spending GPU
credits. JavaScript syntax and asset checks are included in the deployment tests.
The new SQL must still be applied and exercised in the real Supabase project;
then test one real PDF on each server type before a full dataset run. GPU jobs,
university scheduling and simultaneous real PostgreSQL claims have not been
exercised by these local tests.
