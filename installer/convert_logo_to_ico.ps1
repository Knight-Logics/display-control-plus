# PowerShell script to convert PNG logo to ICO for Windows app branding
# Requires ImageMagick (convert.exe) installed and on PATH

$png = "$PSScriptRoot\..\Display Control+ Logo.png"
$ico = "$PSScriptRoot\..\Display Control+ Logo.ico"

if (!(Test-Path $png)) {
    Write-Error "Logo PNG not found: $png"
    exit 1
}

Write-Host "Converting PNG to ICO..."
& convert.exe "$png" -resize 256x256 "$ico"
if (!(Test-Path $ico)) {
    Write-Error "ICO conversion failed."
    exit 1
}
Write-Host "ICO created: $ico"
