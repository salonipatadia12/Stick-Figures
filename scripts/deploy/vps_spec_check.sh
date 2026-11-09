#!/bin/sh
# Read-only VPS spec check (docs/DEPLOY.md, rollout step 1). Changes nothing on the server.
# Usage from the Mac:  ssh -i ~/.ssh/stick_vps_ed25519 <user>@<host> 'sh -s' < scripts/deploy/vps_spec_check.sh
echo "== os";        (. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME"); uname -srm
echo "== cpu";       nproc; grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2
echo "== memory";    free -h | sed -n '1,3p'
echo "== disk";      df -h / | tail -1
echo "== docker";    (docker --version && docker compose version) 2>/dev/null || echo "docker: not installed"
echo "== python";    python3 --version 2>/dev/null || echo "python3: not installed"
echo "== ffmpeg";    ffmpeg -version 2>/dev/null | head -1 || echo "ffmpeg: not installed"
echo "== listening"; (ss -tln 2>/dev/null || netstat -tln 2>/dev/null) | awk 'NR>1 {print $4}' | sort -u | tr '\n' ' '; echo
echo "== firewall";  (ufw status 2>/dev/null | head -3) || echo "ufw: not available"
echo "== ssh auth";  grep -HEi '^\s*(PasswordAuthentication|PermitRootLogin|PubkeyAuthentication)' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null || echo "sshd_config: no explicit settings readable (defaults apply)"
echo "== user";      id; (sudo -n true 2>/dev/null && echo "sudo: yes, without a password") || echo "sudo: needs a password, or not allowed"
echo "== already running (top memory)"; ps -eo rss=,comm= --sort=-rss 2>/dev/null | head -8 | awk '{printf "%6.0f MB  %s\n", $1/1024, $2}'
echo "== containers";  (docker ps --format '{{.Names}}  {{.Image}}  {{.Status}}' 2>/dev/null | head -10) || true
echo "== uptime";    uptime
