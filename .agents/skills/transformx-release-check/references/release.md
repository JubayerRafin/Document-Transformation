# Current release surfaces

| Surface | Files and dependency boundary |
|---|---|
| Local prototype | `transform_app.py`, `ui/transformx.py`; `requirements.txt` |
| Local backend | `backend/api.py`, `config.py`, `db.py`, `service.py`, `worker.py`; `requirements-backend.txt` |
| Shared document engine | `transformation/workflows.py`, `pipeline.py`; `requirements-transformation.txt`, `requirements-deep-doc.txt` |
| Hosted API/assets | `cloud/src/index.mjs`, `cloud/wrangler.jsonc`, `cloud/package.json`, `cloud/package-lock.json` |
| Customer frontend | `frontend/` Next.js static export, TypeScript, Tailwind, Radix/shadcn components, Motion and GSAP; `frontend/package-lock.json` |
| Studio frontend build | `tools/service/build_cloud_frontend.py`, `backend/static/`, `cloud/ui/`, `ui/product-copy.json`; legacy output preserved as `cloud/public/studio.html` |
| Studio visual theme | `cloud/ui/studio.css`, `cloud/ui/studio-welcome.html`; cloud-only dark theme with existing legacy control IDs/actions retained |
| Customer packaging | `tools/service/package_customer_frontend.mjs`; overlays `frontend/out/` into `cloud/public/`, generates `cloud/generated/frontend-csp.json` |
| GPU agent | `tools/service/cloud_worker.py` calling `backend/engine_job.py` |
| PostgreSQL schema and RLS | `supabase/migrations/202610220001_transformx.sql` |
| Private worker assignment/readiness | `supabase/migrations/202610220002_private_workers.sql` |
| Setup/known limits | `docs/CLOUD_SETUP.md`, `docs/PRIVATE_GPU_WORKERS.md` |

The main customer browser UI uses Next.js as a static exporter; no Next.js server
or Vercel runtime is required. Studio retains its plain HTML/CSS/JavaScript UI at
`/studio`. Supabase supplies Auth/PostgreSQL/private Storage;
the Python engine executes on the worker host. Local FastAPI uses SQLite by
default with PostgreSQL configuration support; it is a separate deployment.

Commands from the repository root:

```powershell
npm --prefix frontend ci
npm --prefix frontend run typecheck
npm --prefix frontend test
python -m unittest tests.test_cloud_worker tests.test_separate_workflows tests.test_remote_pipeline
node --check cloud/src/index.mjs
```

From `cloud/`:

```powershell
npm test
npx wrangler deploy --dry-run --outdir .wrangler/build
```

`npm test` invokes `pretest`, which builds the Next static export, rebuilds legacy
Studio using the Python builder and composes both with exact CSP script hashes,
then runs the Node test suite in `cloud/test/`. Check `/`, `/workspace`, `/studio`,
hydration under CSP and mobile keyboard/dialog behavior. When Studio is included,
verify its control IDs remain unique and existing actions/API behavior remain;
the style/welcome overrides are cloud-only. When only the customer app changes,
verify Studio assets remain unchanged. See
`docs/CUSTOMER_FRONTEND.md`. Use installed dependency
versions from the lockfile. `npm run deploy` includes a test/build hook and then
PUBLISHES; it is not an offline validation command. A dry run may need local
Wrangler config/log access but does not prove a live secret or database exists.

Broader Python validation, when warranted, uses:

```powershell
python -m tools.evaluation.offline_checks --group primary --output <new-check-root>
python -m tools.evaluation.offline_checks --group babeldoc --output <new-check-root>
python -m tools.evaluation.offline_checks --group metrics --output <new-check-root>
```

Use the corresponding isolated environments. `--exclude-native-office` records
an exclusion rather than a passing test. Review actual logs and test counts;
historical counts in reports are not evidence for the current candidate.

Live acceptance requires testing anonymous and cross-account denials, viewer
restrictions, exact worker assignment, revoked/busy workers, idempotent upload,
safe interruption and source/output hashes. Use synthetic documents and new run
roots. Do not auto-start a GPU to complete a readiness checklist.


