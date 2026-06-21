$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkDir = Join-Path $ProjectDir "build"
$SpecDir = Join-Path $WorkDir "spec"
$Executable = Join-Path $ProjectDir "Mode Deck.exe"
$Source = Join-Path $ProjectDir "mode_deck.py"

Set-Location $ProjectDir

py -3 -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    py -3 -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller installation failed."
    }
}

if (Test-Path -LiteralPath $Executable) {
    Get-CimInstance Win32_Process |
        Where-Object { $_.ExecutablePath -eq $Executable } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Start-Sleep -Milliseconds 500
    Remove-Item -LiteralPath $Executable -Force
}

py -3 -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "Mode Deck" `
    --distpath $ProjectDir `
    --workpath $WorkDir `
    --specpath $SpecDir `
    $Source

if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Executable)) {
    throw "Mode Deck executable build failed."
}

Write-Host "Built: $Executable"
