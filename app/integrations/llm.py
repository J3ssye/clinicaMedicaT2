from __future__ import annotations

import logging
import re
import time
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from google import genai

from app.core.config import get_settings
from app.schemas.chat import ChatMessage


settings = get_settings()


@dataclass(slots=True)
class LLMResponse:
    provider: str
    text: str
    latency_ms: float
    meta: dict[str, str]


class BaseLLMProvider:
    name = "base"

    def is_enabled(self) -> bool:
        raise NotImplementedError

    def generate(self, *, prompt: str, config: dict[str, Any], kind: str) -> LLMResponse | None:
        raise NotImplementedError


class GeminiProvider(BaseLLMProvider):
    name = "gemini"

    def __init__(self) -> None:
        self.enabled = bool(settings.gemini_api_key)
        self.client = genai.Client(api_key=settings.gemini_api_key) if self.enabled else None

    def is_enabled(self) -> bool:
        return self.enabled

    def generate(self, *, prompt: str, config: dict[str, Any], kind: str) -> LLMResponse | None:
        if not self.enabled or self.client is None:
            return None
        started = time.monotonic()
        response = self.client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=config,
        )
        latency_ms = round((time.monotonic() - started) * 1000, 2)
        text = self._extract_text(response)
        return LLMResponse(
            provider=self.name,
            text=text,
            latency_ms=latency_ms,
            meta={"kind": kind},
        )

    @staticmethod
    def _extract_text(response: Any) -> str:
        text = (getattr(response, "text", "") or "").strip()
        if text:
            return text
        candidates = getattr(response, "candidates", []) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) or []
            fragments: list[str] = []
            for part in parts:
                part_text = (getattr(part, "text", "") or "").strip()
                if part_text:
                    fragments.append(part_text)
            if fragments:
                return " ".join(fragments).strip()
        return ""


class OpenAIProvider(BaseLLMProvider):
    name = "openai"

    def __init__(self) -> None:
        self.enabled = bool(settings.openai_api_key)
        self.client = httpx.Client(
            base_url="https://api.openai.com/v1",
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            timeout=settings.llm_http_timeout_seconds,
        )

    def is_enabled(self) -> bool:
        return self.enabled

    def generate(self, *, prompt: str, config: dict[str, Any], kind: str) -> LLMResponse | None:
        if not self.enabled:
            return None
        payload = {
            "model": settings.openai_model,
            "input": prompt,
            "temperature": config.get("temperature", 0.0),
            "top_p": config.get("top_p", 1.0),
            "max_output_tokens": config.get("max_output_tokens", settings.llm_max_output_tokens),
        }
        started = time.monotonic()
        response = self.client.post("/responses", json=payload)
        response.raise_for_status()
        body = response.json()
        latency_ms = round((time.monotonic() - started) * 1000, 2)
        return LLMResponse(
            provider=self.name,
            text=self._extract_text(body),
            latency_ms=latency_ms,
            meta={"kind": kind, "status": str(body.get("status"))},
        )

    @staticmethod
    def _extract_text(body: dict[str, Any]) -> str:
        output_text = body.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()
        outputs = body.get("output") or []
        fragments: list[str] = []
        for item in outputs:
            for content in item.get("content") or []:
                if content.get("type") in {"output_text", "text"}:
                    text = (content.get("text") or "").strip()
                    if text:
                        fragments.append(text)
        return " ".join(fragments).strip()


