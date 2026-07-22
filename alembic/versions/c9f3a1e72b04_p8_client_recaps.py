"""P8 - client recaps: Account recap cols + recap_log table

Revision ID: c9f3a1e72b04
Revises: a1c7e2f04b91
Create Date: 2026-07-22 05:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9f3a1e72b04'
down_revision: Union[str, Sequence[str], None] = 'a1c7e2f04b91'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('accounts', sa.Column('recap_enabled', sa.Boolean(), nullable=True,
                                        server_default=sa.text('false')))
    op.add_column('accounts', sa.Column('recap_email', sa.String(), nullable=True))
    op.add_column('accounts', sa.Column('recap_auto_send', sa.Boolean(), nullable=True,
                                        server_default=sa.text('false')))
    op.create_table('recap_log',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('business_id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('service_log_id', sa.Integer(), nullable=False),
        sa.Column('source_log_ids', sa.Text(), nullable=True),
        sa.Column('source_text', sa.Text(), nullable=True),
        sa.Column('client_text', sa.Text(), nullable=True),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('channel', sa.String(), nullable=True),
        sa.Column('approved_by_worker_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id']),
        sa.ForeignKeyConstraint(['approved_by_worker_id'], ['workers.id']),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id']),
        sa.ForeignKeyConstraint(['service_log_id'], ['service_logs.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_recap_log_scope', 'recap_log',
                    ['business_id', 'account_id', 'status'])


def downgrade() -> None:
    op.drop_index('ix_recap_log_scope', table_name='recap_log')
    op.drop_table('recap_log')
    op.drop_column('accounts', 'recap_auto_send')
    op.drop_column('accounts', 'recap_email')
    op.drop_column('accounts', 'recap_enabled')
