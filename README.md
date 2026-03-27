# Chatbot da Clínica Médica (WhatsApp + IA)

Assistente de atendimento para WhatsApp com triagem, FAQ e agendamento, usando FastAPI, LangGraph e LLM com failover entre Gemini, OpenAI e Anthropic, além de integrações para calendário, storage e lembretes.

## Arquitetura resumida
- **FastAPI** (`app/main.py`): Webhooks WAHA e API interna.
- **LangGraph** (`app/orchestrator/graph.py`): roteia a conversa para os agentes.
- **Agentes** (`app/agents/*`): FAQ, triagem, agendamento, documentos, feedback.
- **LLM** (`app/services/llm.py`): roteador multi-provedor com failover entre Gemini, OpenAI e Anthropic, retries e logs por provedor.
- **Infra**: PostgreSQL (dados), Redis (fila/locks), Celery + Beat (lembretes), MinIO (S3), Google Calendar, WAHA (WhatsApp).

## Fluxo de mensagens
1. WAHA entrega o webhook em `POST /api/webhooks/waha`.
2. Orquestrador classifica intenção (LLM) → agente específico.
3. Resposta gravada em `messages` e enviada pelo WAHA.
4. Lembretes D-1 de consulta via Celery Beat.

## Variáveis de ambiente principais (.env)
| Chave | Descrição |
| --- | --- |
| GEMINI_API_KEY | Chave do Gemini. |
| GEMINI_MODEL | ex: `gemini-2.5-flash` |
| OPENAI_API_KEY | Chave da OpenAI para primeiro ou segundo failover. |
| OPENAI_MODEL | ex: `gpt-4.1-mini` |
| ANTHROPIC_API_KEY | Chave da Anthropic para failover adicional. |
| ANTHROPIC_MODEL | ex: `claude-3-5-sonnet-latest` |
| LLM_PROVIDER_ORDER | Ordem de tentativa, ex: `gemini,openai,anthropic` |
| WEBHOOK_HMAC_SECRET | HMAC para validar webhooks WAHA |
| WAHA_API_KEY | Token para o dashboard/API do WAHA |
| GOOGLE_SERVICE_ACCOUNT_JSON | JSON inteiro (uma linha) da service account do Calendar |
| GOOGLE_CALENDAR_ID | ID do calendário alvo |
| FAQ_KB_PATH | Caminho do markdown de FAQ (padrão `app/data/faq.md`) |
| DATABASE_URL | Postgres; default em docker-compose |
| REDIS_URL | Redis; default em docker-compose |

Copie `.env.example` para `.env` e preencha as chaves acima.

## Subir com Docker (recomendado)
```bash
docker compose up --build
```
- API: http://localhost:8000
- WAHA dashboard: http://localhost:3001 (usa WAHA_DASHBOARD_* se setado)
- MinIO console: http://localhost:9001 (login em `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`)

## Rodar local (sem Docker)
Requisitos: Python 3.11, Postgres e Redis acessíveis.
```bash
python -m venv .venv
.venv/Scripts/activate  # Windows
pip install --upgrade pip
pip install -e .
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Para tarefas assíncronas:
```bash
celery -A app.tasks.celery_app.celery_app worker --loglevel=INFO
celery -A app.tasks.celery_app.celery_app beat --loglevel=INFO
```

## Logs e observabilidade
- Logging básico ativado em `app/main.py` (stdout, nível INFO).
- `app/services/llm.py` registra: provedor usado, latência, tamanho da resposta, metadata de retorno e tentativas de retry/failover.
- Verifique quedas para fallback rastreando intents `fallback`/`fallback_error` em `messages`.

## Testes rápidos
```bash
pytest
```
(ajuste variáveis de ambiente ou use um `.env` de teste).

## FAQ operacional
- **Porta do WAHA em uso**: altere mapeamento `3001:3000` no `docker-compose.yml`.
- **Respostas vazias da IA**: consulte os logs `llm_response` e `llm_provider_failed` para identificar qual provedor falhou e qual assumiu o failover.
- **Timeouts WhatsApp**: aumente timeout do worker/uvicorn para ≥8s e habilite streaming no LLM se necessário.

## Estrutura de pastas
- `app/api` rotas/webhooks
- `app/agents` lógica de domínio por intenção
- `app/orchestrator` grafo de roteamento
- `app/services` integrações (LLM, calendário, storage, WAHA)
- `app/models` modelos SQLAlchemy
- `app/tasks` tarefas Celery
- `alembic` migrações (opcional; init_db roda no startup)

## Ordem de failover
O roteador tenta os provedores na ordem definida em `LLM_PROVIDER_ORDER`. Se um provedor falhar por quota, autenticacao, permissao ou resposta vazia, o proximo assume automaticamente.
