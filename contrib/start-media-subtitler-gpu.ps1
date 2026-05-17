<# 
Start and check the Windows GPU services used by media_subtitler.

Copy this file and contrib/whisper-server.py to the Windows PC, typically:
  C:\tools\media-subtitler-whisper\

Common use from a full checkout:
  cd C:\tools\media_subtitler
  .\contrib\start-media-subtitler-gpu.ps1 -OllamaModel qwen2.5:14b -WhisperModel large-v3

Common use when copied with whisper-server.py to a standalone folder:
  cd C:\tools\media-subtitler-whisper
  py -3.12 -m venv .venv
  .\.venv\Scripts\Activate.ps1
  pip install faster-whisper flask
  .\start-media-subtitler-gpu.ps1 -OllamaModel qwen2.5:14b -WhisperModel large-v3

Health check only:
  .\start-media-subtitler-gpu.ps1 -HealthOnly -OllamaModel qwen2.5:14b
#>

param(
  [string]$WhisperDir = $PSScriptRoot,
  [string]$WhisperModel = "large-v3",
  [string]$OllamaModel = "qwen2.5:14b",
  [string]$HostAddress = "0.0.0.0",
  [int]$WhisperPort = 5051,
  [int]$OllamaPort = 11434,
  [switch]$HealthOnly,
  [switch]$SkipWhisper,
  [switch]$SkipOllama,
  [switch]$PullOllamaModel
)

$ErrorActionPreference = "Stop"

function Write-Step($Message) {
  Write-Host ""
  Write-Host "==> $Message" -ForegroundColor Cyan
}

function Test-HttpJson($Url) {
  try {
    Invoke-RestMethod -Uri $Url -TimeoutSec 5 | Out-Null
    return $true
  } catch {
    return $false
  }
}

function Ensure-FirewallRule($Name, $Port) {
  try {
    $existing = Get-NetFirewallRule -DisplayName $Name -ErrorAction SilentlyContinue
    if (-not $existing) {
      New-NetFirewallRule `
        -DisplayName $Name `
        -Direction Inbound `
        -LocalPort $Port `
        -Protocol TCP `
        -Action Allow `
        -Profile Private | Out-Null
      Write-Host "Firewall rule added: $Name TCP $Port"
    } else {
      Write-Host "Firewall rule already exists: $Name"
    }
  } catch {
    Write-Warning "Could not add firewall rule '$Name'. Run PowerShell as Administrator, or allow TCP $Port manually."
  }
}

function Get-LanIpHint {
  try {
    $ip = (Get-NetIPAddress -AddressFamily IPv4 |
      Where-Object {
        $_.IPAddress -notlike "127.*" -and
        $_.IPAddress -notlike "169.254.*" -and
        $_.PrefixOrigin -ne "WellKnown"
      } |
      Select-Object -First 1 -ExpandProperty IPAddress)
    if ($ip) {
      return $ip
    }
  } catch {
    Write-Warning "Could not query LAN IP with Get-NetIPAddress. Use ipconfig to find this PC's IPv4 address."
  }
  return "<this-pc-ip>"
}

$WhisperDir = (Resolve-Path $WhisperDir).Path
$VenvSearchDirs = @($WhisperDir)
if ((Split-Path $WhisperDir -Leaf) -eq "contrib") {
  $VenvSearchDirs += (Split-Path $WhisperDir -Parent)
}
$VenvPythonCandidates = foreach ($Dir in $VenvSearchDirs) {
  Join-Path $Dir ".venv\Scripts\python.exe"
  Join-Path $Dir "venv\Scripts\python.exe"
}
$Python = $VenvPythonCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Python) {
  $Python = $VenvPythonCandidates[0]
}
$Server = Join-Path $WhisperDir "whisper-server.py"
$LanIp = Get-LanIpHint

Write-Host "media_subtitler GPU helper"
Write-Host "Whisper dir: $WhisperDir"
Write-Host "LAN IP:      $LanIp"
Write-Host "Mac GPU_BASE_URL should be: http://$LanIp"

if (-not $SkipWhisper) {
  Ensure-FirewallRule -Name "media-subtitler-whisper" -Port $WhisperPort
}
if (-not $SkipOllama) {
  Ensure-FirewallRule -Name "media_subtitler-ollama" -Port $OllamaPort
}

if (-not $SkipOllama) {
  Write-Step "Checking Ollama"
  $env:OLLAMA_HOST = "$HostAddress`:$OllamaPort"
  $currentUserHost = [Environment]::GetEnvironmentVariable("OLLAMA_HOST", "User")
  if ($currentUserHost -ne "$HostAddress`:$OllamaPort") {
    [Environment]::SetEnvironmentVariable("OLLAMA_HOST", "$HostAddress`:$OllamaPort", "User")
    Write-Warning "Set user OLLAMA_HOST=$HostAddress`:$OllamaPort. Restart Ollama after this run so the tray/service picks it up."
  }

  if (-not (Test-HttpJson "http://127.0.0.1:$OllamaPort/api/tags")) {
    Write-Host "Ollama is not responding locally; starting 'ollama serve' in a new window."
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$env:OLLAMA_HOST='$HostAddress`:$OllamaPort'; ollama serve"
    Start-Sleep -Seconds 3
  }

  if (Test-HttpJson "http://127.0.0.1:$OllamaPort/api/tags") {
    Write-Host "Ollama local health: ok"
  } else {
    Write-Warning "Ollama still is not reachable at http://127.0.0.1:$OllamaPort"
  }

  if ($PullOllamaModel) {
    Write-Host "Ensuring Ollama model exists: $OllamaModel"
    ollama pull $OllamaModel
  } else {
    $models = ""
    try { $models = (ollama list | Out-String) } catch {}
    if ($models -notmatch [regex]::Escape($OllamaModel)) {
      Write-Warning "Ollama model '$OllamaModel' was not found in 'ollama list'. Run: ollama pull $OllamaModel"
    }
  }
}

if ($HealthOnly) {
  if (-not $SkipWhisper) {
    Write-Step "Checking Whisper"
    if (Test-HttpJson "http://127.0.0.1:$WhisperPort/health") {
      Write-Host "Whisper local health: ok"
    } else {
      Write-Warning "Whisper is not reachable at http://127.0.0.1:$WhisperPort/health"
    }
  }
  Write-Host ""
  Write-Host "From the Mac, run:"
  Write-Host "  contrib/check-gpu-services.sh http://$LanIp $OllamaModel"
  exit 0
}

if (-not $SkipWhisper) {
  Write-Step "Starting Whisper server"
  if (-not (Test-Path $Python)) {
    throw "Missing virtualenv Python. Expected .venv\Scripts\python.exe or venv\Scripts\python.exe under: $($VenvSearchDirs -join ', '). Create it with: py -3.12 -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install faster-whisper flask"
  }
  if (-not (Test-Path $Server)) {
    throw "Missing whisper server: $Server"
  }

  Write-Host "Starting foreground server. Leave this window open."
  Write-Host "Mac health check: curl http://$LanIp`:$WhisperPort/health"
  & $Python $Server --host $HostAddress --port $WhisperPort --model $WhisperModel
}
