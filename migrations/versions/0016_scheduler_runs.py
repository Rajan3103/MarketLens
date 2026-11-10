"""scheduler runs

Sixteenth migration (scheduler): scheduler_runs keeps every run of a scheduled job (when, ok / failed /
skipped, and a short result) for the Settings page.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-10 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0016'
down_revision: Union[str, Sequence[str], None] = '0015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('scheduler_runs',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('job_id', sa.String(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(), server_default='running', nullable=False),
    sa.Column('message', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('running', 'ok', 'failed', 'skipped')", name=op.f('ck_scheduler_runs_status_valid')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_scheduler_runs'))
    )
    op.create_index(op.f('ix_scheduler_runs_job_id'), 'scheduler_runs', ['job_id'], unique=False)
    op.create_index('ix_scheduler_runs_job_started', 'scheduler_runs', ['job_id', 'started_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_scheduler_runs_job_started', table_name='scheduler_runs')
    op.drop_index(op.f('ix_scheduler_runs_job_id'), table_name='scheduler_runs')
    op.drop_table('scheduler_runs')
