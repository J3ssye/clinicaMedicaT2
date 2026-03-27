from sqlalchemy import select

from app.agents.base import AgentContext
from app.models.document import Document
from app.schemas.chat import OrchestratorResponse
from app.services.llm import GeminiService


class DocumentsAgent:
    def __init__(self) -> None:
        self.llm = GeminiService()

    async def handle(self, context: AgentContext) -> OrchestratorResponse:
        stmt = (
            select(Document)
            .where(Document.patient_id == context.patient.id)
            .order_by(Document.created_at.desc())
        )
        document = (await context.session.execute(stmt)).scalars().first()
        if not document:
            fallback = (
                "Ainda nao encontrei documentos vinculados ao seu cadastro. "
                "Se preferir, nossa equipe pode confirmar manualmente."
            )
            reply = self._draft_documents_reply(
                patient_message=context.incoming_text,
                guidance=(
                    "Nao ha documentos localizados para esse paciente no momento. "
                    "Informe isso e ofereca confirmacao manual da equipe."
                ),
                fallback=fallback,
            )
            return OrchestratorResponse(intent="documents", reply_text=reply)

        fallback = (
            f"Encontrei o documento mais recente: {document.file_name}. "
            "A equipe pode enviar o arquivo em seguida."
        )
        reply = self._draft_documents_reply(
            patient_message=context.incoming_text,
            guidance=(
                f"Documento mais recente encontrado: {document.file_name}. "
                "Informe isso e diga que a equipe pode enviar o arquivo em seguida."
            ),
            fallback=fallback,
        )
        return OrchestratorResponse(intent="documents", reply_text=reply)

    def _draft_documents_reply(self, *, patient_message: str, guidance: str, fallback: str) -> str:
        prompt = (
            "Voce e a atendente virtual da clinica para assuntos de documentos e exames. "
            "Responda em portugues do Brasil, em no maximo 2 frases, de forma clara e cordial. "
            f"\n\nContexto operacional:\n{guidance}"
        )
        reply = self.llm.draft_reply(prompt, patient_message)
        return reply or fallback
