#!/bin/sh
# One-time and repeat setup of a TabDeck hub on a Linux server. Runs as the user; no sudo.
# deploy-hub.sh passes the settings below (from deploy.env); the hub's settings.json is only seeded once.
set -e
: "${HUB_IP:?HUB_IP must be set to the server NetBird IP; deploy-hub.sh passes it}"
SUFFIX="${TABDECK_INSTANCE:+-$TABDECK_INSTANCE}"
PORT="${HUB_PORT:-8765}"
AGENT="${HUB_AGENT:-claude}"
SESSION="${HUB_SESSION:-deck}"
REPO="${HUB_REPO:-tabdeck$SUFFIX}"
D="$HOME/.tabdeck$SUFFIX"
export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
case "$AGENT" in
  claude) command -v claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash ;;
  opencode) command -v opencode >/dev/null || echo "NOTE: opencode not found (install it, e.g. with ODS or opencode.ai)" ;;
  *) echo "unknown HUB_AGENT $AGENT (claude or opencode)"; exit 1 ;;
esac
command -v tmux >/dev/null || echo "NOTE: tmux is not installed. Run: sudo apt install tmux"
cd "$HOME/$REPO"
uv sync -q
mkdir -p "$D" ~/Projects ~/.config/systemd/user
chmod 700 "$D"
# What the session scripts need to know (deck-start.sh, deck-save.sh, agent.sh read it).
printf 'TABDECK_SESSION=%s\nTABDECK_AGENT=%s\nTABDECK_PORT=%s\n' "$SESSION" "$AGENT" "$PORT" > "$D/instance.env"
KEY=""
if [ "${HUB_ODS:-0}" = 1 ]; then
  # ODS (https://github.com/Osmantic/ODS) on this server: its LiteLLM key reaches ODS's current model.
  KEY=$(sed -n 's/^LITELLM_KEY=//p' "${ODS_DIR:-$HOME/ods}/.env" 2>/dev/null | head -1)
  LLM="\"llm_api\": \"openai\", \"llm_url\": \"http://$HUB_IP:4000/v1\", \"llm_key\": \"$KEY\", \"intent_model\": \"default\",
 \"intent_timeout\": 20, \"summary_timeout\": 60, \"stt_url\": \"http://$HUB_IP:9100\", \"tts_url\": \"http://$HUB_IP:8880\","
else
  LLM="${HUB_LLM_JSON:-}"  # e.g. "ollama_url": "http://localhost:11434", "tts_url": "http://...:8880",
fi
[ -f "$D/settings.json" ] || cat > "$D/settings.json" <<JSON
{"source": "tmux", "hub_server": "$(hostname | tr 'A-Z' 'a-z')", "port": $PORT, "agent": "$AGENT", "tmux_session": "$SESSION",
 "assistant_name": "${HUB_ASSISTANT:-Jarvis}", "wake_word": "${HUB_WAKE:-jarvis}", $LLM "tts_voice": "af_heart"}
JSON
chmod 600 "$D/settings.json"
if [ "$AGENT" = opencode ]; then
  # OpenCode for these sessions: the status plugin, plus ODS's model when HUB_ODS=1. The agent entries
  # override models a global ~/.config/opencode/opencode.json may set per agent; that file is left alone.
  mkdir -p "$D/opencode/plugin"
  cp opencode/plugin/tabdeck.js "$D/opencode/plugin/tabdeck.js"
  if [ "${HUB_ODS:-0}" = 1 ]; then
    cat > "$D/opencode/opencode.json" <<JSON
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "ods": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "ODS (LiteLLM, current local model)",
      "options": {"baseURL": "http://$HUB_IP:4000/v1", "apiKey": "$KEY"},
      "models": {"default": {"name": "ODS default model", "limit": {"context": 65536, "output": 8192}}}
    }
  },
  "model": "ods/default",
  "small_model": "ods/default",
  "agent": {"build": {"model": "ods/default"}, "plan": {"model": "ods/default"}, "general": {"model": "ods/default"}}
}
JSON
    chmod 600 "$D/opencode/opencode.json"
  fi
else
  # Claude Code reports status through hooks.
  cp hooks/tabdeck-hook.sh "$D/hook.sh" && chmod 755 "$D/hook.sh"
  TABDECK_HOOK="$D/hook.sh" uv run python - <<'PY'
import json, os, pathlib
from tabdeck.install import merge_hooks
p = pathlib.Path.home() / ".claude" / "settings.json"
p.parent.mkdir(exist_ok=True)
s = json.loads(p.read_text()) if p.exists() else {}
p.write_text(json.dumps(merge_hooks(s, os.environ["TABDECK_HOOK"]), indent=2) + "\n")
PY
  grep -q focus-events ~/.tmux.conf 2>/dev/null || echo 'set -g focus-events on' >> ~/.tmux.conf  # Claude Code asks for it
fi
cp scripts/agent.sh scripts/deck-start.sh scripts/deck-save.sh "$D/" && chmod 755 "$D/agent.sh" "$D/deck-start.sh" "$D/deck-save.sh"
cat > ~/.config/systemd/user/tabdeck$SUFFIX.service <<UNIT
[Unit]
Description=TabDeck hub$SUFFIX
After=network-online.target tabdeck$SUFFIX-tmux.service
Requires=tabdeck$SUFFIX-tmux.service

[Service]
WorkingDirectory=%h/$REPO
Environment=TABDECK_NETBIRD_IP=$HUB_IP
Environment=TABDECK_PORT=$PORT
Environment=TABDECK_INSTANCE=${TABDECK_INSTANCE:-}
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=%h/.local/bin/uv run tabdeck serve
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
UNIT
# The tmux server must not live inside the hub service: systemd kills a service's whole cgroup
# on restart, which would end every agent session on each deploy. It gets its own unit.
cat > ~/.config/systemd/user/tabdeck$SUFFIX-tmux.service <<UNIT
[Unit]
Description=tmux server for TabDeck$SUFFIX sessions: $SESSION, one window per agent session (survives hub restarts)

[Service]
Type=forking
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$D/deck-start.sh
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
UNIT
# Save the session's agent windows every minute (systemd restores them at boot through deck-start.sh).
TAG="# tabdeck$SUFFIX"
(crontab -l 2>/dev/null | grep -v "$TAG\$" || true; echo "* * * * * $D/deck-save.sh $TAG") | crontab -
systemctl --user daemon-reload
systemctl --user enable --now "tabdeck$SUFFIX-tmux" >/dev/null 2>&1
systemctl --user enable "tabdeck$SUFFIX" >/dev/null 2>&1
systemctl --user restart "tabdeck$SUFFIX"
echo "TabDeck$SUFFIX hub restarted (agent: $AGENT, port $PORT)"
