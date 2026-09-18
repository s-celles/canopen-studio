# CAN & CANopen Studio - Automated Windows Installer
# Author: Sébastien Celles
# License: GPL-3.0-or-later

$ErrorActionPreference = "Stop"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   CAN & CANopen Studio - Automated Windows Installer       " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
Set-Location $ProjectRoot

# 1. Check / Install uv package manager
Write-Host "[1/4] Checking Python environment and 'uv' package manager..." -ForegroundColor Yellow
$uvCmd = Get-Command uv -ErrorAction SilentlyContinue

if (-not $uvCmd) {
    Write-Host "      'uv' not found. Installing uv automatically..." -ForegroundColor Cyan
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","User") + ";" + [System.Environment]::GetEnvironmentVariable("Path","Machine")
        $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
    } catch {
        Write-Warning "Could not automatically install uv. Please install uv from https://astral.sh"
    }
}

if ($uvCmd) {
    Write-Host "      Found 'uv': $($uvCmd.Source)" -ForegroundColor Green
} else {
    Write-Error "Failed to locate or install 'uv'. Aborting installation."
    exit 1
}

# 2. Synchronize virtual environment and dependencies
Write-Host "[2/4] Synchronizing virtual environment and dependencies..." -ForegroundColor Yellow
& uv sync
if ($LASTEXITCODE -ne 0) {
    Write-Error "uv sync failed with exit code $LASTEXITCODE."
    exit $LASTEXITCODE
}
Write-Host "      Environment synchronized successfully!" -ForegroundColor Green

# 3. Ensure Application Icon exists
Write-Host "[3/4] Checking application assets and icons..." -ForegroundColor Yellow
$IconPath = Join-Path $ProjectRoot "assets\icon.ico"
if (-not (Test-Path $IconPath)) {
    Write-Host "      Generating application icon..." -ForegroundColor Cyan
    & uv run --with pillow python (Join-Path $ProjectRoot "scripts\generate_icon.py")
}
if (Test-Path $IconPath) {
    Write-Host "      Application icon ready: $IconPath" -ForegroundColor Green
}

# 4. Create Desktop and Start Menu shortcuts
Write-Host "[4/4] Creating Windows shortcuts..." -ForegroundColor Yellow

$VenvPythonw = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $VenvPythonw)) {
    # Fallback to standard python.exe if pythonw is missing
    $VenvPythonw = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
}
$GuiScript = Join-Path $ProjectRoot "src\canopen_studio\gui.py"

# Function to create Windows shortcut (.lnk)
function Create-Shortcut {
    param(
        [string]$ShortcutPath,
        [string]$TargetPath,
        [string]$Arguments,
        [string]$WorkingDirectory,
        [string]$IconLocation,
        [string]$Description
    )
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $TargetPath
    $Shortcut.Arguments = $Arguments
    $Shortcut.WorkingDirectory = $WorkingDirectory
    if (Test-Path $IconLocation) {
        $Shortcut.IconLocation = "$IconLocation,0"
    }
    $Shortcut.Description = $Description
    $Shortcut.Save()
}

# Desktop Shortcut
$DesktopPath = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop)
$DesktopShortcut = Join-Path $DesktopPath "CANopen Studio.lnk"
Create-Shortcut -ShortcutPath $DesktopShortcut `
                -TargetPath $VenvPythonw `
                -Arguments "`"$GuiScript`"" `
                -WorkingDirectory $ProjectRoot `
                -IconLocation $IconPath `
                -Description "CAN & CANopen Studio - Universal Protocol Analyzer & Transmit Station"

Write-Host "      [OK] Desktop shortcut created:" -ForegroundColor Green
Write-Host "           $DesktopShortcut" -ForegroundColor Gray

# Start Menu Shortcut
$StartMenuDir = Join-Path ([System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Programs)) "CANopen Studio"
if (-not (Test-Path $StartMenuDir)) {
    New-Item -ItemType Directory -Path $StartMenuDir -Force | Out-Null
}
$StartMenuShortcut = Join-Path $StartMenuDir "CANopen Studio.lnk"
Create-Shortcut -ShortcutPath $StartMenuShortcut `
                -TargetPath $VenvPythonw `
                -Arguments "`"$GuiScript`"" `
                -WorkingDirectory $ProjectRoot `
                -IconLocation $IconPath `
                -Description "CAN & CANopen Studio - Universal Protocol Analyzer & Transmit Station"

Write-Host "      [OK] Start Menu shortcut created:" -ForegroundColor Green
Write-Host "           $StartMenuShortcut" -ForegroundColor Gray

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   Installation complete!                                   " -ForegroundColor Green
Write-Host "   You can now launch CANopen Studio from your Desktop or   " -ForegroundColor White
Write-Host "   Start Menu, or run: 'just gui' / 'uv run canopen-studio' " -ForegroundColor White
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
