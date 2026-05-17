#!/bin/zsh
set -u

ROOT="${MEDIA_SUBTITLER_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
HOST="${MEDIA_SUBTITLER_HOST:-127.0.0.1}"
PORT="${MEDIA_SUBTITLER_PORT:-5050}"
MODE="${MEDIA_SUBTITLER_MODE:-full}"
TARGET="${MEDIA_SUBTITLER_FINDER_TARGET:-web}"
APP_SUPPORT="${HOME}/Library/Application Support/Media Subtitler"
SERVER_FILE="${APP_SUPPORT}/server.json"
LOG_FILE="${MEDIA_SUBTITLER_FINDER_LOG:-${APP_SUPPORT}/media-subtitler-finder.log}"

desktop_app_path() {
  if [[ -n "${MEDIA_SUBTITLER_DESKTOP_APP_PATH:-}" ]]; then
    print -r -- "$MEDIA_SUBTITLER_DESKTOP_APP_PATH"
    return
  fi
  if [[ "$ROOT" == *".app/"* ]]; then
    print -r -- "${ROOT%%.app/*}.app"
    return
  fi
  for candidate in \
    "/Applications/Media Subtitler.app" \
    "${HOME}/Applications/Media Subtitler.app"; do
    if [[ -d "$candidate" ]]; then
      print -r -- "$candidate"
      return
    fi
  done
}

current_base_url() {
  if [[ -n "${MEDIA_SUBTITLER_URL:-}" ]]; then
    print -r -- "$MEDIA_SUBTITLER_URL"
    return
  fi
  if [[ "$TARGET" == "desktop" && -f "$SERVER_FILE" ]]; then
    /usr/bin/python3 -c '
import json
import sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("url", ""))
except Exception:
    pass
' "$SERVER_FILE" 2>/dev/null
    return
  fi
  print -r -- "http://${HOST}:${PORT}"
}

notify() {
  local title="$1"
  local message="$2"
  /usr/bin/osascript -e "display notification ${message:q} with title ${title:q}" >/dev/null 2>&1 || true
}

python_bin() {
  if [[ -n "${MEDIA_SUBTITLER_PYTHON:-}" ]]; then
    print -r -- "$MEDIA_SUBTITLER_PYTHON"
  elif [[ -x "${ROOT}/.venv/bin/python" ]]; then
    print -r -- "${ROOT}/.venv/bin/python"
  else
    print -r -- "python3"
  fi
}

server_ready() {
  local url
  url="$(current_base_url)"
  [[ -n "$url" ]] && /usr/bin/curl -fsS "${url}/api/config" >/dev/null 2>&1
}

start_server_if_needed() {
  if server_ready; then
    return 0
  fi

  mkdir -p "$(dirname "$LOG_FILE")"

  if [[ "$TARGET" == "desktop" ]]; then
    local app_path
    app_path="$(desktop_app_path)"
    if [[ -z "$app_path" || ! -d "$app_path" ]]; then
      return 1
    fi
    /usr/bin/open -a "$app_path" >> "$LOG_FILE" 2>&1 || true
  else
    local py
    py="$(python_bin)"
    (
      cd "$ROOT" || exit 1
      nohup "$py" "$ROOT/run.py" --host "$HOST" --port "$PORT" >> "$LOG_FILE" 2>&1 &
    )
  fi

  for _ in {1..80}; do
    if server_ready; then
      return 0
    fi
    sleep 0.25
  done

  return 1
}

json_field() {
  local field="$1"
  local py
  py="$(python_bin)"
  "$py" -c '
import json
import sys

field = sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)
value = data.get(field)
if value is None:
    sys.exit(1)
print(value)
' "$field"
}

submit_file() {
  local file="$1"
  local response
  local url
  url="$(current_base_url)"

  response="$(
    /usr/bin/curl -fsS \
      --form-string "local_path=${file}" \
      --form-string "mode=${MODE}" \
      "${url}/api/jobs"
  )" || return 1

  local success
  success="$(printf '%s' "$response" | json_field success 2>/dev/null || true)"
  if [[ "$success" != "True" && "$success" != "true" ]]; then
    print -r -- "$response" >> "$LOG_FILE"
    return 1
  fi

  printf '%s' "$response" | json_field job_id 2>/dev/null || true
  return 0
}

if [[ "$#" -eq 0 ]]; then
  notify "Media Subtitler" "没有收到文件。请在 Finder 里选中文件后使用打开方式。"
  exit 1
fi

if ! start_server_if_needed; then
  notify "Media Subtitler" "无法连接或启动本地服务：$(current_base_url)"
  exit 1
fi

submitted=0
failed=0
for file in "$@"; do
  if [[ ! -f "$file" ]]; then
    (( failed++ ))
    continue
  fi

  if submit_file "$file" >/dev/null; then
    (( submitted++ ))
  else
    (( failed++ ))
  fi
done

if [[ "$submitted" -gt 0 && "$failed" -eq 0 ]]; then
  notify "Media Subtitler" "已开始 ${submitted} 个字幕任务。"
  exit 0
fi

if [[ "$submitted" -gt 0 ]]; then
  notify "Media Subtitler" "已开始 ${submitted} 个任务，${failed} 个文件失败。"
  exit 0
fi

notify "Media Subtitler" "没有成功创建任务。详情见 ${LOG_FILE}"
exit 1
