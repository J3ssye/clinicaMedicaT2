from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentContext
from app.agents.confirmation import ConfirmationAgent
from app.agents.documents import DocumentsAgent
from app.agents.faq import FAQAgent
from app.agents.feedback import FeedbackAgent
from app.agents.health_followup import HealthFollowupAgent
from app.agents.scheduling import SchedulingAgent
from app.agents.triage import TriageAgent
from app.core.config import get_settings
from app.integrations.llm import LLMService
from app.models.patient import Patient
from app.repositories.message_repository import MessageRepository
from app.schemas.chat import ChatMessage, OrchestratorResponse
from app.services.memory.redis_memory import RedisConversationMemory
from app.services.notification_service import NotificationService


settings = get_settings()


class ChatOrchestrator:
    def __init__(self, message_repository: MessageRepository | None = None) -> None:
        self.message_repository = message_repository or MessageRepository()
        self.faq_agent = FAQAgent()
        self.triage_agent = TriageAgent()
        self.scheduling_agent = SchedulingAgent()
        self.documents_agent = DocumentsAgent()
        self.feedback_agent = FeedbackAgent()
        self.confirmation_agent = ConfirmationAgent()
        self.health_followup_agent = HealthFollowupAgent()
        self.llm = LLMService()
        self.memory = RedisConversationMemory()
        self.notification = NotificationService()

    async def run(self, *, session: AsyncSession, patient: Patient, message: str, external_id: str | None = None, session_key: str | None = None) -> OrchestratorResponse:
        db_history = self.message_repository.to_chat_messages(
            await self.message_repository.get_conversation_history(session, patient_id=patient.id, limit=100)
        )
        memory_history = self.memory.get_history(session_key, limit=settings.llm_history_max_messages)
        history = self._merge_histories(db_history, memory_history, limit=settings.llm_history_max_messages)
        context = AgentContext(
            session=session,
            patient=patient,
            incoming_text=message,
            history=history,
            conversation_metadata={"external_id": external_id or "", "session_key": session_key or patient.phone or str(patient.id)},
        )
        effective_session_key = context.conversation_metadata.get("session_key")
        state = self.memory.get_state(effective_session_key)

        # Prioridade de roteamento:
        # 1. Urgência médica (triage) — sobrepõe qualquer fluxo ativo
        # 2. FAQ explícito — interrompe qualquer fluxo (paciente pergunta preço/endereço no meio de outra coisa)
        # 3. Fluxos ativos no Redis (d1_confirmation > post_consult_feedback > health_followup > scheduling)
        # 4. Classificação normal de intent
        intent = self._classify_intent(message)
        active_flow = state.get("active_flow")

        if intent not in {"triage", "faq"}:
            if active_flow == "d1_confirmation":
                intent = "d1_confirmation"
            elif active_flow == "post_consult_feedback":
                intent = "post_consult_feedback"
            elif active_flow == "health_followup":
                intent = "health_followup"
            elif (
                active_flow == "scheduling"
                or state.get("pending_action") in {"confirm_schedule", "confirm_attendance"}
            ):
                intent = "scheduling"

        # Estratégia A — cooldown pós-fluxo proativo:
        # Se não há intenção clara (fallback ou acknowledgement) e o paciente
        # está no período de cooldown, silencia sem gerar resposta.
        # ── Side-effects de notificação — disparam independente do agente ──────
        # Receita e imagem precisam notificar a equipe mesmo quando a mensagem
        # é roteada para FAQ ou outro agente (não apenas para fallback).
        # ── Side-effects de notificação ──────────────────────────────────────
        # Imagens de exame são encaminhadas (texto + foto) no HandleIncomingWebhookUseCase,
        # que é o único ponto com acesso ao media_url. Aqui tratamos apenas os casos
        # de texto puro: receita e solicitações de avaliação de exame/documento.
        _normalized_msg = self._normalize(message)
        if any(t in _normalized_msg for t in ("receita",)):
            try:
                await self.notification.notify_prescription_request(
                    patient_phone=patient.phone or "",
                    patient_name=patient.name,
                )
            except Exception:
                logger.exception("notify_prescription_request_failed")
        elif intent == "documents":
            # Texto pedindo avaliação de exame/documento sem imagem anexada.
            try:
                await self.notification.notify_escalation(
                    reason=f"Paciente solicitou avaliação de exame/documento: \"{message[:120]}\"",
                    patient_phone=patient.phone or "",
                    patient_name=patient.name,
                )
            except Exception:
                logger.exception("notify_escalation_documents_failed")

        if intent in {"fallback", "acknowledgement"} and self.memory.is_in_cooldown(effective_session_key):
            logger.info(
                "orchestrator | session=%s intent=%s → silenced (cooldown active)",
                effective_session_key, intent,
            )
            return OrchestratorResponse(intent="acknowledgement", reply_text="", silent=True)

        logger.info(
            "orchestrator | session=%s intent=%s active_flow=%s | text=%r",
            effective_session_key, intent, state.get("active_flow"), message[:60],
        )

        if intent == "scheduling":
            response = await self.scheduling_agent.handle(context)
        elif intent == "triage":
            response = await self.triage_agent.handle(context)
        elif intent == "documents":
            response = await self.documents_agent.handle(context)
        elif intent in {"feedback", "post_consult_feedback"}:
            response = await self.feedback_agent.handle(context)
        elif intent == "d1_confirmation":
            response = await self.confirmation_agent.handle(context)
        elif intent == "health_followup":
            response = await self.health_followup_agent.handle(context)
        elif intent == "faq":
            response = await self.faq_agent.handle(context)
        elif intent == "acknowledgement":
            # Estratégia B — mensagem confirmatória sem intent real: silencia.
            response = OrchestratorResponse(intent="acknowledgement", reply_text="", silent=True)
        else:
            response = await self.fallback(context)

        # ── Apresentação diária ──────────────────────────────────────────────
        # Prepend do greeting de abertura uma única vez por dia, para QUALQUER
        # intent que gere uma resposta visível.
        # Excluídos:
        #   • triage → emergência médica, sem espaço para saudações
        #   • fluxos proativos (d1_confirmation, post_consult_feedback,
        #     health_followup) → paciente responde no meio de uma conversa
        #     já iniciada pelo bot; greeting seria fora de contexto
        _no_greeting_intents = {
            "triage", "d1_confirmation", "post_consult_feedback", "health_followup",
        }
        if (
            response.reply_text
            and not response.silent
            and intent not in _no_greeting_intents
            and not self.memory.is_greeted_today(effective_session_key)
        ):
            response.reply_text = f"{self._build_greeting()}\n{response.reply_text}"
            self.memory.mark_greeted_today(effective_session_key)

        self.memory.append_history(effective_session_key, role="user", content=message)
        self.memory.append_history(effective_session_key, role="assistant", content=response.reply_text)
        return response

    async def fallback(self, context: AgentContext) -> OrchestratorResponse:
        normalized = self._normalize(context.incoming_text)
        if any(token in normalized for token in ("imagem", "foto", "exame em anexo")):
            return OrchestratorResponse(intent="fallback", reply_text="Encaminhei sua imagem para a equipe — entrarão em contato em breve 💚", llm_used=False)
        if any(token in normalized for token in ("receita", "receita online")):
            return OrchestratorResponse(intent="fallback", reply_text="Encaminhei sua solicitação de receita para a equipe — retornarão em breve 💚", llm_used=False)
        from pathlib import Path
        kb = Path(settings.faq_kb_path).read_text(encoding="utf-8") if Path(settings.faq_kb_path).exists() else ""
        prompt = (
            f"{settings.clinic_assistant_system_prompt}\n\n"
            f"BASE DE CONHECIMENTO:\n{kb}\n\n"
            "REGRAS:\n"
            "- Use a base de conhecimento para responder perguntas sobre a clínica, valores, endereço e procedimentos.\n"
            "- Se a pergunta pedir justificativa ou explicação, responda de forma completa e humana — nunca truncada.\n"
            "- Se a informação NÃO estiver disponível, diga que irá verificar com a equipe e retornará em breve. "
            "NUNCA invente datas, horários, disponibilidade ou qualquer dado clínico.\n"
            "- Se envolver sintomas graves, oriente procura imediata de atendimento.\n"
            "- Nunca cite Google Calendar, agenda externa ou sincronização externa.\n"
            "- Seja objetiva e acolhedora. Feche sempre com um próximo passo quando fizer sentido."
        )
        reply = self.llm.draft_reply(prompt, context.incoming_text, history=context.history)
        if reply:
            return OrchestratorResponse(intent="fallback", reply_text=reply, llm_used=True)
        return OrchestratorResponse(
            intent="fallback",
            reply_text="Posso te ajudar com agendamento, valores, local de atendimento e encaminhamento para a equipe quando necessário.",
            llm_used=False,
        )

    def _build_greeting(self) -> str:
        """Apresentação diária da Malu — prepend ao primeiro response visível do dia."""
        return (
            "Olá! Seja muito bem-vindo(a) à *FIDEM* 💚\n"
            "Sou a Malu, sua assistente virtual.\n\n"
            "Nossos médicos atendem às *terças-feiras*:\n"
            "• *Dr. Lucas Da Costa Cirilo*\n"
            "• *Dra. Vitória Cunha*\n"
        )

    @classmethod
    def _classify_intent(cls, text: str) -> str:
        normalized = cls._normalize(text)

        # 1. Urgências médicas — maior prioridade absoluta
        if cls._contains_any(normalized, ("dor no peito", "falta de ar", "desmaio", "convuls", "sangramento intenso")):
            return "triage"

        # 2. Perguntas sobre preço/endereço/local/lembrete/explicação têm prioridade absoluta
        #    sobre "scheduling". Evita que "qual o valor da consulta", "por que precisa do CPF",
        #    "qual a localização da consulta" etc. sejam classificados como scheduling.
        if cls._contains_any(normalized, (
            # Preço / valores — nunca são agendamento
            "valor", "quanto", "preco", "custo", "custa", "pagar", "pagamento",
            # Formas de pagamento
            "forma de pagamento", "formas de pagamento",
            "aceita pix", "aceita cartao", "aceita cartão", "aceita dinheiro",
            "pix", "cartao", "cartão",
            # Especialidade dos médicos — pergunta informativa, nunca agendamento
            "especialidade", "especialidades",
            "area de atuacao", "área de atuação", "area de atuação",
            "que tipo de medico", "que tipo de médico",
            "qual medico", "qual médico", "quais medicos", "quais médicos",
            "quem sao os medicos", "quem são os médicos",
            # Explicações / justificativas — sempre usa LLM via FAQ
            "por que", "porque", "pra que", "para que", "me explica", "me explique",
            "como funciona", "como assim", "nao entendi", "não entendi",
            "me diz", "me fala", "me fale", "me conta", "o que e isso", "o que é isso",
            # Endereço / localização / lembretes
            "lembrar", "lembrete", "vai me avisar",
            "localiza", "localizacao",
            "onde fica", "onde e", "como chegar", "como ir", "que bairro", "que rua",
            "local da", "qual o local", "qual local",
            "qual o ende", "qual ende", "qual o endere", "manda o ende", "me manda o local",
            "fica onde",
        )):
            return "faq"

        # 2b. Convênio / plano de saúde — informativo, a menos que haja verbo de ação
        #     explícito ("quero agendar com convênio" → scheduling; "atendem convênio?" → faq).
        _insurance_tokens = ("convenio", "convênio", "plano de saude", "plano de saúde", "plano medico", "plano médico")
        _sched_action_verbs = ("agendar", "marcar", "quero", "gostaria", "consulta", "encaixe")
        if cls._contains_any(normalized, _insurance_tokens) and not cls._contains_any(normalized, _sched_action_verbs):
            return "faq"

        # 3. Mensagem interrogativa:
        #    a) Perguntas sobre disponibilidade/agenda → scheduling (consulta banco real)
        #    b) Demais perguntas → FAQ / LLM
        if "?" in normalized:
            if cls._contains_any(normalized, (
                # Ações de agendamento explícitas → scheduling
                "agendar", "remarcar", "cancelar", "marcar", "encaixe",
                # Perguntas de disponibilidade → scheduling para consultar banco
                "atende", "vaga", "horario", "horário",
                "disponivel", "disponível",
                "segunda", "quarta", "quinta", "sexta",
                "quando", "que dia", "quais dias", "que dias",
                "essa semana", "semana que vem",
            )):
                return "scheduling"
            return "faq"

        # 4. Intenção de agendamento
        if cls._contains_any(normalized, (
            "agendar", "consulta", "remarcar", "cancelar", "encaixe",
            "horario", "horário", "vaga", "amanha", "amanhã", "confirmar", "cpf",
            # Pedidos de próximo disponível sem especificar data/hora
            "disponivel", "disponível", "proxima", "próxima", "proximo", "próximo",
            "semana que vem", "semana passada", "mais perto", "primeira vaga",
            # Referências nominais específicas aos médicos → intenção de agendar
            # Removidos: "doutor", "doutora", "dr.", "dra.", "medico", "médico",
            # "medica", "médica" — são substantivos genéricos que capturam perguntas
            # informativas ("qual especialidade dos doutores?") sem intenção de agenda.
            # Quem quer agendar usa o nome ("Dr. Lucas", "Dra. Vitória") ou verbos de ação
            # ("agendar", "marcar", "consulta") que já estão na lista acima.
            "dr lucas", "dra vitoria", "dra vitória",
            # Dias da semana — sempre roteiam para SchedulingAgent, que explica a regra
            # das terças (via _check_non_tuesday_intent) e escalona em caso de urgência.
            # Não restringir a "?" porque "quero para quinta" também é agendamento.
            "segunda", "segunda-feira",
            "terca", "terça", "terca-feira", "terça-feira",
            "quarta", "quarta-feira",
            "quinta", "quinta-feira",
            "sexta", "sexta-feira",
            "sabado", "sábado",
            "domingo",
        )):
            return "scheduling"

        if cls._contains_any(normalized, ("documento", "exame", "resultado", "atestado", "pedido", "laudo", "arquivo")):
            return "documents"
        if cls._contains_any(normalized, ("retorno", "voltar", "melhorei", "nao melhorei", "não melhorei", "feedback", "nota")):
            return "feedback"
        if cls._contains_any(normalized, ("funcionamento", "endereco", "endereço", "onde fica", "convenio", "convênio", "plano", "jejum", "preparo", "valor", "oi", "ola", "olá", "bom dia", "boa tarde", "boa noite")):
            return "faq"

        # Estratégia B — acknowledgement: mensagem curta e confirmatória sem intent real.
        # Checado por último para não sobrepor nenhum intent legítimo.
        _ack_tokens = (
            "obrigad", "valeu", "vlw", "perfeito", "combinado", "show",
            "certo", "entendi", "entendido", "ta bom", "tudo bem", "tudo certo",
            "ok", "legal", "blz", "beleza",
        )
        _ack_emoji = ("👍", "✅", "🙏", "😊", "☑️", "🤝")
        has_ack_text = cls._contains_any(normalized, _ack_tokens)
        has_ack_emoji = any(e in text for e in _ack_emoji)
        if (has_ack_text or has_ack_emoji) and len(normalized.split()) <= 6:
            return "acknowledgement"

        return "fallback"

    @staticmethod
    def _normalize(text: str) -> str:
        normalized = unicodedata.normalize("NFKD", text.lower().strip())
        text_no_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        replacements = {
            # Pronomes / advérbios comuns no WhatsApp brasileiro
            " vc ":  " voce ",
            " tb ":  " tambem ",
            " tbm ": " tambem ",
            " vcs ": " voces ",
            " pra ": " para ",
            # Perguntas — mapeadas para as formas que os tokens de FAQ reconhecem
            " pq ":  " porque ",
            " xq ":  " porque ",
            " oq ":  " o que ",
            " oque ": " o que ",
            " qdo ": " quando ",
            " qnd ": " quando ",
            " qto ": " quanto ",
            " qts ": " quantos ",
            " mto ": " muito ",
            " mts ": " muitos ",
            " qr ":  " quer ",
            " q ":   " que ",
            " nd ":  " nada ",
            " mt ":  " muito ",
            # Reações
            " kkk":  " risada",
            " haha": " risada",
            " rs ":  " risada ",
            # Erros de digitação conhecidos
            "acomrpanbar": "acompanhar",
        }
        padded = f" {text_no_accents} "
        for old, new in replacements.items():
            padded = padded.replace(old, f" {new} ")
        return re.sub(r"\s+", " ", padded).strip()

    @staticmethod
    def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
        return any(token in text for token in tokens)

    @staticmethod
    def _merge_histories(db_history: list[ChatMessage], memory_history: list[ChatMessage], *, limit: int) -> list[ChatMessage]:
        merged: list[ChatMessage] = []
        seen: set[tuple[str, str]] = set()
        for message in [*db_history, *memory_history]:
            key = (message.role, message.content.strip())
            if not message.content.strip() or key in seen:
                continue
            seen.add(key)
            merged.append(message)
        return merged[-limit:]
