from app.models.message import Message
from app.schemas.chat import ChatMessage
from app.services.llm import GeminiService
from app.services.message_service import MessageService


def test_to_chat_messages_uses_roles() -> None:
    history = [
        Message(patient_id=1, direction="inbound", content="Oi"),
        Message(patient_id=1, direction="outbound", content="Como posso ajudar?"),
        Message(patient_id=1, direction="ignored", content="nao entra"),
    ]

    messages = MessageService.to_chat_messages(history)

    assert messages == [
        ChatMessage(role="user", content="Oi"),
        ChatMessage(role="assistant", content="Como posso ajudar?"),
    ]


def test_serialize_messages_preserves_conversation_order() -> None:
    serialized = GeminiService._serialize_messages(
        [
            ChatMessage(role="system", content="Seja objetiva."),
            ChatMessage(role="user", content="Quero marcar consulta."),
            ChatMessage(role="assistant", content="Qual data voce prefere?"),
        ]
    )

    assert "Sistema:\nSeja objetiva." in serialized
    assert "Paciente:\nQuero marcar consulta." in serialized
    assert "Assistente:\nQual data voce prefere?" in serialized
    assert serialized.endswith("Assistente:")
