"""
Abstract base class for all trading strategies.

Each strategy implementation must:
  1. Set `strategy_type` as a class-level string matching the value stored in
     base.trade_strategies.strategy_type (e.g. 'DCA_GRID').
  2. Implement `process()` — called on every bot tick for each active strategy
     record loaded from the DB.

The strategy is responsible for its own DB interactions via get_conn().
The exchange connection is passed in so strategies can be tested with the
PaperTradingConnection without any changes to strategy code.
"""

from abc import ABC, abstractmethod

from common.connections.base import BaseExchangeConnection


class BaseStrategy(ABC):

    #: Must match base.trade_strategies.strategy_type — used as registry key.
    strategy_type: str

    @abstractmethod
    async def process(
        self,
        exchange: BaseExchangeConnection,
        strategy: dict,
    ) -> None:
        """
        Execute one tick of the strategy.

        Called once per poll interval for every ACTIVE strategy record.

        Args:
            exchange: Live or paper exchange connection.
            strategy: Full row from base.trade_strategies, with additional
                      fields: base_asset_code, quote_asset_code.
        """
        ...
