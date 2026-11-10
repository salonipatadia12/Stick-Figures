#!/bin/sh
# Install the Mac worker as a launchd agent (the user runs this; it is never run automatically).
#   sh deploy/launchd/install_worker.sh https://stick.srv1747356.hstgr.cloud
#   sh deploy/launchd/install_worker.sh --uninstall
# Steps: writes ~/.config/stick-worker/config (server + repo; no secrets), checks the Keychain token is there
# (never prints it), runs `python -m macworker --dry-run`, copies the plist to ~/Library/LaunchAgents and loads it.
set -eu

LABEL=com.stickfigures.worker
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=${STICK_REPO:-$(CDPATH= cd -- "$HERE/../.." && pwd)}
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"
CONF_DIR="$HOME/.config/stick-worker"
DOMAIN="gui/$(id -u)"

say() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
    say "unloading $LABEL"
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || say "(it was not loaded)"
    if [ -f "$AGENT" ]; then
        mv "$AGENT" "$AGENT.removed-$(date +%Y%m%d-%H%M%S)"
        say "moved the plist aside (not deleted): $AGENT.removed-*"
    fi
    say "the Keychain token and $CONF_DIR are left as they are"
    exit 0
fi

SERVER=${1:-${STICK_SERVER:-}}
[ -n "$SERVER" ] || die "usage: sh deploy/launchd/install_worker.sh https://<server>"
case "$SERVER" in https://*) ;; *) die "the server must be https://..." ;; esac
[ -x "$REPO/.venv/bin/python" ] || die "no $REPO/.venv/bin/python (set STICK_REPO to the Stick_Figures checkout)"

say "1/5 config $CONF_DIR/config (no secrets)"
mkdir -p "$CONF_DIR"
cat > "$CONF_DIR/config" <<EOF
# Stick Figures Mac worker (read by python -m macworker). The token is in the Keychain, not here.
STICK_SERVER=$SERVER
STICK_REPO=$REPO
# STICK_RENDER_WORKERS=4
# STICK_WORKER_PULL=0
EOF
chmod 600 "$CONF_DIR/config"

say "2/5 worker token in the login Keychain (service stick-worker)"
if ! security find-generic-password -s stick-worker >/dev/null 2>&1; then
    cat <<'EOF'
No token yet. Store it (you are asked for the token; it is not echoed and not kept in shell history):
  security add-generic-password -U -s stick-worker -a stick -w
The VPS keeps only its sha256 (WORKER_TOKEN_SHA256 in /srv/stick/.env); see deploy/README.md, "Worker token".
Then run this script again.
EOF
    exit 1
fi
say "token found (not shown)"

say "3/5 dry run (checks the server and the token; claims nothing)"
(cd "$REPO" && PYTHONPATH="$REPO" "$REPO/.venv/bin/python" -m macworker --dry-run) || die "the dry run found a problem (above); fix it and run again"

say "4/5 launch agent $AGENT"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
sed -e "s#__REPO__#$REPO#g" -e "s#__HOME__#$HOME#g" "$HERE/$LABEL.plist" > "$AGENT.tmp"
plutil -lint "$AGENT.tmp" >/dev/null || die "the plist did not validate"
mv "$AGENT.tmp" "$AGENT"

say "5/5 load"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$AGENT"
launchctl enable "$DOMAIN/$LABEL"
sleep 2
launchctl print "$DOMAIN/$LABEL" 2>/dev/null | grep -E '^\s*(state|pid|last exit code)' || true
say "running. Log: tail -f ~/Library/Logs/stick-worker.log   Stop: sh deploy/launchd/install_worker.sh --uninstall"
