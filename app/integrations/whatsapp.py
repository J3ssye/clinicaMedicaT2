from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from app.core.config import get_settings


settings = get_settings()
logger = logging.getLogger(__name__)


class WhatsAppClient:
    def __init__(self) -> None:
        self.base_url = settings.waha_base_url.rstrip("/")
        self.session = settings.waha_session
        self.headers: dict[str, str] = {}
        if settings.waha_api_key:
            self.headers["X-Api-Key"] = settings.waha_api_key

    async def send_seen(self, chat_id: str) -> dict[str, Any]:
        url = f"{self.base_url}/api/sendSeen"
        payload = {"session": self.session, "chatId": chat_id}
        return await self._post(url, payload)

    async def send_text(self, chat_id: str, text: str, *, reply_to: str | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/api/sendText"
        cleaned = self._sanitize_text(text)
        chunks = self._chunk_text(
            cleaned,
            soft_limit=settings.waha_message_soft_limit,
            hard_limit=settings.waha_message_hard_limit,
        )
        result: dict[str, Any] = {}
        for index, chunk in enumerate(chunks):
            payload: dict[str, Any] = {"session": self.session, "chatId": chat_id, "text": chunk}
            if reply_to and index == 0:
                payload["reply_to"] = reply_to
            result = await self._post(url, payload)
            if index < len(chunks) - 1 and settings.waha_chunk_delay_ms > 0:
                await asyncio.sleep(settings.waha_chunk_delay_ms / 1000)
        return result

    @staticmethod
    def _sanitize_text(text: str) -> str:
        cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned

    @classmethod
    def _chunk_text(
        cls,
        text: str,
        *,
        soft_limit: int = 2600,
        hard_limit: int = 3500,
    ) -> list[str]:
        if len(text) <= soft_limit:
            return [text]

        paragraphs = [part.strip() for part in re.split(r"\n{2,}", text) if part.strip()]
        if not paragraphs:
            return cls._split_oversized_piece(text, hard_limit)

        chunks: list[str] = []
        current = ""
        for paragraph in paragraphs:
            for piece in cls._split_oversized_piece(paragraph, hard_limit):
                candidate = f"{current}\n\n{piece}".strip() if current else piece
                if len(candidate) <= soft_limit:
                    current = candidate
                    continue
                if current:
                    chunks.append(current)
                current = piece
        if current:
            chunks.append(current)
        return chunks or [text]

    @classmethod
    def _split_oversized_piece(cls, text: str, limit: int) -> list[str]:
        if len(text) <= limit:
            return [text]

        sentences = re.split(r"(?<=[.!?…])\s+", text)
        if len(sentences) <= 1:
            return cls._split_by_fallback(text, limit)

        parts: list[str] = []
        current = ""
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) > limit:
                if current:
                    parts.append(current)
                    current = ""
                parts.extend(cls._split_by_fallback(sentence, limit))
                continue
            candidate = f"{current} {sentence}".strip() if current else sentence
            if len(candidate) <= limit:
                current = candidate
                continue
            parts.append(current)
            current = sentence
        if current:
            parts.append(current)
        return parts or cls._split_by_fallback(text, limit)

    @staticmethod
    def _split_by_fallback(text: str, limit: int) -> list[str]:
        chunks: list[str] = []
        remaining = text.strip()
        while remaining:
            if len(remaining) <= limit:
                chunks.append(remaining)
                break
            split_at = remaining.rfind(" ", 0, limit)
            if split_at < 1:
                split_at = limit
            part = remaining[:split_at].strip()
            if part:
                chunks.append(part)
            remaining = remaining[split_at:].strip()
        return chunks or [text]

    async def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(url, json=payload, headers=self.headers)
            data: dict[str, Any]
            try:
                data = response.json()
            except Exception:
                data = {"text": response.text}
            if response.is_error:
                logger.warning(
                    "waha_request_failed status=%s url=%s payload_keys=%s response=%s",
                    response.status_code,
                    url,
                    list(payload.keys()),
                    data,
                )
            response.raise_for_status()
            return data
