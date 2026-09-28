#!/bin/sh
# Remember the agent windows of the tmux session (name and folder) so deck-start.sh can bring them back.
D=$(cd "$(dirname "$0")" && pwd)
case "$(basename "$D")" in .tabdeck*) ;; *) D="$HOME/.tabdeck${TABDECK_INSTANCE:+-$TABDECK_INSTANCE}" ;; esac
TABDECK_SESSION=deck
TABDECK_AGENT=claude
[ -f "$D/instance.env" ] && . "$D/instance.env"
S="$TABDECK_SESSION"
PATH="$HOME/.local/bin:$PATH"
export PATH
LIST="$D/$S.tsv"
# No session yet (e.g. just rebooted, before deck-start.sh ran): keep the old list.
tmux has-session -t "=$S" 2>/dev/null || exit 0
mkdir -p "$D"
old="$LIST"
[ -f "$old" ] || old=/dev/null
# A listed window stays listed while it is open, even if the agent is briefly not in the foreground.
# tmux escapes tabs in formats, so fields are split by "|:|" (names and paths may hold spaces).
tmux list-windows -t "=$S" -F '#{pane_current_command}|:|#{window_name}|:|#{pane_current_path}' |
  awk -v agent="$TABDECK_AGENT" 'FILENAME != "-" { split($0, f, "\t"); keep[f[1]] = 1; next }
    { n = split($0, f, /\|:\|/)
      if (n == 3 && f[2] != "_keep" && (f[1] == agent || (f[2] in keep))) print f[2] "\t" f[3] }' "$old" - \
  > "$LIST.tmp" && mv "$LIST.tmp" "$LIST"
