from typing import Literal, TypedDict


Intent = Literal["faq", "triage", "scheduling", "documents", "feedback", "fallback"]


class ChatState(TypedDict, total=False):
    patient_id: int
    message: str
    intent: Intent
    reply_text: str
    escalate_to_human: bool
