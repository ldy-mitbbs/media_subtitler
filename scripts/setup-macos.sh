#!/usr/bin/env bash
#
# Bootstrap a fresh media_subtitler checkout on macOS.
#
# Run from anywhere:
#   ./scripts/setup-macos.sh
#
# Installs ffmpeg + whisper.cpp (Homebrew), creates .venv, installs Python
# dependencies, downloads the large-v3-turbo ggml model, and writes a
# settings.json tuned for Apple Silicon. Safe to re-run: existing tools,
# models and settings are detected and left alone.
#
# Flags:
#   --skip-model      don't download the ggml model
#   --skip-brew       don't install anything via Homebrew, just check
#   --model NAME      ggml model to install (default: large-v3-turbo)
#
# Note on the Homebrew whisper-cpp bottle: its formula leaves GGML_METAL,
# GGML_ACCELERATE and CMAKE_BUILD_TYPE at their defaults, which on Apple
# Silicon already means Metal + Accelerate + Release. It is equivalent to a
# stock `cmake -DGGML_METAL=ON -DGGML_ACCELERATE=ON` source build. The only
# from-source win worth the trouble is the CoreML encoder (Neural Engine,
# ~2-3x on the encoder) — see docs/macos-coreml.md.

set -euo pipefail

MODEL_NAME="large-v3-turbo"
SKIP_MODEL=0
SKIP_BREW=0

while [ $# -gt 0 ]; do
  case "$1" in
    --skip-model) SKIP_MODEL=1; shift ;;
    --skip-brew)  SKIP_BREW=1; shift ;;
    --model)      MODEL_NAME="${2:?--model needs a value}"; shift 2 ;;
    -h|--help)    sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }
ok()   { printf '    \033[32mok\033[0m  %s\n' "$1"; }
warn() { printf '    \033[33mwarn\033[0m %s\n' "$1"; }

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This script is macOS-only. On Windows use scripts/setup-windows.ps1." >&2
  exit 1
fi

echo "media_subtitler macOS setup"
echo "Repo: $REPO_ROOT"
[ "$(uname -m)" = "arm64" ] && ok "Apple Silicon (Metal acceleration available)" \
                            || warn "Intel Mac — whisper.cpp will run on CPU only"

# --- Homebrew -----------------------------------------------------------
step "Checking Homebrew"
BREW=""
for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew "$(command -v brew 2>/dev/null || true)"; do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then BREW="$candidate"; break; fi
done
if [ -n "$BREW" ]; then
  ok "$BREW"
else
  warn "Homebrew not found. Install it first:"
  echo '      /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  SKIP_BREW=1
fi

brew_install() {  # brew_install <formula> <binary>
  local formula="$1" binary="$2"
  if command -v "$binary" >/dev/null 2>&1; then
    ok "$binary ($(command -v "$binary"))"
    return 0
  fi
  if [ "$SKIP_BREW" = "1" ] || [ -z "$BREW" ]; then
    warn "$binary missing — install with: brew install $formula"
    return 1
  fi
  echo "    installing $formula..."
  "$BREW" install "$formula"
  ok "$binary installed"
}

# --- System tools -------------------------------------------------------
step "Checking media tools"
brew_install ffmpeg ffmpeg || true

# whisper-cli: reuse an existing build (hand-compiled checkout or Homebrew)
# before installing a second copy.
WHISPER_CLI=""
if command -v whisper-cli >/dev/null 2>&1; then
  WHISPER_CLI="$(command -v whisper-cli)"
else
  for candidate in \
    "$HOME/code/whisper.cpp/build/bin/whisper-cli" \
    "$HOME/whisper.cpp/build/bin/whisper-cli" \
    "$HOME/src/whisper.cpp/build/bin/whisper-cli" \
    /opt/homebrew/bin/whisper-cli \
    /usr/local/bin/whisper-cli
  do
    if [ -x "$candidate" ]; then WHISPER_CLI="$candidate"; break; fi
  done
fi

if [ -n "$WHISPER_CLI" ]; then
  ok "whisper-cli ($WHISPER_CLI)"
elif brew_install whisper-cpp whisper-cli; then
  WHISPER_CLI="$(command -v whisper-cli)"
fi

# --- Python environment -------------------------------------------------
step "Setting up Python environment"
if [ ! -x ".venv/bin/python" ]; then
  PYTHON_BIN=""
  for candidate in python3.12 python3.11 python3; do
    command -v "$candidate" >/dev/null 2>&1 && { PYTHON_BIN="$candidate"; break; }
  done
  [ -n "$PYTHON_BIN" ] || { echo "No python3 found. brew install python@3.12" >&2; exit 1; }
  echo "    creating .venv with $PYTHON_BIN"
  "$PYTHON_BIN" -m venv .venv
fi
ok ".venv ($(.venv/bin/python --version))"

echo "    installing dependencies..."
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
.venv/bin/pip install --quiet -e .
ok "dependencies installed"

# --- Model --------------------------------------------------------------
# The pipeline searches ~/.cache/media_subtitler/models/ggml-<ASR_MODEL>.bin
# by default, so a model placed there needs no path configuration at all.
step "Checking whisper.cpp model ($MODEL_NAME)"
CACHE_DIR="$HOME/.cache/media_subtitler/models"
MODEL_FILE="ggml-${MODEL_NAME}.bin"
MODEL_PATH=""
for candidate in \
  "$CACHE_DIR/$MODEL_FILE" \
  "$REPO_ROOT/models/$MODEL_FILE" \
  "$HOME/code/whisper.cpp/models/$MODEL_FILE" \
  "$HOME/whisper.cpp/models/$MODEL_FILE"
