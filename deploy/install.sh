#!/bin/sh
# First install on the VPS (idempotent: safe to run again). Run from the checkout, as the deploy user:
#   sh deploy/install.sh
# It checks Docker and Compose (installs nothing), creates the /srv/stick layout, requires /srv/stick/.env with
# mode 600, pulls/builds the images, starts the stack, waits for health and runs the smoke test if present.
# It never prints secret values and never stops anything it did not start.
set -eu
. "$(dirname -- "$0")/lib.sh"

say "1/7 Docker"
if ! command -v docker >/dev/null 2>&1; then
    cat <<'EOF'
Docker is not installed. Nothing was changed. To install it (Ubuntu/Debian), review and run:
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sudo sh /tmp/get-docker.sh
  sudo usermod -aG docker "$USER"     # then log out and in again
Then run this script again.
EOF
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    cat <<'EOF'
Docker is installed but the Compose plugin is not. Nothing was changed. Install it with:
  sudo apt-get update && sudo apt-get install -y docker-compose-plugin
Then run this script again.
EOF
    exit 1
fi
docker info >/dev/null 2>&1 || die "the Docker daemon is not reachable for $(id -un) (is it running? is $(id -un) in the docker group?)"
say "docker $(docker version --format '{{.Server.Version}}'), $(docker compose version --short 2>/dev/null || docker compose version)"

say "2/7 layout under $STICK_HOME"
for d in "$STICK_HOME" "$BACKUPS"; do
    if [ ! -d "$d" ]; then
        if ! mkdir -p "$d" 2>/dev/null; then
            cat <<EOF
Cannot create $d as $(id -un). Create it once with:
  sudo mkdir -p $STICK_HOME/backups && sudo chown -R $(id -un):$(id -gn) $STICK_HOME && sudo chmod 750 $STICK_HOME
Then run this script again.
EOF
            exit 1
        fi
        say "created $d"
    fi
done
chmod 700 "$BACKUPS" 2>/dev/null || true

say "3/7 env file $ENV_FILE"
if [ ! -f "$ENV_FILE" ]; then
    cat <<EOF
$ENV_FILE does not exist. Create it from the example (then fill in the values; see deploy/README.md):
  cp $REPO_DIR/deploy/.env.example $ENV_FILE && chmod 600 $ENV_FILE && \${EDITOR:-nano} $ENV_FILE
EOF
    exit 1
fi
mode=$(file_mode "$ENV_FILE")
[ "$mode" = 600 ] || [ "$mode" = 400 ] || die "$ENV_FILE has mode $mode; run: chmod 600 $ENV_FILE"
missing=""
for key in PUBLIC_URL POSTGRES_PASSWORD APP_USER APP_PASSWORD_HASH SESSION_SECRET WORKER_TOKEN_SHA256 ANTHROPIC_API_KEY; do
    if ! grep -Eq "^[[:space:]]*$key[[:space:]]*=[[:space:]]*[^[:space:]]" "$ENV_FILE"; then
        missing="$missing $key"
    elif grep -Eq "^[[:space:]]*$key[[:space:]]*=.*change-me" "$ENV_FILE"; then
        missing="$missing $key(placeholder)"
    fi
done
[ -z "$missing" ] || die "set these in $ENV_FILE:$missing"
if grep -Eq '^[[:space:]]*APP_PASSWORD_HASH[[:space:]]*=[[:space:]]*scrypt' "$ENV_FILE"; then
    die "APP_PASSWORD_HASH contains \$ signs: put the value in single quotes ('scrypt\$...\$...') so Compose keeps it as is"
fi
say "env file ok (mode $mode; values not shown)"

say "4/7 ports"
http_port=$(env_value CADDY_HTTP_PORT); http_port=${http_port:-80}
https_port=$(env_value CADDY_HTTPS_PORT); https_port=${https_port:-443}
ours=$(dc ps -q caddy 2>/dev/null || true)
for p in "$http_port" "$https_port"; do
    busy=""
    if command -v ss >/dev/null 2>&1; then
        busy=$(ss -ltnH "sport = :$p" 2>/dev/null || true)
    elif command -v lsof >/dev/null 2>&1; then
        busy=$(lsof -nP -iTCP:"$p" -sTCP:LISTEN 2>/dev/null || true)
    fi
    if [ -n "$busy" ] && [ -z "$ours" ]; then
        die "port $p is already used by another service on this host. Nothing was changed. Set CADDY_HTTP_PORT / CADDY_HTTPS_PORT in $ENV_FILE (see deploy/README.md, 'Ports')."
    fi
done
say "ports $http_port/$https_port are free (or already ours)"

say "5/7 images"
dc pull --quiet caddy postgres
tag=$(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo local)
export STICK_IMAGE_TAG="$tag"
dc build web
docker image ls "stick-web:$tag" --format 'built stick-web:{{.Tag}} ({{.Size}})'

say "6/7 start"
dc up -d --remove-orphans
wait_health 180 || die "the stack did not become healthy; see 'docker compose logs' (nothing else was changed)"
record_deploy "$(git -C "$REPO_DIR" describe --tags --exact-match 2>/dev/null || git -C "$REPO_DIR" rev-parse --short HEAD)" "$tag"

say "7/7 smoke test"
run_smoke_test
dc ps
say "installed. Next: deploy/firewall.sh (prints the rules), deploy/backup.sh (add it to cron), deploy/README.md"