class AnthropicProvider(BaseLLMProvider):
    name = "anthropic"

    def __init__(self) -> None:
        self.enabled = bool(settings.anthropic_api_key)
        self.client = httpx.Client(
            base_url="https://api.anthropic.com/v1",
            headers={
                "x-api-key": settings.anthropic_api_key or "",
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            timeout=settings.llm_http_timeout_seconds,
        )

    def is_enabled(self) -> bool:
        return self.enabled

    def generate(self, *, prompt: str, config: dict[str, Any], kind: str) -> LLMResponse | None:
        if not self.enabled:
            return None
        payload = {
            "model": settings.anthropic_model,
            "system": "Responda em português do Brasil.",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": config.get("max_output_tokens", settings.llm_max_output_tokens),
            "temperature": config.get("temperature", 0.0),
            "top_p": config.get("top_p", 1.0),
        }
        started = time.monotonic()
        response = self.client.post("/messages", json=payload)
        response.raise_for_status()
        body = response.json()
        latency_ms = round((time.monotonic() - started) * 1000, 2)
        fragments = [
            item.get("text", "").strip()
            for item in body.get("content") or []
            if item.get("type") == "text" and item.get("text")
        ]
        return LLMResponse(
            provider=self.name,
            text=" ".join(fragments).strip(),
            latency_ms=latency_ms,
            meta={"kind": kind, "stop_reason": str(body.get("stop_reason"))},
        )


class LLMService:
    """Router multi-provedor com foco em respostas claras, completas e econômicas."""

    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)
        self.short_config = {
            "temperature": 0.0,
            "top_p": 0.9,
            "max_output_tokens": settings.llm_max_output_tokens,
        }
        self.classify_config = {
            "temperature": 0.0,
            "top_p": 0.1,
            "max_output_tokens": settings.llm_classify_max_output_tokens,
        }
        self.providers = self._build_providers()
        self.enabled = any(provider.is_enabled() for provider in self.providers)

    def draft_reply(self, system_prompt: str, message: str, history: Sequence[ChatMessage] | None = None) -> str:
        if not self.enabled:
            return ""
        prompt = self._build_prompt(system_prompt=system_prompt, message=message, history=history or [])
        response = self._call_with_failover(kind="reply", prompt=prompt, config=self.short_config)
        return response.text if response else ""

    def classify_intent(self, message: str) -> str:
        local_intent = self._fallback_intent(message)
        if local_intent != "fallback" or not settings.llm_enable_classification_fallback or not self.enabled:
            return local_intent
        prompt = (
            "Classifique a mensagem em apenas uma etiqueta: faq, triage, scheduling, documents, feedback, fallback.\n"
            "Responda somente com a etiqueta.\n"
            f"Mensagem: {message}"
        )
        response = self._call_with_failover(kind="classify", prompt=prompt, config=self.classify_config)
        label = (response.text.strip().lower() if response else "")
        return label if label in {"faq", "triage", "scheduling", "documents", "feedback", "fallback"} else local_intent

    def _call_with_failover(
        self,
        *,
        kind: str,
        prompt: str,
        config: dict[str, Any],
    ) -> LLMResponse | None:
        for provider in self.providers:
            if not provider.is_enabled():
                continue
            try:
                response = provider.generate(prompt=prompt, config=config, kind=kind)
                if response and response.text:
                    self.logger.info(
                        "llm_response provider=%s kind=%s latency_ms=%.2f prompt_len=%s text_len=%s",
                        response.provider,
                        kind,
                        response.latency_ms,
                        len(prompt),
                        len(response.text),
                    )
                    return response
            except Exception:
                self.logger.warning(
                    "llm_provider_failed provider=%s kind=%s prompt_len=%s",
                    provider.name,
                    kind,
                    len(prompt),
                    exc_info=True,
                )
                continue
        return None

    def _build_providers(self) -> list[BaseLLMProvider]:
        factories: dict[str, type[BaseLLMProvider]] = {
            "gemini": GeminiProvider,
            "openai": OpenAIProvider,
            "anthropic": AnthropicProvider,
        }
        ordered = [name.strip() for name in settings.llm_provider_order.split(",") if name.strip()]
        providers: list[BaseLLMProvider] = []
        seen: set[str] = set()
        for name in ordered:
            factory = factories.get(name)
            if factory and name not in seen:
                providers.append(factory())
                seen.add(name)
        for name, factory in factories.items():
            if name not in seen:
                providers.append(factory())
        return providers

    @staticmethod
    def _build_prompt(
        *,
        system_prompt: str,
        message: str,
        history: Sequence[ChatMessage],
    ) -> str:
        parts: list[str] = [system_prompt.strip()]
        if history:
            parts.append("Histórico recente:")
            for item in history[-settings.llm_history_max_messages :]:
                role = "Paciente" if item.role == "user" else "Assistente"
                parts.append(f"- {role}: {item.content.strip()[:220]}")
        parts.append(f"Mensagem do paciente: {message.strip()[:500]}")
        parts.append("Responda de forma natural, completa e objetiva. Prefira algo entre 4 e 8 frases curtas quando isso evitar resposta truncada ou incompleta. Nunca termine a resposta pela metade.")
        return "\n".join(part for part in parts if part)

    @staticmethod
    def _fallback_intent(message: str) -> str:
        text = LLMService._normalize(message)
        # Perguntas sobre preço/valor são FAQ mesmo que citem "consulta" ou médico
        if any(token in text for token in ["valor", "preco", "quanto", "custo", "custa"]):
            return "faq"
        if any(token in text for token in ["agendar", "consulta", "remarcar", "cancelar", "horario", "vaga"]):
            return "scheduling"
        if any(token in text for token in ["dor", "febre", "falta de ar", "vomito", "tontura", "sintoma"]):
            return "triage"
        if any(token in text for token in ["resultado", "exame", "laudo", "receita", "arquivo", "documento"]):
            return "documents"
        if any(token in text for token in ["nota", "feedback", "reclamacao", "elogio", "avaliacao"]):
            return "feedback"
        if any(token in text for token in ["horario", "endereco", "convenio", "jejum", "preparo", "oi", "ola"]):
            return "faq"
        return "fallback"

    @staticmethod
    def _normalize(message: str) -> str:
        lowered = message.lower().strip()
        normalized = unicodedata.normalize("NFKD", lowered)
        text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        return re.sub(r"\s+", " ", text)


GeminiService = LLMService
