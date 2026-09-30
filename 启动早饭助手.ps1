$ErrorActionPreference = 'Stop'
$breakfastRoot = $PSScriptRoot
$breakfastUrl = 'http://localhost:8765'
$breakfastRunning = $false
try {
    $breakfastResponse = Invoke-WebRequest -UseBasicParsing -Uri $breakfastUrl -TimeoutSec 2
    $breakfastRunning = $breakfastResponse.Content -match 'studio-token'
} catch { }
if (-not $breakfastRunning) {
    $breakfastPython = Join-Path $breakfastRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $breakfastPython)) { throw '请先让 Agent 完成本地 Python 环境配置。' }
    $breakfastLogs = Join-Path $breakfastRoot '.run'
    New-Item -ItemType Directory -Force -Path $breakfastLogs | Out-Null
    $breakfastProcess = Start-Process -FilePath $breakfastPython -ArgumentList @('-m', 'breakfast_assistant.cli', 'serve') -WorkingDirectory $breakfastRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $breakfastLogs 'breakfast-ui.out.log') -RedirectStandardError (Join-Path $breakfastLogs 'breakfast-ui.err.log') -PassThru
    $breakfastProcess.Id | Set-Content (Join-Path $breakfastLogs 'breakfast-ui.pid')
    for ($breakfastAttempt = 0; $breakfastAttempt -lt 20; $breakfastAttempt++) {
        try {
            $breakfastResponse = Invoke-WebRequest -UseBasicParsing -Uri $breakfastUrl -TimeoutSec 1
            if ($breakfastResponse.Content -match 'studio-token') { $breakfastRunning = $true; break }
        } catch { }
        Start-Sleep -Milliseconds 200
    }
}
if (-not $breakfastRunning) { throw '本地服务未能启动，请让 Agent 检查端口和运行日志。' }
Start-Process $breakfastUrl
