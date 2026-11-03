"""background jobs

Twelfth migration (backend API): background_jobs keeps the status, progress and result of slow work
(downloads, backtests, sweeps) started through the API (marketlens/api/jobs.py).

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-03 20:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0012'
down_revision: Union[str, Sequence[str], None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('background_jobs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('status', sa.String(), server_default='queued', nullable=False),
    sa.Column('progress', sa.Numeric(precision=5, scale=1), server_default=sa.text('0'), nullable=False),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('cancel_requested', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('queued', 'running', 'done', 'failed', 'cancelled')",
                       name=op.f('ck_background_jobs_status_valid')),
    sa.CheckConstraint('progress BETWEEN 0 AND 100', name=op.f('ck_background_jobs_progress_0_to_100')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_background_jobs'))
    )
    op.create_index(op.f('ix_background_jobs_created_at'), 'background_jobs', ['created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_background_jobs_created_at'), table_name='background_jobs')
    op.drop_table('background_jobs')
