$workspaceRoot = Split-Path -Parent $PSScriptRoot
$openChatCutRoot = Join-Path $workspaceRoot 'tools\OpenChatCut'

if (-not (Test-Path -LiteralPath (Join-Path $openChatCutRoot 'package.json'))) {
    throw "OpenChatCut is not installed at $openChatCutRoot"
}

$token = [Environment]::GetEnvironmentVariable('OPENCHATCUT_MCP_TOKEN', 'User')
if ([string]::IsNullOrWhiteSpace($token)) {
    throw 'OPENCHATCUT_MCP_TOKEN is not configured in the Windows user environment.'
}
$env:OPENCHATCUT_MCP_TOKEN = $token

fnm env --shell powershell | Out-String | Invoke-Expression
fnm use 24

Push-Location $openChatCutRoot
try {
    npm run dev
}
finally {
    Pop-Location
}

