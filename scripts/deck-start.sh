#!/bin/sh
# Keep the tmux session that holds one window per agent session (default "deck") and bring back the
# windows listed in <data dir>/<session>.tsv, e.g. after a reboot. Safe to run any number of times.
# The data dir is where this script lives (~/.tabdeck, or ~/.tabdeck-<instance>); instance.env there
# names the session and the agent (claude or opencode).
D=$(cd "$(dirname "$0")" && pwd)
case "$(basename "$D")" in .tabdeck*) ;; *) D="$HOME/.tabdeck${TABDECK_INSTANCE:+-$TABDECK_INSTANCE}" ;; esac
TABDECK_SESSION=deck
TABDECK_AGENT=claude
[ -f "$D/instance.env" ] && . "$D/instance.env"
S="$TABDECK_SESSION"
PATH="$HOME/.local/bin:$PATH"
export PATH
LIST="$D/$S.tsv"
# "_keep" is a plain shell: the session outlives its agent windows.
tmux has-session -t "=$S" 2>/dev/null || tmux new-session -d -s "$S" -n _keep || exit 1
[ -f "$LIST" ] || exit 0
open=$(tmux list-windows -t "=$S" -F '#{window_name}')
tab=$(printf '\t')
while IFS="$tab" read -r name cwd; do
  [ -n "$name" ] && [ -d "$cwd" ] || continue
  printf '%s\n' "$open" | grep -qxF -- "$name" && continue
  if [ "$TABDECK_AGENT" = opencode ]; then
    # Resume that folder's last OpenCode session; agent.sh adds its config (e.g. ODS's model, status plugin).
    cmd="$D/agent.sh --continue"
  else
    # Resume the folder's last Claude conversation if there is one (its folder name is the path with
    # every other character replaced by "-"); a lone command is exec'd, so the pane runs claude itself.
    conv="$HOME/.claude/projects/$(printf '%s' "$cwd" | sed 's/[^A-Za-z0-9]/-/g')"
    if ls "$conv"/*.jsonl >/dev/null 2>&1; then cmd="claude --continue"; else cmd="claude"; fi
  fi
  tmux new-window -d -t "=$S:" -n "$name" -c "$cwd" -e "PATH=$PATH" "$cmd"
  open="$open
$name"
done < "$LIST"
exit 0