### Hosted HTML route regression checks

Cloudflare assets use `html_handling: none`. The Worker explicitly maps `/`,
`/workspace`, `/workspace/`, `/studio` and `/studio/` to exported HTML files.
Keep these together: automatic HTML canonicalization plus the former
`/studio` -> `/studio.html` rewrite caused a live self-redirect loop. Test direct
`.html` aliases too, and preserve the customer hydration CSP on every alias.
`cloud/test/assets.test.mjs` models canonicalization, and
`cloud/test/studio-navigation.test.mjs` exercises the generated Studio sign-in
entry points and empty-selection cancellation in jsdom. These are local tests,
not deployed-browser evidence.

For connectivity, `/health/live` only proves configuration presence. Verify an
invited user can sign in and read their job/worker lists before claiming live
Supabase connectivity. Private Storage previews/downloads require an existing
permitted artifact; an empty history cannot validate them. Never print cookies,
passwords, secret keys or private document data in the verification report.


Animation acceptance: check the landing scan/demo transitions, scroll reveals,
scroll progress, decorative pause/resume and reduced-motion settings. Verify
workspace view/navigation animation and Studio CSS entrances independently.
Animation unit tests prove setup/cleanup, not actual browser rendering. Managed Windows execution can intermittently deny Next.js output writes with
EPERM. Preserving generated `.next` outside `frontend/` (its TypeScript include
is recursive) and cleaning generated export output does not always resolve it.
If the build remains blocked, have the user run `npm run deploy` from `cloud/`
in their normal PowerShell terminal; do not publish stale exported HTML.

Repository organization: `README.md`, `docs/README.md` and
`docs/REPOSITORY_LAYOUT.md` describe the current source boundaries. Local results
remain at their original ignored paths; older tracked experiments/results were
removed from the index without deleting local evidence. Do not add them back
while staging a release. Commit migrations, lockfiles, source assets and example
configuration; exclude `*.local.json`, `cloud/.dev.vars`, per-project
`supabase/bootstrap-admin.sql`, models and generated frontend bundles. Use
`supabase/bootstrap-admin.example.sql` for a portable administrator setup example.
Keep module/build roots stable when tidying a working checkout. A dirty snapshot
may include previously untracked engine/UI work; disclose that scope in the
commit rather than calling the entire diff a behavior-neutral cleanup.
Do not deploy stale exported HTML when frontend production source changed.


The landing hero also lazily loads Three.js (`hero-document.tsx` and
`document-scene.ts`). Validate SVG fallback on narrow/reduced-motion/unsupported
WebGL devices, pause behavior and resource cleanup. Only desktop landing uses
WebGL; processing routes must not load it. Tests in `hero-document.test.tsx`
exercise the enhancement lifecycle; deployed desktop rendering requires an
actual browser check. Never treat the decorative document stack as live job
progress or expose user documents to it.


Landing navigation is session-aware through `use-landing-session.ts` and GET
`/v1/me`. Check authenticated desktop/mobile links, anonymous labels, returning
from sign-in, tab focus and session expiry. Ignore superseded/aborted responses;
network failure must not be mistaken for a confirmed guest. Keep personalized
account data out of the statically exported landing document. Regression tests
are in `frontend/test/landing-session.test.tsx`.


Landing sign-out checks: both desktop and mobile buttons require an active
session, use existing DELETE `/v1/session` with cookies/CSRF, disable duplicate
requests, handle failure without pretending to log out, and discard stale
session responses. Verify logout with mocks before deciding whether a live
browser test should deliberately end the user's current session.


Brand updates: canonical SVG mark is `frontend/public/transformx-mark.svg`;
Next `brand.tsx` and metadata plus the cloud Studio builder must publish/use the
same mark. Theme tokens are in `globals.css`, landing and Studio styles; hero
WebGL and SVG art must also match. Current direction is HP-inspired blue/navy/
cyan with an original TransformX mark. Core text contrast is tested in
`frontend/test/branding.test.tsx`. Check a small favicon and the mark on dark/
light backgrounds; export shareable assets with `export_brand_assets.mjs`.
