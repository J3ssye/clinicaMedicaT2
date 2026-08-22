"""
Serviço de notificação interna para a equipe FIDEM.

Usado para:
- Alertar médicos/secretária sobre pacientes menores de 8 anos
- Escalar casos fora do fluxo padrão (receita, dúvida de agenda, etc.)
- Notificar sobre erros críticos no sistema
- Confirmar novos agendamentos feitos pelo bot

Utiliza o WhatsAppClient existente para enviar mensagens diretamente
aos contatos internos via WAHA. Os contatos são definidos em app/core/contacts.py.
"""
from __future__ import annotations

import logging
from datetime import datetime

from app.core.contacts import ALERT_CONTACTS, DEVELOPER, DOCTORS, SECRETARY, TeamContact
from app.integrations.whatsapp import WhatsAppClient
from app.services.media_service import MediaService
from app.services.memory.redis_memory import RedisConversationMemory


logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(
        self,
        whatsapp_client: WhatsAppClient | None = None,
        memory: RedisConversationMemory | None = None,
    ) -> None:
        self._client = whatsapp_client or WhatsAppClient()
        self._memory = memory or RedisConversationMemory()

    # ------------------------------------------------------------------
    # Notificações específicas
    # ------------------------------------------------------------------

    async def notify_minor_patient(
        self,
        *,
        patient_phone: str,
        patient_name: str | None,
        birth_date_str: str,
        age_years: int,
    ) -> None:
        """
        Alerta toda a equipe de alerta sobre paciente menor de 8 anos.
        O agendamento automático já foi bloqueado antes desta chamada.
        """
        name_display = patient_name or patient_phone
        text = (
            f"⚠️ *Alerta MALU — Paciente menor de idade*\n\n"
            f"Contato: {name_display} ({patient_phone})\n"
            f"Data de nascimento: {birth_date_str}\n"
            f"Idade estimada: {age_years} ano(s)\n\n"
            f"O agendamento automático foi bloqueado pois o paciente tem menos de 8 anos. "
            f"Por favor, entre em contato diretamente para orientar a família da melhor forma."
        )
        await self._send_to_contacts(ALERT_CONTACTS, text)

    async def notify_new_appointment(
        self,
        *,
        patient_name: str | None,
        patient_phone: str,
        doctor_name: str,
        scheduled_at: datetime,
        cpf: str,
        notes: str | None = None,
        beneficiary_name: str | None = None,
    ) -> None:
        """
        Avisa secretária e o médico correspondente quando uma consulta é confirmada pelo bot.
        `beneficiary_name` é preenchido quando o agendamento é para um familiar/dependente.
        """
        doctor_contact = next(
            (c for c in DOCTORS if c.name.lower() in doctor_name.lower()),
            None,
        )
        # Secretária sempre recebe; médico específico também quando identificado
        targets = [c for c in [SECRETARY, doctor_contact] if c]
        if not targets:
            logger.warning("notify_new_appointment_no_contacts_configured")
            return

        name_display = patient_name or patient_phone
        beneficiary_line = f"Agendado para: {beneficiary_name}\n" if beneficiary_name else ""
        notes_line = f"Obs: {notes.strip(' |')}\n" if notes and notes.strip(" |") else ""
        text = (
            f"📅 *Nova consulta agendada — MALU*\n\n"
            f"Paciente (contato): {name_display}\n"
            f"Telefone: {patient_phone}\n"
            f"CPF: {cpf}\n"
            f"{beneficiary_line}"
            f"Médico: {doctor_name}\n"
            f"Data/hora: {scheduled_at.strftime('%d/%m/%Y às %H:%M')}\n"
            f"{notes_line}"
        )
        await self._send_to_contacts(targets, text)

    async def notify_escalation(
        self,
        *,
        reason: str,
        patient_phone: str,
        patient_name: str | None = None,
    ) -> None:
        """
        Notifica médicos, secretária e developer sobre um caso que precisa de atenção humana.
        Usado para ambiguidades, exceções de agenda e casos fora do padrão.
        """
        contacts: list[TeamContact] = [
            *DOCTORS,
            *([SECRETARY] if SECRETARY else []),
            *([DEVELOPER] if DEVELOPER else []),
        ]
        if not contacts:
            logger.warning("notify_escalation_no_contacts_configured")
            return
        name_display = patient_name or patient_phone
        text = (
            f"⚠️ *Alerta MALU — Caso para validação*\n\n"
            f"Paciente: {name_display} ({patient_phone})\n"
            f"Motivo: {reason}\n\n"
            f"Por favor, verifique e entre em contato."
        )
        await self._send_to_contacts(contacts, text)

    async def notify_exam_image(
        self,
        *,
        patient_name: str | None,
        patient_phone: str,
        media_url: str,
        media_mime_type: str | None = None,
        caption: str | None = None,
    ) -> None:
        """
        Notifica médicos e secretária sobre imagem de exame enviada pelo paciente.

        WAHA CORE (WEBJS) não suporta envio de mídia — fallback automático:
        usa OpenAI Vision para gerar uma descrição da imagem e inclui na notificação
        de texto. Para envio da imagem real, é necessário WAHA PLUS ou engine NOWJS.
        """
        contacts: list[TeamContact] = [
            *DOCTORS,
            *([SECRETARY] if SECRETARY else []),
        ]
        if not contacts:
            logger.warning("notify_exam_image_no_contacts_configured")
            return

        name_display = patient_name or patient_phone

        # Tenta analisar a imagem com OpenAI Vision para incluir descrição na notificação
        description: str | None = None
        try:
            media_service = MediaService()
            result = await media_service.analyze_image_from_url(
                media_url=media_url,
                mime_type=media_mime_type,
                caption=caption,
            )
            if result and result.text:
                # Remove o prefixo adicionado pelo MediaService
                description = result.text.replace("[Imagem enviada pelo paciente]", "").strip()
        except Exception:
            logger.warning("exam_image_analysis_failed — prosseguindo sem descrição")

        caption_line = f"\nLegenda do paciente: _{caption.strip()}_" if caption and caption.strip() else ""
        description_block = (
            f"\n\n📋 *Descrição automática da imagem:*\n{description}"
            if description
            else "\n\n_(Descrição automática indisponível — visualize a conversa do paciente no bot.)_"
        )

        text = (
            f"🖼️ *Imagem de exame enviada — MALU*\n\n"
            f"Paciente: {name_display} ({patient_phone})"
            f"{caption_line}"
            f"{description_block}"
        )

        await self._send_to_contacts(contacts, text)
        logger.info("exam_image_notification_sent patient=%s contacts=%s", name_display, len(contacts))

    async def notify_cancellation(
        self,
        *,
        patient_name: str | None,
        patient_phone: str,
        doctor_name: str,
        scheduled_at: datetime,
        cpf: str,
        beneficiary_name: str | None = None,
    ) -> None:
        """
        Avisa secretária e o médico correspondente quando uma consulta é cancelada pelo bot.
        """
        doctor_contact = next(
            (c for c in DOCTORS if c.name.lower() in doctor_name.lower()),
            None,
        )
        targets = [c for c in [SECRETARY, doctor_contact] if c]
        if not targets:
            logger.warning("notify_cancellation_no_contacts_configured")
            return

        name_display = patient_name or patient_phone
        beneficiary_line = f"Agendado para: {beneficiary_name}\n" if beneficiary_name else ""
        text = (
            f"❌ *Consulta cancelada — MALU*\n\n"
            f"Paciente (contato): {name_display}\n"
            f"Telefone: {patient_phone}\n"
            f"CPF: {cpf}\n"
            f"{beneficiary_line}"
            f"Médico: {doctor_name}\n"
            f"Data/hora: {scheduled_at.strftime('%d/%m/%Y às %H:%M')}\n"
        )
        await self._send_to_contacts(targets, text)

    async def notify_prescription_request(
        self,
        *,
        patient_phone: str,
        patient_name: str | None = None,
    ) -> None:
        """
        Notifica médicos sobre solicitação de receita.
        """
        name_display = patient_name or patient_phone
        text = (
            f"📋 *Solicitação de receita — MALU*\n\n"
            f"Paciente: {name_display} ({patient_phone})\n"
            f"Aguardando análise da equipe."
        )
        await self._send_to_contacts(ALERT_CONTACTS, text)

    # ------------------------------------------------------------------
    # Envio interno
    # ------------------------------------------------------------------

    async def _send_to_contacts(
        self,
        contacts: list[TeamContact | None],
        text: str,
    ) -> None:
        for contact in contacts:
            if contact is None:
                continue
            try:
                response = await self._client.send_text(chat_id=contact.chat_id, text=text)
                # Registra o ID da mensagem enviada para que webhooks "from_me"
                # gerados por esta notificação não acionem o mecanismo de pausa de IA.
                if isinstance(response, dict):
                    sent_id = str(response.get("id") or response.get("messageId") or "")
                    if sent_id:
                        self._memory.track_bot_sent_id(contact.chat_id, sent_id)
                logger.info("notification_sent contact=%s role=%s", contact.name, contact.role)
            except Exception:
                logger.exception("notification_failed contact=%s", contact.name)
