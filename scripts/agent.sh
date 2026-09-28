#!/bin/sh
# Start OpenCode for a TabDeck session. Its config (e.g. ODS's model) and the status plugin come from
# <data dir>/opencode, written by hub-setup.sh. exec keeps the pane's command "opencode".
D=$(cd "$(dirname "$0")" && pwd)
[ -f "$D/instance.env" ] && . "$D/instance.env"
[ -n "$TABDECK_PORT" ] && export TABDECK_PORT  # the status plugin reports to the hub on this port
PATH="$HOME/.local/bin:$HOME/.opencode/bin:$PATH"
export PATH
if [ -d "$D/opencode" ]; then
  OPENCODE_CONFIG_DIR="$D/opencode"
  export OPENCODE_CONFIG_DIR
  [ -f "$OPENCODE_CONFIG_DIR/opencode.json" ] && OPENCODE_CONFIG="$OPENCODE_CONFIG_DIR/opencode.json" && export OPENCODE_CONFIG
fi
exec opencode "$@"
