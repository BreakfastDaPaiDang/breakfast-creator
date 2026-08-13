$workspaceRoot = Split-Path -Parent $PSScriptRoot
$runRoot = Join-Path $workspaceRoot '.run'
$runner = Join-Path $PSScriptRoot 'run-openchatcut.ps1'
$pidFile = Join-Path $runRoot 'openchatcut.pid'
$stdoutLog = Join-Path $runRoot 'openchatcut.out.log'
$stderrLog = Join-Path $runRoot 'openchatcut.err.log'

New-Item -ItemType Directory -Path $runRoot -Force | Out-Null

if (Test-Path -LiteralPath $pidFile) {
    $existingPid = Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue
    if ($existingPid -and (Get-Process -Id $existingPid -ErrorAction SilentlyContinue)) {
        Write-Output "OpenChatCut is already running (PID $existingPid)."
        exit 0
    }
}

$process = Start-Process `
    -FilePath 'powershell.exe' `
    -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $runner) `
    -WorkingDirectory $workspaceRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

Set-Content -LiteralPath $pidFile -Value $process.Id
Write-Output "OpenChatCut starting in background (PID $($process.Id))."
Write-Output "Logs: $stdoutLog"

