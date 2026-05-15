<#
Quick local readiness check for Windows.

This does not call paid APIs or transcribe media. It verifies Python imports,
ffmpeg availability, optional Ollama reachability, and CUDA visibility through
PyTorch if torch happens to be installed.
#>

param(
  [string]$OllamaUrl = "http://127.0.0.1:11434"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
  throw "Missing .venv. Run .\scripts\setup-windows.ps1 first."
}

Set-Location $RepoRoot
$env:PYTHONUTF8 = "1"

Write-Host "Python:"
& $Python --version

Write-Host ""
Write-Host "Python package imports:"
$ImportCheck = @'
import importlib
mods = ["flask", "requests", "faster_whisper", "drama_subtitler"]
for name in mods:
    try:
        importlib.import_module(name)
        print(f"  {name}: ok")
    except Exception as exc:
        print(f"  {name}: failed ({type(exc).__name__}: {exc})")
'@
$ImportCheck | & $Python -

Write-Host ""
Write-Host "ffmpeg:"
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
  ffmpeg -version | Select-Object -First 1
} else {
  Write-Warning "ffmpeg not found. Install with: winget install Gyan.FFmpeg"
}

Write-Host ""
Write-Host "Ollama:"
try {
  $tags = Invoke-RestMethod -Uri "$OllamaUrl/api/tags" -TimeoutSec 3
  $count = @($tags.models).Count
  Write-Host "  reachable at $OllamaUrl ($count models)"
} catch {
  Write-Host "  not reachable at $OllamaUrl (only needed for TRANSLATION_BACKEND=ollama)"
}

Write-Host ""
Write-Host "CUDA hint:"
$CudaCheck = @'
try:
    import torch
    print(f"  torch cuda available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"  device: {torch.cuda.get_device_name(0)}")
except Exception:
    print("  torch not installed; faster-whisper may still use CUDA via ctranslate2")
'@
$CudaCheck | & $Python -
