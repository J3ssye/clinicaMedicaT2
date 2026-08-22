from __future__ import annotations

import hashlib
import hmac

from fastapi import HTTPException, Request, status

from app.core.config import get_settings


settings = get_settings()


def _get_received_hmac(request: Request) -> str:
    return (
        request.headers.get("X-Webhook-Hmac")
        or request.headers.get("X-Webhook-Signature")
        or ""
    ).strip()


async def verify_webhook_signature(request: Request) -> bytes:
    body = await request.body()

    if not settings.require_webhook_signature:
        return body

    if not settings.webhook_hmac_secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="webhook_signature_not_configured",
        )

    received_signature = _get_received_hmac(request)
    if not received_signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing_webhook_signature",
        )

    algorithm = (request.headers.get("X-Webhook-Hmac-Algorithm") or "sha512").lower().strip()
    if algorithm not in {"sha256", "sha512"}:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unsupported_webhook_hmac_algorithm",
        )

    digestmod = hashlib.sha512 if algorithm == "sha512" else hashlib.sha256
    computed_signature = hmac.new(
        settings.webhook_hmac_secret.encode("utf-8"),
        msg=body,
        digestmod=digestmod,
    ).hexdigest()

    if not hmac.compare_digest(received_signature, computed_signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_webhook_signature",
        )

    return body
