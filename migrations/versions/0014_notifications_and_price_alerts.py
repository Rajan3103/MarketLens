"""notifications and price alerts

Fourteenth migration (Telegram alerts, module 12): notifications also records "skipped" (Telegram keys not
set) with the source and event of each message; price_alerts gains % change, indicator, OI and PCR alerts,
a free symbol, settings (params) and what the last check saw.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-04 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0014'
down_revision: Union[str, Sequence[str], None] = '0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONDITIONS = ("'ABOVE', 'BELOW', 'CHANGE_ABOVE', 'CHANGE_BELOW', 'INDICATOR', 'OI_ABOVE', 'OI_BELOW', "
              "'PCR_ABOVE', 'PCR_BELOW'")


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('notifications', sa.Column('source', sa.String(), nullable=True))
    op.add_column('notifications', sa.Column('event', sa.String(), nullable=True))
    op.drop_constraint(op.f('ck_notifications_status_valid'), 'notifications', type_='check')
    op.create_check_constraint(op.f('ck_notifications_status_valid'), 'notifications',
                               "status IN ('pending', 'sent', 'failed', 'skipped')")

    op.add_column('price_alerts', sa.Column('symbol', sa.String(), nullable=True))
    op.execute("UPDATE price_alerts p SET symbol = i.symbol FROM instruments i WHERE i.id = p.instrument_id")
    op.execute("UPDATE price_alerts SET symbol = '?' WHERE symbol IS NULL")
    op.alter_column('price_alerts', 'symbol', nullable=False)
    op.alter_column('price_alerts', 'instrument_id', existing_type=sa.Integer(), nullable=True)
    op.alter_column('price_alerts', 'target_price', existing_type=sa.Numeric(precision=14, scale=4), nullable=True)
    op.add_column('price_alerts', sa.Column('params', postgresql.JSONB(astext_type=sa.Text()),
                                            server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.add_column('price_alerts', sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('price_alerts', sa.Column('last_value', sa.Numeric(precision=14, scale=4), nullable=True))
    op.add_column('price_alerts', sa.Column('trigger_message', sa.Text(), nullable=True))
    op.drop_constraint(op.f('ck_price_alerts_condition_valid'), 'price_alerts', type_='check')
    op.create_check_constraint(op.f('ck_price_alerts_condition_valid'), 'price_alerts', f"condition IN ({CONDITIONS})")
    op.create_index('ix_price_alerts_active', 'price_alerts', ['is_active'], unique=False)


def downgrade() -> None:
    """Downgrade schema (alerts of the new kinds are removed first)."""
    op.drop_index('ix_price_alerts_active', table_name='price_alerts')
    op.execute("DELETE FROM price_alerts WHERE condition NOT IN ('ABOVE', 'BELOW') OR instrument_id IS NULL "
               "OR target_price IS NULL")
    op.drop_constraint(op.f('ck_price_alerts_condition_valid'), 'price_alerts', type_='check')
    op.create_check_constraint(op.f('ck_price_alerts_condition_valid'), 'price_alerts', "condition IN ('ABOVE', 'BELOW')")
    op.drop_column('price_alerts', 'trigger_message')
    op.drop_column('price_alerts', 'last_value')
    op.drop_column('price_alerts', 'last_checked_at')
    op.drop_column('price_alerts', 'params')
    op.alter_column('price_alerts', 'target_price', existing_type=sa.Numeric(precision=14, scale=4), nullable=False)
    op.alter_column('price_alerts', 'instrument_id', existing_type=sa.Integer(), nullable=False)
    op.drop_column('price_alerts', 'symbol')

    op.execute("UPDATE notifications SET status = 'failed' WHERE status = 'skipped'")
    op.drop_constraint(op.f('ck_notifications_status_valid'), 'notifications', type_='check')
    op.create_check_constraint(op.f('ck_notifications_status_valid'), 'notifications',
                               "status IN ('pending', 'sent', 'failed')")
    op.drop_column('notifications', 'event')
    op.drop_column('notifications', 'source')
