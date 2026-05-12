<#
Bootstrap a fresh drama_subtitler checkout on Windows.

Run from the repository root:
  Set-ExecutionPolicy -Scope Process Bypass
  .\scripts\setup-windows.ps1

The script creates .venv, installs Python dependencies, copies .env.example to
.env if needed, and prints the remaining system checks.
#>

param(
  [string]$Python = "py -3.12",
  [switch]$SkipEditableInstall
)

$ErrorActionPreference = "Stop"

function Write-Step($Message) {
  Write-Host ""
  Write-Host "==> $Message" -ForegroundColor Cyan
}

function Test-Command($Name) {
  return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-ConfiguredPython {
  param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)
  $PythonParts = $Python -split " "
  $Exe = $PythonParts[0]
  $BaseArgs = @()
  if ($PythonParts.Length -gt 1) {
    $BaseArgs = $PythonParts[1..($PythonParts.Length - 1)]
  }
  & $Exe @BaseArgs @Args
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

Write-Host "drama_subtitler Windows setup"
Write-Host "Repo: $RepoRoot"

Write-Step "Checking Python"
Invoke-ConfiguredPython --version

if (-not (Test-Path ".venv\Scripts\python.exe")) {
  Write-Step "Creating virtual environment"
  Invoke-ConfiguredPython -m venv .venv
}

$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$VenvPip = Join-Path $RepoRoot ".venv\Scripts\pip.exe"

Write-Step "Upgrading pip"
& $VenvPython -m pip install --upgrade pip

Write-Step "Installing dependencies"
& $VenvPip install -r requirements.txt
if (-not $SkipEditableInstall) {
  & $VenvPip install -e .
}

if (-not (Test-Path ".env")) {
  Write-Step "Creating .env from .env.example"
  Copy-Item ".env.example" ".env"
  Write-Host "Edit .env and add DEEPSEEK_API_KEY, OPENROUTER_API_KEY, or choose local Ollama."
}

Write-Step "Checking system tools"
if (Test-Command "ffmpeg") {
  Write-Host "ffmpeg: ok"
} else {
  Write-Warning "ffmpeg was not found. Install it, then reopen PowerShell:"
  Write-Host "  winget install Gyan.FFmpeg"
}

if (Test-Command "ollama") {
  Write-Host "ollama: ok"
} else {
  Write-Host "ollama: not found (only needed for TRANSLATION_BACKEND=ollama)"
  Write-Host "  winget install Ollama.Ollama"
}

Write-Step "Done"
Write-Host "Run the web app:"
Write-Host "  .\scripts\run-web-windows.ps1"
Write-Host ""
Write-Host "Run one file from PowerShell:"
Write-Host "  .\.venv\Scripts\python.exe .\subtitle_pipeline.py C:\path\to\episode.mkv --translation-backend deepseek --target-language zh"
