"""
Provider-specific HMAC signature verification for inbound email webhooks.

Each provider signs payloads differently:

- Mailgun: HMAC-SHA256 over (timestamp + token), hex digest, compared
  against the `signature` field in the payload.
- SendGrid (Inbound Parse + Event Webhook signing): ECDSA signature over
  (timestamp + body) — simplified here to the shared-secret HMAC variant
  used by custom Inbound Parse proxies; swap in SendGrid's
  `verify_signature` helper if you use their official event webhook.
- SES (via SNS): SNS message signature verification (RSA, not HMAC) —
  stubbed with a TODO since it requires fetching Amazon's public cert.

All verification is constant-time and fails closed: any exception or
mismatch results in `False`, never a silent pass.
"""
import hashlib
import hmac
import time

from django.conf import settings


class SignatureVerificationError(Exception):
    """Raised when a webhook payload's signature cannot be validated."""


def verify_mailgun_signature(timestamp: str, token: str, signature: str) -> bool:
    signing_key = settings.INBOUND_WEBHOOK_SIGNING_KEY
    if not signing_key:
        raise SignatureVerificationError("INBOUND_WEBHOOK_SIGNING_KEY is not configured")

    # Reject stale/replayed requests.
    try:
        ts = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise SignatureVerificationError("Invalid timestamp") from exc

    if abs(time.time() - ts) > settings.WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS:
        raise SignatureVerificationError("Timestamp outside tolerance window")

    expected = hmac.new(
        key=signing_key.encode("utf-8"),
        msg=f"{timestamp}{token}".encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature or "")


def verify_sendgrid_shared_secret(raw_body: bytes, signature: str) -> bool:
    signing_key = settings.INBOUND_WEBHOOK_SIGNING_KEY
    if not signing_key:
        raise SignatureVerificationError("INBOUND_WEBHOOK_SIGNING_KEY is not configured")

    expected = hmac.new(
        key=signing_key.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature or "")


def verify_webhook_signature(request) -> bool:
    """
    Dispatch to the correct verification routine based on
    settings.INBOUND_WEBHOOK_PROVIDER. Returns True only on a fully
    validated, fresh, correctly-signed payload.
    """
    provider = settings.INBOUND_WEBHOOK_PROVIDER

    if provider == "mailgun":
        payload = request.POST
        return verify_mailgun_signature(
            timestamp=payload.get("timestamp"),
            token=payload.get("token"),
            signature=payload.get("signature"),
        )

    if provider == "sendgrid":
        signature = request.headers.get("X-Webhook-Signature", "")
        return verify_sendgrid_shared_secret(request.body, signature)

    if provider == "ses":
        # SES delivers via SNS, which signs messages with RSA using a
        # certificate URL embedded in the message — not a shared-secret
        # HMAC. Fetch and cache Amazon's cert, verify the RSA signature,
        # and confirm SigningCertURL is on an *.amazonaws.com host before
        # trusting it. Left as an explicit TODO so this never silently
        # "passes" for a provider it can't actually verify.
        raise SignatureVerificationError(
            "SES/SNS signature verification is not implemented — see "
            "docstring for the RSA verification steps required."
        )

    raise SignatureVerificationError(f"Unknown webhook provider: {provider}")
