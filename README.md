# Chatbot da Clínica Médica (WhatsApp + IA)

Assistente de atendimento para WhatsApp (WAHA) que responde pacientes com FAQ, triagem, agendamento, confirmação, acompanhamento e feedback. A arquitetura foi organizada para **reduzir chamadas ao LLM**: a intenção da mensagem é classificada por heurística local e o LLM só é acionado quando realmente necessário (fallback e geração opcional de FAQ).

## Arquitetura resumida
- **FastAPI** (`app/main.py`): expõe o webhook do WAHA e uma API interna.
- **Orquestrador** (`app/orchestrator/graph.py`): classe `ChatOrchestrator` que classifica a intenção por **heurística local** (`_classify_intent`) e roteia para o agente adequado. Não usa LangGraph.
- **Agentes** (`app/agents/*`): FAQ, triagem, agendamento, documentos, feedback, confirmação (D-1) e acompanhamento de saúde.
- **Casos de uso** (`app/use_cases/*`): `handle_incoming_webhook` (pipeline de mensagem recebida) e `handle_chat_message`.
- **LLM** (`app/integrations/llm.py`): roteador multi-provedor com failover entre Gemini, OpenAI e Anthropic, na ordem definida em `LLM_PROVIDER_ORDER`.
- **Infra**: PostgreSQL (dados, SQLAlchemy async), Redis (memória de conversa/locks + broker Celery), Celery + Beat (fluxos proativos), MinIO (S3), Google Calendar, WAHA (WhatsApp).

## Fluxo de mensagens
1. WAHA entrega o webhook em `POST /api/webhooks/waha`.
2. `HandleIncomingWebhookUseCase` identifica/cria o paciente, deduplica a mensagem, transcreve áudio e encaminha imagem de exame para a equipe quando houver.
3. `ChatOrchestrator` classifica a intenção por heurística e chama o agente correspondente (LLM só no fallback).
4. A resposta é gravada em `messages` e enviada pelo WAHA.
5. Fluxos proativos (confirmação D-1, feedback pós-consulta, acompanhamento e reengajamento) são disparados por **Celery Beat**.

### Comportamentos relevantes
- **Handover humano**: mensagens enviadas pelo próprio número (`from_me`) pausam a IA por `AI_HUMAN_PAUSE_HOURS` horas.
- **Memória de conversa em Redis**: histórico curto, saudação diária única e *cooldown* pós-fluxo proativo.
- **Staleness gate**: mensagens com idade acima de `MAX_MESSAGE_AGE_SECONDS` são registradas mas não geram resposta automática.
- **Escalação para a equipe** (`app/services/notification_service.py`): pedidos de receita, imagens de exame e solicitações de análise de documento são notificados à equipe.

## Endpoints
| Método | Rota | Descrição |
| --- | --- | --- |
| GET | `/` | Status do serviço. |
| GET | `/api/health` | Healthcheck. |
| POST | `/api/chat` | API interna de teste do fluxo de conversa. |
| POST | `/api/webhooks/waha` | Webhook de mensagens do WAHA. |

## Variáveis de ambiente
As configurações são lidas por `app/core/config.py` a partir do `.env`. Copie `.env.example` para `.env` e preencha as chaves. Principais grupos:

