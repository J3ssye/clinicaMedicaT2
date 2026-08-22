"""fidem sql only flow

Revision ID: 2fidem_sql_only_flow
Revises: f1beef3b2e5e
Create Date: 2026-04-09 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '2fidem_sql_only_flow'
down_revision = 'f1beef3b2e5e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('pacientes', sa.Column('cpf', sa.String(length=14), nullable=True))
    op.add_column('pacientes', sa.Column('birth_date', sa.Date(), nullable=True))
    op.add_column('pacientes', sa.Column('ai_paused', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('pacientes', sa.Column('paused_reason', sa.String(length=64), nullable=True))
    op.add_column('pacientes', sa.Column('ai_paused_at', sa.DateTime(), nullable=True))
    op.add_column('pacientes', sa.Column('ai_resume_at', sa.DateTime(), nullable=True))
    op.add_column('pacientes', sa.Column('resumed_at', sa.DateTime(), nullable=True))
    op.create_index(op.f('ix_pacientes_cpf'), 'pacientes', ['cpf'], unique=True)

    op.add_column('consultas', sa.Column('confirmation_status', sa.String(length=50), nullable=True))
    op.add_column('consultas', sa.Column('reminder_sent_at', sa.DateTime(), nullable=True))
    op.add_column('consultas', sa.Column('attended_at', sa.DateTime(), nullable=True))
    op.add_column('consultas', sa.Column('cancelled_at', sa.DateTime(), nullable=True))
    op.add_column('consultas', sa.Column('cancellation_reason', sa.Text(), nullable=True))
    op.add_column('consultas', sa.Column('cancellation_followup_sent_at', sa.DateTime(), nullable=True))
    op.add_column('consultas', sa.Column('post_consult_followup_sent_at', sa.DateTime(), nullable=True))
    op.add_column('consultas', sa.Column('reengagement_sent_at', sa.DateTime(), nullable=True))
    op.add_column('consultas', sa.Column('scheduling_failure_reason', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('consultas', 'scheduling_failure_reason')
    op.drop_column('consultas', 'reengagement_sent_at')
    op.drop_column('consultas', 'post_consult_followup_sent_at')
    op.drop_column('consultas', 'cancellation_followup_sent_at')
    op.drop_column('consultas', 'cancellation_reason')
    op.drop_column('consultas', 'cancelled_at')
    op.drop_column('consultas', 'attended_at')
    op.drop_column('consultas', 'reminder_sent_at')
    op.drop_column('consultas', 'confirmation_status')

    op.drop_index(op.f('ix_pacientes_cpf'), table_name='pacientes')
    op.drop_column('pacientes', 'resumed_at')
    op.drop_column('pacientes', 'ai_resume_at')
    op.drop_column('pacientes', 'ai_paused_at')
    op.drop_column('pacientes', 'paused_reason')
    op.drop_column('pacientes', 'ai_paused')
    op.drop_column('pacientes', 'birth_date')
    op.drop_column('pacientes', 'cpf')
