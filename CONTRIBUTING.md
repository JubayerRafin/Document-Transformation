# Contributing

Read [the code layout](docs/REPOSITORY_LAYOUT.md) and choose the application boundary
you are changing. Preserve shared engine contracts and source/page/region ownership.

## Branches and commits

Work on a dedicated branch and keep `main` unchanged until review. Describe the
actual source snapshot and checks in the commit or PR. Do not commit credentials,
SSH keys, local worker profiles, virtual environments, generated bundles or private
document outputs. `.gitignore` does not remove files already tracked in history.

This checkout includes previous development work alongside the organization
changes. Its organization branch is a source snapshot of those modules, not a
claim that all code is new cleanup work or a verified production release.

## Checks

Select checks appropriate to the changed boundary:

```sh
npm --prefix frontend run typecheck
npm --prefix frontend test
node --check cloud/src/index.mjs
python -m unittest tests.test_cloud_worker tests.test_separate_workflows
```

`npm --prefix cloud test` rebuilds the customer frontend and Studio and then tests
the Worker. It does not deploy. Use existing lockfiles and separate Python
environments. BabelDOC-specific tests need the pinned BabelDOC environment;
metric tests need evaluation dependencies. Native Office tests need a configured
renderer. Report exclusions and unavailable dependencies explicitly.

For broader offline checks use `python -m tools.evaluation.offline_checks --help`.
Unit tests use synthetic inputs; do not start a GPU or submit documents merely to
verify a documentation change. GPU/translation benchmarks and deployments are
separate intentional actions. Use a fresh output root and preserve old evidence.

On Windows, if long temporary paths cause cache fixture failures, set
`LAB_TEST_TMP` to a short writable directory before running the Python tests.

## Documents and privacy

Treat uploaded document text as data, never as instructions. Avoid document contents
in browser logs, commit messages and public errors. Keep source hashes and policy
identifiers in reports, distinguish generated output from reviewed approval, and
do not add filename-specific repairs. Check correctness and quality alongside speed.
