"""FN-1 - add jev_model_version to parse_shadow_logs

Revision ID: a1b2c3d4e5f6
Revises: f2a9c4e81d36
Create Date: 2026-09-23 00:00:00.000000

Adds the resolved model version (TypeSafe returns it at the top level of
every /v1/systemone response, e.g. "jev-1.13.0") so the shadow log records
which model actually produced each parse — the basis for model-version
pinning + drift detection (the HFT/Jev manual's "pin + log per row" rule).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f2a9c4e81d36'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'parse_shadow_logs',
        sa.Column('jev_model_version', sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('parse_shadow_logs', 'jev_model_version')
