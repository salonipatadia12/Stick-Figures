#!/bin/sh
# One-time SSH key install for the VPS (docs/DEPLOY.md, rollout step 1). Uses the password file exactly ONCE: a single
# password login that appends this Mac's public key to the user's ~/.ssh/authorized_keys. Then it checks that the key
# alone logs in (password login refused on the client side) and stops. It changes nothing else on the VPS: password
# login stays on until the user OKs the plan.
#
#   scripts/deploy/install_key.sh <user>@<host> <password-file>
#
# - The key is this project's own (~/.ssh/stick_vps_ed25519), separate from any other key on this Mac, so it can be
#   revoked on its own. It is never copied into the repository.
# - The password goes to ssh through SSH_ASKPASS (a throwaway helper that reads the file); it is never printed, never
#   on a command line and never stored anywhere else.
set -eu
[ $# -eq 2 ] || { echo "usage: $0 <user>@<host> <password-file>" >&2; exit 2; }
TARGET=$1
PWFILE=$2
[ -r "$PWFILE" ] || { echo "cannot read the password file" >&2; exit 2; }
KEY="$HOME/.ssh/stick_vps_ed25519"

if [ ! -f "$KEY" ]; then
  ssh-keygen -q -t ed25519 -N '' -C "stick-figures gpu+deploy $(date +%Y-%m-%d)" -f "$KEY"
  echo "made key $KEY"
fi

KEYOPTS="-i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"

if ssh $KEYOPTS -o BatchMode=yes -o PasswordAuthentication=no "$TARGET" true 2>/dev/null; then
  echo "the key already logs in; the password was not used"
  exit 0
fi

ASK=$(mktemp -t stickask)
trap 'rm -f -- "${ASK:?}"' EXIT INT TERM
printf '#!/bin/sh\nexec cat "%s"\n' "$PWFILE" > "$ASK"
chmod 700 "$ASK"

# The one password login: append the public key (no duplicates), nothing else.
SSH_ASKPASS="$ASK" SSH_ASKPASS_REQUIRE=force DISPLAY=:0 \
  ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o PubkeyAuthentication=no \
      -o PreferredAuthentications=password,keyboard-interactive -o NumberOfPasswordPrompts=1 "$TARGET" \
      'umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; k=$(cat); grep -qxF "$k" ~/.ssh/authorized_keys || printf "%s\n" "$k" >> ~/.ssh/authorized_keys; echo "key added for $(whoami)"' \
  < "$KEY.pub"

if ssh $KEYOPTS -o BatchMode=yes -o PasswordAuthentication=no "$TARGET" true; then
  echo "key login works (password not used): ssh -i $KEY $TARGET"
else
  echo "the key did not log in; nothing else was changed" >&2
  exit 1
fi