do
  if [ -f "$candidate" ]; then MODEL_PATH="$candidate"; break; fi
done

if [ -n "$MODEL_PATH" ]; then
  ok "$MODEL_PATH ($(du -h "$MODEL_PATH" | cut -f1))"
elif [ "$SKIP_MODEL" = "1" ]; then
  warn "skipped (--skip-model)"
else
  echo "    downloading $MODEL_FILE to $CACHE_DIR ..."
  MODEL_PATH="$(WHISPER_CPP_MODEL_PATH= .venv/bin/python -m media_subtitler.models download "$MODEL_NAME" | tail -1)"
  ok "$MODEL_PATH"
fi

# --- Settings -----------------------------------------------------------
# settings.json is the single source of truth (config.py) and is gitignored.
# Merge rather than overwrite so an existing API key survives a re-run.
step "Writing settings.json"
WHISPER_CLI="$WHISPER_CLI" MODEL_PATH="$MODEL_PATH" MODEL_NAME="$MODEL_NAME" \
CACHE_DIR="$CACHE_DIR" .venv/bin/python - <<'PY'
import json, os, pathlib

repo = pathlib.Path.cwd()
path = repo / "settings.json"
settings = {}
if path.exists():
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        settings = {}

settings["ASR_BACKEND"] = "whispercpp"
settings["ASR_MODEL"] = os.environ["MODEL_NAME"]

# Only pin an absolute CLI path when the binary is NOT on PATH — otherwise the
# built-in "whisper-cli" default is more portable across machines.
cli = os.environ.get("WHISPER_CLI") or ""
import shutil
if cli and not shutil.which("whisper-cli"):
    settings["WHISPER_CPP_COMMAND"] = cli
else:
    settings.pop("WHISPER_CPP_COMMAND", None)

# Same idea for the model: a model in the searched cache dir needs no path.
model = os.environ.get("MODEL_PATH") or ""
cache = pathlib.Path(os.environ["CACHE_DIR"]).resolve()
if model and pathlib.Path(model).resolve().parent != cache:
    settings["WHISPER_CPP_MODEL_PATH"] = model
else:
    settings["WHISPER_CPP_MODEL_PATH"] = ""

settings.setdefault("TRANSLATION_BACKEND", "deepseek")
settings.setdefault("TRANSLATION_MODEL", "deepseek-v4-flash")
settings.setdefault("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
settings.setdefault("DEEPSEEK_API_KEY", "")
settings.setdefault("TARGET_LANGUAGE", "zh")

path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("    settings.json updated")
PY

# Prompt for the DeepSeek key only when one isn't already stored.
if ! grep -q '"DEEPSEEK_API_KEY": "sk-' settings.json 2>/dev/null; then
  echo ""
  echo "    Translation uses DeepSeek. Paste an API key from"
  echo "    https://platform.deepseek.com/api_keys (or press Enter to skip)."
  printf "    DEEPSEEK_API_KEY: "
  read -r -s DEEPSEEK_KEY || DEEPSEEK_KEY=""
  echo ""
  if [ -n "${DEEPSEEK_KEY:-}" ]; then
    DEEPSEEK_KEY="$DEEPSEEK_KEY" .venv/bin/python - <<'PY'
import json, os, pathlib
path = pathlib.Path("settings.json")
s = json.loads(path.read_text(encoding="utf-8"))
s["DEEPSEEK_API_KEY"] = os.environ["DEEPSEEK_KEY"]
path.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
    ok "API key saved to settings.json (gitignored)"
  else
    warn "no key stored — set it later in the web UI settings page"
  fi
fi

# --- Verify -------------------------------------------------------------
step "Verifying configuration"
.venv/bin/python - <<'PY'
import os, shutil, sys
sys.path.insert(0, os.getcwd())
from config import Config

def check(label, value, ok_):
    mark = "\033[32mok\033[0m " if ok_ else "\033[31mFAIL\033[0m"
    print(f"    {mark} {label}: {value}")
    return ok_

good = True
good &= check("asr backend", Config.ASR_BACKEND, Config.ASR_BACKEND == "whispercpp")
good &= check("asr model", Config.ASR_MODEL, bool(Config.ASR_MODEL))

cli = Config.WHISPER_CPP_COMMAND
resolved = shutil.which(cli) or (cli if os.path.isfile(cli) else None)
good &= check("whisper-cli", resolved or cli, bool(resolved))

if Config.WHISPER_CPP_MODEL_PATH:
    good &= check("model path", Config.WHISPER_CPP_MODEL_PATH,
                  os.path.isfile(Config.WHISPER_CPP_MODEL_PATH))
else:
    cached = os.path.expanduser(
        f"~/.cache/media_subtitler/models/ggml-{Config.ASR_MODEL}.bin")
    good &= check("model (auto-discovered)", cached, os.path.isfile(cached))

good &= check("ffmpeg", shutil.which("ffmpeg") or "not found",
              bool(shutil.which("ffmpeg")))
check("translation", f"{Config.TRANSLATION_BACKEND} / {Config.TRANSLATION_MODEL}", True)
check("api key", "set" if Config.DEEPSEEK_API_KEY else "not set",
      bool(Config.DEEPSEEK_API_KEY))
sys.exit(0 if good else 1)
PY

step "Done"
cat <<'EOF'
Start the web UI:
  .venv/bin/python run.py          # http://127.0.0.1:5050

Subtitle one file from the CLI:
  .venv/bin/python subtitle_pipeline.py /path/to/video.mkv

Optional extras:
  ./scripts/build-macos-app.sh               # package the .app
  ./scripts/install-macos-finder-shortcut.sh # Finder right-click entry
  docs/macos-coreml.md                       # ~2-3x encoder via Neural Engine
EOF
