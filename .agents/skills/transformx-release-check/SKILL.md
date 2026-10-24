---
name: transformx-release-check
description: "Assess TransformX release readiness across the Python engine, generated web assets, Cloudflare API and Supabase worker routing. Use before a requested release or deployment review; checking readiness does not authorize publishing."
---

# TransformX release check

Read [the release surfaces and check matrix](references/release.md). Identify the
requested release surface before running commands or expanding scope.

## Establish the candidate

Inspect repository state and existing instructions. Separate the Streamlit
prototype, local FastAPI service, hosted Workers API and outbound GPU agent.
Record candidate code/config identifiers, applied migrations and dependency
environment. Do not infer applied SQL or deployed code from local files alone.

Check the actual mode/format exposed in the target UI against the engine adapter.
PDF, DOCX and PPTX prototype routes are not proof that all formats are released
in the cloud app. Translation, PII and combined adapters need separate evidence.

## Validate by release boundary

- Run relevant Python tests with the correct dependency environment. Keep
  excluded Office/BabelDOC/metric cases explicit instead of claiming all passed.
- For cloud changes, rebuild assets and run Node tests. Verify real published
  asset paths, account roles, CSRF/session handling, job option allowlists and
  private artifact access. Never expose Supabase secret or worker tokens.
- Review SQL RLS/grants and atomic claim/revoke functions. Current private workers
  require an exact selected worker and matching owner; there is no implemented
  general managed-worker pool. A heartbeat/readiness result is not GPU proof.
- Retain old document baselines and evaluate new artifacts against them when
  processing behavior changes. Distinguish unit tests, live database tests,
  deployed browser checks and actual model/GPU runs in the release record.
- Inspect upload size/memory limits, session expiry, interrupted jobs and missing
  evaluation/retention features when relevant to the release. Report unresolved
  defects rather than replacing them with a broad production-ready claim.

## Handoff and action boundary

Return candidate identity, checks with pass/fail/not-run evidence, migrations
needed, quality blockers and deployment/rollback instructions proportionate to
the change. Identify how to restore previous code without deleting queued jobs,
data or baseline artifacts. Do not promise automatic rollback of applied SQL.

A release check alone does not authorize `npm run deploy`, live SQL changes,
account invitations, GPU rentals or external messages. When those actions are
already authorized, proceed within that scope; stop retries on a concrete failure
and present diagnostics rather than repeatedly publishing or rerunning inference.
