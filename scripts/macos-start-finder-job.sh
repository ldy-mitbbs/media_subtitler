#!/bin/zsh
set -u

ROOT="${DRAMA_SUBTITLER_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
HOST="${DRAMA_SUBTITLER_HOST:-127.0.0.1}"
PORT="${DRAMA_SUBTITLER_PORT:-5050}"
BASE_URL="${DRAMA_SUBTITLER_URL:-http://${HOST}:${PORT}}"
MODE="${DRAMA_SUBTITLER_MODE:-full}"
LOG_FILE="${DRAMA_SUBTITLER_FINDER_LOG:-${ROOT}/drama-subtitler-finder.log}"

notify() {
  local title="$1"
  local message="$2"
  /usr/bin/osascript -e "display notification ${message:q} with title ${title:q}" >/dev/null 2>&1 || true
}

python_bin() {
  if [[ -n "${DRAMA_SUBTITLER_PYTHON:-}" ]]; then
    print -r -- "$DRAMA_SUBTITLER_PYTHON"
  elif [[ -x "${ROOT}/.venv/bin/python" ]]; then
    print -r -- "${ROOT}/.venv/bin/python"
  else
    print -r -- "python3"
  fi
}

server_ready() {
  /usr/bin/curl -fsS "${BASE_URL}/api/config" >/dev/null 2>&1
}

start_server_if_needed() {
  if server_ready; then
    return 0
  fi

  local py
  py="$(python_bin)"
  mkdir -p "$(dirname "$LOG_FILE")"
  (
    cd "$ROOT" || exit 1
    nohup "$py" "$ROOT/run.py" --host "$HOST" --port "$PORT" >> "$LOG_FILE" 2>&1 &
  )

  for _ in {1..40}; do
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

  response="$(
    /usr/bin/curl -fsS \
      --form-string "local_path=${file}" \
      --form-string "mode=${MODE}" \
      "${BASE_URL}/api/jobs"
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
  notify "Drama Subtitler" "没有收到文件。请在 Finder 里选中文件后使用打开方式。"
  exit 1
fi

if ! start_server_if_needed; then
  notify "Drama Subtitler" "无法连接或启动本地服务：${BASE_URL}"
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
  notify "Drama Subtitler" "已开始 ${submitted} 个字幕任务。"
  exit 0
fi

if [[ "$submitted" -gt 0 ]]; then
  notify "Drama Subtitler" "已开始 ${submitted} 个任务，${failed} 个文件失败。"
  exit 0
fi

notify "Drama Subtitler" "没有成功创建任务。详情见 ${LOG_FILE}"
exit 1
