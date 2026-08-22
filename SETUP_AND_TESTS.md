# Passo a passo para subir o projeto e testar no WhatsApp

## 1) Preparar o projeto
1. Copie `.env.example` para `.env`.
2. Preencha as chaves necessárias:
   - `WEBHOOK_HMAC_SECRET`
   - `WAHA_API_KEY`
   - ao menos uma entre `GEMINI_API_KEY`, `OPENAI_API_KEY` ou `ANTHROPIC_API_KEY`
   - `GOOGLE_SERVICE_ACCOUNT_JSON` se quiser integração com Google Calendar
3. Revise `FAQ_KB_PATH` e o arquivo `app/data/faq.md`.

## 2) Observação crítica de segurança
Se o arquivo `.env` real já foi exposto em captura de tela ou compartilhamento, trate todas as credenciais como comprometidas e gere novas chaves antes de publicar ou testar em ambiente externo.

## 3) Subir a stack com Docker
Não há `docker-compose.yml` padrão; use o `docker-compose.robust.yml`:
```bash
docker compose -f docker-compose.robust.yml up --build
```
O serviço `migrate` aplica as migrações Alembic (`alembic upgrade heads`) antes de `api`, `worker` e `beat`.

> Os demais comandos `docker compose` deste documento (`exec`, `logs`, `call`) assumem a mesma flag `-f docker-compose.robust.yml`.

Serviços esperados:
- API FastAPI em `http://localhost:8000`
- WAHA em `http://localhost:3001`
- MinIO em `http://localhost:9001`
- Postgres em `localhost:5432`
- Redis em `localhost:6379`

## 4) Validar saúde da aplicação
```bash
curl http://localhost:8000/
curl http://localhost:8000/api/health
```

## 5) Rodar testes automáticos
No container da API:
```bash
docker compose exec api pytest
```

Lint:
```bash
docker compose exec api ruff check app tests
```

## 6) Conectar o WhatsApp no WAHA
1. Abra `http://localhost:3001`.
2. Entre com o usuário e senha do dashboard do WAHA.
3. Localize a sessão configurada em `WAHA_SESSION`.
4. Faça o pareamento com QR Code.
5. Confirme que o webhook aponta para `http://api:8000/api/webhooks/waha` dentro da rede Docker.

## 7) Testes manuais no WhatsApp
Depois que a sessão estiver conectada, envie mensagens para o número conectado ao WAHA.

### Teste 1: FAQ
Mensagem:
```text
Qual o horário de atendimento?
```
Esperado:
- resposta vinda da base FAQ
- `llm_used` geralmente falso

### Teste 2: Agendamento
Mensagem:
```text
Quero agendar consulta em 28/03/2026 14:30 com Dr. Silva
```
Esperado:
- consulta criada no banco
- evento criado no Google Calendar se configurado
- resposta sem uso de LLM

### Teste 3: Triagem simples
Mensagem:
```text
Estou com febre e dor de garganta
```
Esperado:
- orientação conservadora
- sem diagnóstico
- sem uso de LLM na maioria dos casos

### Teste 4: Documento
Mensagem:
```text
Meu exame já está pronto?
```
Esperado:
- informar se há documento no cadastro
- sem inventar envio automático

### Teste 5: Feedback
Mensagem:
```text
Dou nota 5 para o atendimento
```
Esperado:
- feedback salvo no banco
- resposta curta e objetiva

## 8) Verificar logs
API:
```bash
docker compose logs -f api
```
Worker:
```bash
docker compose logs -f worker
```
Beat:
```bash
docker compose logs -f beat
```
WAHA:
```bash
docker compose logs -f waha
```

## 9) Testar lembretes automáticos
1. Crie uma consulta para aproximadamente 24 horas à frente.
2. Garanta que `worker` e `beat` estejam ativos.
3. Aguarde a janela de execução do beat ou dispare manualmente:
```bash
docker compose exec worker celery -A app.tasks.celery_app.celery_app call app.tasks.reminders.send_day_before_reminders
```
4. Confirme no WhatsApp o recebimento do lembrete.

## 10) Checagens importantes em caso de erro
- assinatura do webhook divergente
- sessão do WAHA desconectada
- chave do LLM ausente
- JSON do Google Service Account inválido
- banco criado, mas volume antigo sem as mudanças novas

## 11) Recomendações para produção
- garantir que o schema seja gerido apenas pelas migrations Alembic (serviço `migrate`), sem depender do `init_db()` de startup
- trocar rate limit em memória por Redis
- colocar observabilidade de custo por intent
- versionar o FAQ separadamente
- adicionar fila própria para envio de WhatsApp
