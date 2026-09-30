param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[\p{L}\p{N}-]+$')]
    [string]$Slug,
    [string]$Date = (Get-Date -Format 'yyyy-MM-dd')
)

$workspaceRoot = Split-Path -Parent $PSScriptRoot
$projectRoot = Join-Path $workspaceRoot "projects\$Date-$Slug"

if (Test-Path -LiteralPath $projectRoot) {
    throw "Project already exists: $projectRoot"
}

# Copywriting stage only; production files are created when that stage is approved.
New-Item -ItemType Directory -Path $projectRoot | Out-Null

$templateRoot = Join-Path $workspaceRoot 'templates\project'
Copy-Item -LiteralPath (Join-Path $templateRoot 'brief.md') -Destination (Join-Path $projectRoot 'brief.md')
New-Item -ItemType File -Path (Join-Path $projectRoot 'research.md') | Out-Null

Write-Output $projectRoot

