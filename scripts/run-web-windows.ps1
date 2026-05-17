<#
Run the media_subtitler Flask UI on Windows from a fresh checkout.

Usage:
  .\scripts\run-web-windows.ps1
  .\scripts\run-web-windows.ps1 -HostAddress 0.0.0.0 -Port 5050 -MediaDir D:\Videos
#>

param(
  [string]$HostAddress = "127.0.0.1",
  [int]$Port = 5050,
  [string]$MediaDir = "",
  [switch]$Browser
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
  throw "Missing .venv. Run .\scripts\setup-windows.ps1 first."
}

Set-Location $RepoRoot
$env:PYTHONUTF8 = "1"

$Args = @("run.py", "--host", $HostAddress, "--port", "$Port")
if ($MediaDir) {
  $Args += @("--media-dir", $MediaDir)
}
if ($Browser) {
  $Args += "--browser"
}

& $Python @Args
