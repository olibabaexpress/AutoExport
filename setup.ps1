<#
.SYNOPSIS
    Set up AutoExport on this machine.

.DESCRIPTION
    Safe to re-run. Requires Windows and the English Ableton Live 12 UI.
    Keep app, vendor, data and .venv together in a writable folder.
    Do not copy .venv between machines; run setup on each machine.
    Explicit -ExportPath and -ProjectDir replace their saved settings.
    Missing saved folders are reported and prompted for in interactive mode.
      1. Find Python 3.11-3.13 (offers to install 3.13 with winget)
      2. Create .venv and install requirements.txt
      3. Find Tesseract OCR (offers to install it with winget)
      4. Detect and save the Tesseract path
      5. Detect Ableton Live 12 (+ Beta) and save the paths to data\config.json
      6. Install patched AbletonOSC into the User Library detected from Live preferences
      7. Save default export / project folders to data\config.json
      8. Create a desktop shortcut
      9. Print a dependency check; exit nonzero if setup is incomplete

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File setup.ps1

.EXAMPLE
    .\setup.ps1 -ExportPath "D:\Bounces" -ProjectDir "D:\Ableton Projects" -NonInteractive
#>
[CmdletBinding()]
param(
    [string]$ExportPath,
    [string[]]$ProjectDir,
    [string]$AbletonExe,
    [string]$AbletonBetaExe,
    [string]$Python,
    [switch]$NonInteractive,
    [switch]$NoShortcut,
    [switch]$SkipOsc
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$App = Join-Path $Root "app"
$Venv = Join-Path $Root ".venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
$VenvPyw = Join-Path $Venv "Scripts\pythonw.exe"
$SupportedMinor = 11..13

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "    $text" -ForegroundColor Green }
function Warn($text) { Write-Host "    $text" -ForegroundColor Yellow }

function Confirm($question) {
    if ($NonInteractive) { return $true }
    $answer = Read-Host "    $question [Y/n]"
    return ($answer -eq "" -or $answer -match "^[Yy]")
}

function Ask($question) {
    if ($NonInteractive) { return "" }
    return (Read-Host "    $question (leave blank to skip)").Trim().Trim('"')
}

function Have-Winget { return [bool](Get-Command winget -ErrorAction SilentlyContinue) }

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

function Python-Minor($exe, [string[]]$prefix = @()) {
    try {
        $out = & $exe @prefix -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out -match "^3\.(\d+)$") { return [int]$Matches[1] }
    } catch { }
    return $null
}

# Returns @(exe, prefix-args) for a supported interpreter, or $null.
function Find-Python {
    if ($Python) {
        if ($SupportedMinor -contains (Python-Minor $Python)) { return @($Python, @()) }
        throw "-Python '$Python' is not Python 3.$($SupportedMinor[0])-3.$($SupportedMinor[-1])"
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($minor in ($SupportedMinor | Sort-Object -Descending)) {
            if ((Python-Minor "py" @("-3.$minor")) -eq $minor) { return @("py", @("-3.$minor")) }
        }
    }
    foreach ($name in "python", "python3") {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notmatch "WindowsApps") {
            if ($SupportedMinor -contains (Python-Minor $cmd.Source)) { return @($cmd.Source, @()) }
        }
    }
    return $null
}

function Run-App([string[]]$arguments) {
    Push-Location -LiteralPath $App
    try {
        & $VenvPy @arguments | Out-Host
        return $LASTEXITCODE
    } finally { Pop-Location }
}

function Read-AppOutput([string[]]$arguments) {
    Push-Location -LiteralPath $App
    try {
        $output = & $VenvPy @arguments
        if ($LASTEXITCODE -ne 0) { throw "Python command failed (exit $LASTEXITCODE): $arguments" }
        return $output
    } finally { Pop-Location }
}

