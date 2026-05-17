#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER="${ROOT}/scripts/macos-start-finder-job.sh"
APP_DIR="${HOME}/Applications"
APP_PATH="${APP_DIR}/Media Subtitler Start Job.app"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer is for macOS Finder."
  exit 1
fi

if [[ ! -x "$HELPER" ]]; then
  chmod +x "$HELPER"
fi

mkdir -p "$APP_DIR"
HELPER_APPLESCRIPT="${HELPER//\\/\\\\}"
HELPER_APPLESCRIPT="${HELPER_APPLESCRIPT//\"/\\\"}"

SCRIPT_FILE="$(mktemp -t media-subtitler-finder.XXXXXX.applescript)"
cat > "$SCRIPT_FILE" <<APPLESCRIPT
property helperPath : "${HELPER_APPLESCRIPT}"

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
  set commandText to quoted form of helperPath
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
/usr/libexec/PlistBuddy -c "Set :CFBundleName Media Subtitler Start Job" "$PLIST" >/dev/null 2>&1 \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleName string Media Subtitler Start Job" "$PLIST" >/dev/null
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName Media Subtitler Start Job" "$PLIST" >/dev/null 2>&1 \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string Media Subtitler Start Job" "$PLIST" >/dev/null
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

echo "Installed: $APP_PATH"
echo "Use Finder: right-click a media file -> Open With -> Media Subtitler Start Job"
echo "The helper posts jobs to ${MEDIA_SUBTITLER_URL:-http://127.0.0.1:5050} and starts the web server if needed."
