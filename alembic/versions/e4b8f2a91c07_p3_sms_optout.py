"""P3 - SMS channel: Worker.sms_opted_out

Revision ID: e4b8f2a91c07
Revises: c9f3a1e72b04
Create Date: 2026-07-24 03:00:00.000000

STOP compliance needs a marker distinct from is_active: a pending SMS invite
is also is_active=False, and without this flag a STOPped number would get the
"reply YES to join" prompt on their next text — a 10DLC violation.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4b8f2a91c07'
down_revision: Union[str, Sequence[str], None] = 'c9f3a1e72b04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('workers', sa.Column('sms_opted_out', sa.Boolean(), nullable=True,
                                       server_default=sa.text('false')))


def downgrade() -> None:
    op.drop_column('workers', 'sms_opted_out')
