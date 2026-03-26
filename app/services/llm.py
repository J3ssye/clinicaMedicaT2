from __future__ import annotations

from textwrap import dedent

from google import genai

from app.core.config import get_settings


settings = get_settings()


class GeminiService:
    def __init__(self) -> None:
        self.enabled = bool(settings.gemini_api_key)
        self.client = genai.Client(api_key=settings.gemini_api_key) if self.enabled else None

    def classify_intent(self, message: str) -> str:
        if not self.enabled:
            return self._fallback_intent(message)

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

        response = self.client.models.generate_content(model=settings.gemini_model, contents=prompt)
        text = getattr(response, "text", "") or ""
        label = text.strip().lower()
        if label in {"faq", "triage", "scheduling", "documents", "feedback", "fallback"}:
            return label
        return self._fallback_intent(message)

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
        response = self.client.models.generate_content(model=settings.gemini_model, contents=prompt)
        return (getattr(response, "text", "") or "").strip()

    @staticmethod
    def _fallback_intent(message: str) -> str:
        text = message.lower()
        if any(token in text for token in ["agendar", "consulta", "remarcar", "cancelar", "horario"]):
            return "scheduling"
        if any(token in text for token in ["dor", "febre", "enjoo", "vomito", "sintoma", "mal-estar"]):
            return "triage"
        if any(token in text for token in ["resultado", "exame", "documento", "laudo", "receita"]):
            return "documents"
        if any(token in text for token in ["nota", "avaliacao", "reclamacao", "elogio", "feedback"]):
            return "feedback"
        if any(token in text for token in ["horario", "endereco", "convenio", "valor", "duvida"]):
            return "faq"
        return "fallback"
