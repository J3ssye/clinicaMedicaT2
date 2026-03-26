from typing import Any

import httpx

from app.core.config import get_settings


settings = get_settings()


class WahaClient:
    def __init__(self) -> None:
        self.base_url = settings.waha_base_url.rstrip("/")
        self.session = settings.waha_session
        self.headers = {}
        if settings.waha_api_key:
            self.headers["X-Api-Key"] = settings.waha_api_key

    async def send_text(self, chat_id: str, text: str) -> dict[str, Any]:
        url = f"{self.base_url}/api/sendText"
        payload = {"session": self.session, "chatId": chat_id, "text": text}
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(url, json=payload, headers=self.headers)
            response.raise_for_status()
            return response.json()
