$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Executable = Join-Path $ProjectDir "Mode Deck.exe"
$Target = if (Test-Path -LiteralPath $Executable) {
    $Executable
} else {
    Join-Path $ProjectDir "launch_mode_deck.bat"
}
$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "Mode Deck.lnk"

$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $Target
$Shortcut.WorkingDirectory = $ProjectDir
$Shortcut.WindowStyle = 1
$Shortcut.Description = "Preview, activate, and restore Windows activity modes"
if (Test-Path -LiteralPath $Executable) {
    $Shortcut.IconLocation = "$Executable,0"
}
$Shortcut.Save()

Write-Host "Created shortcut: $ShortcutPath"
