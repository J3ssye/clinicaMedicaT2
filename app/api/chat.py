from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.orchestrator.graph import ChatOrchestrator
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.patient_service import PatientService


router = APIRouter()
orchestrator = ChatOrchestrator()


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ChatResponse:
    patient = await PatientService.get_or_create_by_phone(
        session,
        phone=payload.session_id,
        name=payload.patient_name,
    )
    try:
        result = await orchestrator.run(
            session=session,
            patient=patient,
            message=payload.message,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="assistant_unavailable") from exc
    return ChatResponse(
        session_id=payload.session_id,
        reply_text=result.reply_text,
        messages=result.messages,
    )
