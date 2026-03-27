from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from textwrap import dedent

import httpx
from google import genai

from app.core.config import get_settings


settings = get_settings()


@dataclass
class LLMResponse:
    provider: str
    text: str
    latency_ms: float
    meta: dict[str, str]


class BaseLLMProvider:
    name = "base"

    def is_enabled(self) -> bool:
        raise NotImplementedError

    def generate(self, *, prompt: str, config: dict, kind: str) -> LLMResponse | None:
        raise NotImplementedError


class GeminiProvider(BaseLLMProvider):
    name = "gemini"

    def __init__(self) -> None:
        self.enabled = bool(settings.gemini_api_key)
        self.client = genai.Client(api_key=settings.gemini_api_key) if self.enabled else None

    def is_enabled(self) -> bool:
        return self.enabled

    def generate(self, *, prompt: str, config: dict, kind: str) -> LLMResponse | None:
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
        candidates = getattr(response, "candidates", []) or []
        first = candidates[0] if candidates else None
        meta = {
            "finish_reason": str(getattr(first, "finish_reason", None)),
            "prompt_feedback": str(getattr(response, "prompt_feedback", None)),
            "kind": kind,
        }
        return LLMResponse(provider=self.name, text=text, latency_ms=latency_ms, meta=meta)

    def _extract_text(self, response) -> str:
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
            joined = " ".join(fragments).strip()
            if joined:
                return joined
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
            timeout=20.0,
        )

    def is_enabled(self) -> bool:
        return self.enabled

    def generate(self, *, prompt: str, config: dict, kind: str) -> LLMResponse | None:
        if not self.enabled:
            return None
        payload = {
            "model": settings.openai_model,
            "input": prompt,
            "temperature": config.get("temperature", 0.0),
            "top_p": config.get("top_p", 1.0),
            "max_output_tokens": config.get("max_output_tokens", 256),
        }
        started = time.monotonic()
        response = self.client.post("/responses", json=payload)
        response.raise_for_status()
        body = response.json()
        latency_ms = round((time.monotonic() - started) * 1000, 2)
        text = self._extract_text(body)
        meta = {
            "response_id": str(body.get("id")),
            "status": str(body.get("status")),
            "kind": kind,
        }
        return LLMResponse(provider=self.name, text=text, latency_ms=latency_ms, meta=meta)

    @staticmethod
    def _extract_text(body: dict) -> str:
        output_text = body.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        outputs = body.get("output") or []
        fragments: list[str] = []
        for item in outputs:
            contents = item.get("content") or []
            for content in contents:
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
            timeout=20.0,
        )

    def is_enabled(self) -> bool:
        return self.enabled

    def generate(self, *, prompt: str, config: dict, kind: str) -> LLMResponse | None:
        if not self.enabled:
            return None
        system_prompt, user_prompt = self._split_prompt(prompt)
        payload = {
            "model": settings.anthropic_model,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "max_tokens": config.get("max_output_tokens", 256),
            "temperature": config.get("temperature", 0.0),
            "top_p": config.get("top_p", 1.0),
        }
        started = time.monotonic()
        response = self.client.post("/messages", json=payload)
        response.raise_for_status()
        body = response.json()
        latency_ms = round((time.monotonic() - started) * 1000, 2)
        text = self._extract_text(body)
        meta = {
            "id": str(body.get("id")),
            "stop_reason": str(body.get("stop_reason")),
            "kind": kind,
        }
        return LLMResponse(provider=self.name, text=text, latency_ms=latency_ms, meta=meta)

    @staticmethod
    def _split_prompt(prompt: str) -> tuple[str, str]:
        marker = "Mensagem do paciente:"
        if marker not in prompt:
            return "", prompt
        system_prompt, user_prompt = prompt.split(marker, maxsplit=1)
        return system_prompt.strip(), user_prompt.strip()

    @staticmethod
    def _extract_text(body: dict) -> str:
        fragments: list[str] = []
        for item in body.get("content") or []:
            if item.get("type") == "text":
                text = (item.get("text") or "").strip()
                if text:
                    fragments.append(text)
        return " ".join(fragments).strip()


