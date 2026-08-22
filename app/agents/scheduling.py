from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import logging
import re
import unicodedata
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

from app.agents.base import AgentContext
from app.core.config import get_settings
from app.models.appointment import Appointment
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.patient_repository import PatientRepository
from app.schemas.chat import ChatMessage, OrchestratorResponse
from app.services.memory.redis_memory import RedisConversationMemory
from app.services.notification_service import NotificationService


settings = get_settings()
MONTHS = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}
YES_TOKENS = {"sim", "pode", "ok", "confirmo", "confirmar", "isso", "isso mesmo", "perfeito", "fechado", "combinado"}


@dataclass(slots=True)
class DoctorProfile:
    canonical_name: str
    fee_text: str
    accepts_insurance: bool
    duration_minutes: int


DR_LUCAS = DoctorProfile("Dr. Lucas Da Costa Cirilo", "R$ 600,00", False, 60)
DRA_VITORIA = DoctorProfile("Dra. Vitória Cunha", "R$ 600,00", False, 30)


class SchedulingAgent:
    # Idade mínima para atendimento direto pelos médicos
    MIN_AGE_YEARS: int = 8

    def __init__(
        self,
        repository: AppointmentRepository | None = None,
        patient_repository: PatientRepository | None = None,
        memory: RedisConversationMemory | None = None,
        notification_service: NotificationService | None = None,
    ) -> None:
        self.repository = repository or AppointmentRepository()
        self.patient_repository = patient_repository or PatientRepository()
        self.memory = memory or RedisConversationMemory()
        self.notification = notification_service or NotificationService()
        self._timezone = ZoneInfo(settings.clinic_timezone)

    async def handle(self, context: AgentContext) -> OrchestratorResponse:
        text = context.incoming_text
        action = self._detect_action(text)
        if action == "cancel":
            return await self._handle_cancel(context)
        if action == "reschedule":
            return await self._handle_reschedule(context)
        if action == "check":
            return await self._handle_check(context)
        if action == "availability_inquiry":
            return await self._handle_availability_inquiry(context)
        return await self._handle_schedule(context)

    async def _handle_schedule(self, context: AgentContext) -> OrchestratorResponse:
        session_key = self._session_key(context)
        state = self.memory.get_state(session_key)
        normalized = self._normalize(context.incoming_text)

        # ------------------------------------------------------------------
        # Coleta do nome do beneficiário para agendamentos de terceiros
        # Quando pending_action == "collect_beneficiary_name", a mensagem
        # atual É o nome — salva e continua o fluxo normal.
        # ------------------------------------------------------------------
        if state.get("pending_action") == "collect_beneficiary_name":
            incoming = context.incoming_text.strip()
            incoming_norm = self._normalize(incoming)
            # Mensagens que parecem intenção de agendamento (dias da semana, médicos, verbos)
            # NÃO são tratadas como nome — descartamos o pending_action e continuamos o fluxo.
            _scheduling_words = (
                "segunda", "terca", "terca-feira", "quarta", "quarta-feira",
                "quinta", "quinta-feira", "sexta", "sexta-feira", "sabado", "domingo",
                "amanha", "amanha", "hoje",
                "agendar", "marcar", "horario", "disponivel",
                "dr lucas", "dra vitoria", "proximo",
            )
            is_scheduling_msg = any(t in incoming_norm for t in _scheduling_words)

            if is_scheduling_msg:
                # O paciente mudou de assunto para agendamento — descarta o pending
                # e deixa o fluxo normal tratar a mensagem abaixo.
                self.memory.clear_state_keys(session_key, "pending_action")
                state = self.memory.get_state(session_key)
                # Não retorna aqui — cai no fluxo normal de _handle_schedule
            elif not incoming or self._extract_cpf(incoming) or self._extract_birth_date(incoming):
                # Parece CPF ou data de nascimento — pede o nome novamente
                return OrchestratorResponse(
                    intent="scheduling",
                    reply_text="Por favor, me informe o nome completo do(a) paciente que será atendido(a).",
                    llm_used=False,
                )
            else:
                # Salva o nome e remove o pending_action para retomar o fluxo normal
                name_title = " ".join(w.capitalize() for w in incoming.split())
                self.memory.merge_state(session_key, {
                    "beneficiary_name": name_title,
                    "pending_action": None,
                })
                return OrchestratorResponse(
                    intent="scheduling",
                    reply_text=(
                        f"Perfeito! Agendamento para *{name_title}* 💚\n"
                        "Agora me diga o médico e a data desejada. "
                        "Exemplo: *'Dr. Lucas próximo disponível'* ou *'Dra. Vitória 14/05 às 15h'*."
                    ),
                    llm_used=False,
                )

        if state.get("pending_action") == "confirm_schedule":
            pending_doctor = self._extract_doctor(state.get("pending_doctor_name") or "")
            pending_scheduled_at = self._parse_iso_datetime(state.get("pending_scheduled_at"))
            if self._is_confirmation_message(normalized):
                # Paciente confirmou com "sim", "ok", "confirmo" etc.
                if pending_doctor and pending_scheduled_at:
                    return await self._finalize_schedule(context=context, doctor=pending_doctor, scheduled_at=pending_scheduled_at, session_key=session_key)
            else:
                selected_at = self._resolve_datetime(context.incoming_text)
                if selected_at and pending_doctor:
                    if self._is_allowed_slot(pending_doctor, selected_at, state.get("insurance_kind")):
                        # Paciente selecionou um slot válido da lista oferecida
                        self.memory.merge_state(session_key, {"pending_scheduled_at": selected_at.isoformat()})
                        return await self._finalize_schedule(
                            context=context, doctor=pending_doctor, scheduled_at=selected_at, session_key=session_key
                        )
                    else:
                        # Paciente pediu uma data/horário fora da agenda do médico.
                        # NÃO usa o slot pendente silenciosamente — informa e pergunta.
                        day_names = {0: "segunda", 1: "terça", 2: "quarta", 3: "quinta", 4: "sexta", 5: "sábado", 6: "domingo"}
                        requested_day = day_names.get(selected_at.weekday(), "")
                        pending_label = pending_scheduled_at.strftime("%d/%m às %H:%M") if pending_scheduled_at else None
                        if pending_label:
                            return OrchestratorResponse(
                                intent="scheduling",
                                reply_text=(
                                    f"Essa data ({selected_at.strftime('%d/%m')} é {requested_day}) não está disponível — "
                                    f"o {pending_doctor.canonical_name} atende somente às *terças-feiras*, das 13h40 às 18h.\n"
                                    f"Posso confirmar o horário que separei (*{pending_label}*) ou prefere que eu busque outras opções?"
                                ),
                                llm_used=False,
                            )
                        # Sem slot pendente: busca alternativas próximas à data pedida
                        suggestions = await self._build_suggestions(context=context, doctor=pending_doctor, requested_at=selected_at, insurance_kind=state.get("insurance_kind"))
                        if suggestions:
                            chosen = suggestions[0]
                            self.memory.merge_state(session_key, {"pending_scheduled_at": chosen.isoformat()})
                            return OrchestratorResponse(intent="scheduling", reply_text=self._compose_offer(pending_doctor, suggestions), llm_used=False)
                        return OrchestratorResponse(
                            intent="scheduling",
                            reply_text="Não encontrei horários disponíveis próximos a essa data. Quer que eu verifique outro período?",
                            llm_used=False,
                        )
                elif selected_at is None:
                    # Sem datetime detectado — paciente está fornecendo CPF/data de nascimento
                    if pending_doctor and pending_scheduled_at:
                        return await self._finalize_schedule(
                            context=context, doctor=pending_doctor, scheduled_at=pending_scheduled_at, session_key=session_key
                        )

        # Intercept: paciente pediu um dia que não é terça-feira.
        # Só roda quando não há um confirm_schedule pendente (o slot já foi oferecido).
        if not state.get("pending_action"):
            non_tuesday_response = await self._check_non_tuesday_intent(context, session_key, state)
            if non_tuesday_response is not None:
                return non_tuesday_response

        extracted = self._extract_profile_updates(context.incoming_text)
        if extracted:
            self.memory.merge_state(session_key, extracted)
            if extracted.get("patient_name") and not context.patient.name:
                context.patient.name = extracted["patient_name"]

        # Detecta agendamento para terceiro e persiste no estado para que
        # _finalize_schedule possa usar os campos beneficiary_* sem sobrescrever
        # o perfil (CPF, data de nascimento) do titular do número.
        if self._detect_third_party(normalized):
            self.memory.merge_state(session_key, {"is_third_party": "true"})

            # Se ainda não temos o nome do beneficiário, tentamos extrair da
            # mensagem atual (ex: "para minha filha Ana Paula" → "Ana Paula").
            # Se não encontrar inline, paramos o fluxo e pedimos explicitamente.
            if not state.get("beneficiary_name"):
                inline_name = self._extract_beneficiary_name_inline(context.incoming_text)
                if inline_name:
                    self.memory.merge_state(session_key, {"beneficiary_name": inline_name})
                    # Informa e pede médico/data
                    return OrchestratorResponse(
                        intent="scheduling",
                        reply_text=(
                            f"Claro, vou agendar para *{inline_name}* 💚\n"
                            "Me diga o médico e a data desejada. "
                            "Exemplo: *'Dr. Lucas próximo disponível'* ou *'Dra. Vitória 14/05 às 15h'*."
                        ),
                        llm_used=False,
                    )
                else:
                    # Pede o nome antes de continuar
                    relation = self._extract_relation(context.incoming_text)
                    self.memory.merge_state(session_key, {"pending_action": "collect_beneficiary_name"})
                    return OrchestratorResponse(
                        intent="scheduling",
                        reply_text=f"Claro! Qual é o nome completo {'da ' + relation if relation else 'do(a) paciente'} que será atendido(a)?",
                        llm_used=False,
                    )

        doctor = self._extract_doctor(context.incoming_text) or self._extract_doctor(state.get("pending_doctor_name") or "")
        # Só usa o scheduled_at do estado se o paciente não está pedindo "próximo disponível"
        # para evitar reaproveitar uma data antiga de uma mensagem diferente
        if self._is_next_available_request(normalized):
            scheduled_at = None
        else:
            scheduled_at = self._resolve_datetime(context.incoming_text) or self._parse_iso_datetime(state.get("pending_scheduled_at"))
        insurance_kind = self._extract_insurance_kind(normalized)
        if insurance_kind:
            self.memory.merge_state(session_key, {"insurance_kind": insurance_kind})

        # Persiste o médico no estado para que os próximos turnos continuem no fluxo de agenda
        # sem depender de nova classificação de intent.
        if doctor:
            self.memory.merge_state(session_key, {
                "active_flow": "scheduling",
                "pending_doctor_name": doctor.canonical_name,
            })

        if doctor and scheduled_at:
            suggestions = await self._build_suggestions(context=context, doctor=doctor, requested_at=scheduled_at, insurance_kind=insurance_kind or state.get("insurance_kind"))
            if not suggestions:
                try:
                    await self.notification.notify_escalation(
                        reason=f"Sem horários disponíveis para {doctor.canonical_name} em 35 dias. Paciente aguarda contato da equipe.",
                        patient_phone=context.patient.phone or "",
                        patient_name=context.patient.name,
                    )
                except Exception:
                    logger.exception("notify_escalation_failed")
                return OrchestratorResponse(intent="scheduling", reply_text="Consultei a agenda e não encontrei horários disponíveis para esse período. Encaminhei para a equipe — entrarão em contato em breve 💚", llm_used=False)
            chosen = suggestions[0]
            self.memory.merge_state(session_key, {
                "active_flow": "scheduling",
                "pending_action": "confirm_schedule",
                "pending_doctor_name": doctor.canonical_name,
                "pending_scheduled_at": chosen.isoformat(),
            })
            return OrchestratorResponse(
                intent="scheduling",
                reply_text=self._compose_offer(doctor, suggestions),
                llm_used=False,
            )

        # Paciente quer o próximo disponível sem especificar data/hora (ou diz "amanhã" sem horário).
        # Usa hint de data se houver (ex: "amanhã" → busca a partir de amanhã 13:40).
        # NUNCA inventa horários — consulta o banco real.
        if doctor and self._is_next_available_request(normalized):
            hint = self._resolve_start_hint(context.incoming_text)
            now_naive = datetime.now(self._timezone).replace(tzinfo=None)
            search_from = hint if hint else now_naive
            suggestions = await self._build_suggestions(
                context=context,
                doctor=doctor,
                requested_at=search_from,
                insurance_kind=insurance_kind or state.get("insurance_kind"),
            )
            if suggestions:
                chosen = suggestions[0]
                self.memory.merge_state(session_key, {
                    "active_flow": "scheduling",
                    "pending_action": "confirm_schedule",
                    "pending_doctor_name": doctor.canonical_name,
                    "pending_scheduled_at": chosen.isoformat(),
                })
                return OrchestratorResponse(
                    intent="scheduling",
                    reply_text=self._compose_offer(doctor, suggestions),
                    llm_used=False,
                )
            # Banco consultado e sem disponibilidade — escalona para equipe, não inventa
            try:
                await self.notification.notify_escalation(
                    reason=f"Sem horários disponíveis para {doctor.canonical_name} nos próximos 35 dias. Paciente solicitou próximo disponível.",
                    patient_phone=context.patient.phone or "",
                    patient_name=context.patient.name,
                )
            except Exception:
                logger.exception("notify_escalation_failed")
            return OrchestratorResponse(
                intent="scheduling",
                reply_text="Consultei a agenda e não encontrei horários disponíveis nos próximos dias. Encaminhei para a equipe confirmar uma data — entrarão em contato em breve 💚",
                llm_used=False,
            )

        if doctor and not scheduled_at:
            return OrchestratorResponse(
                intent="scheduling",
                reply_text=(
                    f"Perfeito. Qual dia e horário você prefere com {doctor.canonical_name}? "
                    "Posso te sugerir as melhores opções disponíveis 💚\n"
                    "Você pode me dizer, por exemplo: *'amanhã'*, *'14/04 às 15h'*, *'terça às 16h'* ou simplesmente *'próximo disponível'*."
                ),
                llm_used=False,
            )
        if scheduled_at and not doctor:
            return OrchestratorResponse(intent="scheduling", reply_text="Consigo seguir nessa data. Você prefere com o Dr. Lucas ou com a Dra. Vitória?", llm_used=False)
        return OrchestratorResponse(
            intent="scheduling",
            reply_text=(
                "Para eu organizar seu agendamento, me diga o médico e a data desejada. "
                "Exemplos: *'Dr. Lucas amanhã'*, *'Dra. Vitória 14/04 às 15h'* ou *'próximo disponível com o Dr. Lucas'*."
            ),
            llm_used=False,
        )

    async def _finalize_schedule(self, *, context: AgentContext, doctor: DoctorProfile, scheduled_at: datetime, session_key: str) -> OrchestratorResponse:
        state = self.memory.get_state(session_key)
        is_third_party = state.get("is_third_party") == "true"

        # Extrai dados da mensagem atual; fallback para o que já estava no estado.
        # Se é agendamento para terceiro, as chaves no estado ficam em "beneficiary_cpf"
        # e "beneficiary_birth_date" para não misturar com os dados do titular.
        if is_third_party:
            cpf = self._extract_cpf(context.incoming_text) or state.get("beneficiary_cpf")
            birth_date = (
                self._extract_birth_date(context.incoming_text)
                or self._parse_iso_date(state.get("beneficiary_birth_date"))
            )
        else:
            cpf = self._extract_cpf(context.incoming_text) or state.get("cpf")
            birth_date = (
                self._extract_birth_date(context.incoming_text)
                or self._parse_iso_date(state.get("birth_date"))
            )

        patient_name = (
            self._extract_name(context.incoming_text)
            or context.patient.name
            or state.get("patient_name")
        )
        beneficiary_name = state.get("beneficiary_name") or (patient_name if is_third_party else None)

        # Persiste no estado para que próximas mensagens no mesmo turno não precisem
        # reenviar tudo (ex: paciente envia CPF e birth_date em mensagens separadas).
        updates: dict[str, str] = {}
        if cpf:
            updates["beneficiary_cpf" if is_third_party else "cpf"] = cpf
        if birth_date:
            updates["beneficiary_birth_date" if is_third_party else "birth_date"] = birth_date.isoformat()
        if patient_name:
            updates["patient_name"] = patient_name
        if updates:
            self.memory.merge_state(session_key, updates)

        missing = []
        if not cpf:
            missing.append("CPF")
        if not birth_date:
            missing.append("data de nascimento")
        if missing:
            instructions: list[str] = []
            if "CPF" in missing:
                label = "CPF do paciente (somente os números, ex: 70895463212)" if is_third_party else "CPF (somente os números, ex: 70895463212)"
                instructions.append(label)
            if "data de nascimento" in missing:
                label = "data de nascimento do paciente (ex: 08/08/2003)" if is_third_party else "data de nascimento (ex: 08/08/2003)"
                instructions.append(label)
            instructions_str = " e ".join(instructions)
            subject = "para finalizar o agendamento do paciente, precisamos do" if is_third_party else "para eu conseguir deixar seu agendamento certinho, preciso do seu"
            return OrchestratorResponse(
                intent="scheduling",
                reply_text=f"Para {'finalizar o agendamento, preciso do ' + instructions_str + ' do paciente' if is_third_party else 'eu conseguir deixar seu agendamento certinho, preciso do seu ' + instructions_str}.",
                llm_used=False,
            )

        # --- Regra de idade mínima ---
        age_years = self._calculate_age(birth_date)
        if age_years < self.MIN_AGE_YEARS:
            try:
                await self.notification.notify_minor_patient(
                    patient_phone=context.patient.phone or "",
                    patient_name=beneficiary_name or patient_name,
                    birth_date_str=birth_date.strftime("%d/%m/%Y"),
                    age_years=int(age_years),
                )
            except Exception:
                pass
            self.memory.clear_state_keys(
                session_key, "active_flow", "pending_action", "pending_doctor_name",
                "pending_scheduled_at", "is_third_party", "beneficiary_name",
                "beneficiary_cpf", "beneficiary_birth_date", "non_tuesday_warned",
            )
            return OrchestratorResponse(
                intent="scheduling",
                reply_text=(
                    "Obrigada por compartilhar as informações 💚 "
                    "Para crianças menores de 8 anos, nossos médicos não realizam atendimento direto nesse momento. "
                    "Encaminhei o caso para a equipe, que entrará em contato para orientar da melhor forma possível."
                ),
                llm_used=False,
            )

        # --- Menor de idade (8-17 anos) — registra observação ---
        if age_years < 18:
            notes_extra = f" | Paciente menor de idade ({int(age_years)} anos)"
        else:
            notes_extra = ""

        try:
            # Somente atualiza o perfil do TITULAR quando o agendamento é para si mesmo.
            # Para terceiros, preservamos o perfil do dono do número intacto.
            if not is_third_party:
                await self.patient_repository.update_profile(
                    context.session,
                    patient=context.patient,
                    name=patient_name,
                    cpf=cpf,
                    birth_date=birth_date,
                )

            notes_prefix = f"Agendado via chat para {beneficiary_name} (CPF: {cpf})" if is_third_party else f"Agendado via chat | CPF: {cpf}"
            await self.repository.create(
                session=context.session,
                patient_id=context.patient.id,
                scheduled_at=scheduled_at,
                doctor_name=doctor.canonical_name,
                specialty="Clínica médica",
                notes=f"{notes_prefix}{notes_extra}",
                is_third_party=is_third_party,
                beneficiary_name=beneficiary_name if is_third_party else None,
                beneficiary_cpf=cpf if is_third_party else None,
                beneficiary_birth_date=birth_date if is_third_party else None,
            )
            # Notifica secretária + médico correspondente sobre o novo agendamento.
            # Envolvido em try/except para nunca bloquear o fluxo se o WAHA estiver instável.
            try:
                await self.notification.notify_new_appointment(
                    patient_name=patient_name,
                    patient_phone=context.patient.phone or "",
                    doctor_name=doctor.canonical_name,
                    scheduled_at=scheduled_at,
                    cpf=cpf or "",
                    notes=notes_extra or None,
                    beneficiary_name=beneficiary_name if is_third_party else None,
                )
            except Exception:
                logger.exception("notify_new_appointment_failed")
            self.memory.clear_state_keys(
                session_key, "active_flow", "pending_action", "pending_doctor_name",
                "pending_scheduled_at", "is_third_party", "beneficiary_name",
                "beneficiary_cpf", "beneficiary_birth_date", "non_tuesday_warned",
            )
            display = beneficiary_name or patient_name or "paciente"
            suffix = f" para {display}" if is_third_party else ""
            return OrchestratorResponse(
                intent="scheduling",
                reply_text=(
                    f"Tudo certo 💚 Consulta confirmada{suffix} para "
                    f"{scheduled_at.strftime('%d/%m/%Y às %H:%M')} com {doctor.canonical_name}. "
                    f"Você receberá um lembrete no dia anterior. Se precisar de algo mais, é só me chamar."
                ),
                llm_used=False,
            )
        except Exception:
            return OrchestratorResponse(intent="scheduling", reply_text="Entraremos em contato em breve para formalizar a consulta.", llm_used=False)

    async def _handle_check(self, context: AgentContext) -> OrchestratorResponse:
        cpf = self._extract_cpf(context.incoming_text) or context.patient.cpf

        if not cpf:
            # Fallback: busca diretamente pelo patient_id quando o titular ainda não tem CPF cadastrado.
            appointment = await self.repository.get_next_by_patient_id(context.session, patient_id=context.patient.id)
            if appointment is None:
                return OrchestratorResponse(
                    intent="scheduling",
                    reply_text=(
                        "Não encontrei consulta futura no seu cadastro. "
                        "Se quiser, me envie seu CPF para eu buscar com mais precisão."
                    ),
                    llm_used=False,
                )
            return OrchestratorResponse(
                intent="scheduling",
                reply_text=self._format_check_reply(appointment),
                llm_used=False,
            )

        appointment = await self.repository.get_next_by_patient_cpf(context.session, cpf=cpf)
        if appointment is None:
            return OrchestratorResponse(
                intent="scheduling",
                reply_text="No momento não encontrei consulta futura vinculada a esse CPF.",
                llm_used=False,
            )
        return OrchestratorResponse(
            intent="scheduling",
            reply_text=self._format_check_reply(appointment),
            llm_used=False,
        )

    @staticmethod
    def _format_check_reply(appointment: "Appointment") -> str:
        """Formata a resposta de consulta de agendamento, incluindo nome do beneficiário quando aplicável."""
        date_str = appointment.scheduled_at.strftime("%d/%m/%Y às %H:%M")
        if appointment.is_third_party and appointment.beneficiary_name:
            return f"Sua próxima consulta para *{appointment.beneficiary_name}* está marcada para {date_str} com {appointment.doctor_name}."
        return f"Sua próxima consulta está marcada para {date_str} com {appointment.doctor_name}."

    async def _handle_cancel(self, context: AgentContext) -> OrchestratorResponse:
        cpf = self._extract_cpf(context.incoming_text) or context.patient.cpf
        if not cpf:
            return OrchestratorResponse(intent="scheduling", reply_text="Para cancelar certinho, me envie seu CPF.", llm_used=False)
        appointment = await self.repository.get_next_by_patient_cpf(context.session, cpf=cpf)
        if appointment is None:
            return OrchestratorResponse(intent="scheduling", reply_text="Não encontrei consulta futura para esse CPF.", llm_used=False)
        await self.repository.mark_cancelled(context.session, appointment=appointment, reason=context.incoming_text)
        try:
            await self.notification.notify_cancellation(
                patient_name=context.patient.name,
                patient_phone=context.patient.phone or "",
                doctor_name=appointment.doctor_name,
                scheduled_at=appointment.scheduled_at,
                cpf=cpf,
                beneficiary_name=appointment.beneficiary_name if appointment.is_third_party else None,
            )
        except Exception:
            logger.exception("notify_cancellation_failed")
        return OrchestratorResponse(intent="scheduling", reply_text="Pronto, registrei o cancelamento. Se quiser, em seguida já posso te oferecer novas opções de horário.", llm_used=False)

    async def _handle_reschedule(self, context: AgentContext) -> OrchestratorResponse:
        cpf = self._extract_cpf(context.incoming_text) or context.patient.cpf
        if not cpf:
            return OrchestratorResponse(intent="scheduling", reply_text="Para remarcar com segurança, me envie seu CPF e o novo horário desejado.", llm_used=False)
        appointment = await self.repository.get_next_by_patient_cpf(context.session, cpf=cpf)
        if appointment is None:
            return OrchestratorResponse(intent="scheduling", reply_text="Não encontrei consulta ativa para esse CPF. Se quiser, já posso organizar um novo agendamento.", llm_used=False)
        doctor = self._extract_doctor(appointment.doctor_name) or DRA_VITORIA
        requested = self._resolve_datetime(context.incoming_text)
        if not requested:
            return OrchestratorResponse(intent="scheduling", reply_text="Me diga o novo dia e horário desejados para eu te oferecer as melhores opções reagrupadas.", llm_used=False)
        suggestions = await self._build_suggestions(context=context, doctor=doctor, requested_at=requested, insurance_kind=None)
        if not suggestions:
            try:
                await self.notification.notify_escalation(
                    reason=f"Remarcação sem disponibilidade para {doctor.canonical_name}. Paciente aguarda nova data.",
                    patient_phone=context.patient.phone or "",
                    patient_name=context.patient.name,
                )
            except Exception:
                logger.exception("notify_escalation_failed")
            return OrchestratorResponse(intent="scheduling", reply_text="Não encontrei horários disponíveis para esse período. Encaminhei para a equipe — entrarão em contato para confirmar uma nova data 💚", llm_used=False)
        session_key = self._session_key(context)
        self.memory.merge_state(session_key, {"pending_action": "confirm_schedule", "pending_doctor_name": doctor.canonical_name, "pending_scheduled_at": suggestions[0].isoformat()})
        return OrchestratorResponse(intent="scheduling", reply_text=self._compose_offer(doctor, suggestions), llm_used=False)

    async def _build_suggestions(self, *, context: AgentContext, doctor: DoctorProfile, requested_at: datetime, insurance_kind: str | None) -> list[datetime]:
        if insurance_kind == "insurance" and doctor.canonical_name == DR_LUCAS.canonical_name:
            return []
        # Normaliza para o início do slot de 30 min mais próximo
        start = requested_at.replace(minute=0 if requested_at.minute < 30 else 30, second=0, microsecond=0)
        candidates: list[datetime] = []
        current = start
        # Busca slots por até 35 dias — suficiente para cobrir 5 terças-feiras
        deadline = requested_at + timedelta(days=35)
        while len(candidates) < 3 and current <= deadline:
            if self._is_allowed_slot(doctor, current, insurance_kind):
                # Consulta o banco de dados para verificar conflitos reais — NUNCA inventa disponibilidade
                conflicts = await self.repository.list_conflicts(
                    context.session,
                    doctor_name=doctor.canonical_name,
                    starts_at=current,
                    ends_at=current + timedelta(minutes=doctor.duration_minutes),
                    duration_minutes=doctor.duration_minutes,
                )
                if not conflicts:
                    candidates.append(current)
            current += timedelta(minutes=30)
        return self._prioritize_candidates(candidates, doctor)

    def _prioritize_candidates(self, candidates: list[datetime], doctor: DoctorProfile) -> list[datetime]:
        """Prioriza horários preferenciais 15:00–17:00 para ambos os médicos."""
        def score(dt: datetime) -> tuple[int, datetime]:
            slot = dt.time()
            # Faixa preferencial operacional: 15:00–17:00
            if time(15, 0) <= slot <= time(17, 0):
                preference = 0  # melhor
            else:
                preference = 1  # fora da faixa preferencial (ainda válido: 13:40–14:30 ou 17:30–18:00)
            return (preference, dt)
        return sorted(candidates, key=score)[:3]

    def _is_allowed_slot(self, doctor: DoctorProfile, dt: datetime, insurance_kind: str | None) -> bool:
        """
        Regras oficiais de agenda (fonte de verdade):
          - Ambos os médicos atendem SOMENTE às terças-feiras (weekday == 1).
          - Faixa permitida: 13:40–18:00.
          - Preferência operacional: 15:00–17:00 (aplicada em _prioritize_candidates).
          - Ambos atendem somente particular — convênio não é aceito para consultas.
        """
        # Somente terças-feiras
        if dt.weekday() != 1:
            return False
        slot_time = dt.time()
        # Faixa obrigatória de atendimento: 13:40–18:00
        if not (time(13, 40) <= slot_time <= time(18, 0)):
            return False
        # Nenhum dos médicos aceita convênio para consultas
        if insurance_kind == "insurance":
            return False
        return True

    def _compose_offer(self, doctor: DoctorProfile, suggestions: list[datetime]) -> str:
        if not suggestions:
            return "Vou verificar com a equipe e consultar os doutores."
        formatted = [f"{item.strftime('%d/%m às %H:%M')}" for item in suggestions]
        if len(formatted) == 1:
            return f"Consigo te oferecer {formatted[0]} com {doctor.canonical_name}. Qual prefere?"
        if len(formatted) == 2:
            return f"Consigo te oferecer {formatted[0]} ou {formatted[1]} com {doctor.canonical_name}. Qual prefere?"
        return f"Consigo te oferecer {formatted[0]}, {formatted[1]} ou {formatted[2]} com {doctor.canonical_name}. Qual prefere?"

    @staticmethod
    def _calculate_age(birth_date: date) -> float:
        """Calcula a idade em anos com base na data de nascimento."""
        today = date.today()
        years = today.year - birth_date.year - (
            (today.month, today.day) < (birth_date.month, birth_date.day)
        )
        return float(years)

    @staticmethod
    def _detect_action(text: str) -> str:
        normalized = SchedulingAgent._normalize(text)
        if any(token in normalized for token in ("cancel", "desmarc", "cancelar")):
            return "cancel"
        if any(token in normalized for token in ("remar", "reagend", "mudar horario", "mudar horário")):
            return "reschedule"

        # Tokens explícitos de consulta (possessivo ou construção específica)
        _check_explicit = (
            "tenho consulta", "minha consulta",
            "ver minha consulta", "ver consulta",
            "consulta marcada", "consulta agendada",
            "marcada a consulta", "marcada minha consulta",  # ordem inversa
            "qual dia", "que dia e minha", "que dia é minha",
            "quando e minha consulta", "quando é minha consulta",
            "meu horario", "meu horário", "qual meu horario", "qual meu horário",
            "horario da minha", "horário da minha",
            "quando foi minha ultima", "quando foi minha última",
        )
        if any(token in normalized for token in _check_explicit):
            return "check"

        # Condição composta: interrogativa + "consulta" sem intenção de criação.
        # Cobre toda a classe semântica "quando/qual/que dia é a consulta?" independente
        # de variações de artigo, possessivo ou ordem das palavras.
        _interrogatives = ("quando sera", "quando esta", "quando e a", "para quando",
                           "qual data", "que data", "que horas", "qual horario",
                           "quando fica", "qual e a data")
        _creation_tokens = ("agendar", "marcar", "quero", "gostaria", "proximo", "proxima",
                            "disponivel", "nova consulta", "novo horario")
        if (
            "consulta" in normalized
            and any(t in normalized for t in _interrogatives)
            and not any(t in normalized for t in _creation_tokens)
        ):
            return "check"
        # Pergunta sobre disponibilidade/dias de atendimento — consulta banco real
        _avail_tokens = (
            "quais dias", "que dias", "que dia atend", "quando atend",
            "dias de atendimento", "dias que atende", "dia que atende",
            "tem vaga", "tem horario", "tem horário",
            "atende hoje", "atende amanha", "atende segunda", "atende quarta",
            "atende quinta", "atende sexta", "atende sabado", "atende domingo",
            "atende essa semana", "atende semana que vem",
            "essa semana tem", "semana que vem tem",
            "ha vaga", "há vaga", "existe vaga", "existem vagas",
        )
        if any(token in normalized for token in _avail_tokens):
            return "availability_inquiry"
        # "?" + palavra de disponibilidade sem ação de agendamento → inquiry
        if "?" in normalized and any(token in normalized for token in ("atende", "disponivel", "disponível", "vaga", "horario", "horário")):
            if not any(token in normalized for token in ("agendar", "marcar", "remarcar", "quero", "gostaria")):
                return "availability_inquiry"
        return "schedule"

    # ------------------------------------------------------------------
    # Helpers de disponibilidade / urgência / data
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_relation(text: str) -> str | None:
        """
        Extrai a relação familiar mencionada pelo paciente para personalizar a pergunta
        de coleta do nome. Ex: "para minha filha" → "filha".
        """
        normalized = SchedulingAgent._normalize(text)
        relations = {
            "filha": "filha", "filho": "filho",
            "mae": "mãe", "mãe": "mãe",
            "pai": "pai",
            "esposa": "esposa", "marido": "marido",
            "irma": "irmã", "irmã": "irmã",
            "irmao": "irmão", "irmão": "irmão",
            "avo": "avó", "avó": "avó",
            "avo": "avô", "avô": "avô",
            "dependente": "dependente", "familiar": "familiar",
        }
        for token, label in relations.items():
            if token in normalized:
                return label
        return None

    @staticmethod
    def _extract_beneficiary_name_inline(text: str) -> str | None:
        """
        Tenta capturar o nome do beneficiário diretamente da mensagem quando
        o paciente já o informa na frase (ex: "para minha filha Ana Paula").
        Retorna None se não encontrar nome após a relação.
        """
        # Padrões: "para minha filha [Nome]", "para meu filho [Nome]", etc.
        pattern = re.compile(
            r"para\s+(?:minha?|seu?|a|o)\s+"
            r"(?:filha?|filho|mae|m[ãa]e|pai|esposa|marido|irm[ãa]|irm[ão]o|av[oó]|av[ôo]|dependente|familiar)"
            r"\s+([A-Za-zÀ-ÿ]{2,}(?:\s+[A-Za-zÀ-ÿ]{2,})*)",
            re.IGNORECASE,
        )
        match = pattern.search(text)
        if match:
            name = match.group(1).strip()
            # Rejeita se parece com uma palavra de agendamento (médico, data, etc.)
            scheduling_words = {"dr", "dra", "lucas", "vitoria", "amanha", "hoje", "proximo", "próximo"}
            if name.lower().split()[0] not in scheduling_words:
                return " ".join(w.capitalize() for w in name.split())
        return None

    @staticmethod
    def _detect_third_party(normalized_text: str) -> bool:
        """
        Detecta se o agendamento é para um terceiro (familiar, dependente).
        Quando True, o SchedulingAgent usa campos beneficiary_* na consulta
        e NÃO sobrescreve o perfil (CPF/data de nascimento) do titular do número.
        """
        tokens = (
            "para minha filha", "para meu filho",
            "para minha mae", "para minha mãe", "para meu pai",
            "para minha esposa", "para meu marido",
            "para minha irma", "para minha irmã",
            "para meu irmao", "para meu irmão",
            "para minha avo", "para minha avó",
            "para meu avo", "para meu avô",
            "para meu familiar", "para minha familiar",
            "para outra pessoa", "para um familiar",
            "meu dependente", "minha dependente",
            "terceiro", "dependente",
        )
        return any(t in normalized_text for t in tokens)

    def _is_urgent_request(self, text: str) -> bool:
        """Detecta urgência explícita na mensagem do paciente."""
        normalized = self._normalize(text)
        return any(token in normalized for token in (
            "urgente", "urgencia", "urgência", "emergencia", "emergência",
            "nao posso esperar", "não posso esperar",
            "nao pode esperar", "não pode esperar",
            "preciso hoje", "preciso amanha", "precisa hoje", "precisa amanha",
            "nao da pra esperar", "não da pra esperar",
            "nao tem como esperar", "não tem como esperar",
            "encaixe urgente", "muito urgente",
            "nao pode ser terca", "não pode ser terca",
            "nao quero esperar terca", "nao quero esperar ate terca",
        ))

    def _asked_non_tuesday(self, text: str, now: datetime) -> bool:
        """Retorna True se o paciente mencionou um dia que NÃO é terça-feira."""
        normalized = self._normalize(text)
        # Resolve "amanhã" / "hoje" para o dia da semana real
        if any(t in normalized for t in ("amanha", "amanhã")):
            return (now.date() + timedelta(days=1)).weekday() != 1
        if "hoje" in normalized:
            return now.weekday() != 1
        # Dias da semana explícitos que não são terça
        return any(t in normalized for t in (
            "segunda", "segunda-feira",
            "quarta", "quarta-feira",
            "quinta", "quinta-feira",
            "sexta", "sexta-feira",
            "sabado", "sábado",
            "domingo",
        ))

    def _next_tuesday(self, from_dt: datetime) -> date:
        """Retorna a próxima terça-feira (inclusive hoje se for terça e ainda há horários)."""
        days_ahead = (1 - from_dt.weekday()) % 7  # 1 = Tuesday
        if days_ahead == 0 and from_dt.time() >= time(18, 0):
            days_ahead = 7  # Terça de hoje já encerrou
        return from_dt.date() + timedelta(days=days_ahead)

    # ------------------------------------------------------------------
    # Handler: intercept non-Tuesday scheduling request
    # ------------------------------------------------------------------

    async def _check_non_tuesday_intent(
        self, context: AgentContext, session_key: str, state: dict
    ) -> OrchestratorResponse | None:
        """
        Verifica se o paciente pediu agendamento em dia que NÃO é terça-feira.

        Retorna uma OrchestratorResponse para interromper o fluxo normal, ou
        None para deixar o _handle_schedule continuar normalmente.

        Comportamento (Alternativa A):
          • 1ª solicitação fora de terça → explica a regra, mostra as próximas
            terças disponíveis, grava `non_tuesday_warned = "true"` no Redis.
          • Insistência (non_tuesday_warned já True) OU mensagem com urgência
            → aciona notify_escalation com dados do paciente e informa que a
            equipe entrará em contato. Exibe slot de terça pendente, se houver.
        """
        text = context.incoming_text
        normalized = self._normalize(text)
        now_naive = datetime.now(self._timezone).replace(tzinfo=None)

        # Guards: ignora se o paciente já está falando de terça, confirmando,
        # ou fornecendo CPF/data de nascimento (dados solicitados pelo bot).
        if any(t in normalized for t in ("terca", "terca-feira", "terca feira")):
            return None
        if self._is_confirmation_message(normalized):
            return None
        if self._extract_cpf(text):
            return None
        if self._extract_birth_date(text):
            return None

        # Só aciona se de fato mencionou um dia não-terça
        if not self._asked_non_tuesday(text, now_naive):
            return None

        is_urgent = self._is_urgent_request(text)
        already_warned = state.get("non_tuesday_warned") == "true"

        # ── Urgência ou insistência → escala para equipe ────────────────
        if is_urgent or already_warned:
            reason_prefix = "URGENTE — " if is_urgent else "Insistência — "
            doctor = (
                self._extract_doctor(text)
                or self._extract_doctor(state.get("pending_doctor_name") or "")
            )
            reason = (
                f"{reason_prefix}Paciente solicitou agendamento fora de terça-feira"
                f"{f' com {doctor.canonical_name}' if doctor else ''}. "
                "Aguarda contato da equipe para verificar possibilidade de encaixe."
            )
            try:
                await self.notification.notify_escalation(
                    reason=reason,
                    patient_phone=context.patient.phone or "",
                    patient_name=context.patient.name,
                )
            except Exception:
                logger.exception("notify_escalation_failed")

            pending_at = self._parse_iso_datetime(state.get("pending_scheduled_at"))
            if pending_at and doctor:
                return OrchestratorResponse(
                    intent="scheduling",
                    reply_text=(
                        "Entendido 💚 Acionei a equipe para verificar uma possibilidade de encaixe — "
                        "entrarão em contato em breve.\n"
                        f"Enquanto isso, tenho reservado *{pending_at.strftime('%d/%m às %H:%M')}* com "
                        f"{doctor.canonical_name} (terça-feira). "
                        "Posso confirmar esse horário caso a equipe não consiga um encaixe antes?"
                    ),
                    llm_used=False,
                )
            return OrchestratorResponse(
                intent="scheduling",
                reply_text=(
                    "Entendido 💚 Acionei a equipe para verificar uma possibilidade de encaixe — "
                    "entrarão em contato em breve."
                ),
                llm_used=False,
            )

        # ── Primeiro aviso → explica regra e mostra próxima terça ──────
        next_tue = self._next_tuesday(now_naive)
        doctor = (
            self._extract_doctor(text)
            or self._extract_doctor(state.get("pending_doctor_name") or "")
        )
        search_from = datetime(next_tue.year, next_tue.month, next_tue.day, 13, 40)
        doctors_to_check = [doctor] if doctor else [DR_LUCAS, DRA_VITORIA]

        lines = [
            "Nossos médicos atendem somente às *terças-feiras à tarde* 💚",
            f"Próximos horários disponíveis (terça, {next_tue.strftime('%d/%m')}):",
        ]

        first_suggestion: datetime | None = None
        first_doctor: DoctorProfile | None = None

        for doc in doctors_to_check:
            suggs = await self._build_suggestions(
                context=context,
                doctor=doc,
                requested_at=search_from,
                insurance_kind=state.get("insurance_kind"),
            )
            if suggs:
                slots = " | ".join(s.strftime("%H:%M") for s in suggs[:3])
                lines.append(f"• *{doc.canonical_name}*: {slots}")
                if first_suggestion is None:
                    first_suggestion = suggs[0]
                    first_doctor = doc

        lines.append(
            "\nQual horário você prefere? "
            "Se precisar de um encaixe com urgência, me avise que aciono a equipe 💚"
        )

        # Pré-seleciona o primeiro slot e registra o aviso no Redis
        state_updates: dict[str, str] = {"non_tuesday_warned": "true"}
        effective_doctor = doctor or first_doctor
        if first_suggestion and effective_doctor:
            state_updates.update({
                "active_flow": "scheduling",
                "pending_action": "confirm_schedule",
                "pending_doctor_name": effective_doctor.canonical_name,
                "pending_scheduled_at": first_suggestion.isoformat(),
            })
        self.memory.merge_state(session_key, state_updates)

        return OrchestratorResponse(
            intent="scheduling",
            reply_text="\n".join(lines),
            llm_used=False,
        )

    # ------------------------------------------------------------------
    # Handler: pergunta sobre disponibilidade
    # ------------------------------------------------------------------

    async def _handle_availability_inquiry(self, context: AgentContext) -> OrchestratorResponse:
        """
        Responde perguntas sobre dias/horários de atendimento consultando o banco real.
        Regras:
          - Padrão: terças-feiras à tarde (prioridade sempre).
          - Dias fora da terça sem urgência → redireciona para próxima terça + mostra slots do banco.
          - Urgência → mostra slots disponíveis de qualquer terça próxima;
                       se nenhum disponível → escala para humano.
        """
        session_key = self._session_key(context)
        state = self.memory.get_state(session_key)
        now_naive = datetime.now(self._timezone).replace(tzinfo=None)
        text = context.incoming_text

        is_urgent = self._is_urgent_request(text)
        doctor = self._extract_doctor(text) or self._extract_doctor(state.get("pending_doctor_name") or "")
        asked_non_tuesday = self._asked_non_tuesday(text, now_naive)
        next_tue = self._next_tuesday(now_naive)

        logger.info(
            "availability_inquiry | today=%s weekday=%d next_tuesday=%s "
            "urgent=%s doctor=%s asked_non_tuesday=%s | text=%r",
            now_naive.date(), now_naive.weekday(), next_tue,
            is_urgent, doctor.canonical_name if doctor else None,
            asked_non_tuesday, text[:80],
        )

        # Busca slots reais no banco — sempre a partir da próxima terça
        search_from = datetime(next_tue.year, next_tue.month, next_tue.day, 13, 40)
        doctors_to_check = [doctor] if doctor else [DR_LUCAS, DRA_VITORIA]

        suggestions_by_doctor: dict[str, list[datetime]] = {}
        for doc in doctors_to_check:
            # Usa "private" para mostrar máxima disponibilidade na consulta inicial;
            # o tipo de plano será confirmado ao agendar.
            suggs = await self._build_suggestions(
                context=context, doctor=doc,
                requested_at=search_from,
                insurance_kind="private",
            )
            if suggs:
                suggestions_by_doctor[doc.canonical_name] = suggs[:3]

        logger.info(
            "availability_inquiry | results=%s",
            {k: [s.strftime("%d/%m %H:%M") for s in v] for k, v in suggestions_by_doctor.items()},
        )

        # ── Sem vagas no banco ──────────────────────────────────────────
        if not suggestions_by_doctor:
            if is_urgent:
                logger.warning("availability_inquiry | escalate=True | reason=no_slots_urgent")
                try:
                    await self.notification.notify_escalation(
                        reason="URGENTE — Paciente solicitou encaixe urgente e não há vagas disponíveis nos próximos 35 dias.",
                        patient_phone=context.patient.phone or "",
                        patient_name=context.patient.name,
                    )
                except Exception:
                    logger.exception("notify_escalation_failed")
                return OrchestratorResponse(
                    intent="scheduling",
                    reply_text=(
                        "Não encontrei vagas disponíveis no momento. "
                        "Como é urgente, já acionei a equipe para verificar um encaixe — retornarão em breve 💚"
                    ),
                    llm_used=False,
                )
            return OrchestratorResponse(
                intent="scheduling",
                reply_text=(
                    "Não encontrei horários disponíveis nas próximas semanas. "
                    "Quer indicar um período específico ou prefere que eu consulte a equipe?"
                ),
                llm_used=False,
            )

        # ── Pediu dia fora da terça, sem urgência → redireciona ────────
        if asked_non_tuesday and not is_urgent:
            lines = [
                "Nosso atendimento padrão é às *terças-feiras à tarde* 💚",
                f"Próximos horários disponíveis (terça {next_tue.strftime('%d/%m')}):",
            ]
            for doc_name, suggs in suggestions_by_doctor.items():
                slots = " | ".join(s.strftime("%H:%M") for s in suggs)
                lines.append(f"• *{doc_name}*: {slots}")
            lines.append("\nQual médico e horário você prefere? Posso já deixar reservado 😊")
            return OrchestratorResponse(intent="scheduling", reply_text="\n".join(lines), llm_used=False)

        # ── Resposta padrão: mostra slots reais do banco ────────────────
        lines = ["Atendimento às *terças-feiras à tarde* 💚\nPróximos horários disponíveis:"]
        for doc_name, suggs in suggestions_by_doctor.items():
            slots = " | ".join(s.strftime("%d/%m às %H:%M") for s in suggs)
            lines.append(f"• *{doc_name}*: {slots}")
        lines.append("\nQual médico e horário você prefere?")

        # Persiste médico no estado se um foi identificado
        if doctor:
            self.memory.merge_state(session_key, {
                "active_flow": "scheduling",
                "pending_doctor_name": doctor.canonical_name,
            })

        return OrchestratorResponse(intent="scheduling", reply_text="\n".join(lines), llm_used=False)

    @staticmethod
    def _normalize(text: str) -> str:
        normalized = unicodedata.normalize("NFKD", (text or "").lower().strip())
        no_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        replacements = {" vc ": " voce ", " tb ": " tambem ", " kkk": " risada", " haha": " risada", " rs": " risada", "acomrpanbar": "acompanhar", "3 horas da tarde": "15h"}
        padded = f" {no_accents} "
        for old, new in replacements.items():
            padded = padded.replace(old, f" {new} ")
        return re.sub(r"\s+", " ", padded).strip()

    @staticmethod
    def _extract_doctor(text: str) -> DoctorProfile | None:
        normalized = SchedulingAgent._normalize(text)
        if any(token in normalized for token in ("dr lucas", "lucas", "cirilo")):
            return DR_LUCAS
        if any(token in normalized for token in ("dra vitoria", "dra vitória", "vitoria", "vitória", "cunha")):
            return DRA_VITORIA
        return None

    @staticmethod
    def _extract_insurance_kind(normalized_text: str) -> str | None:
        if any(token in normalized_text for token in ("particular", "sem convenio", "sem convênio")):
            return "private"
        if any(token in normalized_text for token in ("convenio", "convênio", "plano")):
            return "insurance"
        return None

    @staticmethod
    def _extract_cpf(text: str) -> str | None:
        """Extrai CPF de qualquer formato: 000.000.000-00, 00000000000, com ou sem pontuação."""
        # Tenta formato com máscara primeiro (ex: 708.954.632-12)
        match = re.search(r"\b(\d{3}\.?\d{3}\.?\d{3}-?\d{2})\b", text or "")
        if match:
            digits = re.sub(r"\D", "", match.group(1))
        else:
            # Tenta sequência contínua de 11 dígitos
            digits = re.sub(r"\D", "", text or "")
            if len(digits) != 11:
                return None
        # Rejeita apenas CPFs com todos os dígitos iguais (ex: 11111111111)
        if len(digits) != 11 or len(set(digits)) == 1:
            return None
        return digits

    @staticmethod
    def _extract_birth_date(text: str) -> date | None:
        """
        Extrai data de nascimento em múltiplos formatos:
          - DD/MM/AAAA ou DD-MM-AAAA  (ex: 08/08/2003)
          - DD de MMMM de AAAA        (ex: 08 de agosto de 2003)
          - DD MMMM AAAA              (ex: 8 agosto 2003)
        """
        # Formato numérico: DD/MM/AAAA ou DD-MM-AAAA
        match = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", text or "")
        if match:
            day, month, year = map(int, match.groups())
            year = year + 2000 if year < 100 else year
            try:
                value = date(year, month, day)
            except ValueError:
                return None
            if value > date.today() or value < date(1900, 1, 1):
                return None
            return value
        # Formato textual: DD de MMMM de AAAA  /  DD MMMM AAAA
        normalized = SchedulingAgent._normalize(text or "")
        for month_name, month_num in MONTHS.items():
            pattern = rf"\b(\d{{1,2}})\s+(?:de\s+)?{month_name}\s+(?:de\s+)?(\d{{2,4}})\b"
            m = re.search(pattern, normalized)
            if m:
                day = int(m.group(1))
                year = int(m.group(2))
                year = year + 2000 if year < 100 else year
                try:
                    value = date(year, month_num, day)
                except ValueError:
                    return None
                if value > date.today() or value < date(1900, 1, 1):
                    return None
                return value
        return None

    @staticmethod
    def _extract_name(text: str) -> str | None:
        match = re.search(r"(?:meu nome e|me chamo|sou)\s+([A-Za-zÀ-ÿ ]{3,})", text or "", re.IGNORECASE)
        return match.group(1).strip() if match else None

    @staticmethod
    def _extract_profile_updates(text: str) -> dict[str, str]:
        data: dict[str, str] = {}
        name = SchedulingAgent._extract_name(text)
        cpf = SchedulingAgent._extract_cpf(text)
        birth = SchedulingAgent._extract_birth_date(text)
        if name:
            data["patient_name"] = name
        if cpf:
            data["cpf"] = cpf
        if birth:
            data["birth_date"] = birth.isoformat()
        return data

    def _resolve_datetime(self, text: str) -> datetime | None:
        normalized = self._normalize(text)
        now = datetime.now(self._timezone)
        base_date = None
        if "amanha" in normalized:
            base_date = (now + timedelta(days=1)).date()
        elif "hoje" in normalized:
            base_date = now.date()
        else:
            dm = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", normalized)
            if dm:
                day = int(dm.group(1)); month = int(dm.group(2)); year = int(dm.group(3) or now.year)
                year = year + 2000 if year < 100 else year
                try:
                    base_date = date(year, month, day)
                except ValueError:
                    return None
            else:
                for month_name, month_num in MONTHS.items():
                    if month_name in normalized:
                        md = re.search(rf"(\d{{1,2}})\s+de\s+{month_name}", normalized)
                        if md:
                            base_date = date(now.year, month_num, int(md.group(1)))
                            break
        tm = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(h|hs|horas?)\b", normalized)
        if tm:
            hour = int(tm.group(1))
            minute = int(tm.group(2) or 0)
        else:
            # Aceita formato HH:MM sem sufixo "h" (ex: "14:00", "às 14:00")
            tm2 = re.search(r"\b(\d{1,2}):(\d{2})\b", normalized)
            if not tm2:
                return None
            hour = int(tm2.group(1))
            minute = int(tm2.group(2))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return None
        if "tarde" in normalized and hour < 12:
            hour += 12
        if base_date is None:
            return None
        return datetime.combine(base_date, time(hour, minute))

    @staticmethod
    def _is_next_available_request(normalized_text: str) -> bool:
        """
        Detecta quando o paciente pede o próximo horário disponível (ou um
        horário em data relativa como "amanhã") sem especificar hora exata.
        Nesses casos o agente consulta o banco a partir da data inferida
        em vez de devolver erro ou inventar horários.
        """
        tokens = (
            "proxima disponivel", "mais proxima", "proximo disponivel",
            "mais perto", "primeira vaga", "qualquer horario",
            "data mais proxima", "semana que vem", "essa semana", "esta semana",
            "proximo horario", "mais rapido", "o mais proximo",
            "disponivel", "proxima vaga", "logo disponivel",
            # Referências de dia relativo sem hora especificada
            "amanha", "hoje",
        )
        return any(token in normalized_text for token in tokens)

    def _resolve_start_hint(self, text: str) -> datetime | None:
        """
        Extrai um hint de data de início para a busca de próximo disponível.
        Retorna datetime no início do horário de atendimento (13:40) para
        que _build_suggestions comece a varrer a partir desse ponto.
        Retorna None quando não há hint (busca começa de agora).
        """
        normalized = self._normalize(text)
        now_naive = datetime.now(self._timezone).replace(tzinfo=None)
        today = now_naive.date()

        if "amanha" in normalized:
            d = today + timedelta(days=1)
            return datetime(d.year, d.month, d.day, 13, 40)
        if "hoje" in normalized:
            return datetime(today.year, today.month, today.day, 13, 40)
        # Formato DD/MM (sem hora) — ex: "14/04"
        dm = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", normalized)
        if dm:
            try:
                day = int(dm.group(1))
                month = int(dm.group(2))
                year = int(dm.group(3) or today.year)
                year = year + 2000 if year < 100 else year
                return datetime(year, month, day, 13, 40)
            except ValueError:
                pass
        return None

    @staticmethod
    def _is_confirmation_message(normalized_text: str) -> bool:
        return normalized_text in YES_TOKENS or any(token in normalized_text for token in YES_TOKENS)

    @staticmethod
    def _parse_iso_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _parse_iso_date(value: str | None) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _session_key(context: AgentContext) -> str:
        return context.conversation_metadata.get("session_key") or context.patient.phone or str(context.patient.id)
