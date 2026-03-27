from typing import Literal

from pydantic import BaseModel, Field


ChatRole = Literal["system", "user", "assistant"]


class ChatMessage(BaseModel):
    role: ChatRole
    content: str


class ChatRequest(BaseModel):
    session_id: str = Field(
        ...,
        description="Identificador da sessao. No WhatsApp, use o numero/chat_id do paciente.",
    )
    message: str
    patient_name: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    reply_text: str
    messages: list[ChatMessage]


class OrchestratorResponse(BaseModel):
    reply_text: str
    messages: list[ChatMessage]
