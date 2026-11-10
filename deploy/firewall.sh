#!/bin/sh
# The VPS firewall (ufw). By default this only PRINTS the commands; the user approves firewall changes.
#   sh deploy/firewall.sh                              # print the rules
#   EXTRA_PORTS="5678/tcp 8080" sh deploy/firewall.sh  # keep ports that services already running here need
#   sudo sh deploy/firewall.sh --apply                 # apply them (after review)
# SSH_PORT (22), HTTP_PORT (80) and HTTPS_PORT (443: the host's nginx, which fronts this app and the other sites)
# come from the environment. The app itself (127.0.0.1:8020) and postgres are never exposed.
# The SSH rule is added before ufw is enabled, so the current session is not cut off.
set -eu
. "$(dirname -- "$0")/lib.sh"

apply=0
[ "${1:-}" = "--apply" ] && apply=1

ssh_port=${SSH_PORT:-22}
http_port=${HTTP_PORT:-80}
https_port=${HTTPS_PORT:-443}
extra=$(printf '%s' "${EXTRA_PORTS:-}" | tr ',' ' ')

rules_file=$(mktemp)
trap 'rm -f "$rules_file"' EXIT
{
    echo "ufw default deny incoming"
    echo "ufw default allow outgoing"
    echo "ufw allow $ssh_port/tcp comment 'ssh'"
    echo "ufw allow $http_port/tcp comment 'nginx http (ACME + redirect)'"
    echo "ufw allow $https_port/tcp comment 'nginx https'"
    for p in $extra; do
        case "$p" in
            */*) echo "ufw allow $p comment 'existing service'" ;;
            *) echo "ufw allow $p/tcp comment 'existing service'" ;;
        esac
    done
    echo "ufw --force enable"
    echo "ufw status verbose"
} > "$rules_file"

if [ "$apply" != 1 ]; then
    say "firewall rules (NOT applied; review, then run: sudo sh deploy/firewall.sh --apply)"
    sed 's/^/    sudo /' "$rules_file"
    cat <<'EOF'
Notes:
  - If ufw is already active (sudo ufw status), only the "allow" lines are needed; re-running them is harmless.
  - The stick stack publishes only 127.0.0.1:8020 (nginx -> web); postgres publishes nothing. Docker-published
    ports bypass ufw, which is why nothing of ours binds a public address.
  - Anything else that listens publicly today and is not in EXTRA_PORTS becomes unreachable once ufw is on.
    Check first with: sudo ss -ltnp
EOF
    exit 0
fi

[ "$(id -u)" = 0 ] || die "--apply needs root: sudo sh deploy/firewall.sh --apply"
command -v ufw >/dev/null 2>&1 || die "ufw is not installed (sudo apt-get install ufw); nothing was changed"
say "applying the firewall rules"
while read -r cmd; do
    say "$cmd"
    sh -c "$cmd"
done < "$rules_file"
