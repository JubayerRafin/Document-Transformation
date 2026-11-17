# Run from the user's normal PowerShell terminal, outside the agent sandbox.
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$pythonPath = Join-Path $projectRoot '.venv-paddleocr\Scripts\python.exe'
$appPath = Join-Path $projectRoot 'transform_app.py'
if (!(Test-Path -LiteralPath $pythonPath) -or !(Test-Path -LiteralPath $appPath)) {
    throw 'The TransformX environment or entry point is missing.'
}
$listeners = @(Get-NetTCPConnection -LocalPort 8504 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique)
if ($listeners.Count -gt 1) { throw 'Multiple processes own port 8504; close the old app manually.' }
if ($listeners.Count -eq 1) {
    $streamlitOwner = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $listeners[0])
    if (!$streamlitOwner -or $streamlitOwner.CommandLine -notmatch '(?i)streamlit.+\brun\b.+transform_app\.py') {
        throw 'Port 8504 belongs to an unverified process. Nothing was stopped.'
    }
    Write-Host 'Stopping the old TransformX Streamlit process...'
    Stop-Process -Id $streamlitOwner.ProcessId
    Wait-Process -Id $streamlitOwner.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
}
Set-Location -LiteralPath $projectRoot
Write-Host 'Starting updated TransformX at http://127.0.0.1:8504/'
& $pythonPath -m streamlit run $appPath --server.address 127.0.0.1 --server.port 8504 --server.fileWatcherType poll --server.runOnSave true
