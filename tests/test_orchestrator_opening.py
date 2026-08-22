from __future__ import annotations

from app.orchestrator.graph import ChatOrchestrator


def test_opening_greeting_requests_registration_fields() -> None:
    orchestrator = ChatOrchestrator()
    reply = orchestrator._apply_opening_greeting("", is_new_conversation=True)

    assert "nome completo" in reply
    assert "convênio" in reply
    assert "data de nascimento" in reply
