#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER="${ROOT}/scripts/macos-start-finder-job.sh"
APP_DIR="${HOME}/Applications"
TARGET="web"

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --target)
      TARGET="${2:-web}"
      shift 2
      ;;
    --target=*)
      TARGET="${1#*=}"
      shift
      ;;
    *)
      echo "未知参数: $1" >&2
      exit 2
      ;;
  esac
done

case "$TARGET" in
  web)
    APP_NAME="Media Subtitler 网页版启动任务"
    ;;
  desktop)
    APP_NAME="Media Subtitler 桌面版启动任务"
    ;;
  *)
    echo "无效目标: $TARGET（应为 web 或 desktop）" >&2
    exit 2
    ;;
esac

APP_PATH="${APP_DIR}/${APP_NAME}.app"
LEGACY_APP_PATH="${APP_DIR}/Media Subtitler Start Job.app"
LEGACY_WEB_APP_PATH="${APP_DIR}/Media Subtitler Web Start Job.app"
LEGACY_DESKTOP_APP_PATH="${APP_DIR}/Media Subtitler Desktop Start Job.app"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "此安装脚本仅适用于 macOS Finder。"
  exit 1
fi

if [[ ! -x "$HELPER" ]]; then
  chmod +x "$HELPER"
fi

mkdir -p "$APP_DIR"
rm -rf "$LEGACY_APP_PATH"
rm -rf "$LEGACY_WEB_APP_PATH"
rm -rf "$LEGACY_DESKTOP_APP_PATH"
HELPER_APPLESCRIPT="${HELPER//\\/\\\\}"
HELPER_APPLESCRIPT="${HELPER_APPLESCRIPT//\"/\\\"}"

SCRIPT_FILE="$(mktemp -t media-subtitler-finder.XXXXXX.applescript)"
cat > "$SCRIPT_FILE" <<APPLESCRIPT
property helperPath : "${HELPER_APPLESCRIPT}"
property finderTarget : "${TARGET}"

on run
  tell application "Finder"
    set selectedItems to selection
  end tell
  if selectedItems is {} then
    display notification "请先在 Finder 里选中一个媒体文件。" with title "Media Subtitler"
    return
  end if
  open selectedItems
end run

on open selectedItems
  set commandText to "MEDIA_SUBTITLER_FINDER_TARGET=" & quoted form of finderTarget & " " & quoted form of helperPath
  repeat with selectedItem in selectedItems
    set commandText to commandText & " " & quoted form of POSIX path of selectedItem
  end repeat
  do shell script commandText
end open
APPLESCRIPT

rm -rf "$APP_PATH"
osacompile -o "$APP_PATH" "$SCRIPT_FILE"
rm -f "$SCRIPT_FILE"

PLIST="${APP_PATH}/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleName ${APP_NAME}" "$PLIST" >/dev/null 2>&1 \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleName string ${APP_NAME}" "$PLIST" >/dev/null
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName ${APP_NAME}" "$PLIST" >/dev/null 2>&1 \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string ${APP_NAME}" "$PLIST" >/dev/null
/usr/libexec/PlistBuddy -c "Delete :CFBundleDocumentTypes" "$PLIST" >/dev/null 2>&1 || true
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes array" "$PLIST" >/dev/null
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0 dict" "$PLIST" >/dev/null
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeName string Media files" "$PLIST" >/dev/null
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeRole string Viewer" "$PLIST" >/dev/null
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:LSHandlerRank string Alternate" "$PLIST" >/dev/null
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes array" "$PLIST" >/dev/null
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes:0 string public.movie" "$PLIST" >/dev/null
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes:1 string public.audio" "$PLIST" >/dev/null
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes:2 string public.data" "$PLIST" >/dev/null

LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
if [[ -x "$LSREGISTER" ]]; then
  "$LSREGISTER" -f "$APP_PATH" >/dev/null 2>&1 || true
fi

echo "已安装: $APP_PATH"
echo "使用方式: 在 Finder 里右键媒体文件 -> 打开方式 -> ${APP_NAME}"
if [[ "$TARGET" == "desktop" ]]; then
  echo "此入口会把任务提交给正在运行的桌面应用；如有需要会先打开桌面应用。"
else
  echo "此入口会把任务提交给 ${MEDIA_SUBTITLER_URL:-http://127.0.0.1:5050}；如有需要会先启动网页服务。"
fi
