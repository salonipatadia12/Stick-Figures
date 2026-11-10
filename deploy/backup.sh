#!/bin/sh
# Back up the database and the episode/output files:
#   sh deploy/backup.sh
# Writes /srv/stick/backups/<UTC time>/{db.dump, data.tar.gz, commit} (mode 700), keeps the newest $KEEP (7)
# (SKIP_PRUNE=1 keeps all) and prints the sizes. Restore: deploy/rollback.sh --restore-db <time> [--restore-data <time>] (deploy/README.md).
# Cron (daily 03:30): 30 3 * * * cd /srv/stick/app && sh deploy/backup.sh >> /srv/stick/backups/backup.log 2>&1
set -eu
. "$(dirname -- "$0")/lib.sh"
KEEP=${KEEP:-7}

[ -n "$(dc ps -q postgres 2>/dev/null || true)" ] || die "postgres is not running; start the stack first"
umask 077
stamp=$(timestamp)
dir="$BACKUPS/$stamp"
mkdir -p "$dir"
say "backup into $dir"

say "database (pg_dump, custom format)"
dc exec -T postgres pg_dump -U stick -d stick -Fc > "$dir/db.dump.partial"
mv "$dir/db.dump.partial" "$dir/db.dump"

say "files (/data/episodes, /data/outputs)"
tar_cmd='mkdir -p /data/episodes /data/outputs 2>/dev/null; tar -C /data -czf - episodes outputs'
if [ -n "$(dc ps -q web 2>/dev/null || true)" ]; then
    dc exec -T web sh -c "$tar_cmd" > "$dir/data.tar.gz.partial"
else
    dc run --rm --no-deps -T --entrypoint sh web -c "$tar_cmd" > "$dir/data.tar.gz.partial"
fi
mv "$dir/data.tar.gz.partial" "$dir/data.tar.gz"
git -C "$REPO_DIR" rev-parse HEAD > "$dir/commit" 2>/dev/null || true
cat "$STICK_HOME/current" > "$dir/deploy" 2>/dev/null || true

say "sizes"
du -sh "$dir/db.dump" "$dir/data.tar.gz" | sed 's/^/    /'

if [ "${SKIP_PRUNE:-0}" = 1 ]; then
    say "done: $stamp (old backups not pruned)"
    exit 0
fi
say "keeping the newest $KEEP backups"
count=$(find "$BACKUPS" -mindepth 1 -maxdepth 1 -type d -name '20*' | wc -l | tr -d ' ')
if [ "$count" -gt "$KEEP" ]; then
    find "$BACKUPS" -mindepth 1 -maxdepth 1 -type d -name '20*' | sort | head -n $((count - KEEP)) | while read -r old; do
        say "removing old backup $(basename "$old")"
        rm -rf "$old"
    done
fi
du -sh "$BACKUPS" | sed 's/^/    all backups: /'
say "done: $stamp"
