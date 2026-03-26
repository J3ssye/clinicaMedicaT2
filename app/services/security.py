import hashlib
import hmac

from fastapi import HTTPException, Request, status

from app.core.config import get_settings


settings = get_settings()


async def verify_webhook_signature(request: Request) -> bytes:
    body = await request.body()
    expected_signature = request.headers.get("X-Webhook-Signature", "")
    computed_signature = hmac.new(
        settings.webhook_hmac_secret.encode(),
        msg=body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    if expected_signature and not hmac.compare_digest(expected_signature, computed_signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature.",
        )

    return body
