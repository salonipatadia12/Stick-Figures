#!/bin/sh
# The VPS firewall (ufw). By default this only PRINTS the commands; the user approves firewall changes.
#   sh deploy/firewall.sh                              # print the rules
#   EXTRA_PORTS="5678/tcp 8080" sh deploy/firewall.sh  # keep ports that services already running here need
#   sudo sh deploy/firewall.sh --apply                 # apply them (after review)
# SSH_PORT (22), CADDY_HTTP_PORT (80) and CADDY_HTTPS_PORT (443) are read from the environment or the env file.
# The SSH rule is added before ufw is enabled, so the current session is not cut off.
set -eu
. "$(dirname -- "$0")/lib.sh"

apply=0
[ "${1:-}" = "--apply" ] && apply=1

val() { v=$(eval "printf '%s' \"\${$1:-}\""); [ -n "$v" ] || { [ -r "$ENV_FILE" ] && v=$(env_value "$1"); }; printf '%s' "${v:-$2}"; }
ssh_port=$(val SSH_PORT 22)
http_port=$(val CADDY_HTTP_PORT 80)
https_port=$(val CADDY_HTTPS_PORT 443)
extra=$(printf '%s' "${EXTRA_PORTS:-}" | tr ',' ' ')

rules_file=$(mktemp)
trap 'rm -f "$rules_file"' EXIT
{
    echo "ufw default deny incoming"
    echo "ufw default allow outgoing"
    echo "ufw allow $ssh_port/tcp comment 'ssh'"
    echo "ufw allow $http_port/tcp comment 'stick caddy http (ACME + redirect)'"
    echo "ufw allow $https_port/tcp comment 'stick caddy https'"
    echo "ufw allow $https_port/udp comment 'stick caddy http3'"
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
  - Ports Docker publishes (Caddy's) bypass ufw through Docker's own iptables rules; postgres and web publish
    nothing, so they stay internal either way.
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
