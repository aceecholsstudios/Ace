"""Typed configuration from config/wall2.yaml. Bad values fail at startup, not mid-session."""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SessionConfig(_Strict):
    autostart: time = time(8, 0)
    first_entry: time = time(8, 35)
    closeout_minutes_before_close: int = Field(
        10, ge=1, le=60
    )  # 14:50 on full days, 11:50 on half-days
    report_at: time = time(15, 15)


class SignalConfig(_Strict):
    ema_len: int = Field(9, ge=2)
    trend_minutes: int = 15
    entry_minutes: int = 5
    setup_ttl_bars: int = Field(2, ge=1)
    warmup_days: int = Field(3, ge=0, le=10)


class SelectionConfig(_Strict):
    budget_usd: Decimal = Field(Decimal("20"), gt=0)
    target_delta_low: float = 0.30
    target_delta_high: float = 0.40
    min_delta: float = Field(0.10, gt=0, lt=1)
    min_bid: Decimal = Decimal("0.05")
    tie_delta_tolerance: float = Field(0.02, ge=0)
    fallback_annual_vol: dict[str, float] = {"SPY": 0.16, "QQQ": 0.20, "IWM": 0.22}

    @model_validator(mode="after")
    def _band(self) -> SelectionConfig:
        if not (self.min_delta <= self.target_delta_low <= self.target_delta_high < 1):
            raise ValueError("need min_delta <= target_delta_low <= target_delta_high < 1")
        return self


class OrderConfig(_Strict):
    fill_wait_sec: float = Field(5, gt=0)
    reprice_attempts: int = Field(1, ge=0, le=3)
    profit_exit_mid_wait_sec: float = Field(5, gt=0)
    exit_bid_retries: int = Field(10, ge=1)
    cancel_confirm_timeout_sec: float = Field(10, gt=0)


class ManagementConfig(_Strict):
    tighten_after_gain_pct: float = Field(100, gt=0)
    salvage_min_value_pct: float = Field(50, ge=0, le=100)


class RiskConfig(_Strict):
    max_trades_per_day: int = Field(3, ge=1)
    one_position_per_etf: bool = True
    settled_cash_only: bool = True
    halt_stale_sec: float = Field(15, gt=0)
    halt_resume_healthy_sec: float = Field(120, ge=0)


class Wall2Config(_Strict):
    timezone: Literal["America/Chicago"] = "America/Chicago"
    mode: Literal["paper", "live"] = "paper"
    underlyings: tuple[str, ...] = ("SPY", "QQQ", "IWM")
    session: SessionConfig = SessionConfig()
    signals: SignalConfig = SignalConfig()
    selection: SelectionConfig = SelectionConfig()
    orders: OrderConfig = OrderConfig()
    management: ManagementConfig = ManagementConfig()
    risk: RiskConfig = RiskConfig()
    fees_per_contract: Decimal = Field(
        Decimal("0.00"), ge=0
    )  # per side; from your Webull statement

    @field_validator("underlyings")
    @classmethod
    def _known(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        allowed = {"SPY", "QQQ", "IWM"}
        if not v or not set(v) <= allowed:
            raise ValueError(f"underlyings must be a non-empty subset of {sorted(allowed)}")
        return v


def load_config(path: str | Path) -> Wall2Config:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return Wall2Config.model_validate(raw)
