"""add google_event_id to consultas

Revision ID: 4a7b2c3d9e01
Revises: 3c9e1f4a8b02
Create Date: 2026-04-13 00:00:00.000000

A coluna google_event_id estava na migration inicial (108c5046d425) mas
não foi criada no banco em alguns ambientes (banco pré-existente ou
parcialmente inicializado). Esta migration adiciona a coluna com segurança
usando IF NOT EXISTS para não quebrar ambientes que já a possuem.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text


revision = "4a7b2c3d9e01"
down_revision = "3c9e1f4a8b02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD COLUMN IF NOT EXISTS — seguro para ambientes que já têm a coluna
    op.execute(text("ALTER TABLE consultas ADD COLUMN IF NOT EXISTS google_event_id VARCHAR(255)"))
    # Índice único parcial: NULL não é considerado duplicado (comportamento desejado)
    op.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_consultas_google_event_id "
        "ON consultas (google_event_id) WHERE google_event_id IS NOT NULL"
    ))


def downgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS uq_consultas_google_event_id"))
    op.execute(text("ALTER TABLE consultas DROP COLUMN IF EXISTS google_event_id"))
