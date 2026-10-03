"""Order manager against the simulator, including the cancel/fill race."""

from decimal import Decimal

from conftest import quote, signal

from wall2.broker.base import BrokerPosition
from wall2.broker.simulator import SimBroker
from wall2.config import OrderConfig
from wall2.execution.orders import EntryFailed, EntryFill, OrderManager
from wall2.selection.selector import NoFit, Selection

FAST = OrderConfig(
    fill_wait_sec=0.01,
    profit_exit_mid_wait_sec=0.01,
    cancel_confirm_timeout_sec=0.05,
    reprice_attempts=1,
    exit_bid_retries=3,
)


def setup(cash: str = "1000"):
    b = SimBroker(Decimal(cash))
    q = quote("601", "0.18", "0.20", 0.35)
    b.set_quote(q)
    return b, OrderManager(b, FAST), Selection(signal(), q, 0.35, "broker", 1)


async def test_buy_fills_at_ask() -> None:
    b, om, sel = setup()
    r = await om.buy(sel, reselect=_fail)
    assert isinstance(r, EntryFill) and r.price == Decimal("0.20") and r.qty == 1
    assert await b.settled_cash() == Decimal("980.00")


async def test_unfilled_reprices_once_then_gives_up() -> None:
    b, om, sel = setup()
    b.never_fill = True
    calls = 0

    async def reselect():
        nonlocal calls
        calls += 1
        return sel

    r = await om.buy(sel, reselect)
    assert isinstance(r, EntryFailed) and r.attempts == 2 and calls == 1
    assert await b.positions() == []


async def test_reselect_no_fit_stops() -> None:
    b, om, sel = setup()
    b.never_fill = True

    async def reselect():
        return NoFit(sel.signal, "gone")

    r = await om.buy(sel, reselect)
    assert isinstance(r, EntryFailed) and "gone" in r.reason


async def test_fill_during_cancel_is_not_doubled() -> None:
    """Cancel crosses with a fill: the manager must see the fill and NOT place a second order."""
    b, om, sel = setup()
    b.never_fill = True
    b.fill_on_cancel = True

    async def reselect():
        raise AssertionError("must not re-order after a fill")

    r = await om.buy(sel, reselect)
    assert isinstance(r, EntryFill)
    (p,) = await b.positions()
    assert p.qty == 1


async def test_profit_exit_mid_then_bid() -> None:
    b, om, _ = setup()
    q = quote("601", "0.40", "0.44", 0.5)
    b.add_position(BrokerPosition(q.contract, 1, Decimal("0.20")))
    b.set_quote(q)
    f = await om.sell(q.contract, 1, profit=True)  # mid 0.42 won't fill in the sim; then bid 0.40
    assert (f.qty, f.avg_price, f.unsold) == (1, Decimal("0.40"), 0)


async def test_exit_with_no_bid_leaves_unsold() -> None:
    b, om, _ = setup()
    q = quote("610", "0.00", "0.01", 0.01)
    b.add_position(BrokerPosition(q.contract, 1, Decimal("0.20")))
    b.set_quote(q)
    f = await om.sell(q.contract, 1, profit=False)
    assert f.qty == 0 and f.unsold == 1


async def _fail():
    raise AssertionError("unexpected reselect")
