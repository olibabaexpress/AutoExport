<#
.SYNOPSIS
    Build the offline Windows x64 AutoExport application and Inno Setup installer.
.DESCRIPTION
    Build-machine requirements: Python 3.11-3.13 x64, Inno Setup 6,
    and a Windows Tesseract installation with English tessdata and license docs.
    Build dependencies go into .build-venv, never the application's .venv.
    End-user machines do not need Python, pip, winget or internet access.
#>
[CmdletBinding()]
param(
    [string]$Python = (Join-Path $PSScriptRoot ".venv\Scripts\python.exe"),
    [string]$TesseractDir,
    [string]$InnoCompiler = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version = "0.1.8"
)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$BuildPython = Join-Path $Root ".build-venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Python not found: $Python" }
if (-not (Test-Path -LiteralPath $InnoCompiler -PathType Leaf)) {
    throw "Inno Setup compiler not found: $InnoCompiler. Install Inno Setup 6 or pass -InnoCompiler."
}
& $Python -c "import sys, struct; assert sys.platform == 'win32' and struct.calcsize('P') == 8 and (3, 11) <= sys.version_info[:2] <= (3, 13), 'Windows x64 Python 3.11-3.13 is required'"
if ($LASTEXITCODE -ne 0) { throw "Unsupported build interpreter" }
if (-not $TesseractDir) {
    $TesseractDir = & $Python -c "import sys, pathlib; sys.path.insert(0, str(pathlib.Path(sys.argv[1]) / 'app')); import system_deps; p = system_deps.find_tesseract(); print(str(pathlib.Path(p).parent) if p else '')" $Root
    if ($LASTEXITCODE -ne 0) { throw "Tesseract detection failed" }
}
if (-not $TesseractDir -or -not (Test-Path -LiteralPath (Join-Path $TesseractDir "tessdata\eng.traineddata"))) {
    throw "Tesseract with English OCR data is required. Pass -TesseractDir to its installation folder."
}
if (-not (Test-Path -LiteralPath $BuildPython)) {
    & $Python -m venv (Join-Path $Root ".build-venv")
    if ($LASTEXITCODE -ne 0) { throw "Could not create the build environment" }
}
& $BuildPython -m pip install -r (Join-Path $Root "requirements-build.txt") --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw "Build dependency installation failed" }
$previousTesseract = $env:AUTOEXPORT_TESSERACT_DIR
$env:AUTOEXPORT_TESSERACT_DIR = [IO.Path]::GetFullPath($TesseractDir)
Push-Location -LiteralPath $Root
try {
    & $BuildPython (Join-Path $Root "packaging\wizard_assets.py")
    if ($LASTEXITCODE -ne 0) { throw "Installer artwork generation failed" }
    & $BuildPython -m PyInstaller --noconfirm --distpath (Join-Path $Root "dist") --workpath (Join-Path $Root "build\pyinstaller") (Join-Path $Root "packaging\AutoExport.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }
    $TestHome = Join-Path ([IO.Path]::GetTempPath()) ("AutoExportBundleTest-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $TestHome | Out-Null
    $previousHome = $env:AUTOEXPORT_HOME
    $env:AUTOEXPORT_HOME = $TestHome
    try {
        $worker = Start-Process -FilePath (Join-Path $Root "dist\AutoExport\AutoExportWorker.exe") -ArgumentList @(
            "self-test", "--output", ('"' + (Join-Path $TestHome "result.json") + '"')
        ) -PassThru
        if (-not $worker.WaitForExit(90000)) {
            Stop-Process -Id $worker.Id
            throw "Background runtime smoke test timed out"
        }
        if ($worker.ExitCode -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $TestHome "result.json"))) {
            Get-ChildItem -LiteralPath (Join-Path $TestHome "LOGS") -Filter "*.log" | ForEach-Object {
                Get-Content -LiteralPath $_.FullName | Out-Host
            }
            throw "Frozen runtime smoke test failed; installer was not built"
        }
        $result = Get-Content -LiteralPath (Join-Path $TestHome "result.json") -Raw | ConvertFrom-Json
        if (-not $result.frozen -or -not $result.osc_bundled -or -not $result.multiprocessing) {
            throw "Frozen runtime smoke test returned an incomplete result"
        }
        $GuiResult = Join-Path $TestHome "gui-result.json"
        $gui = Start-Process -FilePath (Join-Path $Root "dist\AutoExport\AutoExport.exe") -ArgumentList @(
            "--self-test", "--output", ('"' + $GuiResult + '"')
        ) -PassThru
        if (-not $gui.WaitForExit(90000)) {
            Stop-Process -Id $gui.Id
            $startupLog = Join-Path $TestHome "LOGS\startup-error.log"
            if (Test-Path -LiteralPath $startupLog) { Get-Content -LiteralPath $startupLog | Out-Host }
            throw "Windowed runtime smoke test timed out"
        }
        if ($gui.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $GuiResult)) {
            throw "Windowed runtime smoke test failed (exit $($gui.ExitCode))"
        }
        $result = Get-Content -LiteralPath $GuiResult -Raw | ConvertFrom-Json
        if (-not $result.frozen -or -not $result.export_imported -or -not $result.multiprocessing) {
            throw "Windowed runtime smoke test returned an incomplete result"
        }
    } finally {
        $env:AUTOEXPORT_HOME = $previousHome
        Remove-Item -LiteralPath $TestHome -Recurse -Force
    }
    & $InnoCompiler "/DAppVersion=$Version" (Join-Path $Root "packaging\AutoExport.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed" }
    Write-Host "Installer ready: $(Join-Path $Root "dist\installer\AutoExport-Setup-$Version.exe")"
} finally {
    Pop-Location
    $env:AUTOEXPORT_TESSERACT_DIR = $previousTesseract
}
