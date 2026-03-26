from typing import Literal

from pydantic import BaseModel


class OrchestratorResponse(BaseModel):
    intent: Literal["faq", "triage", "scheduling", "documents", "feedback", "fallback"]
    reply_text: str
    escalate_to_human: bool = False
