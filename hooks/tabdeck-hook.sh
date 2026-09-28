#!/bin/sh
# TabDeck: forward Claude Code hook events to the local TabDeck service.
# Must never block or break Claude: no stdout, 1 s timeout, always exit 0.
if [ -n "$ITERM_SESSION_ID" ]; then
  HEADER="X-Iterm-Session: $ITERM_SESSION_ID"
elif [ -n "$TMUX_PANE" ]; then
  HEADER="X-Tmux-Pane: $TMUX_PANE"
else
  cat >/dev/null
  exit 0
fi
# instance.env beside this script (in the data folder) names the port of this setup's service.
[ -f "$(dirname "$0")/instance.env" ] && . "$(dirname "$0")/instance.env"
curl -sk --max-time 1 -X POST "https://127.0.0.1:${TABDECK_PORT:-8765}/hook" \
  -H "Content-Type: application/json" \
  -H "$HEADER" \
  --data-binary @- >/dev/null 2>&1
exit 0
