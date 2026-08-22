from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings


settings = get_settings()
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MediaProcessingResult:
    text: str
    source: str
    mime_type: str | None = None


class MediaService:
    def __init__(self) -> None:
        self._openai_headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
        }

    async def transcribe_audio_from_url(
        self,
        *,
        media_url: str,
        mime_type: str | None = None,
        caption: str | None = None,
    ) -> MediaProcessingResult | None:
        if not settings.openai_api_key:
            return None
        audio_bytes = await self._download_bytes(media_url)
        if not audio_bytes:
            return None

        filename = self._guess_filename(mime_type, fallback="audio.ogg")
        files = {
            "file": (filename, audio_bytes, mime_type or "application/octet-stream"),
        }
        data = {
            "model": settings.openai_audio_model,
            "language": "pt",
            "response_format": "text",
        }
        try:
            async with httpx.AsyncClient(timeout=settings.llm_http_timeout_seconds * 3) as client:
                response = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers=self._openai_headers,
                    data=data,
                    files=files,
                )
                response.raise_for_status()
            transcript = response.text.strip()
        except Exception:
            logger.warning("audio_transcription_failed", exc_info=True)
            return None
        if not transcript:
            return None
        prefix = f"Legenda do paciente: {caption.strip()}\n\n" if caption and caption.strip() else ""
        return MediaProcessingResult(
            text=f"{prefix}[Áudio transcrito do paciente] {transcript}".strip(),
            source="openai_audio",
            mime_type=mime_type,
        )

    async def analyze_image_from_url(
        self,
        *,
        media_url: str,
        mime_type: str | None = None,
        caption: str | None = None,
    ) -> MediaProcessingResult | None:
        if not settings.openai_api_key:
            return None
        image_bytes = await self._download_bytes(media_url)
        if not image_bytes:
            return None
        mime = mime_type or "image/jpeg"
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:{mime};base64,{b64}"
        instructions = (
            "Você é a MALU, secretária virtual de uma clínica médica. "
            "Analise a imagem enviada pelo paciente e descreva apenas informações visuais úteis para o atendimento. "
            "Se parecer exame, pedido, receita, atestado, documento ou foto clínica, resuma os dados legíveis e o tipo provável do material. "
            "Não invente diagnóstico. Responda em português do Brasil em até 8 linhas."
        )
        if caption and caption.strip():
            instructions += f"\n\nLegenda enviada pelo paciente: {caption.strip()}"
        payload = {
            "model": settings.openai_model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": instructions},
                        {"type": "input_image", "image_url": data_url},
                    ],
                }
            ],
            "max_output_tokens": 500,
        }
        try:
            async with httpx.AsyncClient(timeout=settings.llm_http_timeout_seconds * 3) as client:
                response = await client.post(
                    "https://api.openai.com/v1/responses",
                    headers={**self._openai_headers, "Content-Type": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
            analysis = self._extract_response_text(body)
        except Exception:
            logger.warning("image_analysis_failed", exc_info=True)
            return None
        if not analysis:
            return None
        return MediaProcessingResult(
            text=f"[Imagem enviada pelo paciente] {analysis}".strip(),
            source="openai_image",
            mime_type=mime_type,
        )

    async def _download_bytes(self, media_url: str) -> bytes:
        headers: dict[str, str] = {}
        if settings.waha_api_key:
            headers["X-Api-Key"] = settings.waha_api_key
        async with httpx.AsyncClient(timeout=settings.llm_http_timeout_seconds * 3) as client:
            response = await client.get(media_url, headers=headers)
            response.raise_for_status()
            return response.content

    @staticmethod
    def _guess_filename(mime_type: str | None, *, fallback: str) -> str:
        mapping = {
            "audio/ogg": "audio.ogg",
            "audio/opus": "audio.ogg",
            "audio/mpeg": "audio.mp3",
            "audio/mp4": "audio.m4a",
            "audio/webm": "audio.webm",
            "audio/wav": "audio.wav",
        }
        if mime_type and mime_type in mapping:
            return mapping[mime_type]
        return fallback

    @staticmethod
    def _extract_response_text(body: dict[str, Any]) -> str:
        output_text = body.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()
        outputs = body.get("output") or []
        fragments: list[str] = []
        for item in outputs:
            for content in item.get("content") or []:
                content_type = content.get("type")
                if content_type in {"output_text", "text"}:
                    text = (content.get("text") or "").strip()
                    if text:
                        fragments.append(text)
        return " ".join(fragments).strip()
