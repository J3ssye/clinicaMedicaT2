from sqlalchemy import select

from app.agents.base import AgentContext
from app.models.feedback import Feedback
from app.schemas.chat import OrchestratorResponse
from app.services.llm import GeminiService


class FeedbackAgent:
    def __init__(self) -> None:
        self.llm = GeminiService()

    async def handle(self, context: AgentContext) -> OrchestratorResponse:
        lowered = context.incoming_text.lower()
        if "consult" in lowered and "feedback" in lowered:
            stmt = (
                select(Feedback)
                .where(Feedback.patient_id == context.patient.id)
                .order_by(Feedback.created_at.desc())
            )
            recent_feedbacks = (await context.session.execute(stmt)).scalars().all()
            if not recent_feedbacks:
                reply = self._draft_feedback_reply(
                    patient_message=context.incoming_text,
                    guidance="Nao ha feedbacks anteriores vinculados ao cadastro do paciente.",
                    fallback="Nao encontrei feedbacks anteriores vinculados ao seu cadastro.",
                )
                return OrchestratorResponse(intent="feedback", reply_text=reply)
            latest = recent_feedbacks[0]
            rating_text = f"Nota: {latest.rating}/5. " if latest.rating else ""
            reply = self._draft_feedback_reply(
                patient_message=context.incoming_text,
                guidance=(
                    "Foi localizado o feedback mais recente do paciente. "
                    f"{rating_text}Mensagem registrada: {latest.raw_message}"
                ),
                fallback=(
                    f"Seu feedback mais recente foi localizado. {rating_text}"
                    f"Mensagem registrada: {latest.raw_message}"
                ),
            )
            return OrchestratorResponse(intent="feedback", reply_text=reply)

        rating = next((int(ch) for ch in context.incoming_text if ch.isdigit() and ch in "12345"), None)
        feedback = Feedback(
            patient_id=context.patient.id,
            rating=rating,
            summary="feedback via whatsapp",
            raw_message=context.incoming_text,
        )
        context.session.add(feedback)
        await context.session.commit()
        reply = self._draft_feedback_reply(
            patient_message=context.incoming_text,
            guidance="O feedback do paciente foi registrado com sucesso.",
            fallback="Obrigado pelo seu feedback. Seu retorno ajuda a melhorar nosso atendimento.",
        )
        return OrchestratorResponse(intent="feedback", reply_text=reply)

    def _draft_feedback_reply(self, *, patient_message: str, guidance: str, fallback: str) -> str:
        prompt = (
            "Voce e a atendente virtual da clinica para retorno de feedback. "
            "Responda em portugues do Brasil, em no maximo 2 frases, com tom acolhedor e objetivo. "
            f"\n\nContexto operacional:\n{guidance}"
        )
        reply = self.llm.draft_reply(prompt, patient_message)
        return reply or fallback
