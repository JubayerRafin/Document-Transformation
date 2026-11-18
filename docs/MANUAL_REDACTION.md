# Manual PDF redaction review

## Scope

This is an additional review step for a completed hosted PDF job, including
translation, PII, combined, and a previous manual-redaction result. It does not
change the existing extraction, translation, detection or reconstruction path.
The workspace opens the `/redaction-review.html?job=<job UUID>` editor.
It uses the pinned official `pdfjs-dist` package, served locally with its worker,
fonts, character maps and WASM resources. No document is sent to a PDF viewer CDN.

## User workflow

1. Open a generated result and select **Review & add redactions**.
2. Inspect every page. Draw areas, move/resize them, or use Add area and the
   labelled percentage fields with a keyboard. Undo, redo and delete are local
   until the draft is saved.
3. Save draft to keep the plan privately in the account's history. A second tab
   cannot overwrite a newer revision without a conflict. Unsaved edits warn
   before leaving the page.
4. Select an online, updated processing server, confirm the page review and apply.
5. Download and visually inspect the new result. The original, automatic result
   and older versions remain available; their review decisions do not transfer.

If finalization fails, **Reopen saved selections** creates a fresh editable draft
with the saved areas, retaining the failed attempt as history. It does not replay
translation or PII detection and does not automatically submit the new draft.

The editor is not an automatic PII guarantee. Users choose what to remove.
Automatic redactions already present in its input cannot be undone here.
Combined jobs are reviewed after translation, on the final delivered PDF.

## API and data lifecycle

No schema migration or new Supabase secret is required. Private non-runnable
`tx_jobs` rows (`status=uploading`, `stage=manual_review`) store the draft in their
options. `manual_redaction` is a separate operation and is deliberately rejected
by the existing multipart transformation-upload endpoint.

| Route | Contract |
| --- | --- |
| POST `/v1/jobs/:parent/redactions` | Owner-only; completed result artifact ID, SHA-256 and Idempotency-Key create/recover a child draft. |
| GET `/v1/jobs/:draft/redaction-draft` | Owner-only plan, PDF version binding and opaque revision. |
| PUT `/v1/jobs/:draft/redaction-draft` | Conditional revision update; 0–500 normalized regions. |
| POST `/v1/jobs/:draft/redaction-finalize` | Matching saved revision, 1–500 regions, online owned capable worker. Server-side Storage copy of the bound PDF precedes conditional queueing. |
| GET `/v1/jobs/:id/artifacts/:artifact/content` | Session-authorized no-store PDF proxy for same-origin PDF.js loading. Existing signed download routes remain unchanged. |

Regions contain only `{page, x, y, width, height}`. Page is one-based; coordinates
are fractions of the displayed rotated crop box. The server validates fields,
numbers, page bounds and area bounds; the worker validates against actual pages.
The browser verifies the PDF bytes against the saved SHA-256 before rendering.
Every API mutation retains the existing origin, CSRF, invitation and role gates.
Even an administrator cannot edit another owner's PDF through this workflow.

Finalization uses the existing atomic worker claim and finish functions. Older
workers do not advertise `manual_redaction` and cannot claim these jobs. A newer
worker handles existing operations as before. There is no automatic replay.

## Processing and checks

`tools/service/cloud_worker.py` starts
`transformation.validation.manual_redaction` in a separate engine-Python process,
reports heartbeats and enforces a 10-minute manual-processing timeout. No Ollama,
BabelDOC, source extraction or OCR call is made by this operation.

The worker converts selections into unrotated PyMuPDF coordinates, explicitly
unrotates during application, then restores rotation. It applies actual content
redactions, including image-pixel replacement and intersecting vector removal.
It flattens visible form values and removes comments/annotations, links,
attachments, JavaScript, metadata and XML metadata in the exported copy.
Digital signatures are not preserved. Intersecting vector paths can be removed
beyond the drawn rectangle, so a final visual check is still necessary.

The result is a non-incremental, garbage-collected new PDF. Checks reopen those
bytes, inspect remaining character geometry, render selected interiors to verify
opaque black pixels, check ancillary payload cleanup and bind the output hash.
The private worker saves `manual-redaction-audit.json`. The hosted record stores
bounded selected-area evidence; acceptance remains false and review is required.
These checks do not certify unknown PII, unselected duplicates, or the complete
absence of sensitive information from every possible PDF encoding.

Text removal/verification uses actual glyph outlines (`TEXT_ACCURATE_BBOXES`)
rather than oversized font line boxes. Glyphs intersecting a selected area,
including thin edge intersections, are removed as whole characters in bounded
text-only passes. Final fills are restored only after content removal. Tests
check thin intersections, neighboring text, and actual embedded-image pixels;
verification failures still prevent publication.

Drafts are small database records. Applying changes duplicates the input PDF
in private Storage and saves a new output, consuming storage quota. Existing
retention limits still apply. PDF limit: 50 MB and 200 pages; maximum 500 areas.

## Validation and deployment

Run from the repository root in a normal PowerShell terminal:

```powershell
.\tools\service\test_manual_redaction.ps1
```

This runs Python engine/PII/worker regressions, frontend type checking and unit
tests, a fresh production frontend build, the editor and pdf.js copy, and all cloud tests.
DOM interaction tests stub PDF.js rendering; they are not browser-render evidence.
Python tests independently exercise real native/scanned PDFs, forms, rotations,
crop boxes, preserved unrelated pixels, version mismatch and the worker process.

For an isolated browser smoke test after a successful build:

```powershell
node cloud/test/manual-redaction-fixture.mjs
```

Open the localhost URL printed by this synthetic-only server. Draw on page 1,
switch pages, mark the rotated/cropped page and scanned page, save/reload, adjust
zoom, undo/redo, finalize and inspect the downloaded PDF. Check phone width and
keyboard controls. The fixture binds only localhost, uses synthetic documents,
invokes the real redaction implementation and never loads production credentials.
Stop it with Ctrl+C. Its output is under `output/manual-redaction-checks/browser/`.

Before deploying, complete real browser rendering/selection/download checks and
all normal tests. An expired Wrangler login must be refreshed in the user's
normal terminal. Do not deploy stale `frontend/out` files after a failed export.
Then deploy with `npm run deploy` from `cloud/` (the predeploy hook rechecks).
Update the private worker's code while idle and restart it with its existing
configuration, preserving model/settings files. Wait for `manual_redaction` in
reported capabilities. Test a synthetic live draft, save/reload and final PDF
before using real documents. Never stop an actively processing worker.

Rollback: restore the previous frontend/API and worker code together. Existing
originals and results are retained. Draft and queued manual jobs are not deleted;
old workers cannot claim them. Restore updated code to resume the workflow.
