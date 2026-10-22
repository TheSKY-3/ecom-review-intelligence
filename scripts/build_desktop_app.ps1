[CmdletBinding()]
param(
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$DistPath = Join-Path $ProjectRoot "output\desktop"
$WorkPath = Join-Path $ProjectRoot "tmp\desktop-build"
$StorePath = Join-Path $ProjectRoot "data\public\ice_makers_public.sqlite"
$ManifestsPath = Join-Path $ProjectRoot "artifacts\manifests"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Project virtual environment is missing: $Python"
}
if (-not (Test-Path -LiteralPath $StorePath)) {
    throw "The bundled data subset is missing: $StorePath (run scripts/build_public_subset.py)"
}

New-Item -ItemType Directory -Force $DistPath | Out-Null
New-Item -ItemType Directory -Force $WorkPath | Out-Null

# PyInstaller 6 wants SOURCE:DEST for --add-data, so every source below is relative
# to the project root: an absolute Windows path would collide with the separator.
$Arguments = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--windowed",
    "--name", "EComReviewIntelligence",
    "--distpath", $DistPath,
    "--workpath", $WorkPath,
    "--specpath", $WorkPath,
    "--paths", "src",
    "--collect-all", "webview",
    "--collect-data", "ecom_review_intelligence",
    # The three-product subset and its frozen config, so the packaged app runs on a
    # machine that has neither the raw dataset nor this repository. Sources are
    # absolute because PyInstaller resolves them against --specpath, and the last
    # colon separates source from destination.
    "--add-data=${StorePath}:data/public",
    "--add-data=${ManifestsPath}:artifacts/manifests"
)

if ($OneFile) {
    $Arguments += "--onefile"
}
else {
    $Arguments += "--onedir"
}

$Arguments += "scripts/run_desktop_app.py"

Push-Location $ProjectRoot
try {
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

$Mode = if ($OneFile) { "onefile" } else { "onedir" }
$Executable = if ($OneFile) {
    Join-Path $DistPath "EComReviewIntelligence.exe"
}
else {
    Join-Path $DistPath "EComReviewIntelligence\EComReviewIntelligence.exe"
}

if (-not (Test-Path -LiteralPath $Executable)) {
    throw "PyInstaller completed without producing $Executable"
}

# PowerShell's Start-Process can report a spurious non-zero code for a
# --windowed executable; subprocess.run observes the actual process result.
$SmokeCode = "import subprocess, sys; raise SystemExit(subprocess.run([sys.argv[1], '--smoke'], check=False, timeout=120).returncode)"
& $Python -c $SmokeCode $Executable
if ($LASTEXITCODE -ne 0) {
    throw "The packaged desktop app failed its smoke check with exit code $LASTEXITCODE."
}

$SizeMb = [math]::Round((Get-Item -LiteralPath $Executable).Length / 1MB, 1)
Write-Output "DESKTOP_APP_MODE=$Mode"
Write-Output "DESKTOP_APP=$Executable"
Write-Output "DESKTOP_APP_SIZE_MB=$SizeMb"
