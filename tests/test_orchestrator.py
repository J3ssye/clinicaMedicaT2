from app.agents.scheduling import SchedulingAgent
from app.orchestrator.graph import ChatOrchestrator


def test_classify_scheduling() -> None:
    assert ChatOrchestrator._classify_intent("Quero agendar consulta amanhã") == "scheduling"


def test_classify_triage() -> None:
    assert ChatOrchestrator._classify_intent("Estou com febre e dor") == "triage"


def test_classify_documents() -> None:
    assert ChatOrchestrator._classify_intent("Meu exame ficou pronto?") == "documents"


def test_detect_reschedule_action() -> None:
    assert SchedulingAgent()._detect_action("quero remarcar minha consulta") == "reschedule"


def test_detect_cancel_action() -> None:
    assert SchedulingAgent()._detect_action("preciso desmarcar") == "cancel"


def test_extract_relative_datetime_for_tomorrow() -> None:
    parsed = SchedulingAgent()._extract_datetime("Dr. Lucas amanhã às 16h")
    assert parsed is not None
    assert parsed.hour == 16


def test_confirmation_message_detection() -> None:
    assert SchedulingAgent()._is_confirmation_message("sim") is True


def test_classify_scheduling_when_only_date_and_time() -> None:
    assert ChatOrchestrator._classify_intent("dia 7/4 às 15h") == "scheduling"
