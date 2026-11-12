#!/bin/bash
# Safe weekly cleanup for the Stick_Figures repo (user 2026-11-11). DRY RUN BY DEFAULT.
#
#   scripts/cleanup.sh            list what would be removed and how much space it would free; deletes nothing
#   scripts/cleanup.sh --apply    remove it, and log every file and the space freed to logs/cleanup.log
#   scripts/cleanup.sh --days N   age limit in days (default 7)
#
# What it removes, and nothing else:
#   1. episodes/<slug>/cache/search-*.json and llm-*.json older than N days, only for an episode whose video is
#      rendered (renders/sample/<slug>/<slug>.mp4 or video.mp4). These are the free path's search and model caches.
#      The research, storyline and script records (research-*, story-*, script-*) stay: they are the episode's
#      provenance, a few KB each, and the paid research among them is never re-run.
#   2. cache/pages/page-*.json older than N days (the shared page cache; pipeline/cache.py expires them at 7 days).
#   3. __pycache__/*.pyc inside the repo (not in .venv, .venvs or .git), then each emptied __pycache__ folder.
#   4. `git gc` (default pruning, never --prune=now or --aggressive).
#
# What it never removes: the repo itself, any episode folder, episode.json or ledger.json, any rendered video
# (*.mp4, *.mov, *.wav, *.srt), any .env file, any SSH key, or anything outside this repo. It deletes regular
# files one at a time (rm -f -- <file>); it never runs rm -r, never follows a symlink and checks every path
# against the repo root and the deny-list before touching it.
set -u

DAYS=7
APPLY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --apply) APPLY=1; shift ;;
        --days) [ $# -ge 2 ] || { echo "cleanup: --days needs a number" >&2; exit 2; }
                case "$2" in ''|*[!0-9]*) echo "cleanup: --days needs a whole number" >&2; exit 2 ;; esac
                DAYS=$2; shift 2 ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) echo "cleanup: unknown option $1 (use --apply, --days N)" >&2; exit 2 ;;
    esac
done
[ "$DAYS" -ge 1 ] || { echo "cleanup: --days must be at least 1" >&2; exit 2; }

# ---- the repo: this script's parent folder, and it must really be Stick_Figures -------------------------------
REPO=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd -P) || { echo "cleanup: cannot find the repo" >&2; exit 1; }
if [ ! -d "$REPO/.git" ] || [ ! -f "$REPO/pipeline/cache.py" ] || [ ! -d "$REPO/episodes" ] \
   || ! grep -q 'stick' "$REPO/pyproject.toml" 2>/dev/null; then
    echo "cleanup: $REPO does not look like the Stick_Figures repo; nothing done" >&2
    exit 1
fi
case "$REPO" in /|"$HOME"|/Users|/Users/*/Desktop|/Users/*/Projects) echo "cleanup: refusing repo root $REPO" >&2; exit 1 ;; esac

LOG="$REPO/logs/cleanup.log"
MODE=$([ $APPLY -eq 1 ] && echo apply || echo dry-run)
NOW=$(date '+%Y-%m-%dT%H:%M:%S')
COUNT=0
BYTES=0
SKIPPED=0
[ $APPLY -eq 1 ] && mkdir -p "$REPO/logs"
LIST=$(mktemp "${TMPDIR:-/tmp}/stick-cleanup.XXXXXX") || exit 1
trap 'rm -f -- "$LIST"' EXIT

log() {
    if [ $APPLY -eq 1 ]; then echo "$NOW $*" >> "$LOG"; fi
}

size_of() {   # bytes of a regular file (macOS stat, then GNU stat)
    stat -f %z "$1" 2>/dev/null || stat -c %s "$1" 2>/dev/null || echo 0
}

# Every deletion goes through here. A second argument "quiet" logs nothing per file (the .pyc files).
safe_rm() {
    f=$1
    quiet=${2:-}
    case "$f" in "$REPO"/*) ;; *) echo "  SKIP (outside the repo): $f"; SKIPPED=$((SKIPPED + 1)); return ;; esac
    case "$f" in */..*|*/./*) echo "  SKIP (odd path): $f"; SKIPPED=$((SKIPPED + 1)); return ;; esac
    base=${f##*/}
    case "$base" in
        *.mp4|*.mov|*.m4v|*.wav|*.mp3|*.srt|.env|.env.*|*.env|id_*|*_ed25519*|*_rsa*|*.pem|*.key|*key*|episode.json|ledger.json)
            echo "  SKIP (protected): $f"; SKIPPED=$((SKIPPED + 1)); return ;;
    esac
    if [ -L "$f" ] || [ ! -f "$f" ]; then
        echo "  SKIP (not a regular file): $f"; SKIPPED=$((SKIPPED + 1)); return
    fi
    real=$(cd "$(dirname "$f")" 2>/dev/null && pwd -P)/$base
    case "$real" in "$REPO"/*) ;; *) echo "  SKIP (resolves outside the repo): $f"; SKIPPED=$((SKIPPED + 1)); return ;; esac
    n=$(size_of "$f")
    rel=${f#"$REPO"/}
    if [ $APPLY -eq 1 ]; then
        if rm -f -- "$f"; then
            COUNT=$((COUNT + 1)); BYTES=$((BYTES + n))
            [ -n "$quiet" ] || log "removed $rel ($n bytes)"
        else
            echo "  could not remove $rel"
        fi
    else
        COUNT=$((COUNT + 1)); BYTES=$((BYTES + n))
        echo "  would remove $rel ($n bytes)"
    fi
}

human() {   # bytes -> "12.3 MB"
    awk -v b="$1" 'BEGIN { if (b >= 1e9) printf "%.2f GB", b/1e9; else if (b >= 1e6) printf "%.1f MB", b/1e6; else printf "%.1f KB", b/1e3 }'
}

echo "Stick_Figures cleanup ($MODE, older than $DAYS days) in $REPO"
log "---- cleanup $MODE, older than $DAYS days"

# ---- 1. episode caches, only for episodes with a finished render --------------------------------------------
echo "1. Episode search and model caches (episodes with a rendered video only)"
for ep in "$REPO"/episodes/*/; do
    ep=${ep%/}
    slug=${ep##*/}
    case "$slug" in _*|.*) continue ;; esac                # _archive and hidden folders are never touched
    [ -d "$ep/cache" ] && [ ! -L "$ep/cache" ] || continue
    out="$REPO/renders/sample/$slug"
    if [ ! -f "$out/$slug.mp4" ] && [ ! -f "$out/video.mp4" ]; then
        continue
    fi
    find -P "$ep/cache" -maxdepth 1 -type f \( -name 'search-*.json' -o -name 'llm-*.json' \) -mtime +"$DAYS" -print \
        2>/dev/null > "$LIST"
    while IFS= read -r f; do safe_rm "$f"; done < "$LIST"
