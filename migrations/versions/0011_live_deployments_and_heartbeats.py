"""live deployments and service heartbeats

Eleventh migration (Live Algo Trading & Bot Monitor, module 11):
- live_deployments: strategies deployed on page 11 (settings, Start / Pause / Stop, today's live view).
- service_heartbeats: "I am alive" from each background process (live runner, later the bot and scheduler).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-03 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0011'
down_revision: Union[str, Sequence[str], None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('live_deployments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('strategy_id', sa.Integer(), nullable=False),
    sa.Column('mode', sa.String(), server_default='paper', nullable=False),
    sa.Column('status', sa.String(), server_default='running', nullable=False),
    sa.Column('lots_multiplier', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('max_loss_rs', sa.Numeric(precision=16, scale=2), nullable=True),
    sa.Column('confirm', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('capital', sa.Numeric(precision=16, scale=2), nullable=True),
    sa.Column('trade_date', sa.Date(), nullable=True),
    sa.Column('state', sa.String(), nullable=True),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('pnl', sa.Numeric(precision=16, scale=2), nullable=True),
    sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('stopped_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("mode IN ('paper', 'live')", name=op.f('ck_live_deployments_mode_valid')),
    sa.CheckConstraint("status IN ('running', 'paused', 'stopping', 'stopped')",
                       name=op.f('ck_live_deployments_status_valid')),
    sa.CheckConstraint('lots_multiplier >= 1', name=op.f('ck_live_deployments_lots_multiplier_positive')),
    sa.ForeignKeyConstraint(['strategy_id'], ['strategies.id'],
                            name=op.f('fk_live_deployments_strategy_id_strategies'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_live_deployments')),
    sa.UniqueConstraint('strategy_id', name=op.f('uq_live_deployments_strategy_id'))
    )
    op.create_table('service_heartbeats',
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('pid', sa.Integer(), nullable=True),
    sa.Column('beat_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('message', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('name', name=op.f('pk_service_heartbeats'))
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('service_heartbeats')
    op.drop_table('live_deployments')
