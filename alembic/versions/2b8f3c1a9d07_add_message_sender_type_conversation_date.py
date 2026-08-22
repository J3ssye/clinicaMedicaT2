"""add message sender_type and conversation_date

Revision ID: 2b8f3c1a9d07
Revises: 108c5046d425
Create Date: 2026-04-10 00:00:00.000000

Amplia a tabela `mensagens` com dois campos para melhorar rastreabilidade
e eficiência nas queries de contexto diário por paciente:

- sender_type: classifica o remetente ("patient" | "bot" | "human_agent")
- conversation_date: data local da mensagem, indexada para queries por dia
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "2b8f3c1a9d07"
down_revision = "2fidem_sql_only_flow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Adiciona coluna sender_type (nullable para compatibilidade com registros antigos)
    op.add_column(
        "mensagens",
        sa.Column("sender_type", sa.String(length=20), nullable=True),
    )
    op.create_index(
        "ix_mensagens_sender_type",
        "mensagens",
        ["sender_type"],
        unique=False,
    )

    # Adiciona coluna conversation_date (nullable para compatibilidade com registros antigos)
    op.add_column(
        "mensagens",
        sa.Column("conversation_date", sa.Date(), nullable=True),
    )
    op.create_index(
        "ix_mensagens_patient_date",
        "mensagens",
        ["patient_id", "conversation_date"],
        unique=False,
    )

    # Preenche sender_type para registros existentes com base em direction e intent
    op.execute(
        """
        UPDATE mensagens
        SET sender_type = CASE
            WHEN direction = 'inbound' THEN 'patient'
            WHEN intent = 'human_takeover' THEN 'human_agent'
            ELSE 'bot'
        END
        WHERE sender_type IS NULL
        """
    )

    # Preenche conversation_date para registros existentes
    op.execute(
        """
        UPDATE mensagens
        SET conversation_date = DATE(created_at)
        WHERE conversation_date IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_mensagens_patient_date", table_name="mensagens")
    op.drop_column("mensagens", "conversation_date")
    op.drop_index("ix_mensagens_sender_type", table_name="mensagens")
    op.drop_column("mensagens", "sender_type")
