from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.chat import ChatRequest, ChatResponse
from app.use_cases.handle_chat_message import HandleChatMessageUseCase


router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ChatResponse:
    use_case = HandleChatMessageUseCase(session=session)
    try:
        result = await use_case.execute(
            session_id=payload.session_id,
            patient_name=payload.patient_name,
            message=payload.message,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="assistant_unavailable") from exc

    return ChatResponse(
        session_id=payload.session_id,
        reply_text=result.reply_text,
        messages=result.messages,
        intent=result.intent,
        escalate_to_human=result.escalate_to_human,
        llm_used=result.llm_used,
    )