function ConvertTo-FullPath([string]$path) {
    $full = [IO.Path]::GetFullPath($path)
    $root = [IO.Path]::GetPathRoot($full)
    if ($full.TrimEnd('\') -eq $root.TrimEnd('\')) { return $root.TrimEnd('\') + '\' }
    return $full.TrimEnd('\')
}

Write-Host "AutoExport setup  ($Root)" -ForegroundColor White

# 1. Python ------------------------------------------------------------------
Step "Python"
$found = Find-Python
if (-not $found -and (Have-Winget) -and (Confirm "Python 3.11-3.13 not found. Install Python 3.13 with winget?")) {
    winget install --id Python.Python.3.13 -e --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "Python installation failed (winget exit $LASTEXITCODE)" }
    Refresh-Path
    $found = Find-Python
}
if (-not $found) {
    throw "Python 3.11-3.13 is required. Install it from https://www.python.org/downloads/ and re-run setup."
}
$BasePy, $BasePyArgs = $found
Ok ("Using " + (& $BasePy @BasePyArgs -c "import sys; print(sys.executable, sys.version.split()[0])"))

# 2. Virtual environment -----------------------------------------------------
Step "Virtual environment (.venv)"
if ((Test-Path $VenvPy) -and -not ($SupportedMinor -contains (Python-Minor $VenvPy))) {
    Warn "Existing .venv uses an unsupported Python - recreating it"
    Remove-Item $Venv -Recurse -Force
}
if (-not (Test-Path $VenvPy)) {
    & $BasePy @BasePyArgs -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create $Venv" }
}
& $VenvPy -m pip install --upgrade pip --quiet --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed (exit $LASTEXITCODE)" }
& $VenvPy -m pip install -r (Join-Path $Root "requirements.txt") --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
Ok "Packages installed"

# 3. Tesseract OCR -----------------------------------------------------------
Step "Tesseract OCR"
$tess = Read-AppOutput @("-c", "import system_deps; print(system_deps.find_tesseract() or '')")
if (-not $tess) {
    if ((Have-Winget) -and (Confirm "Tesseract OCR not found. Install it with winget?")) {
        winget install --id UB-Mannheim.TesseractOCR -e --source winget --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { throw "Tesseract installation failed (winget exit $LASTEXITCODE)" }
        Refresh-Path
    } else {
        Warn "Install Tesseract from https://github.com/UB-Mannheim/tesseract/wiki"
        Warn "or copy it to vendor\Tesseract-OCR, then re-run setup."
    }
}

# 4. Save the Tesseract path -------------------------------------------------
Step "Tesseract path"
$code = Run-App @("system_deps.py", "--setup")
if ($code -ne 0) { throw "Tesseract setup failed (exit $code). Install Tesseract and re-run setup." }

# 5 + 7. Ableton paths and default folders -----------------------------------
Step "Ableton Live 12 and default folders"
$cfg = Read-AppOutput @("-c", "import json, app_config, ableton_build as ab; ab.ensure_detected(); print(json.dumps({'exe': ab.get_exe(ab.RELEASE), 'beta': ab.get_exe(ab.BETA), 'export': app_config.get('export_path') or '', 'projects': app_config.get('project_directories') or []}))") | ConvertFrom-Json

if (-not $AbletonExe -and -not $AbletonBetaExe -and -not $cfg.exe -and -not $cfg.beta) {
    Warn "Ableton Live 12 was not found in the default install locations."
    $AbletonExe = Ask "Path to the Ableton Live 12 exe, or a folder containing it"
}
$exportValid = $cfg.export -and (Test-Path -LiteralPath $cfg.export -PathType Container)
if ($cfg.export -and -not $exportValid) { Warn "Saved export folder no longer exists: $($cfg.export)" }
if (-not $ExportPath -and -not $exportValid) {
    $ExportPath = Ask "Default export folder"
}
$missingProjects = @($cfg.projects | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Container) })
foreach ($dir in $missingProjects) { Warn "Saved project folder no longer exists: $dir" }
if (-not $ProjectDir -and ($cfg.projects.Count -eq 0 -or $missingProjects.Count -gt 0)) {
    $answer = Ask "Folder containing your Ableton projects"
    if ($answer) { $ProjectDir = @($answer) }
}

$configArgs = @("system_deps.py", "--overwrite")
if ($AbletonExe)     { $configArgs += @("--ableton-exe", (ConvertTo-FullPath $AbletonExe)) }
if ($AbletonBetaExe) { $configArgs += @("--ableton-beta-exe", (ConvertTo-FullPath $AbletonBetaExe)) }
if ($ExportPath)     { $configArgs += @("--export-path", (ConvertTo-FullPath $ExportPath)) }
foreach ($dir in $ProjectDir) { if ($dir) { $configArgs += @("--project-dir", (ConvertTo-FullPath $dir)) } }
if ($configArgs.Count -gt 2) {
    $code = Run-App $configArgs
    if ($code -ne 0) { throw "Could not save setup choices (exit $code)" }
}

# 6. AbletonOSC -------------------------------------------------------------
Step "AbletonOSC remote script"
if ($SkipOsc) {
    Warn "Skipped (-SkipOsc)"
} else {
    $oscArgs = @("osc_installer.py")
    $code = Run-App $oscArgs
    if ($code -eq 0) {
        Ok "In Live: Settings > Link, Tempo & MIDI > Control Surface > choose 'AbletonOSC'."
    } else {
        throw "AbletonOSC installation failed (exit $code). Open Live 12 once, confirm its User Library preferences, close Live and re-run setup."
    }
}

# 8. Shortcut ---------------------------------------------------------------
if (-not $NoShortcut) {
    Step "Desktop shortcut"
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "AutoExport.lnk"))
    $link.TargetPath = $VenvPyw
    $link.Arguments = '"' + (Join-Path $App "GUI.py") + '"'
    $link.WorkingDirectory = $App
    $link.Description = "Ableton AutoExport"
    $link.Save()
    Ok "Created AutoExport.lnk on the desktop"
}

# 9. Summary ----------------------------------------------------------------
Step "Dependency check"
$checkArgs = @("system_deps.py", "--check")
if ($SkipOsc) { $checkArgs += "--skip-osc" }
$code = Run-App $checkArgs
Write-Host ""
if ($code -eq 0) {
    Write-Host "Setup complete." -ForegroundColor Green
    exit 0
} else {
    Write-Host "Setup incomplete (see [!!] above). Fix them and re-run setup.ps1." -ForegroundColor Yellow
    exit 1
}
