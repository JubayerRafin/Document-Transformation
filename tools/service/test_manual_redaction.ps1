param([string]$Python = '.venv-paddleocr/Scripts/python.exe')
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
Push-Location $projectRoot
try {
    $env:LAB_TEST_TMP = Join-Path $projectRoot 'output/manual-redaction-checks/test-tmp'
    New-Item -ItemType Directory -Force -Path $env:LAB_TEST_TMP | Out-Null
    & (Join-Path $projectRoot $Python) -m unittest tests.test_manual_redaction tests.test_cloud_worker tests.test_backend_adapter tests.test_separate_workflows tests.test_remote_pipeline tests.test_pii_credentials tests.test_pii_occurrences tests.test_privacy_proposals -q
    if ($LASTEXITCODE -ne 0) { throw 'Python regression checks failed.' }
    npm --prefix frontend run typecheck
    if ($LASTEXITCODE -ne 0) { throw 'Frontend type checking failed.' }
    npm --prefix frontend test
    if ($LASTEXITCODE -ne 0) { throw 'Frontend tests failed.' }
    npm --prefix cloud test
    if ($LASTEXITCODE -ne 0) { throw 'Production build or cloud tests failed. Do not deploy stale assets.' }
    Write-Output 'Offline checks passed. Complete the synthetic browser check before deployment; see docs/MANUAL_REDACTION.md.'
} finally { Pop-Location }
