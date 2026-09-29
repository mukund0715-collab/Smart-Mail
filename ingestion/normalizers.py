"""
Normalize provider-specific webhook payloads into the flat shape
EmailRecord expects: message_id, sender_email, subject, received_at, body.
"""
from datetime import datetime, timezone

from django.conf import settings


def _now():
    return datetime.now(timezone.utc)


def normalize_mailgun(payload: dict) -> dict:
    return {
        "message_id": payload.get("Message-Id") or payload.get("message-id", ""),
        "sender_email": payload.get("sender", "") or payload.get("from", ""),
        "subject": payload.get("subject", ""),
        "received_at": _now(),
        "body": payload.get("body-plain", ""),
    }


def normalize_sendgrid(payload: dict) -> dict:
    return {
        "message_id": payload.get("headers", "").split("Message-ID:")[-1].split("\n")[0].strip()
        if "Message-ID:" in payload.get("headers", "")
        else payload.get("envelope", ""),
        "sender_email": payload.get("from", ""),
        "subject": payload.get("subject", ""),
        "received_at": _now(),
        "body": payload.get("text", ""),
    }


def normalize_ses(payload: dict) -> dict:
    mail = payload.get("mail", {})
    common_headers = mail.get("commonHeaders", {})
    return {
        "message_id": mail.get("messageId", ""),
        "sender_email": (common_headers.get("from") or [""])[0],
        "subject": common_headers.get("subject", ""),
        "received_at": _now(),
        "body": payload.get("content", ""),
    }


NORMALIZERS = {
    "mailgun": normalize_mailgun,
    "sendgrid": normalize_sendgrid,
    "ses": normalize_ses,
}


def normalize_payload(payload: dict) -> dict:
    provider = settings.INBOUND_WEBHOOK_PROVIDER
    normalizer = NORMALIZERS.get(provider)
    if normalizer is None:
        raise ValueError(f"No normalizer registered for provider: {provider}")
    return normalizer(payload)
