from sqlalchemy import select

from app.agents.base import AgentContext
from app.models.document import Document
from app.schemas.chat import OrchestratorResponse


class DocumentsAgent:
    async def handle(self, context: AgentContext) -> OrchestratorResponse:
        stmt = (
            select(Document)
            .where(Document.patient_id == context.patient.id)
            .order_by(Document.created_at.desc())
        )
        document = (await context.session.execute(stmt)).scalars().first()
        if not document:
            return OrchestratorResponse(
                intent="documents",
                reply_text=(
                    "Ainda nao encontrei documentos vinculados ao seu cadastro. "
                    "Se preferir, nossa equipe pode confirmar manualmente."
                ),
            )
        return OrchestratorResponse(
            intent="documents",
            reply_text=f"Encontrei o documento mais recente: {document.file_name}. A equipe pode enviar o arquivo em seguida.",
        )
