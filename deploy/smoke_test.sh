#!/bin/sh
# Post-deploy smoke test for the Stick Figures web app (docs/WEBAPP.md). POSIX sh; never prints secrets.
#
#   [PUBLIC_URL=https://stick.srv1747356.hstgr.cloud] [SMOKE_USER=... SMOKE_PASSWORD=...] deploy/smoke_test.sh [--ssh root@host]
#
# Checks: TLS certificate (valid chain + days left), /api/health (ok, db), worker status, anonymous requests
# refused, login (only when SMOKE_USER and SMOKE_PASSWORD are set), the history page after login, a static asset.
# With --ssh user@host it also checks `docker compose ps` (all running and healthy), container restart counts, disk
# free and memory on the host (read-only commands only).
#
# Environment (all optional; PUBLIC_URL defaults to production, https://stick.srv1747356.hstgr.cloud behind nginx):
#   SMOKE_USER, SMOKE_PASSWORD  the web login (the password goes to curl through a 0600 temp file, never argv)
#   SMOKE_CA_FILE               a CA bundle for curl (tests with a self-signed certificate)
#   SMOKE_TIMEOUT               seconds per HTTP request (default 15)
#   SMOKE_CERT_MIN_DAYS         warn when the certificate has fewer days left (default 14)
#   COMPOSE_DIR                 the stick directory on the host (default /srv/stick; its disk is checked too)
#   COMPOSE_PROJECT             the compose project name (default stick)
#
# One line per check (PASS / WARN / FAIL / SKIP), then a summary line. Exit 0 when nothing failed, 1 otherwise,
# 2 on bad usage.
set -eu

usage() {
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
}

SSH_TARGET=""
while [ $# -gt 0 ]; do
    case "$1" in
        --ssh) [ $# -ge 2 ] || { echo "smoke: --ssh needs user@host" >&2; exit 2; }; SSH_TARGET=$2; shift 2 ;;
        --ssh=*) SSH_TARGET=${1#--ssh=}; shift ;;
        --url) [ $# -ge 2 ] || { echo "smoke: --url needs a URL" >&2; exit 2; }; PUBLIC_URL=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "smoke: unknown argument: $1 (see --help)" >&2; exit 2 ;;
    esac
done

URL=${PUBLIC_URL:-https://stick.srv1747356.hstgr.cloud}
URL=${URL%/}
case "$URL" in
    http://*|https://*) ;;
    *) echo "smoke: PUBLIC_URL must start with http:// or https:// (got '$URL')" >&2; exit 2 ;;
esac
TIMEOUT=${SMOKE_TIMEOUT:-15}
MIN_DAYS=${SMOKE_CERT_MIN_DAYS:-14}
COMPOSE_DIR=${COMPOSE_DIR:-/srv/stick}

umask 077
TMP=$(mktemp -d "${TMPDIR:-/tmp}/stick-smoke.XXXXXX")
trap 'rm -rf "$TMP"' EXIT
trap 'exit 130' INT TERM

NPASS=0
NFAIL=0
NWARN=0
pass() { NPASS=$((NPASS + 1)); printf 'PASS  %-9s %s\n' "$1" "$2"; }
fail() { NFAIL=$((NFAIL + 1)); printf 'FAIL  %-9s %s\n' "$1" "$2"; }
warn() { NWARN=$((NWARN + 1)); printf 'WARN  %-9s %s\n' "$1" "$2"; }
skip() { printf 'SKIP  %-9s %s\n' "$1" "$2"; }

have() { command -v "$1" >/dev/null 2>&1; }

# http OUTFILE [curl args...] -> prints the HTTP status (000 when the request failed)
http() {
    _out=$1
    shift
    if [ -n "${SMOKE_CA_FILE:-}" ]; then
        _code=$(curl -sS --max-time "$TIMEOUT" --cacert "$SMOKE_CA_FILE" -o "$_out" -w '%{http_code}' "$@" 2>"$TMP/curl.err") || true
    else
        _code=$(curl -sS --max-time "$TIMEOUT" -o "$_out" -w '%{http_code}' "$@" 2>"$TMP/curl.err") || true
    fi
    case "$_code" in
        [0-9][0-9][0-9]) printf '%s' "$_code" ;;
        *) printf '000' ;;
    esac
}

