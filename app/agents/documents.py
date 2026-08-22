from __future__ import annotations

from app.agents.base import AgentContext
from app.repositories.document_repository import DocumentRepository
from app.schemas.chat import OrchestratorResponse


class DocumentsAgent:
    def __init__(self, repository: DocumentRepository | None = None) -> None:
        self.repository = repository or DocumentRepository()

    async def handle(self, context: AgentContext) -> OrchestratorResponse:
        document = await self.repository.get_latest_for_patient(context.session, context.patient.id)
        if document:
            return OrchestratorResponse(
                intent="documents",
                reply_text=(
                    f"Encontrei um documento recente no seu cadastro: {document.file_name}. "
                    "Posso deixar a equipe avisada para seguir com o envio."
                ),
                llm_used=False,
            )
        return OrchestratorResponse(
            intent="documents",
            reply_text=(
                "No momento não localizei documento vinculado ao seu cadastro. "
                "Vou deixar a equipe avisada para conferir."
            ),
            llm_used=False,
        )
