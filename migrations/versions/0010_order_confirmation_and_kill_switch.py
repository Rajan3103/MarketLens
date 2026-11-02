"""order confirmation and kill switch

Tenth migration (order service, module 11):
- orders.status may be WAITING_CONFIRMATION (a live order waiting for your Telegram Confirm / Reject).
- kill_switch_events: each time the kill switch is switched on or off.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-02 18:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0010'
down_revision: Union[str, Sequence[str], None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_STATUSES = "'PENDING', 'PLACED', 'PARTIAL', 'FILLED', 'REJECTED', 'CANCELLED'"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('kill_switch_events',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('mode', sa.String(), server_default='paper', nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('squared_off', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.CheckConstraint("mode IN ('paper', 'live')", name=op.f('ck_kill_switch_events_mode_valid')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_kill_switch_events'))
    )
    op.create_index(op.f('ix_kill_switch_events_ts'), 'kill_switch_events', ['ts'], unique=False)
    # Written by hand (Alembic does not see changed CHECK rules)
    op.drop_constraint(op.f('ck_orders_status_valid'), 'orders', type_='check')
    op.create_check_constraint(op.f('ck_orders_status_valid'), 'orders',
                               f"status IN ({OLD_STATUSES}, 'WAITING_CONFIRMATION')")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE orders SET status = 'CANCELLED' WHERE status = 'WAITING_CONFIRMATION'")
    op.drop_constraint(op.f('ck_orders_status_valid'), 'orders', type_='check')
    op.create_check_constraint(op.f('ck_orders_status_valid'), 'orders', f"status IN ({OLD_STATUSES})")
    op.drop_index(op.f('ix_kill_switch_events_ts'), table_name='kill_switch_events')
    op.drop_table('kill_switch_events')
