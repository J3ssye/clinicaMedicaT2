"""add appointment_id to feedbacks

Revision ID: 5a3b1c2d0f05
Revises: 4a7b2c3d9e01
Create Date: 2026-05-06 00:00:00.000000

Vincula cada registro de feedback à consulta que o originou.
Permite rastrear avaliações pós-consulta e relacionar nota com médico/data.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text


revision = "5a3b1c2d0f05"
down_revision = "4a7b2c3d9e01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text(
        "ALTER TABLE feedbacks ADD COLUMN IF NOT EXISTS appointment_id INTEGER "
        "REFERENCES consultas(id) ON DELETE SET NULL"
    ))
    op.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_feedbacks_appointment_id ON feedbacks (appointment_id)"
    ))


def downgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS ix_feedbacks_appointment_id"))
    op.execute(text("ALTER TABLE feedbacks DROP COLUMN IF EXISTS appointment_id"))
