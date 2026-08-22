# Changelog

Todas as mudanças relevantes deste projeto são registradas neste arquivo.
O formato é baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/);
a versão atual do pacote está em `pyproject.toml`.

## [Não lançado]
### Alterado
- Documentação sincronizada com o código real: `README.md`, `.env.example` e `SETUP_AND_TESTS.md`.

### Removido
- Shims mortos em `app/services/` (`calendar.py`, `llm.py`, `storage.py`, `waha_client.py`) que apenas re-exportavam de `app/integrations/`. Nenhum código os consumia; as integrações passam a ser importadas diretamente de `app/integrations/`.

## [0.2.0]
### Adicionado
- Camadas `app/repositories/` (acesso a dados) e `app/use_cases/` (orquestração de casos de uso).
- Camada `app/integrations/` para integrações externas (Google Calendar, LLM, MinIO, WhatsApp/WAHA).
- Agentes de confirmação de consulta (D-1) e de acompanhamento de saúde (`health_followup`).
- Memória de conversa em Redis: histórico curto, saudação diária única e *cooldown* pós-fluxo proativo.
- Escalação para a equipe (`notification_service`): pedidos de receita, imagens de exame e solicitações de análise de documento.
- Transcrição de áudio recebido pelo paciente (modelo de áudio da OpenAI).
- Handover humano: a IA é pausada por `AI_HUMAN_PAUSE_HOURS` quando há atendimento manual no chat.
- Staleness gate (`MAX_MESSAGE_AGE_SECONDS`): mensagens antigas são registradas mas não geram resposta automática.
- Fluxos proativos via Celery Beat: confirmação D-1, feedback pós-consulta, follow-up de cancelamento e reengajamento.
- Migrações Alembic para a evolução do schema.

### Alterado
- Orquestração deixou de classificar a intenção por LLM e passou a usar **heurística local** (`ChatOrchestrator._classify_intent`), reduzindo chamadas ao LLM — que agora só atua no fallback e na geração opcional de FAQ.
- Roteador de LLM multi-provedor com failover na ordem `LLM_PROVIDER_ORDER` (Gemini → OpenAI → Anthropic).

### Removido
- Dependência `langgraph`: a orquestração passou a ser feita por heurística local, sem a biblioteca.

## [0.1.0]
### Adicionado
- Estrutura inicial do projeto: FastAPI, webhook do WAHA, agentes de FAQ/triagem/agendamento, modelos SQLAlchemy, tarefas Celery e infraestrutura Docker.
