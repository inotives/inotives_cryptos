"""
Strategy registry.

Maps strategy_type strings (as stored in base.trade_strategies.strategy_type)
to their handler instances.

Adding a new strategy:
  1. Create strategies/<name>.py with a class extending BaseStrategy.
  2. Set strategy_type = '<TYPE_STRING>' on the class.
  3. Import and register it below.
"""

from .base import BaseStrategy
from .dca_grid import DcaGridStrategy
from .trend_following import TrendFollowingStrategy

_REGISTRY: dict[str, BaseStrategy] = {
    DcaGridStrategy.strategy_type:       DcaGridStrategy(),
    TrendFollowingStrategy.strategy_type: TrendFollowingStrategy(),
}


def get_strategy(strategy_type: str) -> BaseStrategy | None:
    """Return the handler for a given strategy_type, or None if unregistered."""
    return _REGISTRY.get(strategy_type)
