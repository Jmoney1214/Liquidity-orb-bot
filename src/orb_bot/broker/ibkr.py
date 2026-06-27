"""Interactive Brokers execution adapter (reference implementation).

This drives a **live or paper** IB account through native bracket orders, so
IB itself holds the protective stop and target. It depends on ``ib_async``,
declared as the optional ``ibkr`` extra::

    pip install -e ".[ibkr]"

Connect TWS or IB Gateway first. Paper trading uses port 7497 (TWS) /
4002 (Gateway); live uses 7496 / 4001. **Always validate on a paper account
before risking real capital.**

Note: unlike :class:`~orb_bot.broker.paper.PaperBroker`, this adapter does not
simulate fills from bars — IB reports real fills. ``on_bar`` simply harvests
any round-trips that completed since the previous call.
"""

from __future__ import annotations

import logging

from ..config import ContractSpec
from ..models import Bar, Position, Side, Trade

logger = logging.getLogger(__name__)


def _require_ib():
    try:
        import ib_async  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise ImportError(
            "ib_async is required for live IB trading. Install with: "
            'pip install -e ".[ibkr]"'
        ) from exc
    return __import__("ib_async", fromlist=["*"])


class IBKRBroker:
    """Broker adapter backed by Interactive Brokers via ib_async."""

    def __init__(
        self,
        contract_spec: ContractSpec,
        ib=None,
        ib_contract=None,
    ) -> None:
        self.contract = contract_spec
        self._ib = ib
        self._ib_contract = ib_contract
        self._position: Position | None = None
        self._trades: list[Trade] = []
        self._pending_exits: list[Trade] = []

    # -- connection helpers ---------------------------------------------

    @classmethod
    def connect(
        cls,
        contract_spec: ContractSpec,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 1,
        expiry: str | None = None,
    ) -> "IBKRBroker":
        ib_async = _require_ib()
        ib = ib_async.IB()
        ib.connect(host, port, clientId=client_id)

        if expiry:
            contract = ib_async.Future(contract_spec.symbol, expiry, "CME")
        else:
            # Front-month continuous contract resolved to a tradable future.
            cont = ib_async.ContFuture(contract_spec.symbol, "CME")
            ib.qualifyContracts(cont)
            details = ib.reqContractDetails(cont)
            contract = details[0].contract if details else cont
        ib.qualifyContracts(contract)
        logger.info("Connected to IB %s:%d, trading %s", host, port, contract.localSymbol)

        broker = cls(contract_spec, ib=ib, ib_contract=contract)
        ib.execDetailsEvent += broker._on_exec
        return broker

    # -- Broker interface ------------------------------------------------

    def position(self) -> Position | None:
        return self._position

    @property
    def closed_trades(self) -> list[Trade]:
        return self._trades

    def enter_bracket(
        self,
        side: Side,
        quantity: int,
        stop_price: float,
        target_price: float | None,
        reference_price: float,
        tag: str = "entry",
    ) -> Position:
        ib_async = _require_ib()
        action = "BUY" if side is Side.LONG else "SELL"
        stop_price = self.contract.round_to_tick(stop_price)
        take = self.contract.round_to_tick(target_price) if target_price else None

        # IB requires a limit for bracketOrder; without a target we place a
        # manual market parent + stop child.
        if take is not None:
            bracket = self._ib.bracketOrder(
                action, quantity, limitPrice=reference_price,
                takeProfitPrice=take, stopLossPrice=stop_price,
            )
            # Use a market parent so we enter immediately on the breakout.
            bracket.parent.orderType = "MKT"
            for o in bracket:
                self._ib.placeOrder(self._ib_contract, o)
        else:
            parent = ib_async.MarketOrder(action, quantity)
            parent.transmit = False
            trade = self._ib.placeOrder(self._ib_contract, parent)
            stop = ib_async.StopOrder(
                "SELL" if side is Side.LONG else "BUY", quantity, stop_price
            )
            stop.parentId = trade.order.orderId
            stop.transmit = True
            self._ib.placeOrder(self._ib_contract, stop)

        self._position = Position(
            side=side,
            quantity=quantity,
            entry_price=reference_price,
            entry_time=None,
            stop_price=stop_price,
            target_price=take,
        )
        logger.info("Submitted %s bracket: %d @ mkt stop %.2f target %s",
                    side.value, quantity, stop_price, take)
        return self._position

    def flatten(self, reference_price: float, reason: str) -> Trade | None:
        if self._position is None:
            return None
        ib_async = _require_ib()
        pos = self._position
        action = "SELL" if pos.side is Side.LONG else "BUY"
        self._ib.reqGlobalCancel()  # drop resting stop/target first
        order = ib_async.MarketOrder(action, pos.quantity)
        self._ib.placeOrder(self._ib_contract, order)
        logger.info("Flatten (%s): %s %d @ mkt", reason, action, pos.quantity)
        # The execDetails event will finalise the round-trip; mark the reason.
        self._flatten_reason = reason
        return None

    def on_bar(self, bar: Bar) -> list[Trade]:
        # Pump the IB event loop so fills/exec events are delivered.
        if self._ib is not None:
            self._ib.sleep(0)
        done, self._pending_exits = self._pending_exits, []
        return done

    # -- IB event handling ----------------------------------------------

    def _on_exec(self, trade, fill) -> None:  # pragma: no cover - needs live IB
        """Reconstruct round-trip Trades as IB reports executions."""
        exec_ = fill.execution
        side_bought = exec_.side == "BOT"
        price = exec_.price
        qty = exec_.shares

        if self._position is None:
            return
        pos = self._position
        entering = (pos.side is Side.LONG and side_bought) or (
            pos.side is Side.SHORT and not side_bought
        )
        if entering:
            pos.entry_price = price  # refine to the real fill
            return

        # Exit fill -> close the round-trip.
        points = (price - pos.entry_price) * pos.side.sign
        fees = self.contract.fees_per_side * 2 * qty
        pnl = points * self.contract.point_value * qty - fees
        self._pending_exits.append(
            Trade(
                side=pos.side, quantity=qty,
                entry_time=pos.entry_time, entry_price=pos.entry_price,
                exit_time=fill.time, exit_price=price,
                reason=getattr(self, "_flatten_reason", "bracket"),
                points=points, pnl=pnl, fees=fees,
            )
        )
        self._trades.append(self._pending_exits[-1])
        self._position = None
