#!/bin/sh
# Copy TabDeck to the hub server, issue its HTTPS cert from the local mkcert CA, seed projects, run setup.
set -e
cd "$(dirname "$0")/.."
# Addresses and choices live in the untracked deploy.env (see deploy.env.example), never in the repo.
# DEPLOY_ENV=deploy-other.env picks another file, e.g. one per setup.
ENVFILE="${DEPLOY_ENV:-deploy.env}"
[ -f "$ENVFILE" ] && . "./$ENVFILE"
: "${HUB_HOST:?set HUB_HOST (ssh target of the hub server, e.g. user@host) in deploy.env}"
: "${HUB_IP:?set HUB_IP (the hub server NetBird IP) in deploy.env}"
SUFFIX="${TABDECK_INSTANCE:+-$TABDECK_INSTANCE}"
REPO="${HUB_REPO:-tabdeck$SUFFIX}"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
CERTS="$HOME/.tabdeck$SUFFIX"
mkdir -p "$CERTS"
[ -f "$CERTS/hub-cert.pem" ] || mkcert -cert-file "$CERTS/hub-cert.pem" \
  -key-file "$CERTS/hub-key.pem" "$HUB_IP" ${HUB_FQDN:+"$HUB_FQDN"} localhost 127.0.0.1
uv run tabdeck export-projects > "$TMP/projects.json"
rsync -az --delete --exclude .venv --exclude widget/.build --exclude .superpowers --exclude .remember --exclude __pycache__ \
  --exclude .pytest_cache --exclude 'deploy*.env' ./ "$HUB_HOST:$REPO/"
ssh "$HUB_HOST" "mkdir -p ~/.tabdeck$SUFFIX"
scp -q "$CERTS/hub-cert.pem" "$HUB_HOST:.tabdeck$SUFFIX/cert.pem"
scp -q "$CERTS/hub-key.pem" "$HUB_HOST:.tabdeck$SUFFIX/key.pem"
scp -q "$(mkcert -CAROOT)/rootCA.pem" "$HUB_HOST:.tabdeck$SUFFIX/ca.pem"  # public CA cert: OpenCode's plugin trusts the hub
scp -q "$TMP/projects.json" "$HUB_HOST:.tabdeck$SUFFIX/projects.json"
ssh "$HUB_HOST" "HUB_IP=$HUB_IP HUB_REPO=$REPO TABDECK_INSTANCE=${TABDECK_INSTANCE:-} HUB_PORT=${HUB_PORT:-8765} \
HUB_AGENT=${HUB_AGENT:-claude} HUB_SESSION=${HUB_SESSION:-deck} HUB_ASSISTANT='${HUB_ASSISTANT:-Jarvis}' \
HUB_WAKE='${HUB_WAKE:-jarvis}' HUB_ODS=${HUB_ODS:-0} sh ~/$REPO/scripts/hub-setup.sh"
echo "Hub: https://$HUB_IP:${HUB_PORT:-8765}"
