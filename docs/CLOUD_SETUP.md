# TransformX V1: Cloudflare + Supabase + GPU worker

Worker routing update: apply `202610220002_private_workers.sql` as well and follow
`PRIVATE_GPU_WORKERS.md`. Every processing user now selects an owned worker;
the previous administrator-wide claim behavior below has been removed.

## Status

The new cloud deployment is isolated in `cloud/`. The existing local FastAPI
application, document-processing engine and saved baseline runs are not replaced.
The supplied project URL and browser publishable key are configured in
`cloud/wrangler.jsonc`. A read-only Auth settings request verified that this project
is reachable and email authentication is enabled. This does **not** mean the schema,
accounts, storage bucket or Cloudflare deployment have been installed.

Implemented code: invitation-based profile permissions; password sign-in via
Supabase with a short-lived HttpOnly access cookie; PostgreSQL schema and RLS;
private Storage; PDF submission and idempotency; atomic job claiming; authenticated
outbound worker registration/heartbeat; output upload; source/output PDF comparison;
download; metrics; and revision notes bound to an exact output hash.

Pending deployment validation: execute migrations; create administrator; configure
server secret; deploy Cloudflare; test real-user RLS/storage access; register a GPU
server; perform an actual PDF run; then run the seven demonstration documents.
Local mocked API tests do not substitute for PostgreSQL or GPU integration tests.

Not implemented in this cloud milestone: historical baseline import and named
baseline creation, DOCX/PPTX release, refresh-token sessions, invitations within the
application, model readiness preflight, remote interruption/cancellation,
automatic evaluation execution, IR/diagnostic upload, and all-document page-image
previews. The cloud app uses browser PDF viewers. Baseline controls are hidden.
No source PDFs or prior approvals have been uploaded automatically.

## 1. Install Supabase schema

Open the project's SQL Editor:
https://supabase.com/dashboard/project/addlhzcbsuuqudhxcnlo/sql/new

Run the complete file `supabase/migrations/202610220001_transformx.sql` once.
It executes in a transaction. It creates only `tx_*` tables and a private bucket
named `transformx-private`. Do not replace it with public-bucket policies.

In Authentication > Users, create/invite `mjhossain808@gmail.com` and set up that
account's password using Supabase's supported account flow. Do not send its password
in chat. Run `supabase/bootstrap-admin.sql` afterwards; it must return exactly one
administrator profile. A zero-row result means the Auth user is not present yet.

Disable public signup in the project's email authentication settings for this pilot.
The application independently rejects accounts without an explicitly active profile.
For future users, invite the Auth user, then insert their UUID into `tx_profiles`
with an explicit role: customer, mentor, viewer or admin. Mentor can submit jobs and
review permitted results; viewer cannot submit. User metadata does not grant roles.

## 2. Configure backend secret locally

Find the project's server secret key in Supabase Settings > API Keys. The key
is privileged and bypasses RLS. Never place it in the frontend, wrangler vars,
a committed file, a screenshot, or chat.

Install Node.js dependencies from the project folder:

```powershell
.\.venv-backend\Scripts\python.exe tools\service\build_cloud_frontend.py
cd cloud
npm ci
Copy-Item .dev.vars.example .dev.vars
```

Edit `.dev.vars` locally with the real server secret, then use `npm run dev`.
This cloud application is separate from the old local FastAPI application on 8000.
Sign in with your invited email and password. Missing configuration fails closed.
Sessions expire after at most one hour; sign in again when prompted.

## 3. Cloudflare deployment

```powershell
# From cloud/
npx wrangler login
npx wrangler secret put SUPABASE_SECRET_KEY
npm test
npm run deploy
```

Enter the secret only in Wrangler's interactive prompt. Use the HTTPS workers.dev
address returned by a successful deployment. Do not invent or distribute an address
before deployment succeeds. Keep development and real confidential documents separate.
Review current provider quotas, storage limits, project pause behavior and pricing.
A free deployment is not a guaranteed 24/7 service-level agreement.

## 4. GPU server worker

Install this repository and the established engine dependencies on the server.
Start Ollama; install the selected models and verify actual GPU placement. This
agent does not install packages, start billed pods, or silently switch to another
server. All engine parsing/reconstruction and model inference execute on this server.

From the repository root, using the engine's Python environment:

```bash
python -m tools.service.cloud_worker register \
  --url https://YOUR-DEPLOYED-APP.workers.dev \
  --email mjhossain808@gmail.com --name mentor-gpu
python -m tools.service.cloud_worker run
```

Registration prompts privately for the account password and writes a generated
worker token to `configs/gpu.worker.local.json`. This file is ignored by Git.
Do not share it or reuse the server's SSH private key as a worker token.
A mentor registers using their own invited account. Admin-owned workers can claim
only explicitly assigned jobs belonging to the same owner, just like mentor and
customer workers. One running
job per worker is enforced in PostgreSQL. Jobs are never automatically reclaimed.

Worker dependencies/models must be verified before accepting customer uploads.
Input PDFs are checked for size/hash/encryption/page count before processing.
Source and result uploads are bounded to 50 MB. Generated is distinct from approved:
fresh jobs do not inherit COMET scores or prior human approval.

If the worker crashes or result transport fails, inspect its private logs and
processes first. A persisted running lease may represent still-running GPU work.
Only after confirming it stopped should an administrator mark the job interrupted
and submit a new run. Closing the website does not stop GPU billing.

## 5. Deployment acceptance checks

1. Anonymous access to jobs and private artifacts fails.
2. Account A cannot fetch account B's private job, review or Storage object.
3. A viewer cannot submit or register a worker; a mentor can submit their own job.
4. No browser can mutate its role or a job's status directly through Supabase.
5. Queue a PDF while no worker is connected; it must remain queued.
6. Register the GPU worker, verify model/device evidence, submit one synthetic PDF.
7. Verify source SHA-256, generated output, runtime scope and private downloads.
8. Repeat the same upload/idempotency key: no duplicate job or inference.
9. Verify concurrent claims cannot run the same job twice.
10. Disable a worker; its credential must stop authorizing new requests.
11. Inspect interrupted upload/result states; do not label partial work successful.
12. Run Samples01–06 and the slide PDF as NEW runs after these checks. Preserve the
old baseline files and compare PDF by PDF. Semantic quality, PII leakage and layout
acceptance require their separate evaluation evidence.

## Security and operations limits

This is a pilot implementation, not a production certification. Before broad use:
add tested SQL migrations and backups/restores; bounded retention; distributed
rate limits and upload quotas; audit logs; worker lifecycle administration;
session refresh/revocation; safe result-transfer recovery; baseline import; and
real deployment integration tests. Supabase Auth's rate limits are not a substitute
for application-wide quotas. The privileged API must retain explicit access checks
even though the database also has RLS policies for authenticated browser reads.
