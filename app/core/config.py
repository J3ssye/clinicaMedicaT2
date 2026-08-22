from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = Field(default="clinica-chatbot", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    app_secret_key: str = Field(default="change-me", alias="APP_SECRET_KEY")
    webhook_hmac_secret: str = Field(default="", alias="WEBHOOK_HMAC_SECRET")
    require_webhook_signature: bool = Field(default=True, alias="REQUIRE_WEBHOOK_SIGNATURE")
    api_rate_limit_per_minute: int = Field(default=60, alias="API_RATE_LIMIT_PER_MINUTE")

    database_url: str = Field(default="postgresql+psycopg://clinica:clinica@postgres:5432/clinica", alias="DATABASE_URL")
    redis_url: str = Field(default="redis://redis:6379/0", alias="REDIS_URL")
    celery_broker_url: str = Field(default="redis://redis:6379/1", alias="CELERY_BROKER_URL")
    celery_result_backend: str = Field(default="redis://redis:6379/2", alias="CELERY_RESULT_BACKEND")

    waha_base_url: str = Field(default="http://waha:3000", alias="WAHA_BASE_URL")
    waha_session: str = Field(default="default", alias="WAHA_SESSION")
    waha_api_key: str | None = Field(default=None, alias="WAHA_API_KEY")
    waha_mark_as_seen_before_reply: bool = Field(default=True, alias="WAHA_MARK_AS_SEEN_BEFORE_REPLY")
    waha_ignore_group_messages: bool = Field(default=True, alias="WAHA_IGNORE_GROUP_MESSAGES")
    waha_ignore_status_messages: bool = Field(default=True, alias="WAHA_IGNORE_STATUS_MESSAGES")
    waha_ignore_own_messages: bool = Field(default=False, alias="WAHA_IGNORE_OWN_MESSAGES")
    waha_process_events: str = Field(default="message.any", alias="WAHA_PROCESS_EVENTS")
    waha_message_soft_limit: int = Field(default=1500, alias="WAHA_MESSAGE_SOFT_LIMIT")
    waha_message_hard_limit: int = Field(default=2000, alias="WAHA_MESSAGE_HARD_LIMIT")
    waha_chunk_delay_ms: int = Field(default=350, alias="WAHA_CHUNK_DELAY_MS")

    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_MODEL")
    openai_audio_model: str = Field(default="gpt-4o-mini-transcribe", alias="OPENAI_AUDIO_MODEL")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-3-5-sonnet-latest", alias="ANTHROPIC_MODEL")
    llm_provider_order: str = Field(default="gemini,openai,anthropic", alias="LLM_PROVIDER_ORDER")
    llm_http_timeout_seconds: float = Field(default=20.0, alias="LLM_HTTP_TIMEOUT_SECONDS")
    llm_max_output_tokens: int = Field(default=1800, alias="LLM_MAX_OUTPUT_TOKENS")
    llm_classify_max_output_tokens: int = Field(default=16, alias="LLM_CLASSIFY_MAX_OUTPUT_TOKENS")
    llm_history_max_messages: int = Field(default=30, alias="LLM_HISTORY_MAX_MESSAGES")
    llm_enable_classification_fallback: bool = Field(default=False, alias="LLM_ENABLE_CLASSIFICATION_FALLBACK")
    llm_enable_faq_generation_fallback: bool = Field(default=True, alias="LLM_ENABLE_FAQ_GENERATION_FALLBACK")
    faq_cache_ttl_seconds: int = Field(default=900, alias="FAQ_CACHE_TTL_SECONDS")
    conversation_memory_ttl_seconds: int = Field(default=86_400, alias="CONVERSATION_MEMORY_TTL_SECONDS")
    conversation_memory_history_limit: int = Field(default=100, alias="CONVERSATION_MEMORY_HISTORY_LIMIT")

    clinic_assistant_system_prompt: str = Field(
        default=(
            "Você é MALU, secretária virtual da FIDEM, em nome da Dra. Vitória Cunha e do Dr. Lucas Da Costa Cirilo. "
            "Fale sempre em português do Brasil, com acolhimento, clareza, objetividade e foco comercial ético. "
            "Seu papel é captar, esclarecer, sugerir próximos passos e converter para consulta presencial, sem prometer resultado, sem sensacionalismo e sem pressão agressiva. "
            "Marca: use FIDEM como referência institucional. Cite 'Clínica Médica Valéria Frota' somente quando o paciente perguntar o endereço/local ou em lembretes com localização. "
            "Regra de idade: os médicos não realizam atendimento para pacientes com menos de 8 anos. Se a data de nascimento indicar menos de 8 anos, não finalize o agendamento — informe que o caso será encaminhado para a equipe. "
            "Para pacientes menores de idade (8-17 anos), solicite confirmação de responsável quando relevante. "
            "Nomes dos médicos: cite Dr. Lucas Da Costa Cirilo e Dra. Vitória Cunha quando agregar valor clínico ou comercial (ex.: apresentar especialidades, confirmar consulta, avaliações, pós-consulta). Não repita os nomes em despedidas, retomadas simples ou mensagens de ajuste. "
            "Evite respostas curtas sem fechamento, respostas quebradas e frases como 'recapitulando' ou 'conforme informado anteriormente'. Seja completa e objetiva em uma única mensagem sempre que possível. "
            "Nunca diga que usa Google Calendar ou agenda externa. Toda a agenda é interna e confirmada pelo sistema. "
            "Só considere um agendamento concluído quando houver CPF válido e data de nascimento válida. "
            "Para pedidos de receita, imagens, exceções de agenda e casos que precisem análise humana, informe que vai encaminhar para a equipe e que retornarão o mais breve possível. "
            "Nunca interprete imagem clínica de forma conclusiva e nunca prometa emissão imediata de receita. "
            "Convênio e plano de saúde: as consultas são exclusivamente particulares (R$ 600,00 cada médico; pacote com os dois: R$ 800,00). Nenhum dos médicos aceita convênio para consultas. Porém, durante a consulta é possível solicitar pedidos de exames via convênio — informe isso se o paciente perguntar. NUNCA mencione convênio proativamente. "
            "Sempre que possível, feche a resposta com um próximo passo objetivo."
        ),
        alias="CLINIC_ASSISTANT_SYSTEM_PROMPT",
    )

    clinic_secretary_name: str = Field(default="Malu", alias="CLINIC_SECRETARY_NAME")
    clinic_name: str = Field(default="Clínica Médica Valéria Frota", alias="CLINIC_NAME")
    clinic_company_name: str = Field(default="FIDEM", alias="CLINIC_COMPANY_NAME")
    clinic_address: str = Field(default="Setor Aeroporto, Rua 9A, 160, Goiânia - GO", alias="CLINIC_ADDRESS")
    clinic_timezone: str = Field(default="America/Sao_Paulo", alias="CLINIC_TIMEZONE")
    default_appointment_duration_minutes: int = Field(default=30, alias="DEFAULT_APPOINTMENT_DURATION_MINUTES")
    google_review_link: str | None = Field(default=None, alias="GOOGLE_REVIEW_LINK")
    google_review_link_vitoria: str | None = Field(default=None, alias="GOOGLE_REVIEW_LINK_VITORIA")
    google_review_link_lucas: str | None = Field(default=None, alias="GOOGLE_REVIEW_LINK_LUCAS")
    ai_human_pause_hours: int = Field(default=3, alias="AI_HUMAN_PAUSE_HOURS")

    # Staleness gate: mensagens recebidas pelo webhook com idade superior a este
    # limite (em segundos) NÃO geram resposta automática — apenas são registradas
    # no histórico com status "ignored_stale".
    # 300 s (5 min) cobre qualquer atraso normal de rede/webhook sem aceitar
    # backlog de pausas longas ou replays após reinicialização.
    # Ajustável via variável de ambiente MAX_MESSAGE_AGE_SECONDS.
    max_message_age_seconds: int = Field(default=300, alias="MAX_MESSAGE_AGE_SECONDS")

    minio_endpoint: str = Field(default="minio:9000", alias="MINIO_ENDPOINT")
    minio_access_key: str = Field(default="minioadmin", alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(default="minioadmin", alias="MINIO_SECRET_KEY")
    minio_bucket: str = Field(default="clinica-documentos", alias="MINIO_BUCKET")
    minio_secure: bool = Field(default=False, alias="MINIO_SECURE")

    faq_kb_path: str = Field(default="app/data/faq.md", alias="FAQ_KB_PATH")

    @field_validator("waha_message_soft_limit")
    @classmethod
    def _validate_waha_message_soft_limit(cls, value: int) -> int:
        return min(max(value, 500), 3500)

    @field_validator("waha_message_hard_limit")
    @classmethod
    def _validate_waha_message_hard_limit(cls, value: int) -> int:
        return min(max(value, 800), 4096)

    @field_validator("waha_chunk_delay_ms")
    @classmethod
    def _validate_waha_chunk_delay_ms(cls, value: int) -> int:
        return min(max(value, 0), 5000)

    @field_validator("llm_provider_order")
    @classmethod
    def _normalize_provider_order(cls, value: str) -> str:
        items = [item.strip().lower() for item in value.split(",") if item.strip()]
        return ",".join(dict.fromkeys(items)) or "gemini,openai,anthropic"

    @field_validator("waha_process_events")
    @classmethod
    def _normalize_waha_events(cls, value: str) -> str:
        items = [item.strip().lower() for item in value.split(",") if item.strip()]
        return ",".join(dict.fromkeys(items)) or "message.any"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
