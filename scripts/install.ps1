# Install AprilAire Home for the current Windows user.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Data = if ($env:APRILAIRE_DATA_DIR) { $env:APRILAIRE_DATA_DIR } else { Join-Path $env:APPDATA "AprilAire Home" }
New-Item -ItemType Directory -Force -Path $Data | Out-Null
$Venv = Join-Path $Data "venv"
$Python = Join-Path $Venv "Scripts\python.exe"

if (-not (Test-Path $Python)) {
  py -3.12 -m venv $Venv
  if (-not (Test-Path $Python)) {
    py -3 -m venv $Venv
  }
}

& $Python -m pip install --upgrade pip
& $Python -m pip install $Root

$Launcher = Join-Path $Data "AprilAire Home.vbs"
@"
Set shell = CreateObject("Wscript.Shell")
shell.Run """$Venv\Scripts\pythonw.exe"" -m aprilaire_homekit", 0, False
"@ | Set-Content -Encoding ASCII $Launcher

$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "AprilAire Home.lnk"
$Wsh = New-Object -ComObject WScript.Shell
$Shortcut = $Wsh.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = Join-Path $Venv "Scripts\pythonw.exe"
$Shortcut.Arguments = "-m aprilaire_homekit"
$Shortcut.WorkingDirectory = $Data
$Shortcut.WindowStyle = 7
$Shortcut.Description = "Connect an AprilAire thermostat to Apple Home"
$Shortcut.Save()

Write-Host "Installed. Use the AprilAire Home shortcut on the desktop."
Write-Host "Or run: $Venv\Scripts\aprilaire-homekit.exe"
