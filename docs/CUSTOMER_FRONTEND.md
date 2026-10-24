# TransformX customer interface

The customer app is in `frontend/`: Next.js App Router, TypeScript, Tailwind CSS,
source-owned shadcn/ui components built on Radix, Motion dashboard transitions,
and lazily imported GSAP/ScrollTrigger landing animations. The current theme uses
HP-inspired blue, navy and cyan with an original TransformX mark. The presentation includes an animated
character field, scroll-linked product reveal, staggered section entrances and
a sticky workflow story. A pause control and reduced-motion settings disable
decorative motion. The product illustration uses fictional content and makes no
API calls; real transformations start in the authenticated workspace.
There are no remote fonts or document-content console messages. The landing hero
lazily loads a decorative Three.js document scene on eligible desktop devices;
the SVG fallback remains available for reduced motion, narrow screens and WebGL
failures. The processing workspace does not load that scene.

## Hosting boundary

Next.js exports static HTML/CSS/JS. Cloudflare serves the landing page at `/` and
the customer workspace at `/workspace`, alongside the existing Workers API.
No Node server, Next API routes, new database tables or new API endpoints are
introduced. Supabase Auth, PostgreSQL, Storage and outbound GPU agents retain
their existing roles. Local FastAPI and Streamlit remain separate applications.

`/studio` continues to serve the legacy control structure and JavaScript, with
the new dark theme in `cloud/ui/studio.css` and an invitation welcome screen in
`cloud/ui/studio-welcome.html`. Its existing IDs, actions and API behavior remain.
The original builder remains `tools/service/build_cloud_frontend.py`. The new
packager saves its output as `studio.html`, overlays the static customer app,
and generates exact SHA-256 CSP hashes for Next's inline hydration scripts.
Studio and API routes keep their existing script policy. Never deploy stale
HTML with hashes from a different build.

## Install and verify (repository root)

```powershell
npm --prefix frontend ci
npm --prefix cloud ci
npm --prefix frontend run typecheck
npm --prefix frontend test
npm --prefix cloud test
```

Cloud tests rebuild both frontends before running API/asset tests. The customer
test suite uses synthetic documents and mocked API responses. It does not start
a GPU, create live users or certify translation/PII quality. The existing engine
and document evaluation suites remain necessary when processing code changes.

## Preview

```powershell
npm --prefix cloud run dev
```

Open the localhost URL Wrangler prints. This tests the production static export
with the real same-origin API router. Configure backend secrets using the
existing Cloud setup instructions to sign in against your Supabase project.
Do not commit `.dev.vars` or GPU configuration files.

For rapid layout development, `npm --prefix frontend run dev` starts Next on
port 3000. Its standalone development server does not provide `/v1/` APIs;
use Wrangler for integrated sign-in/upload tests. No proxy endpoints are invented.

Public deployment is a separate, deliberate action: `npm --prefix cloud run
deploy` runs type checks, frontend tests, the production build and cloud tests
before publishing. Existing Cloudflare account and Supabase secret configuration
are reused. No schema migration is required by this frontend change.

For an offline UI demonstration with synthetic account/job data only, run
`node frontend/test/preview-server.mjs` after a successful cloud build and open
`http://127.0.0.1:8787/`. This fixture binds only to localhost, never contacts
Supabase or a GPU, and must never be deployed. Its job submissions are test data
held in memory. It is not a substitute for authenticated end-to-end testing.

## Feature contracts

- Sign-in: existing cookie session and CSRF APIs; no browser-stored access tokens.
- Upload: one PDF, up to 50 MB; signature checked locally, backend remains
  authoritative. Exact selected private worker, existing options and categories.
  An unchanged failed submission retries with the same idempotency key.
- Progress: recorded events and states, five-second polling while visible;
  no fabricated percentage or estimated completion time. Cancel queued jobs only.
- History: search/status filters over the latest API results (up to 100).
- Preview: matched source/result signed URLs, native browser PDF viewer, reload
  for expired links and existing authenticated download paths. Mobile browsers
  may require opening or downloading PDFs instead of inline viewing.
