from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ChatRole = Literal["system", "user", "assistant"]
ChatIntent = Literal[
    "faq", "triage", "scheduling", "documents",
    "feedback", "post_consult_feedback",
    "d1_confirmation",
    "health_followup",
    "acknowledgement",
    "fallback",
]


class ChatMessage(BaseModel):
    role: ChatRole
    content: str


class ChatRequest(BaseModel):
    session_id: str = Field(
        ...,
        description="Identificador da sessão. No WhatsApp, use o número/chat_id do paciente.",
    )
    message: str
    patient_name: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    reply_text: str
    messages: list[ChatMessage] = Field(default_factory=list)
    intent: ChatIntent = "fallback"
    escalate_to_human: bool = False
    llm_used: bool = False


class OrchestratorResponse(BaseModel):
    reply_text: str
    messages: list[ChatMessage] = Field(default_factory=list)
    intent: ChatIntent = "fallback"
    escalate_to_human: bool = False
    llm_used: bool = False
    silent: bool = False  # True = não enviar resposta no WhatsApp nem logar outbound
