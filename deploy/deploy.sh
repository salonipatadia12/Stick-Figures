#!/bin/sh
# Deploy the latest pipeline-v2 on the VPS:
#   sh deploy/deploy.sh                 # backup, pull, tag deploy-<time>, build, up, health, smoke test
#   SKIP_BACKUP=1 sh deploy/deploy.sh   # without the backup first
# The commit that was running before is recorded in /srv/stick/previous for deploy/rollback.sh. Each deploy
# builds stick-web:<short commit>, so a rollback to a recent deploy reuses its image without a rebuild.
set -eu
. "$(dirname -- "$0")/lib.sh"
BRANCH=${STICK_BRANCH:-pipeline-v2}
cd "$REPO_DIR"

[ -f "$ENV_FILE" ] || die "$ENV_FILE is missing; run deploy/install.sh first"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    die "the checkout $REPO_DIR has local changes; commit or remove them first (nothing was changed)"
fi

before=$(git rev-parse --short HEAD)
if [ "${SKIP_BACKUP:-0}" != 1 ] && [ -n "$(dc ps -q postgres 2>/dev/null || true)" ]; then
    say "backup before deploying"
    sh "$REPO_DIR/deploy/backup.sh"
fi

say "fetching $BRANCH"
git fetch --quiet --tags origin "$BRANCH"
git checkout --quiet "$BRANCH"
git merge --quiet --ff-only "origin/$BRANCH" || die "$BRANCH cannot fast-forward to origin/$BRANCH; resolve by hand"
after=$(git rev-parse --short HEAD)
say "code: $before -> $after"

tag="deploy-$(timestamp)"
git tag "$tag" HEAD
say "tagged $tag (local tag on the VPS checkout; not pushed)"

export STICK_IMAGE_TAG="$after"
say "building stick-web:$STICK_IMAGE_TAG"
dc build web
dc pull --quiet caddy postgres
say "starting"
dc up -d --remove-orphans
if ! wait_health 180; then
    prev=$(cat "$STICK_HOME/current" 2>/dev/null || echo "$before")
    die "the new version is not healthy. Roll back with: sh deploy/rollback.sh --to $prev"
fi
record_deploy "$tag" "$STICK_IMAGE_TAG"
run_smoke_test
prune_stick_images 3
say "deployed $tag ($after)"
