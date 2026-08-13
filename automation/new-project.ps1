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

New-Item -ItemType Directory -Path $projectRoot | Out-Null
foreach ($directory in @('assets\raw', 'assets\generated', 'timeline', 'renders', 'tmp')) {
    New-Item -ItemType Directory -Path (Join-Path $projectRoot $directory) | Out-Null
}

$templateRoot = Join-Path $workspaceRoot 'templates\project'
Copy-Item -LiteralPath (Join-Path $templateRoot 'brief.md') -Destination (Join-Path $projectRoot 'brief.md')
Copy-Item -LiteralPath (Join-Path $templateRoot 'manifest.yaml') -Destination (Join-Path $projectRoot 'manifest.yaml')
Copy-Item -LiteralPath (Join-Path $templateRoot 'review.md') -Destination (Join-Path $projectRoot 'review.md')
New-Item -ItemType File -Path (Join-Path $projectRoot 'research.md') | Out-Null
New-Item -ItemType File -Path (Join-Path $projectRoot 'script.md') | Out-Null
New-Item -ItemType File -Path (Join-Path $projectRoot 'storyboard.yaml') | Out-Null

Write-Output $projectRoot

