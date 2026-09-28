#!/bin/sh
# Build the widget in release mode and assemble a signed app bundle for one TabDeck setup.
# TABDECK_INSTANCE=<name> builds DeckWidget-<name>.app (com.tabdeck-<name>.widget) for ~/.tabdeck-<name>;
# its name, wake phrase and port come from that setup's settings.json (assistant_name, wake_word, port).
set -e
cd "$(dirname "$0")"
DEST="${1:-$HOME/Applications}"
SUFFIX="${TABDECK_INSTANCE:+-$TABDECK_INSTANCE}"
DATA=".tabdeck$SUFFIX"
eval "$(python3 - "$HOME/$DATA/settings.json" <<'PY'
import json, shlex, sys
try:
    s = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    s = {}
name = str(s.get("assistant_name") or "Jarvis")
wake = " ".join(str(s.get("wake_word") or name).split())
phrase = name if wake.lower() == name.lower() else " ".join(name if w.lower() == name.lower() else w.capitalize() for w in wake.split())
print(f"ASSISTANT={shlex.quote(name)} PHRASE={shlex.quote(phrase)} PORT={int(s.get('port') or 8765)}")
PY
)"
swift build -c release
APP="$DEST/DeckWidget$SUFFIX.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp .build/release/DeckWidget "$APP/Contents/MacOS/DeckWidget"
PL="$APP/Contents/Info.plist"
cp Info.plist "$PL"
plutil -replace CFBundleIdentifier -string "com.tabdeck$SUFFIX.widget" "$PL"
plutil -replace CFBundleName -string "$ASSISTANT" "$PL"
plutil -replace CFBundleDisplayName -string "$ASSISTANT" "$PL"
plutil -replace TabDeckDataDir -string "$DATA" "$PL"
plutil -replace TabDeckPort -string "$PORT" "$PL"
plutil -replace TabDeckAssistant -string "$ASSISTANT" "$PL"
plutil -replace TabDeckWakePhrase -string "$PHRASE" "$PL"
plutil -replace NSMicrophoneUsageDescription -string "$ASSISTANT listens for '$PHRASE, ...' voice commands to control your coding-agent sessions." "$PL"
# Sign with a stable identity when one exists: macOS ties the microphone permission to the
# signature, and an ad-hoc signature changes on every build (the app then silently gets no audio).
IDENTITY="${DECK_SIGN_ID:-$(security find-identity -v -p codesigning 2>/dev/null | sed -n 's/.*"\(Apple Development:[^"]*\)".*/\1/p' | head -1)}"
codesign --force --sign "${IDENTITY:--}" "$APP"
echo "signed with: ${IDENTITY:-ad-hoc}"
echo "$APP ($ASSISTANT, \"$PHRASE\", port $PORT, ~/$DATA)"
