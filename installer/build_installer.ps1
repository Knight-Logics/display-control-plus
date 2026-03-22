# PowerShell script to build and package Display Control+
# 1. Build DisplayControl.exe, tray.exe, and overlay_bg.exe with PyInstaller
# 2. Build installer with Inno Setup
# 3. Output ready-to-distribute installer

param(
    [string]$ProjectRoot = "$PSScriptRoot\.."
)

$ErrorActionPreference = 'Stop'

$projectPython = Join-Path $ProjectRoot "venv\Scripts\python.exe"
if (-not (Test-Path $projectPython)) {
    Write-Error "Project venv python not found: $projectPython"
    exit 1
}

# Step 1: Build GUI executable
Write-Host "[1/4] Building DisplayControl.exe..."
Push-Location $ProjectRoot
try {
    & $projectPython -m PyInstaller --noconfirm --clean --onefile --windowed --name "DisplayControl" --distpath "$ProjectRoot\dist" "$ProjectRoot\main.py"
} catch {
    Write-Error "DisplayControl build failed: $_"
    exit 1
}

# Step 2: Build tray executable
Write-Host "[2/5] Building tray.exe..."
try {
    & $projectPython -m PyInstaller --noconfirm --clean --onefile --windowed --name "tray" --distpath "$ProjectRoot\dist" "$ProjectRoot\tray.py"
} catch {
    Write-Error "tray build failed: $_"
    exit 1
}

# Step 3: Build background executable
Write-Host "[3/5] Building overlay_bg.exe..."
try {
    & $projectPython -m PyInstaller --noconfirm --clean --onefile --console --name "overlay_bg" --distpath "$ProjectRoot\dist" "$ProjectRoot\overlay_bg.py"
} catch {
    Write-Error "overlay_bg build failed: $_"
    exit 1
}
Pop-Location

# Step 4: Validate core artifacts exist
Write-Host "[4/5] Validating build artifacts..."
$required = @(
    "$ProjectRoot\dist\DisplayControl.exe",
    "$ProjectRoot\dist\tray.exe",
    "$ProjectRoot\dist\overlay_bg.exe"
)
foreach ($artifact in $required) {
    if (-not (Test-Path $artifact)) {
        Write-Error "Missing required artifact: $artifact"
        exit 1
    }
}

# Step 5: Build installer with Inno Setup
Write-Host "[5/5] Building installer with Inno Setup..."
$innoSetup = "ISCC.exe"
$innoCandidates = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
)
if (-not (Get-Command $innoSetup -ErrorAction SilentlyContinue)) {
    $resolved = $innoCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($resolved) {
        $innoSetup = $resolved
    } else {
        Write-Error "Inno Setup compiler not found. Install Inno Setup or add ISCC.exe to PATH."
        exit 1
    }
}
$issFile = "$PSScriptRoot\OLEDProtector.iss"
try {
    & $innoSetup "$issFile"
} catch {
    Write-Error "Inno Setup build failed: $_"
    exit 1
}

# Output result
Write-Host "Build complete! Installer is in $ProjectRoot\DisplayControlSetup_v1.0.2.exe"
