from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentContext
from app.agents.documents import DocumentsAgent
from app.agents.faq import FAQAgent
from app.agents.feedback import FeedbackAgent
from app.agents.scheduling import SchedulingAgent
from app.agents.triage import TriageAgent
from app.models.patient import Patient
from app.orchestrator.state import ChatState
from app.schemas.chat import OrchestratorResponse
from app.services.llm import GeminiService


class ChatOrchestrator:
    def __init__(self) -> None:
        self.llm = GeminiService()
        self.faq_agent = FAQAgent()
        self.triage_agent = TriageAgent()
        self.scheduling_agent = SchedulingAgent()
        self.documents_agent = DocumentsAgent()
        self.feedback_agent = FeedbackAgent()
        graph = StateGraph(ChatState)
        graph.add_node("classify", self._classify)
        graph.add_node("faq", self._faq)
        graph.add_node("triage", self._triage)
        graph.add_node("scheduling", self._scheduling)
        graph.add_node("documents", self._documents)
        graph.add_node("feedback", self._feedback)
        graph.add_node("fallback", self._fallback)
        graph.add_edge(START, "classify")
        graph.add_conditional_edges(
            "classify",
            self._route,
            {
                "faq": "faq",
                "triage": "triage",
                "scheduling": "scheduling",
                "documents": "documents",
                "feedback": "feedback",
                "fallback": "fallback",
            },
        )
        for node_name in ["faq", "triage", "scheduling", "documents", "feedback", "fallback"]:
            graph.add_edge(node_name, END)
        self.graph = graph.compile()

    async def run(
        self,
        *,
        session: AsyncSession,
        patient: Patient,
        message: str,
    ) -> OrchestratorResponse:
        state: ChatState = {"patient_id": patient.id, "message": message}
        result = await self.graph.ainvoke(
            state,
            config={"configurable": {"session": session, "patient": patient}},
        )
        return OrchestratorResponse(
            intent=result["intent"],
            reply_text=result["reply_text"],
            escalate_to_human=result.get("escalate_to_human", False),
        )

    async def _classify(self, state: ChatState) -> ChatState:
        intent = self.llm.classify_intent(state["message"])
        # Mensagens muito curtas e ambíguas funcionam melhor como FAQ guiada do que como fallback seco.
        if intent == "fallback":
            intent = "faq"
        return {"intent": intent}

    def _route(self, state: ChatState) -> str:
        return state["intent"]

    async def _faq(self, state: ChatState, config) -> ChatState:
        response = await self.faq_agent.handle(self._context_from_config(state, config))
        return response.model_dump()

    async def _triage(self, state: ChatState, config) -> ChatState:
        response = await self.triage_agent.handle(self._context_from_config(state, config))
        return response.model_dump()

    async def _scheduling(self, state: ChatState, config) -> ChatState:
        response = await self.scheduling_agent.handle(self._context_from_config(state, config))
        return response.model_dump()

    async def _documents(self, state: ChatState, config) -> ChatState:
        response = await self.documents_agent.handle(self._context_from_config(state, config))
        return response.model_dump()

    async def _feedback(self, state: ChatState, config) -> ChatState:
        response = await self.feedback_agent.handle(self._context_from_config(state, config))
        return response.model_dump()

    async def _fallback(self, state: ChatState) -> ChatState:
        reply = self.llm.draft_fallback_reply(state["message"])
        if reply:
            return {
                "intent": "fallback",
                "reply_text": reply,
                "escalate_to_human": False,
            }
        return {
            "intent": "fallback",
            "reply_text": (
                "Posso te ajudar com agendamento, horarios, convenios, sintomas, documentos e retorno da equipe. "
                "Se quiser, me diga em uma frase o que voce precisa e eu sigo com o atendimento."
            ),
            "escalate_to_human": False,
        }

    @staticmethod
    def _context_from_config(state: ChatState, config) -> AgentContext:
        configurable = config["configurable"]
        return AgentContext(
            session=configurable["session"],
            patient=configurable["patient"],
            incoming_text=state["message"],
        )