curl_error() { head -c 200 "$TMP/curl.err" 2>/dev/null | tr '\n' ' '; }

# jget FILE a.b.c -> the JSON value (true/false/null/number, strings unquoted); empty when missing or not JSON
jget() {
    if have python3; then
        python3 - "$1" "$2" <<'PY' 2>/dev/null || true
import json, sys
try:
    v = json.load(open(sys.argv[1]))
    for k in sys.argv[2].split('.'):
        v = v[k]
except Exception:
    sys.exit(0)
print(v if isinstance(v, str) else json.dumps(v))
PY
    else
        # crude fallback: the last key's scalar value anywhere in the document
        _k=${2##*.}
        tr -d '\n' <"$1" | sed -n "s/.*\"$_k\"[[:space:]]*:[[:space:]]*\"\{0,1\}\([^\",}]*\)\"\{0,1\}.*/\1/p" | head -n 1
    fi
}

# ------------------------------------------------------------------------------------------------- TLS

to_epoch() {   # an openssl date ("Nov  9 12:00:00 2026 GMT") -> seconds since 1970, or nothing
    date -u -d "$1" +%s 2>/dev/null && return 0
    date -u -j -f '%b %e %H:%M:%S %Y %Z' "$1" +%s 2>/dev/null && return 0
    if have python3; then
        python3 -c 'import sys,calendar,time; print(calendar.timegm(time.strptime(" ".join(sys.argv[1].split()), "%b %d %H:%M:%S %Y %Z")))' "$1" 2>/dev/null && return 0
    fi
    return 1
}

check_tls() {
    case "$URL" in
        https://*) ;;
        *) skip tls "PUBLIC_URL is not https"; return 0 ;;
    esac
    hostport=${URL#https://}
    hostport=${hostport%%/*}
    host=${hostport%%:*}
    port=443
    [ "$hostport" != "$host" ] && port=${hostport#*:}
    if ! have openssl; then
        warn tls "openssl not installed: certificate dates not checked"
    fi
    code=$(http /dev/null "$URL/api/health")
    if [ "$code" = 000 ]; then
        fail tls "HTTPS request failed (certificate invalid, expired, wrong host, or unreachable): $(curl_error)"
        return 0
    fi
    have openssl || return 0
    end=$(openssl s_client -connect "$host:$port" -servername "$host" </dev/null 2>/dev/null \
          | openssl x509 -noout -enddate 2>/dev/null | sed 's/^notAfter=//') || end=""
    if [ -z "$end" ]; then
        fail tls "could not read the certificate from $host:$port"
        return 0
    fi
    now=$(date -u +%s)
    if endsec=$(to_epoch "$end"); then
        days=$(( (endsec - now) / 86400 ))
        if [ "$endsec" -le "$now" ]; then
            fail tls "certificate expired ($end)"
        elif [ "$days" -lt "$MIN_DAYS" ]; then
            warn tls "certificate valid but only $days days left (until $end); check Caddy's renewal"
        else
            pass tls "certificate valid, $days days left (until $end)"
        fi
    else
        pass tls "certificate valid (until $end; days left not computed on this system)"
    fi
}

# ------------------------------------------------------------------------------------------------- HTTP checks

check_health() {
    code=$(http "$TMP/health.json" "$URL/api/health")
    if [ "$code" != 200 ]; then
        fail health "/api/health returned $code $(curl_error)"
        return 0
    fi
    ok=$(jget "$TMP/health.json" ok)
    db=$(jget "$TMP/health.json" db)
    case "$ok:$db" in
        true:true|true:ok|true:\"ok\") pass health "/api/health ok, db ok" ;;
        true:*) fail health "/api/health ok but db is '${db:-missing}'" ;;
        *) fail health "/api/health not ok (ok='${ok:-missing}', db='${db:-missing}')" ;;
    esac
}

check_worker() {
    [ -s "$TMP/health.json" ] || { fail worker "no health response to read the worker status from"; return 0; }
    online=$(jget "$TMP/health.json" worker.online)
    seen=$(jget "$TMP/health.json" worker.last_seen)
    busy=$(jget "$TMP/health.json" worker.busy)
    case "$online" in
        true) pass worker "Mac worker online (last seen ${seen:-?}, busy: ${busy:-null})" ;;
        false)
            if [ -z "$seen" ] || [ "$seen" = null ]; then
                warn worker "Mac worker never seen (jobs wait in the queue until it connects)"
            else
                warn worker "Mac worker offline, last seen $seen (jobs wait in the queue)"
            fi ;;
        *) fail worker "/api/health has no worker.online" ;;
    esac
}

check_anonymous() {
    a=$(http /dev/null "$URL/api/episodes")
    w=$(http /dev/null -X POST -H 'Content-Type: application/json' --data '{"kinds":["paint","render"]}' \
        "$URL/api/worker/claim")
    b=$(http /dev/null -X POST -H 'Content-Type: application/json' -H 'Authorization: Bearer smoke-not-a-token' \
        --data '{"host":"smoke","version":"smoke"}' "$URL/api/worker/heartbeat")
    case "$a:$w:$b" in
        40[13]:40[13]:40[13]) pass anon "anonymous API, no-token and bad-token worker calls refused ($a/$w/$b)" ;;
        *) fail anon "expected 401/403 without login or worker token, got episodes=$a claim=$w heartbeat=$b" ;;
    esac
}

check_login() {
    if [ -z "${SMOKE_USER:-}" ] || [ -z "${SMOKE_PASSWORD:-}" ]; then
        skip login "SMOKE_USER/SMOKE_PASSWORD not set"
        skip history "needs a login"
        return 0
    fi
    body=$TMP/login.json
    if have python3; then
        python3 -c 'import json,os; print(json.dumps({"user": os.environ["SMOKE_USER"], "password": os.environ["SMOKE_PASSWORD"]}))' >"$body"
    else
        esc() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
        printf '{"user":"%s","password":"%s"}' "$(esc "$SMOKE_USER")" "$(esc "$SMOKE_PASSWORD")" >"$body"
    fi
    code=$(http "$TMP/login.out" -c "$TMP/cookies" -X POST -H 'Content-Type: application/json' \
           --data-binary "@$body" "$URL/api/login")
    rm -f "$body"
    if [ "$code" = 200 ] && [ "$(jget "$TMP/login.out" ok)" = true ]; then
        pass login "POST /api/login accepted"
    else
        fail login "POST /api/login returned $code"
        skip history "needs a login"
        return 0
    fi
    code=$(http "$TMP/history.html" -b "$TMP/cookies" -A 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) Mobile/15E148' "$URL/")
    case "$code" in
        200) pass history "history page (/) 200 after login" ;;
        30*) fail history "history page redirected ($code): the session cookie was not accepted (Secure cookie over http?)" ;;
        *) fail history "history page returned $code" ;;
    esac
}

check_static() {
    code=$(http "$TMP/login.html" "$URL/login")
    if [ "$code" != 200 ]; then
        fail static "/login returned $code, no page to find a static asset on"
        return 0
    fi
    asset=$(grep -o '/static/[^"'"'"' >)?#]*' "$TMP/login.html" | head -n 1) || asset=""
    if [ -z "$asset" ]; then
        fail static "/login references no /static/ asset"
        return 0
    fi
    code=$(http "$TMP/asset" "$URL$asset")
    if [ "$code" = 200 ] && [ -s "$TMP/asset" ]; then
        pass static "$asset served ($(wc -c <"$TMP/asset" | tr -d ' ') bytes)"
    else
        fail static "$asset returned $code"
    fi
}

# ------------------------------------------------------------------------------------------------- the host

check_host() {
    if [ -z "$SSH_TARGET" ]; then
        return 0
    fi
    # Read-only commands; prints tagged lines. Never reads .env or prints environment variables.
    if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$SSH_TARGET" sh -s -- "$COMPOSE_DIR" "${COMPOSE_PROJECT:-stick}" >"$TMP/host.txt" 2>"$TMP/ssh.err" <<'REMOTE'
dir=$1
project=$2
if ! cd "$dir" 2>/dev/null; then echo "ERR|no stick directory $dir"; exit 0; fi
if ! docker compose version >/dev/null 2>&1; then echo "ERR|docker compose not available"; exit 0; fi
docker compose -p "$project" ps -a --format '{{.Service}}|{{.State}}|{{.Health}}' 2>/dev/null | sed 's/^/PS|/'
ids=$(docker compose -p "$project" ps -aq 2>/dev/null)
[ -n "$ids" ] && docker inspect -f '{{.Name}}|{{.RestartCount}}' $ids 2>/dev/null | sed 's/^/RC|/'
df -P / | awk 'NR==2 {print "DF|/|" $5 "|" $4}'
df -P "$dir" | awk 'NR==2 {print "DF|" $6 "|" $5 "|" $4}'
if command -v free >/dev/null 2>&1; then free -m | awk '/^Mem:/ {print "MEM|" $2 "|" $7}'; fi
echo "END"
REMOTE
    then
        fail ssh "ssh $SSH_TARGET failed: $(head -c 200 "$TMP/ssh.err" | tr '\n' ' ')"
        return 0
    fi
    if ! grep -q '^END$' "$TMP/host.txt"; then
        fail ssh "$(sed -n 's/^ERR|//p' "$TMP/host.txt" | head -n 1)"
        return 0
    fi

    total=$(grep -c '^PS|' "$TMP/host.txt") || total=0
    bad=$(awk -F'|' '$1 == "PS" && ($3 != "running" || ($4 != "" && $4 != "healthy")) {printf "%s%s=%s%s", sep, $2, $3, ($4 != "" ? "/" $4 : ""); sep=", "}' "$TMP/host.txt")
    if [ "$total" -eq 0 ]; then
        fail compose "no containers in compose project ${COMPOSE_PROJECT:-stick}"
    elif [ -n "$bad" ]; then
        fail compose "not running/healthy: $bad"
    else
        pass compose "$total containers running (healthy where a healthcheck exists)"
    fi

    restarts=$(awk -F'|' '$1 == "RC" && $3 > 0 {sub("^/", "", $2); printf "%s%s=%s", sep, $2, $3; sep=", "}' "$TMP/host.txt")
    most=$(awk -F'|' 'BEGIN {m = 0} $1 == "RC" && $3 > m {m = $3} END {print m}' "$TMP/host.txt")
    if [ -z "$restarts" ]; then
        pass restarts "no container restarts"
    elif [ "$most" -ge 5 ]; then
        fail restarts "restart loop? $restarts"
    else
        warn restarts "$restarts"
    fi

    # Judged by the space left, not the percentage: 10% of a 100 GB disk is plenty, 10% of 10 GB is not.
    # A video with its previews is 20-60 MB; a new web image build needs about 1 GB.
    awk -F'|' '$1 == "DF" {u = $3; sub("%", "", u); print $2, u, int($4 / 1048576)}' "$TMP/host.txt" | sort -u >"$TMP/df.txt"
    while read -r mount used gb; do
        free=$((100 - used))
        if [ "$gb" -lt 2 ]; then fail disk "$mount: only $gb GB free ($free%)"
        elif [ "$gb" -lt 5 ]; then warn disk "$mount: $gb GB free ($free%)"
        else pass disk "$mount: $gb GB free ($free%)"
        fi
    done <"$TMP/df.txt"

    mem=$(sed -n 's/^MEM|//p' "$TMP/host.txt" | head -n 1)
    if [ -z "$mem" ]; then
        warn memory "free(1) not available on the host"
    else
        mtotal=${mem%%|*}
        mavail=${mem#*|}
        pct=$((mavail * 100 / (mtotal > 0 ? mtotal : 1)))
        if [ "$mavail" -lt 100 ]; then fail memory "only ${mavail} MB available of ${mtotal} MB"
        elif [ "$pct" -lt 15 ]; then warn memory "${mavail} MB available of ${mtotal} MB ($pct%)"
        else pass memory "${mavail} MB available of ${mtotal} MB ($pct%)"
        fi
    fi
}

echo "smoke: $URL"
have curl || { echo "smoke: curl is required" >&2; exit 2; }
check_tls
check_health
check_worker
check_anonymous
check_login
check_static
check_host
printf 'smoke: %d passed, %d failed, %d warnings (%s)\n' "$NPASS" "$NFAIL" "$NWARN" "$URL"
[ "$NFAIL" -eq 0 ]
