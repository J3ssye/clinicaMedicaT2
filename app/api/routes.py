from fastapi import APIRouter

from app.api.webhooks import router as webhook_router


api_router = APIRouter()
api_router.include_router(webhook_router, tags=["webhooks"])


@api_router.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "healthy"}
