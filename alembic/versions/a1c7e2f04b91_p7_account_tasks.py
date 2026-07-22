"""P7 - account_tasks + pending_task_closes

Revision ID: a1c7e2f04b91
Revises: d833e1cdb4fa
Create Date: 2026-07-22 03:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c7e2f04b91'
down_revision: Union[str, Sequence[str], None] = 'd833e1cdb4fa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('account_tasks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('business_id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('details', sa.Text(), nullable=True),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('assigned_worker_id', sa.Integer(), nullable=True),
        sa.Column('supplies_needed', sa.Text(), nullable=True),
        sa.Column('due_date', sa.String(), nullable=True),
        sa.Column('source', sa.String(), nullable=True),
        sa.Column('created_by_worker_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.Column('closed_by_worker_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id']),
        sa.ForeignKeyConstraint(['assigned_worker_id'], ['workers.id']),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id']),
        sa.ForeignKeyConstraint(['closed_by_worker_id'], ['workers.id']),
        sa.ForeignKeyConstraint(['created_by_worker_id'], ['workers.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_account_tasks_scope', 'account_tasks',
                    ['business_id', 'account_id', 'status'])
    op.create_table('pending_task_closes',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('business_id', sa.Integer(), nullable=False),
        sa.Column('worker_id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id']),
        sa.ForeignKeyConstraint(['task_id'], ['account_tasks.id']),
        sa.ForeignKeyConstraint(['worker_id'], ['workers.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_pending_task_closes_worker', 'pending_task_closes',
                    ['worker_id', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_pending_task_closes_worker', table_name='pending_task_closes')
    op.drop_table('pending_task_closes')
    op.drop_index('ix_account_tasks_scope', table_name='account_tasks')
    op.drop_table('account_tasks')
