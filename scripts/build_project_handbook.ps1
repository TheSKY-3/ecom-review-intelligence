[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SourceDir = Join-Path $ProjectRoot "docs/project-handbook"
$BuildDir = Join-Path $ProjectRoot "tmp/pdfs/project-handbook/build"
$PublicOutput = "output/pdf/ecom-review-intelligence-project-handbook.pdf"
$OutputPdf = Join-Path $ProjectRoot $PublicOutput

if (-not (Test-Path -LiteralPath (Join-Path $SourceDir "main.tex"))) {
    throw "Handbook source main.tex is missing."
}

$Tectonic = $null
$Installed = Get-Command tectonic -ErrorAction SilentlyContinue
if ($Installed) {
    $Tectonic = $Installed.Source
} else {
    $Portable = Join-Path $ProjectRoot ".tools/tectonic/tectonic.exe"
    if (Test-Path -LiteralPath $Portable) {
        $Tectonic = $Portable
    }
}

if (-not $Tectonic) {
    throw "Tectonic was not found on PATH or in .tools/tectonic/."
}

New-Item -ItemType Directory -Force $BuildDir | Out-Null
New-Item -ItemType Directory -Force (Split-Path -Parent $OutputPdf) | Out-Null

Push-Location $SourceDir
try {
    foreach ($Pass in 1..2) {
        & $Tectonic -X compile main.tex --outdir $BuildDir --keep-logs --keep-intermediates
        if ($LASTEXITCODE -ne 0) {
            throw "Tectonic compilation pass $Pass failed with exit code $LASTEXITCODE."
        }
    }
} finally {
    Pop-Location
}

$BuiltPdf = Join-Path $BuildDir "main.pdf"
if (-not (Test-Path -LiteralPath $BuiltPdf)) {
    throw "Tectonic completed without producing main.pdf."
}

Copy-Item -LiteralPath $BuiltPdf -Destination $OutputPdf -Force
Write-Output "HANDBOOK_PDF=$PublicOutput"
