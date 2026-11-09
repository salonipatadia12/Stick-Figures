#!/bin/sh
# Read-only VPS spec check (docs/DEPLOY.md, rollout step 1). Changes nothing on the server.
# Usage from the Mac:  ssh <user>@<host> 'sh -s' < scripts/deploy/vps_spec_check.sh
echo "== os";        (. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME"); uname -srm
echo "== cpu";       nproc; grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2
echo "== memory";    free -h | sed -n '1,2p'
echo "== disk";      df -h / | tail -1
echo "== docker";    (docker --version && docker compose version) 2>/dev/null || echo "docker: not installed"
echo "== python";    python3 --version 2>/dev/null || echo "python3: not installed"
echo "== ffmpeg";    ffmpeg -version 2>/dev/null | head -1 || echo "ffmpeg: not installed"
echo "== listening"; (ss -tln 2>/dev/null || netstat -tln 2>/dev/null) | awk 'NR>1 {print $4}' | sort -u | tr '\n' ' '; echo
echo "== firewall";  (ufw status 2>/dev/null | head -3) || echo "ufw: not available"
echo "== ssh auth";  grep -Ei '^\s*(PasswordAuthentication|PermitRootLogin|PubkeyAuthentication)' /etc/ssh/sshd_config 2>/dev/null || echo "sshd_config: not readable"
echo "== uptime";    uptime
