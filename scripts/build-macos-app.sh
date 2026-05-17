#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This build script is intended for macOS." >&2
  exit 1
fi

python3.12 -m venv .venv-desktop
source .venv-desktop/bin/activate
python -m pip install --upgrade pip wheel
python -m pip install -r requirements-desktop.txt

pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "Media Subtitler" \
  --add-data "app/templates:app/templates" \
  --add-data "app/static:app/static" \
  --add-data "scripts:scripts" \
  --hidden-import "app.routes" \
  --hidden-import "app.models.subtitle_pipeline" \
  --hidden-import "app.models.cost_estimator" \
  desktop_launcher.py

echo
echo "构建完成: $ROOT_DIR/dist/Media Subtitler.app"
echo "发布给其他机器前，请先对应用做代码签名和公证。"
