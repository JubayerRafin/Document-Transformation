# Private GPU servers for every processing user

## Enable this update

1. In the existing project's Supabase SQL Editor, run the complete file
   `supabase/migrations/202610220002_private_workers.sql` once. Do not rerun the
   initial migration. This adds worker capability metadata, explicit assignment
   and replacement claim/revoke functions without deleting jobs or documents.
2. From `cloud/`, run `npm run deploy`. The script rebuilds and tests the app.
3. Hard refresh the deployed site. Sign in, open **GPU servers**, register a
   server and download its private configuration. Customer, mentor and admin
   accounts can register their own workers. Viewer accounts remain read-only.

Registration names describe a server; they do not select a provider or install
software. The worker agent works with RunPod, a university allocation or a private
compatible NVIDIA server that permits outbound HTTPS. No incoming port or public
IP is required. Workers are private; cross-account sharing is not enabled.

## Run the server agent

Copy the updated repository and install the existing processing dependencies on
the chosen server. Set up CUDA-enabled PaddleOCR, Ollama and the selected models.
Transfer `gpu.worker.local.json` privately to that server; restrict permissions:

```bash
chmod 600 /path/to/gpu.worker.local.json
python -m tools.service.cloud_worker run --config /path/to/gpu.worker.local.json
```

Use the Python environment containing the engine dependencies. The downloaded
configuration intentionally contains no laptop Python paths. The agent resolves
Python/OCR Python on the server. Alternatively use the CLI registration command
documented in CLOUD_SETUP.md; registration now also works for customer accounts.

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
installed Ollama models. The database checks readiness, a recent heartbeat and
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
