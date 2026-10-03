from decimal import Decimal

import pytest
from conftest import ct, quote, signal

from wall2.config import SelectionConfig
from wall2.core.types import Direction, Right
from wall2.selection.delta import moneyness_delta
from wall2.selection.selector import ContractSelector, NoFit, Selection, contracts_affordable

sel = ContractSelector(SelectionConfig())
B = Decimal("20")


def pick(chain, d=Direction.UP, budget=B):
    return sel.select(signal(d=d), chain, 600.0, budget, ct(10, 0), ct(15, 0))


def test_affordability_math() -> None:
    assert contracts_affordable(B, Decimal("0.20")) == 1
    assert contracts_affordable(B, Decimal("0.21")) == 0
    assert contracts_affordable(B, Decimal("0.09")) == 2


def test_prefers_band_contract_closest_to_035() -> None:
    chain = [
        quote("600", "0.15", "0.17", 0.45),
        quote("601", "0.12", "0.14", 0.36),
        quote("602", "0.09", "0.10", 0.31),
    ]
    r = pick(chain)
    assert isinstance(r, Selection) and r.quote.contract.strike == Decimal("601")
    assert r.contracts == 1 and r.delta_source == "broker"


def test_goes_further_otm_when_band_unaffordable() -> None:
    chain = [
        quote("600", "0.80", "0.82", 0.50),
        quote("601", "0.40", "0.42", 0.35),
        quote("603", "0.15", "0.17", 0.22),
        quote("605", "0.06", "0.07", 0.11),
    ]
    r = pick(chain)
    assert isinstance(r, Selection) and r.quote.contract.strike == Decimal(
        "603"
    )  # highest affordable


def test_delta_floor_and_min_bid() -> None:
    chain = [
        quote("601", "0.40", "0.42", 0.35),
        quote("606", "0.06", "0.07", 0.09),
        quote("607", "0.03", "0.04", 0.12),
    ]  # 607 fails min bid
    r = pick(chain)
    assert isinstance(r, NoFit) and "floor" in r.reason


def test_puts_use_absolute_delta() -> None:
    chain = [quote("599", "0.13", "0.15", -0.34, Right.PUT), quote("601", "0.13", "0.15", 0.34)]
    r = pick(chain, Direction.DOWN)
    assert isinstance(r, Selection) and r.quote.contract.right is Right.PUT and r.delta == 0.34


def test_moneyness_fallback_when_no_greeks() -> None:
    chain = [
        quote("600", "1.49", "1.51", None),
        quote("600", "1.49", "1.51", None, Right.PUT),
        quote("602", "0.18", "0.20", None),
        quote("604", "0.05", "0.06", None),
    ]
    r = pick(chain)
    assert isinstance(r, Selection) and r.delta_source == "moneyness"
    # straddle 3.00 → σ_move ≈ 3.76; strike 602 is 2/3.76 ≈ 0.53σ OTM → Φ(−0.53) ≈ 0.30
    assert r.quote.contract.strike == Decimal("602") and r.delta == pytest.approx(0.30, abs=0.02)


def test_moneyness_delta_symmetry() -> None:
    assert moneyness_delta(Right.CALL, 600, 600, 2.0) == pytest.approx(0.5)
    assert moneyness_delta(Right.PUT, 600, 600, 2.0) == pytest.approx(-0.5)
    assert moneyness_delta(Right.CALL, 602, 600, 2.0) == pytest.approx(
        1 - moneyness_delta(Right.CALL, 598, 600, 2.0)
    )


def test_cheapest_fit_highest_delta_then_first_signal() -> None:
    a = Selection(signal("SPY", m=1), quote("601", "0.1", "0.2", 0.20), 0.20, "broker", 1)
    b = Selection(signal("IWM", m=0), quote("220", "0.1", "0.2", 0.21, u="IWM"), 0.21, "broker", 1)
    c = Selection(signal("QQQ", m=2), quote("500", "0.1", "0.2", 0.30, u="QQQ"), 0.30, "broker", 1)
    assert sel.pick_cheapest_fit([a, b, c]) is c
    assert sel.pick_cheapest_fit([a, b]) is b  # near-tie (0.01 apart): earliest signal
