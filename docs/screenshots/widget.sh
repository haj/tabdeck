#!/bin/sh
# The widget against the demo hub, for screenshots: a throwaway "demo" setup (~/.tabdeck-demo, port 8799).
# First run the demo hub over https:  DEMO_CERT=~/.tabdeck/cert.pem DEMO_KEY=~/.tabdeck/key.pem uv run python docs/screenshots/demo.py
# Then screenshot the widget window (click its bar to expand it). Clean up afterwards with: sh docs/screenshots/widget.sh clean
set -e
if [ "$1" = clean ]; then
  pkill -f DeckWidget-demo.app || true
  rm -rf "$HOME/.tabdeck-demo" "${TMPDIR:-/tmp}/tabdeck-demo-widget"
  defaults delete com.tabdeck-demo.widget 2>/dev/null || true
  exit 0
fi
mkdir -p "$HOME/.tabdeck-demo"
printf '{"hub_url": "https://localhost:8799/", "port": 8799, "assistant_name": "Jarvis", "wake_word": "jarvis"}\n' \
  > "$HOME/.tabdeck-demo/settings.json"
OUT="${TMPDIR:-/tmp}/tabdeck-demo-widget"
TABDECK_INSTANCE=demo sh "$(dirname "$0")/../../widget/bundle.sh" "$OUT"
open -n "$OUT/DeckWidget-demo.app"
