"""add beneficiary fields to appointments

Revision ID: 6b4c2d3e0f05
Revises: 5a3b1c2d0f05
Create Date: 2026-05-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "6b4c2d3e0f05"
down_revision = "5a3b1c2d0f05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("consultas", sa.Column("beneficiary_name", sa.String(255), nullable=True))
    op.add_column("consultas", sa.Column("beneficiary_cpf", sa.String(11), nullable=True))
    op.add_column("consultas", sa.Column("beneficiary_birth_date", sa.Date(), nullable=True))
    op.add_column(
        "consultas",
        sa.Column("is_third_party", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_consultas_beneficiary_cpf", "consultas", ["beneficiary_cpf"])


def downgrade() -> None:
    op.drop_index("ix_consultas_beneficiary_cpf", table_name="consultas")
    op.drop_column("consultas", "is_third_party")
    op.drop_column("consultas", "beneficiary_birth_date")
    op.drop_column("consultas", "beneficiary_cpf")
    op.drop_column("consultas", "beneficiary_name")