class GeminiService:
    """Router multi-provedor com failover automatico para WhatsApp."""

    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)
        self.generation_config = {"temperature": 0.0, "top_p": 0.9, "max_output_tokens": 256}
        self.classify_config = {"temperature": 0.0, "top_p": 0.1, "max_output_tokens": 32}
        self.providers = self._build_providers()
        self.enabled = any(provider.is_enabled() for provider in self.providers)

    def classify_intent(self, message: str) -> str:
        local_intent = self._fallback_intent(message)
        if local_intent != "fallback":
            return local_intent
        if not self.enabled:
            return local_intent

        prompt = dedent(
            f"""
            Classifique a mensagem do paciente em apenas uma etiqueta:
            faq, triage, scheduling, documents, feedback, fallback.

            Regras:
            - faq: horarios, convenio, localizacao, preparo, contatos.
            - triage: sintomas, dor, febre, mal-estar, urgencia, orientacao clinica.
            - scheduling: marcar, remarcar, cancelar, consultar horario.
            - documents: exame, resultado, laudo, receita, documento.
            - feedback: avaliacao, nota, experiencia, elogio, reclamacao.
            - fallback: qualquer outra coisa.

            Mensagem: {message}
            Responda somente com a etiqueta.
            """
        ).strip()

        response = self._call_with_failover(kind="classify", prompt=prompt, config=self.classify_config)
        if response is None:
            return local_intent

        label = response.text.strip().lower()
        if label in {"faq", "triage", "scheduling", "documents", "feedback", "fallback"}:
            return label
        return local_intent

    def draft_reply(self, system_prompt: str, message: str) -> str:
        if not self.enabled:
            return ""

        prompt = dedent(
            f"""
            {system_prompt}

            Mensagem do paciente:
            {message}
            """
        ).strip()
        response = self._call_with_failover(kind="generation", prompt=prompt, config=self.generation_config)
        return response.text if response else ""

    def draft_fallback_reply(self, message: str) -> str:
        if not self.enabled:
            return ""
        prompt = dedent(
            """
            Voce e a atendente virtual de uma clinica medica no WhatsApp.
            Responda em portugues do Brasil, em no maximo 2 frases, de forma objetiva e cordial.
            Seu objetivo e manter a conversa andando sem deixar o paciente sem resposta.

            Regras:
            - Se o paciente quiser marcar, remarcar ou cancelar consulta, explique o proximo passo.
            - Se a mensagem parecer uma duvida geral, responda de forma pratica.
            - Se houver sintomas, oriente procurar consulta e cite urgencia apenas se houver risco evidente.
            - Nunca mencione fallback, erro interno ou falha da IA.
            """
        ).strip()
        return self.draft_reply(prompt, message)

    def _call_with_failover(self, *, kind: str, prompt: str, config: dict) -> LLMResponse | None:
        for provider in self.providers:
            if not provider.is_enabled():
                continue
            attempts = 2
            last_exc: Exception | None = None
            for attempt in range(1, attempts + 1):
                try:
                    response = provider.generate(prompt=prompt, config=config, kind=kind)
                    if response is None:
                        break
                    self._log_success(response)
                    if response.text:
                        return response
                    self.logger.warning(
                        "llm_empty_response provider=%s kind=%s attempt=%s",
                        provider.name,
                        kind,
                        attempt,
                    )
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    self.logger.warning(
                        "llm_retry provider=%s kind=%s attempt=%s prompt_len=%s",
                        provider.name,
                        kind,
                        attempt,
                        len(prompt),
                        exc_info=True,
                    )
                    if self._is_fast_fail_error(exc):
                        break
                    time.sleep(0.2)
            if last_exc is not None:
                self.logger.exception(
                    "llm_provider_failed provider=%s kind=%s attempts=%s prompt_len=%s",
                    provider.name,
                    kind,
                    attempts,
                    len(prompt),
                    exc_info=last_exc,
                )
            else:
                self.logger.warning(
                    "llm_provider_failed provider=%s kind=%s attempts=%s prompt_len=%s reason=empty_response",
                    provider.name,
                    kind,
                    attempts,
                    len(prompt),
                )
        return None

    def _log_success(self, response: LLMResponse) -> None:
        self.logger.info(
            "llm_response provider=%s latency_ms=%.2f text_len=%s meta=%s",
            response.provider,
            response.latency_ms,
            len(response.text or ""),
            response.meta,
        )

    def _build_providers(self) -> list[BaseLLMProvider]:
        factories: dict[str, type[BaseLLMProvider]] = {
            "gemini": GeminiProvider,
            "openai": OpenAIProvider,
            "anthropic": AnthropicProvider,
        }
        providers: list[BaseLLMProvider] = []
        seen: set[str] = set()
        order = [item.strip().lower() for item in settings.llm_provider_order.split(",") if item.strip()]
        for name in order:
            factory = factories.get(name)
            if factory is None or name in seen:
                continue
            providers.append(factory())
            seen.add(name)
        for name, factory in factories.items():
            if name not in seen:
                providers.append(factory())
        return providers

    @staticmethod
    def _is_fast_fail_error(exc: Exception) -> bool:
        message = str(exc).upper()
        return any(token in message for token in ["RESOURCE_EXHAUSTED", "QUOTA", "429", "401", "403"])

    @staticmethod
    def _fallback_intent(message: str) -> str:
        text = GeminiService._normalize_text(message)
        if any(
            token in text
            for token in [
                "agendar",
                "agendamento",
                "consulta",
                "remarcar",
                "cancelar",
                "horario",
                "marcar",
            ]
        ):
            return "scheduling"
        if re.search(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", text) and re.search(r"\d{1,2}:\d{2}", text):
            return "scheduling"
        if any(
            token in text
            for token in [
                "dor",
                "febre",
                "enjoo",
                "vomito",
                "sintoma",
                "sintomas",
                "mal-estar",
                "mal estar",
                "atendimento medico",
            ]
        ):
            return "triage"
        if any(
            token in text
            for token in [
                "resultado",
                "exame",
                "documento",
                "documentos",
                "laudo",
                "receita",
                ".pdf",
                ".jpg",
                ".png",
                ".doc",
                ".zip",
                "arquivo",
            ]
        ):
            return "documents"
        if any(
            token in text
            for token in [
                "nota",
                "avaliacao",
                "reclamacao",
                "elogio",
                "feedback",
                "sugestao",
            ]
        ):
            return "feedback"
        if any(
            token in text
            for token in [
                "horario",
                "endereco",
                "convenio",
                "convenios",
                "duvidas",
                "valor",
                "duvida",
                "duvido",
                "ajuda",
                "informacao",
                "informacoes",
                "ola",
                "oi",
                "bom dia",
                "boa tarde",
                "boa noite",
                "atendem",
                "aceita",
                "olaa",
            ]
        ):
            return "faq"
        return "fallback"

    @staticmethod
    def _normalize_text(message: str) -> str:
        lowered = message.lower().strip()
        normalized = unicodedata.normalize("NFKD", lowered)
        return "".join(char for char in normalized if not unicodedata.combining(char))