| Grupo | Chaves (exemplos) |
| --- | --- |
| Aplicação | `APP_ENV`, `APP_PORT`, `APP_SECRET_KEY`, `WEBHOOK_HMAC_SECRET`, `REQUIRE_WEBHOOK_SIGNATURE`, `API_RATE_LIMIT_PER_MINUTE`, `MAX_MESSAGE_AGE_SECONDS` |
| Banco/Cache | `DATABASE_URL`, `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` |
| WAHA | `WAHA_BASE_URL`, `WAHA_SESSION`, `WAHA_API_KEY`, `WAHA_PROCESS_EVENTS`, `WAHA_MESSAGE_SOFT_LIMIT`, `WAHA_MESSAGE_HARD_LIMIT`, `WAHA_CHUNK_DELAY_MS` |
| LLM | `GEMINI_API_KEY`/`GEMINI_MODEL`, `OPENAI_API_KEY`/`OPENAI_MODEL`, `OPENAI_AUDIO_MODEL`, `ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL`, `LLM_PROVIDER_ORDER`, `LLM_HISTORY_MAX_MESSAGES` |
| Clínica | `CLINIC_NAME`, `CLINIC_COMPANY_NAME`, `CLINIC_ADDRESS`, `CLINIC_TIMEZONE`, `CLINIC_ASSISTANT_SYSTEM_PROMPT`, `AI_HUMAN_PAUSE_HOURS`, `DEFAULT_APPOINTMENT_DURATION_MINUTES` |
| Google Calendar | `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_CALENDAR_ID` |
| MinIO | `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET`, `MINIO_SECURE` |
| FAQ | `FAQ_KB_PATH` (padrão `app/data/faq.md`), `FAQ_CACHE_TTL_SECONDS` |

> A lista completa e comentada está em `.env.example`.

## Subir com Docker 
Não há `docker-compose.yml` padrão no repositório; use o arquivo `docker-compose.robust.yml`:
```bash
docker compose -f docker-compose.robust.yml up --build
```
Serviços e portas:
- API: http://localhost:8000
- WAHA dashboard: http://localhost:3001
- MinIO console: http://localhost:9001 (S3 em 9000)
- PostgreSQL: localhost:5432 · Redis: localhost:6379

O serviço `migrate` aplica as migrações Alembic (`alembic upgrade heads`) antes de subir `api`, `worker` e `beat`.

## Rodar local (sem Docker)
Requisitos: Python 3.11, PostgreSQL e Redis acessíveis.
```bash
python -m venv .venv
.venv/Scripts/activate  # Windows
pip install --upgrade pip
pip install -e .[dev]
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Tarefas assíncronas (fluxos proativos):
```bash
celery -A app.tasks.celery_app.celery_app worker --loglevel=INFO --pool=solo
celery -A app.tasks.celery_app.celery_app beat --loglevel=INFO
```

## Logs e observabilidade
- Logging configurado em `app/core/logging.py` (stdout).
- O orquestrador loga por sessão a intenção classificada e o fluxo ativo.
- `app/integrations/llm.py` registra o provedor usado e tentativas de failover.

## Testes
```bash
pytest
```
Suíte em `tests/` (health, orquestrador, agendamento/calendário, lembretes, webhook WAHA). Lint:
```bash
ruff check app tests
```

## Estrutura de pastas
- `app/api` — rotas e webhook (`chat`, `webhooks`, `routes`)
- `app/agents` — lógica de domínio por intenção
- `app/orchestrator` — orquestrador heurístico (`graph`, `state`)
- `app/use_cases` — orquestração de casos de uso (webhook, chat)
- `app/repositories` — acesso a dados (paciente, consulta, mensagem, documento, feedback)
- `app/integrations` — integrações externas (Google Calendar, LLM, MinIO, WAHA)
- `app/services` — serviços auxiliares (memória Redis, notificações, mídia/áudio, buffer)
- `app/models` — modelos SQLAlchemy
- `app/tasks` — Celery (`celery_app`, `reminders`)
- `app/core` — configuração, logging, segurança (HMAC), rate limit
- `app/db` — engine/sessão async e `init_db`
- `app/schemas` — schemas Pydantic (`chat`, `events`)
- `app/data` — base de conhecimento do FAQ (`faq.md`)
- `alembic` — migrações de schema

## Status
Em desenvolvimento. Sem deploy público configurado no repositório.

Histórico de mudanças em [`CHANGELOG.md`](CHANGELOG.md).

## Passo a passo de setup e testes manuais no WhatsApp
Ver [`SETUP_AND_TESTS.md`](SETUP_AND_TESTS.md).
