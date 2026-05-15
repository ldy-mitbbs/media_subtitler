#!/usr/bin/env bash
# Check the remote GPU services drama_subtitler expects:
#   - faster-whisper server at GPU_BASE_URL:5051
#   - Ollama server at GPU_BASE_URL:11434
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: contrib/check-gpu-services.sh [BASE_URL] [OLLAMA_MODEL]

Examples:
  contrib/check-gpu-services.sh http://192.168.0.28 qwen2.5:14b
  GPU_BASE_URL=http://192.168.0.28 contrib/check-gpu-services.sh

If BASE_URL is omitted, the script tries GPU_BASE_URL from the environment.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

BASE_URL="${1:-${GPU_BASE_URL:-}}"
MODEL="${2:-${OLLAMA_MODEL:-qwen2.5:14b}}"

if [[ -z "$BASE_URL" ]]; then
  echo "error: missing BASE_URL. Pass http://<gpu-pc-ip> or set GPU_BASE_URL." >&2
  exit 2
fi

BASE_URL="${BASE_URL%/}"
BASE_URL="${BASE_URL%:}"
WHISPER_URL="${BASE_URL}:5051"
OLLAMA_URL="${BASE_URL}:11434"

echo "[drama_subtitler] GPU base: $BASE_URL"
echo

echo "[1/3] Whisper health: $WHISPER_URL/health"
if curl -fsS --connect-timeout 3 --max-time 10 "$WHISPER_URL/health"; then
  echo
  echo "  ok: whisper server is reachable"
else
  echo
  echo "  failed: whisper server is not reachable on $WHISPER_URL" >&2
  WHISPER_FAILED=1
fi

echo
echo "[2/3] Ollama model list: $OLLAMA_URL/api/tags"
if TAGS="$(curl -fsS --connect-timeout 3 --max-time 10 "$OLLAMA_URL/api/tags")"; then
  echo "$TAGS"
  echo
  echo "  ok: Ollama is reachable"
else
  echo
  echo "  failed: Ollama is not reachable on $OLLAMA_URL" >&2
  OLLAMA_FAILED=1
fi

echo
echo "[3/3] Ollama generate smoke test: $MODEL"
PAYLOAD="$(printf '{"model":"%s","prompt":"Translate to Simplified Chinese: Hello, this is a subtitle translation test.","stream":false}' "$MODEL")"
if curl -fsS --connect-timeout 3 --max-time 60 \
  "$OLLAMA_URL/api/generate" \
  -H 'Content-Type: application/json' \
  -d "$PAYLOAD"; then
  echo
  echo "  ok: Ollama generated with $MODEL"
else
  echo
  echo "  failed: Ollama could not generate with $MODEL" >&2
  GENERATE_FAILED=1
fi

if [[ -n "${WHISPER_FAILED:-}" || -n "${OLLAMA_FAILED:-}" || -n "${GENERATE_FAILED:-}" ]]; then
  cat >&2 <<EOF

One or more checks failed.
On the Windows PC, make sure:
  - whisper-server.py is running on 0.0.0.0:5051
  - Ollama is listening on 0.0.0.0:11434
  - Windows Firewall allows TCP 5051 and 11434 on Private networks
  - the model exists: ollama pull $MODEL
EOF
  exit 1
fi

echo
echo "All remote GPU service checks passed."
