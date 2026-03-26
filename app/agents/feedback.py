from sqlalchemy import select

from app.agents.base import AgentContext
from app.models.feedback import Feedback
from app.schemas.chat import OrchestratorResponse


class FeedbackAgent:
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
                return OrchestratorResponse(
                    intent="feedback",
                    reply_text="Nao encontrei feedbacks anteriores vinculados ao seu cadastro.",
                )
            latest = recent_feedbacks[0]
            rating_text = f"Nota: {latest.rating}/5. " if latest.rating else ""
            return OrchestratorResponse(
                intent="feedback",
                reply_text=f"Seu feedback mais recente foi localizado. {rating_text}Mensagem registrada: {latest.raw_message}",
            )

        rating = next((int(ch) for ch in context.incoming_text if ch.isdigit() and ch in "12345"), None)
        feedback = Feedback(
            patient_id=context.patient.id,
            rating=rating,
            summary="feedback via whatsapp",
            raw_message=context.incoming_text,
        )
        context.session.add(feedback)
        await context.session.commit()
        return OrchestratorResponse(
            intent="feedback",
            reply_text="Obrigado pelo seu feedback. Seu retorno ajuda a melhorar nosso atendimento.",
        )
