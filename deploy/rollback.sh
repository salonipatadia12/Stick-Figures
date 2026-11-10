#!/bin/sh
# Roll the VPS back to an earlier deploy, optionally restoring a backup:
#   sh deploy/rollback.sh                                  # to /srv/stick/previous (the deploy before this one)
#   sh deploy/rollback.sh --to deploy-20261110-091500      # to a deploy tag or any commit
#   sh deploy/rollback.sh --restore-db 20261110-033000     # also restore the database from that backup
#   sh deploy/rollback.sh --restore-data 20261110-033000   # also unpack that backup's episodes/outputs
#   add --yes to skip the confirmation for a restore
# A database restore replaces the current database (pg_restore --clean); the current one is backed up first.
# The checkout is left on a detached commit; the next deploy/deploy.sh returns it to pipeline-v2.
set -eu
. "$(dirname -- "$0")/lib.sh"
cd "$REPO_DIR"

target="" db="" data="" yes=0
while [ $# -gt 0 ]; do
    case "$1" in
        --to) target=${2:?--to needs a tag or commit}; shift 2 ;;
        --restore-db) db=${2:?--restore-db needs a backup name}; shift 2 ;;
        --restore-data) data=${2:?--restore-data needs a backup name}; shift 2 ;;
        --yes) yes=1; shift ;;
        -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
        *) die "unknown option $1 (see --help)" ;;
    esac
done
[ -n "$target" ] || target=$(cat "$STICK_HOME/previous" 2>/dev/null || true)
[ -n "$target" ] || die "no previous deploy recorded in $STICK_HOME/previous; pass --to <tag or commit>"
git rev-parse --verify --quiet "$target^{commit}" >/dev/null || die "unknown tag or commit: $target"
for b in $db $data; do
    [ -d "$BACKUPS/$b" ] || die "no backup $BACKUPS/$b (ls $BACKUPS)"
done
[ -z "$db" ] || [ -f "$BACKUPS/$db/db.dump" ] || die "$BACKUPS/$db has no db.dump"
[ -z "$data" ] || [ -f "$BACKUPS/$data/data.tar.gz" ] || die "$BACKUPS/$data has no data.tar.gz"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    die "the checkout has local changes; nothing was changed"
fi

if { [ -n "$db" ] || [ -n "$data" ]; } && [ "$yes" != 1 ]; then
    printf 'Roll back to %s and restore%s%s? Type yes: ' "$target" "${db:+ database from $db}" "${data:+ files from $data}"
    read -r answer
    [ "$answer" = yes ] || die "cancelled; nothing was changed"
fi

current=$(cat "$STICK_HOME/current" 2>/dev/null || git rev-parse --short HEAD)
say "rolling back: $current -> $target"
git checkout --quiet --detach "$target"
short=$(git rev-parse --short HEAD)
export STICK_IMAGE_TAG="$short"
if docker image inspect "stick-web:$short" >/dev/null 2>&1; then
    say "reusing image stick-web:$short"
else
    say "building stick-web:$short"
    dc build web
fi

if [ -n "$db" ]; then
    say "backing up the current state first"
    SKIP_PRUNE=1 sh "$REPO_DIR/deploy/backup.sh"
    say "stopping web and runner for the restore"
    dc stop web runner
    say "restoring the database from $db"
    dc exec -T postgres pg_restore -U stick -d stick --clean --if-exists --no-owner < "$BACKUPS/$db/db.dump"
fi
if [ -n "$data" ]; then
    say "unpacking episodes/outputs from $data into /data (files newer than the backup stay)"
    dc run --rm --no-deps -T --entrypoint sh web -c 'tar -C /data -xzf -' < "$BACKUPS/$data/data.tar.gz"
fi

dc up -d --remove-orphans
wait_health 180 || die "not healthy after the rollback; see docker compose logs"
record_deploy "$target" "$short"
run_smoke_test
say "rolled back to $target ($short)"