done

# ---- 2. shared page cache ------------------------------------------------------------------------------------
echo "2. Shared page cache (cache/pages)"
if [ -d "$REPO/cache/pages" ] && [ ! -L "$REPO/cache/pages" ]; then
    find -P "$REPO/cache/pages" -maxdepth 1 -type f -name 'page-*.json' -mtime +"$DAYS" -print 2>/dev/null > "$LIST"
    while IFS= read -r f; do safe_rm "$f"; done < "$LIST"
fi

# ---- 3. __pycache__ ------------------------------------------------------------------------------------------
echo "3. __pycache__ (outside .venv, .venvs and .git)"
find -P "$REPO" \( -path "$REPO/.git" -o -path "$REPO/.venv" -o -path "$REPO/.venvs" -o -path "$REPO/node_modules" \) -prune \
    -o -type f -path '*/__pycache__/*.pyc' -print 2>/dev/null > "$LIST"
PYC=$(wc -l < "$LIST" | tr -d ' ')
PYC_BYTES=0
while IFS= read -r f; do
    if [ $APPLY -eq 1 ]; then
        before=$BYTES; safe_rm "$f" quiet; PYC_BYTES=$((PYC_BYTES + BYTES - before))
    else                                                   # one summary line instead of hundreds
        PYC_BYTES=$((PYC_BYTES + $(size_of "$f"))); COUNT=$((COUNT + 1))
    fi
done < "$LIST"
if [ $APPLY -eq 1 ]; then
    log "removed $PYC .pyc file(s) under __pycache__ ($PYC_BYTES bytes)"
    echo "  removed $PYC .pyc file(s) ($(human $PYC_BYTES))"
    find -P "$REPO" \( -path "$REPO/.git" -o -path "$REPO/.venv" -o -path "$REPO/.venvs" \) -prune \
        -o -type d -name __pycache__ -empty -print 2>/dev/null | while IFS= read -r d; do rmdir -- "$d" 2>/dev/null; done
else
    BYTES=$((BYTES + PYC_BYTES))
    echo "  would remove $PYC .pyc file(s) ($(human $PYC_BYTES)) and the emptied __pycache__ folders"
fi

# ---- 4. git gc -----------------------------------------------------------------------------------------------
echo "4. git gc"
GIT_BEFORE=$(du -sk "$REPO/.git" 2>/dev/null | awk '{print $1}')
if [ $APPLY -eq 1 ]; then
    git -C "$REPO" gc --quiet 2>&1 | sed 's/^/  /'
    GIT_AFTER=$(du -sk "$REPO/.git" 2>/dev/null | awk '{print $1}')
    GIT_FREED=$(( (GIT_BEFORE - GIT_AFTER) * 1024 ))
    [ $GIT_FREED -lt 0 ] && GIT_FREED=0
    echo "  .git: ${GIT_BEFORE} KB -> ${GIT_AFTER} KB"
    log "git gc: .git ${GIT_BEFORE} KB -> ${GIT_AFTER} KB"
else
    GIT_FREED=0
    echo "  would run git gc (.git is ${GIT_BEFORE} KB now)"
fi

# ---- summary -------------------------------------------------------------------------------------------------
TOTAL=$((BYTES + GIT_FREED))
if [ $APPLY -eq 1 ]; then
    echo "Removed $COUNT file(s), freed $(human $TOTAL) (git gc included); $SKIPPED skipped. Logged to logs/cleanup.log."
    log "done: removed $COUNT file(s), freed $TOTAL bytes ($(human $TOTAL)), $SKIPPED skipped"
else
    echo "Dry run: would remove $COUNT file(s), about $(human $TOTAL); $SKIPPED skipped. Nothing was deleted."
    echo "Run scripts/cleanup.sh --apply to do it."
fi
