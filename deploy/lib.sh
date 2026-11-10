# Shared helpers for the deploy scripts (POSIX sh; sourced, not run). Never prints secret values.
#
#   STICK_HOME          /srv/stick                      layout root (.env, backups/, deploys.log, current, previous)
#   STICK_ENV_FILE      $STICK_HOME/.env                the env file (mode 600)
#   STICK_REPO_DIR      the checkout holding this script
#   STICK_COMPOSE_ARGS  extra `docker compose` args, e.g. "-p sticktest -f deploy/dev/compose.stub.yaml"
#   CURL_INSECURE=1     accept Caddy's internal CA (local tests with https://localhost only)

STICK_HOME=${STICK_HOME:-/srv/stick}
ENV_FILE=${STICK_ENV_FILE:-$STICK_HOME/.env}
REPO_DIR=${STICK_REPO_DIR:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}
COMPOSE_FILE=$REPO_DIR/deploy/compose.yaml
BACKUPS=$STICK_HOME/backups
export STICK_ENV_FILE="$ENV_FILE"

say() { printf '==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# The image tag of the running deploy (set by deploy.sh/rollback.sh), so every script talks to the same images.
if [ -z "${STICK_IMAGE_TAG:-}" ] && [ -r "$STICK_HOME/current_image" ]; then
    STICK_IMAGE_TAG=$(cat "$STICK_HOME/current_image")
fi
export STICK_IMAGE_TAG="${STICK_IMAGE_TAG:-local}"

dc() {
    # shellcheck disable=SC2086
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ${STICK_COMPOSE_ARGS:-} "$@"
}

file_mode() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1"; }

# A NON-secret value from the env file (PUBLIC_URL, ports). Never call it for passwords, keys or hashes.
env_value() {
    sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" | tail -n 1 | sed "s/^[\"']//; s/[\"']\$//"
}

timestamp() { date -u +%Y%m%d-%H%M%S; }

# Wait until the web container answers /api/health with ok=true (inside the network, then on the host port), then
# try PUBLIC_URL through nginx: that last check only warns (nginx/certbot may not be set up yet).
wait_health() {
    limit=${1:-120}
    say "waiting for the web app to report healthy (up to ${limit}s)"
    t=0
    until dc exec -T web python -c "import json,sys,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4); sys.exit(0 if json.load(r).get('ok') else 1)" >/dev/null 2>&1; do
        t=$((t + 3))
        if [ "$t" -ge "$limit" ]; then
            warn "web is not healthy after ${limit}s; last log lines:"
            dc logs --tail 30 web >&2 || true
            return 1
        fi
        sleep 3
    done
    say "web is healthy inside the network"
    if command -v curl >/dev/null 2>&1; then
        port=$(env_value WEB_PORT); port=${port:-8020}
        body=$(curl -fsS --max-time 10 "http://127.0.0.1:$port/api/health" 2>/dev/null) || { warn "http://127.0.0.1:$port/api/health does not answer on the host"; return 1; }
        say "host port: http://127.0.0.1:$port/api/health -> $body"
        url=$(env_value PUBLIC_URL)
        k=""
        [ "${CURL_INSECURE:-0}" = 1 ] && k="-k"
        # shellcheck disable=SC2086
        if [ -n "$url" ] && body=$(curl -fsS $k --max-time 10 "$url/api/health" 2>/dev/null); then
            say "public: $url/api/health -> $body"
        else
            warn "$url/api/health is not reachable yet (nginx site / certificate not set up? see deploy/README.md)"
        fi
    fi
    return 0
}

run_smoke_test() {
    if [ -f "$REPO_DIR/deploy/smoke_test.sh" ]; then
        say "running deploy/smoke_test.sh"
        PUBLIC_URL=$(env_value PUBLIC_URL) sh "$REPO_DIR/deploy/smoke_test.sh"
    else
        say "no deploy/smoke_test.sh yet; skipped"
    fi
}

record_deploy() {   # record_deploy <git ref> <image tag>
    prev=$(cat "$STICK_HOME/current" 2>/dev/null || true)
    if [ -n "$prev" ] && [ "$prev" != "$1" ]; then
        printf '%s\n' "$prev" > "$STICK_HOME/previous"
        cp "$STICK_HOME/current_image" "$STICK_HOME/previous_image" 2>/dev/null || true
    fi
    printf '%s\n' "$1" > "$STICK_HOME/current"
    printf '%s\n' "$2" > "$STICK_HOME/current_image"
    printf '%s %s commit=%s image=%s previous=%s\n' "$(timestamp)" "$1" "$(git -C "$REPO_DIR" rev-parse --short HEAD)" "$2" "${prev:-none}" \
        >> "$STICK_HOME/deploys.log"
    say "recorded deploy $1 (previous: ${prev:-none}) in $STICK_HOME/deploys.log"
}

# Keep the newest N stick-web images (the ones these scripts built); never touches other images.
prune_stick_images() {
    keep=${1:-3}
    docker image ls stick-web --format '{{.CreatedAt}}|{{.Tag}}' | sort -r | awk -F'|' -v k="$keep" 'NR>k {print $2}' |
    while read -r tag; do
        [ "$tag" = "$STICK_IMAGE_TAG" ] && continue
        say "removing old image stick-web:$tag"
        docker image rm "stick-web:$tag" >/dev/null 2>&1 || warn "stick-web:$tag is still in use; kept"
    done
}
