"""Web Push to the user's phone (user 2026-11-13: "notify me when it's done"). No account and no paid service: the
server signs each message with its own VAPID key pair (VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY in /srv/stick/.env) and
the browser vendor's push service delivers it encrypted. On an iPhone, Web Push works for a site added to the Home
Screen (iOS 16.4+); on Android and desktop browsers it works from the browser.

    python -m webapp.push keys      prints a new key pair as .env lines (run once on the VPS)
"""
from __future__ import annotations

import base64
import json
import logging
import sys

log = logging.getLogger('stick-web')


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def generate_keys() -> tuple[str, str]:
    """(public, private), base64url: the public key as the browser's applicationServerKey (an uncompressed P-256
    point), the private key as its raw 32-byte scalar."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    private = key.private_numbers().private_value.to_bytes(32, 'big')
    return _b64(public), _b64(private)


def send(subscription: dict, payload: dict, private_key: str, subject: str, timeout: float = 10.0) -> int:
    """Send one notification. Returns the push service's HTTP status (201 delivered; 404/410 the subscription is gone,
    so the caller removes it); 0 when it could not be sent at all."""
    from pywebpush import WebPushException, webpush
    try:
        r = webpush(subscription_info=subscription, data=json.dumps(payload), vapid_private_key=private_key,
                    vapid_claims={'sub': subject}, timeout=timeout, ttl=24 * 3600)
        return int(getattr(r, 'status_code', 201) or 201)
    except WebPushException as exc:
        status = getattr(getattr(exc, 'response', None), 'status_code', 0) or 0
        log.warning('push: %s (HTTP %s)', str(exc)[:200], status)
        return int(status)
    except Exception as exc:                          # noqa: BLE001: a notification must never break a job
        log.warning('push failed: %s: %s', type(exc).__name__, str(exc)[:200])
        return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] != ['keys']:
        print('usage: python -m webapp.push keys', file=sys.stderr)
        return 2
    public, private = generate_keys()
    print(f'VAPID_PUBLIC_KEY={public}\nVAPID_PRIVATE_KEY={private}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
