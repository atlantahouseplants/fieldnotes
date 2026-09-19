"""FN-1 - parse_shadow_logs (Jev shadow-mode comparison log)

Revision ID: f2a9c4e81d36
Revises: e4b8f2a91c07
Create Date: 2026-09-18 00:00:00.000000

Phase 1 of the Jev parse-chain integration: when PARSER_BACKEND=shadow,
every note runs through BOTH the current LLM chain and the Jev adapter.
This table stores the comparison — Jev's result, latency, token usage,
and agreement flags — alongside the current parser's result. Purely
additive/observational; nothing here changes user-facing behavior.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a9c4e81d36'
down_revision: Union[str, Sequence[str], None] = 'e4b8f2a91c07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('parse_shadow_logs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('business_id', sa.Integer(), nullable=True),
        sa.Column('worker_id', sa.Integer(), nullable=True),
        sa.Column('service_log_id', sa.Integer(), nullable=True),
        sa.Column('raw_note', sa.Text(), nullable=False),
        sa.Column('current_result', sa.Text(), nullable=True),
        sa.Column('jev_result', sa.Text(), nullable=True),
        sa.Column('jev_error', sa.Text(), nullable=True),
        sa.Column('jev_skipped_reason', sa.String(), nullable=True),
        sa.Column('jev_latency_ms', sa.Integer(), nullable=True),
        sa.Column('jev_input_tokens', sa.Integer(), nullable=True),
        sa.Column('jev_output_tokens', sa.Integer(), nullable=True),
        sa.Column('account_agree', sa.Boolean(), nullable=True),
        sa.Column('status_agree', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id']),
        sa.ForeignKeyConstraint(['worker_id'], ['workers.id']),
        sa.ForeignKeyConstraint(['service_log_id'], ['service_logs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_parse_shadow_logs_business_created', 'parse_shadow_logs',
                     ['business_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_parse_shadow_logs_business_created', table_name='parse_shadow_logs')
    op.drop_table('parse_shadow_logs')
