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

# Step 0: Generate/refresh the .ico from the PNG
$icoPath = "$ProjectRoot\Display Control+ Logo.ico"
$pngPath = "$ProjectRoot\Display Control+ Logo.png"
Write-Host "[0/5] Generating ICO from PNG..."
& $projectPython -c "
from PIL import Image, ImageOps
img = Image.open(r'$pngPath').convert('RGBA')
sizes = [(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)]
frames = []
for w, h in sizes:
    canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    # Keep source proportions while fitting to icon-safe area for sharper desktop/taskbar rendering.
    fit = ImageOps.contain(img, (int(w * 0.84), int(h * 0.84)), Image.Resampling.LANCZOS)
    x = (w - fit.width) // 2
    y = (h - fit.height) // 2
    canvas.alpha_composite(fit, (x, y))
    frames.append(canvas)
frames[0].save(r'$icoPath', format='ICO', sizes=sizes, append_images=frames[1:])
print('ICO ready')
"

$iconArg = "--icon=$icoPath"

function Invoke-BuildStep {
    param(
        [scriptblock]$Command,
        [string]$FailureMessage
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        Write-Error $FailureMessage
        exit 1
    }
}

# Step 1: Build GUI executable
Write-Host "[1/5] Building DisplayControl.exe..."
Push-Location $ProjectRoot
Invoke-BuildStep {
    & $projectPython -m PyInstaller --noconfirm --clean --onefile --windowed --name "DisplayControl" $iconArg --distpath "$ProjectRoot\dist" "$ProjectRoot\main.py"
} "DisplayControl build failed."

# Step 2: Build tray executable
Write-Host "[2/5] Building tray.exe..."
Invoke-BuildStep {
    & $projectPython -m PyInstaller --noconfirm --clean --onefile --windowed --name "tray" $iconArg --distpath "$ProjectRoot\dist" "$ProjectRoot\tray.py"
} "tray build failed."

# Step 3: Build background executable
Write-Host "[3/5] Building overlay_bg.exe..."
Invoke-BuildStep {
    & $projectPython -m PyInstaller --noconfirm --clean --onefile --windowed --name "overlay_bg" $iconArg --distpath "$ProjectRoot\dist" "$ProjectRoot\overlay_bg.py"
} "overlay_bg build failed."
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
Invoke-BuildStep {
    & $innoSetup "$issFile"
} "Inno Setup build failed."

# Output result
Write-Host "Build complete! Installer is in $ProjectRoot\DisplayControlSetup_v1.0.9.exe"
