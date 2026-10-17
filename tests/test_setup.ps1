param([string]$Python)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $root "setup.ps1"), [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count) { throw ($parseErrors.Message -join "`n") }

# Load only helpers: never install packages, write config or create shortcuts.
foreach ($name in "Run-App", "Read-AppOutput", "ConvertTo-FullPath") {
    $definition = $ast.Find({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    }, $true)
    Invoke-Expression $definition.Extent.Text
}
$App = Join-Path $root "app"
if (-not $Python) { $Python = Join-Path $root ".venv\Scripts\python.exe" }
$VenvPy = $Python
$initialLocation = (Get-Location).Path

$code = Run-App @("-c", "print('Visible dependency status')")
if ($code -isnot [int] -or $code -ne 0) { throw "Run-App mixed stdout with the exit code" }
$code = Run-App @("-c", "import sys; print('Visible failure details'); sys.exit(7)")
if ($code -isnot [int] -or $code -ne 7) { throw "Run-App lost the failure exit code" }
$code = Run-App @("-c", "import sys; print('Visible warning', file=sys.stderr); print('Visible status')")
if ($code -isnot [int] -or $code -ne 0) { throw "Run-App confused stderr with a failed command" }
if ((Get-Location).Path -ne $initialLocation) { throw "Run-App did not restore the working directory" }

$output = Read-AppOutput @("-c", "print('captured JSON')")
if ($output -ne "captured JSON") { throw "Read-AppOutput did not preserve stdout" }
$failed = $false
try { Read-AppOutput @("-c", "import sys; sys.exit(9)") | Out-Null }
catch {
    $failed = $true
    if ($_.Exception.Message -notmatch "exit 9") { throw }
}
if (-not $failed) { throw "Read-AppOutput did not reject a failed command" }
if ((Get-Location).Path -ne $initialLocation) { throw "Read-AppOutput did not restore the working directory" }

foreach ($path in @("D:\", "\\server\share\", "D:\Folder With Spaces\", "D:\Folder[1]\")) {
    $normalized = ConvertTo-FullPath $path
    $expected = $path
    if ($path -like "D:\Folder*") { $expected = $path.TrimEnd('\') }
    if ($normalized -ne $expected) { throw "Incorrect normalization: $path -> $normalized" }
    $actual = Read-AppOutput @("-c", "import sys; print(sys.argv[1])", $normalized)
    if ($actual -ne $expected) { throw "Native argument changed: $expected -> $actual" }
}

Write-Host "PowerShell setup regression tests passed."
