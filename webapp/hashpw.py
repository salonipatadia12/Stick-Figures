"""`python -m webapp.hashpw` prints an APP_PASSWORD_HASH for a password typed at the prompt (or piped on stdin)."""
import getpass
import sys

from .auth import hash_password


def main(argv=None):
    if sys.stdin.isatty():
        password = getpass.getpass('Password: ')
        if password != getpass.getpass('Again: '):
            print('The two passwords differ.', file=sys.stderr)
            return 1
    else:
        password = sys.stdin.readline().rstrip('\n')
    if len(password) < 8:
        print('Use at least 8 characters.', file=sys.stderr)
        return 1
    print(hash_password(password))
    return 0


if __name__ == '__main__':
    sys.exit(main())