- Private servers: existing registration and revocation APIs; one-time private
  JSON configuration is held in memory only. This is the same engine profile as
  the legacy registration screen, not automatic infrastructure provisioning.
- Review: generated output is labeled for review; approval and evaluation tools
  remain in Studio. PII quality evaluation is still pending.

## Rollback

Restore the previous frontend build script and static route/CSP changes, rebuild
the legacy assets, then deploy that code. Do not delete documents, jobs, accounts,
worker credentials or baseline results. Keep frontend lockfiles with the source.


Cloudflare route handling: keep `cloud/wrangler.jsonc` assets
`html_handling: "none"` with the explicit document mapping in
`cloud/src/index.mjs`. The default asset canonicalization redirects `.html` to
extensionless paths and otherwise loops when Studio is internally rewritten.
Both workspace and Studio accept extensionless, trailing-slash and `.html`
URLs. Hydration CSP hashes must match customer document aliases as well.


Motion behavior: the landing page lazily loads GSAP/ScrollTrigger for hero and
section reveals, preview movement and a scroll progress indicator. Its fictional
PDF illustration includes a decorative scan and transitions when switching
translation/redaction/review. The pause control stops decorative loops and
reverts GSAP; OS reduced-motion preferences skip them. Workspace navigation,
mobile navigation, headings and view changes use Motion, with small CSS card
entrances. Studio uses CSS entrances and hover feedback without changing its
legacy API/actions. `frontend/test/animation.test.tsx` checks GSAP initialization,
pause cleanup and reduced-motion behavior; rendered animation must also be
checked in the deployed browser. None of these animations report real processing
progress or introduce new endpoints.


Hero sculpture: `hero-document.tsx` always renders a local SVG illustration and
lazy-loads `document-scene.ts` (Three.js) only for screens at least 900px wide
with no reduced-motion preference. It uses local geometry/materials, no models,
textures, fonts or document data. Pixel ratio is capped at 1.5 and rendering at
30fps. The pause control, hidden tabs and off-screen visibility stop animation;
resize, media changes and unmount release renderer resources. A lost/unsupported
WebGL context retains the static illustration. Keep this enhancement out of the
workspace/Studio processing routes. Reference:
https://threejs.org/docs/#WebGLRenderer


Landing session navigation uses `use-landing-session.ts` and existing `GET
/v1/me`. Static HTML initially shows a neutral Workspace link. Authenticated
visitors see Your workspace/Open workspace; confirmed anonymous visitors see
Sign in. Focus, pageshow and visible-tab events recheck the private session,
covering return navigation and sign-in/out in another tab. Checks are abortable,
with no stored credentials or private user details in marketing HTML. Service
errors keep neutral navigation instead of falsely asserting a signed-out state.


Authenticated landing headers and mobile menus expose Sign out, calling the
existing CSRF-protected DELETE `/v1/session` through the same ApiClient that
checked the session. Duplicate clicks and in-flight session checks cannot undo
logout. Failed logout leaves signed-in navigation visible with an error for
retry; successful logout changes links to Sign in. No new backend route is used.


Brand identity: `frontend/public/transformx-mark.svg` is the canonical original
TransformX document/X mark. `brand.tsx`, Next metadata and the cloud Studio
builder use it; the Studio builder copies it into the published assets. The
new palette uses navy #07131F, surface #0D2032, HP-inspired blue #0096D6,
action sky #54C7F4 and cyan #6FE3F2. Use dark #051A29 text on sky action buttons.
Landing, workspace, Studio and both WebGL/SVG hero art share this direction.
The HP logo is not used. Export SVG/PNG wordmarks, monochrome mark and a brand
board with `node tools/service/export_brand_assets.mjs <output-directory>`.
The exporter uses the installed Sharp dependency. Semantic warning/error states
and document paper surfaces remain distinct. Branding tests measure core text
contrast; full rendered contrast still requires browser review.
