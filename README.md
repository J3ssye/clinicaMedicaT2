# Clinica Chatbot

MVP funcional de um chatbot para clinica medica com:

- FastAPI para webhooks e API interna
- WAHA para integracao com WhatsApp (console exposto na porta 3001)
- LangGraph para orquestracao dos agentes
- Google Gemini como LLM
- PostgreSQL para persistencia
- Celery + Redis para lembretes automaticos
- MinIO para documentos
- Sincronizacao com Google Calendar

## Componentes

- `app/api`: rotas HTTP e webhook do WAHA
- `app/agents`: agentes de FAQ, triagem, agendamento, documentos e lembretes
- `app/orchestrator`: grafo de roteamento e estado de conversa
- `app/services`: integracoes externas
- `app/models`: modelos SQLAlchemy
- `app/tasks`: tarefas Celery

## Como subir

1. Copie `.env.example` para `.env`
2. Preencha `GEMINI_API_KEY`, `WEBHOOK_HMAC_SECRET` e `GOOGLE_SERVICE_ACCOUNT_JSON`
3. Execute `docker compose up --build` (se a porta 3000 ja estiver em uso, o WAHA sobe em 3001)

## Fluxos MVP

- Recepcao de mensagens do WhatsApp via webhook WAHA
- Identificacao de paciente por telefone
- Classificacao de intencao para FAQ, triagem, agendamento, documentos e feedback
- Agendamento em PostgreSQL com sincronizacao no Google Calendar
- Lembretes D-1 por Celery Beat
- Registro de mensagens e feedback

## Observacoes

- A triagem opera em modo conservador e sempre evita diagnostico.
- Documentos usam MinIO como storage S3-compativel.
- O projeto faz bootstrap do schema no startup para facilitar o MVP. Em producao, prefira migracoes Alembic.
