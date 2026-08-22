"""add message processing_status

Revision ID: 3c9e1f4a8b02
Revises: 2b8f3c1a9d07
Create Date: 2026-04-10 00:00:00.000000

Adiciona a coluna `processing_status` à tabela `mensagens` para rastrear
o resultado do processamento no webhook.

Valores possíveis:
  "processed"          — mensagem processada normalmente pelo orquestrador
  "ignored_stale"      — mensagem muito antiga descartada sem resposta (backlog/replay)
  "ignored_duplicate"  — mensagem já processada anteriormente (idempotência)

Registros antigos ficam com NULL (equivalente a "processed" para fins históricos).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "3c9e1f4a8b02"
down_revision = "2b8f3c1a9d07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mensagens",
        sa.Column("processing_status", sa.String(length=20), nullable=True),
    )
    op.create_index(
        "ix_mensagens_processing_status",
        "mensagens",
        ["processing_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_mensagens_processing_status", table_name="mensagens")
    op.drop_column("mensagens", "processing_status")
