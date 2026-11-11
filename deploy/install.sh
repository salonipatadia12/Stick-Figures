#!/bin/sh
# First install on the VPS (idempotent: safe to run again). Run from the checkout /srv/stick/app, as the
# deploy user:
#   sh deploy/install.sh                  # the stack; PRINTS the nginx + certbot commands, changes no nginx file
#   sh deploy/install.sh --apply-nginx    # also installs the nginx site and runs certbot (after the user's OK)
# It checks Docker and Compose (installs nothing), creates /srv/stick, requires /srv/stick/.env with mode 600,
# pulls/builds the images, starts the `stick` project, waits for health and runs the smoke test if present.
# It never prints secret values and never touches other projects, containers, sites or Docker data.
set -eu
. "$(dirname -- "$0")/lib.sh"

apply_nginx=0
for a in "$@"; do
    case "$a" in
        --apply-nginx) apply_nginx=1 ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) die "unknown option $a (see --help)" ;;
    esac
done
NGINX_CONF_SRC=$REPO_DIR/deploy/nginx/stick.conf
NGINX_SITE=${NGINX_SITE:-/etc/nginx/sites-available/stick.conf}
NGINX_LINK=${NGINX_LINK:-/etc/nginx/sites-enabled/stick.conf}

say "1/8 Docker"
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
say "docker $(docker version --format '{{.Server.Version}}'), compose $(docker compose version --short 2>/dev/null || echo '?')"

say "2/8 layout under $STICK_HOME"
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

say "3/8 env file $ENV_FILE"
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
required="PUBLIC_URL POSTGRES_PASSWORD APP_USER APP_PASSWORD_HASH SESSION_SECRET WORKER_TOKEN_SHA256"
provider=$(env_value SCRIPT_PROVIDER | tr 'A-Z' 'a-z' | tr -d '[:space:]'); provider=${provider:-ollama}   # ollama: no key
case "$provider" in
    anthropic) required="$required ANTHROPIC_API_KEY" ;;
    ollama) ;;
    *) die "SCRIPT_PROVIDER must be ollama or anthropic, not $provider" ;;
esac
for key in $required; do
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

say "4/8 port"
web_port=$(env_value WEB_PORT); web_port=${web_port:-8020}
ours=$(dc ps -q web 2>/dev/null || true)
busy=""
if command -v ss >/dev/null 2>&1; then
    busy=$(ss -ltnH "sport = :$web_port" 2>/dev/null || true)
elif command -v lsof >/dev/null 2>&1; then
    busy=$(lsof -nP -iTCP:"$web_port" -sTCP:LISTEN 2>/dev/null || true)
fi
if [ -n "$busy" ] && [ -z "$ours" ]; then
    die "port $web_port is already used by another service. Nothing was changed. Set WEB_PORT in $ENV_FILE and the same port in deploy/nginx/stick.conf."
fi
say "127.0.0.1:$web_port is free (or already ours)"

say "5/8 images"
dc pull --quiet postgres
tag=$(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo local)
export STICK_IMAGE_TAG="$tag"
dc build web
docker image ls "stick-web:$tag" --format 'built stick-web:{{.Tag}} ({{.Size}})'

say "6/8 start (project stick: web, runner, postgres)"
dc up -d --remove-orphans web runner postgres
wait_health 180 || die "the stack did not become healthy; see: docker compose -p stick logs (nothing else was changed)"
record_deploy "$(git -C "$REPO_DIR" describe --tags --exact-match 2>/dev/null || git -C "$REPO_DIR" rev-parse --short HEAD)" "$tag"

say "7/8 nginx site + certificate"
host=$(env_value PUBLIC_URL | sed 's#^https\{0,1\}://##; s#[:/].*$##')
sudo_=""
[ "$(id -u)" = 0 ] || sudo_="sudo "
nginx_cmds="${sudo_}install -m 644 $NGINX_CONF_SRC $NGINX_SITE
${sudo_}ln -sfn $NGINX_SITE $NGINX_LINK
${sudo_}nginx -t
${sudo_}systemctl reload nginx
${sudo_}certbot --nginx -d $host --redirect --non-interactive --keep-until-expiring
${sudo_}nginx -t
${sudo_}systemctl reload nginx"
if grep -q "server_name $host;" "$NGINX_CONF_SRC"; then :; else
    warn "deploy/nginx/stick.conf has a different server_name than PUBLIC_URL's host ($host); edit it first"
fi
if [ "$apply_nginx" != 1 ]; then
    say "NOT applied (needs the user's OK). To add the site and its certificate, review and run:"
    printf '%s\n' "$nginx_cmds" | sed 's/^/    /'
    say "or run: sh deploy/install.sh --apply-nginx"
else
    if [ -f "$NGINX_SITE" ] && ! cmp -s "$NGINX_CONF_SRC" "$NGINX_SITE"; then
        if grep -q 'managed by Certbot' "$NGINX_SITE"; then
            say "$NGINX_SITE already has certbot's TLS lines; kept as is (diff it by hand to change it)"
            nginx_cmds=$(printf '%s\n' "$nginx_cmds" | sed '1d')
        else
            say "backing up the existing $NGINX_SITE"
            ${sudo_}cp "$NGINX_SITE" "$NGINX_SITE.bak-$(timestamp)"
        fi
    fi
    while read -r cmd; do
        say "$cmd"
        if ! sh -c "$cmd"; then
            warn "failed: $cmd"
            say "removing our site link so a later nginx reload (e.g. another site's renewal) is not blocked"
            ${sudo_}rm -f "$NGINX_LINK"
            ${sudo_}nginx -t >/dev/null 2>&1 && ${sudo_}systemctl reload nginx || true
            die "nginx keeps serving the other sites; fix the problem and run again"
        fi
    done <<NGINX_CMDS
$nginx_cmds
NGINX_CMDS
    wait_health 60 || true
fi

say "8/8 smoke test"
run_smoke_test
dc ps
say "installed. Next: deploy/firewall.sh (prints the rules), deploy/backup.sh in cron, deploy/README.md"
