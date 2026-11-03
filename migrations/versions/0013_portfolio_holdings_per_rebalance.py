"""portfolio holdings per rebalance

Thirteenth migration (Momentum Engine, module 8): portfolio_holdings can now also keep the holdings after
every rebalance of a saved backtest run (run_id + rebalance_date, with the price and value that day).
Rows without a run_id are still "what a strategy holds right now", one per stock.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-04 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0013'
down_revision: Union[str, Sequence[str], None] = '0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('portfolio_holdings', sa.Column('run_id', sa.Integer(), nullable=True))
    op.add_column('portfolio_holdings', sa.Column('rebalance_date', sa.Date(), nullable=True))
    op.add_column('portfolio_holdings', sa.Column('price', sa.Numeric(precision=14, scale=4), nullable=True))
    op.add_column('portfolio_holdings', sa.Column('value', sa.Numeric(precision=16, scale=2), nullable=True))
    op.alter_column('portfolio_holdings', 'strategy_id', existing_type=sa.Integer(), nullable=True)
    op.create_foreign_key(op.f('fk_portfolio_holdings_run_id_backtest_runs'), 'portfolio_holdings',
                          'backtest_runs', ['run_id'], ['id'], ondelete='CASCADE')
    op.drop_constraint(op.f('uq_portfolio_holdings_strategy_id_instrument_id'), 'portfolio_holdings', type_='unique')
    op.create_unique_constraint(op.f('uq_portfolio_holdings_run_id_rebalance_date_instrument_id'),
                                'portfolio_holdings', ['run_id', 'rebalance_date', 'instrument_id'])
    op.create_index('uq_portfolio_holdings_live', 'portfolio_holdings', ['strategy_id', 'instrument_id'],
                    unique=True, postgresql_where=sa.text('run_id IS NULL'))


def downgrade() -> None:
    """Downgrade schema (backtest rows are removed first: the old table only held today's holdings)."""
    op.execute("DELETE FROM portfolio_holdings WHERE run_id IS NOT NULL")
    op.drop_index('uq_portfolio_holdings_live', table_name='portfolio_holdings',
                  postgresql_where=sa.text('run_id IS NULL'))
    op.drop_constraint(op.f('uq_portfolio_holdings_run_id_rebalance_date_instrument_id'), 'portfolio_holdings',
                       type_='unique')
    op.create_unique_constraint(op.f('uq_portfolio_holdings_strategy_id_instrument_id'), 'portfolio_holdings',
                                ['strategy_id', 'instrument_id'])
    op.drop_constraint(op.f('fk_portfolio_holdings_run_id_backtest_runs'), 'portfolio_holdings', type_='foreignkey')
    op.alter_column('portfolio_holdings', 'strategy_id', existing_type=sa.Integer(), nullable=False)
    op.drop_column('portfolio_holdings', 'value')
    op.drop_column('portfolio_holdings', 'price')
    op.drop_column('portfolio_holdings', 'rebalance_date')
    op.drop_column('portfolio_holdings', 'run_id')
