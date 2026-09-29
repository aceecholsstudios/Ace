"""Typed models for ``config/ace.yaml`` and ``config/fees.yaml``.

Every model is frozen and rejects unknown keys, so a typo in the YAML fails at
startup instead of silently falling back to a default. Cross-field rules (for
example, that weights sum to one) live in validators next to the fields they
constrain.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import time
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ace.core.instruments import INSTRUMENTS, SIGNAL_ONLY, TRADABLE, Root

WindowName = Literal["asia", "london", "rth"]
WINDOW_NAMES: tuple[WindowName, ...] = ("asia", "london", "rth")

Percentile = Annotated[float, Field(gt=0, le=100)]
Fraction = Annotated[float, Field(ge=0, le=1)]
PositiveFloat = Annotated[float, Field(gt=0)]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]

_WEIGHT_SUM_TOLERANCE = 1e-9


class ConfigError(Exception):
    """A config file is missing, unreadable, or fails validation."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _check_weights_sum_to_one(model: BaseModel) -> None:
    values = [float(v) for v in model.model_dump().values()]
    total = math.fsum(values)
    if abs(total - 1.0) > _WEIGHT_SUM_TOLERANCE:
        raise ValueError(f"weights must sum to 1.0, got {total:g}")


# --- feed -------------------------------------------------------------------


class FeedConfig(_Model):
    provider: Literal["databento"]
    dataset: str = Field(min_length=1)
    schema_: Literal["mbp-10"] = Field(alias="schema")
    symbology: Literal["continuous_volume"]
    stale_sec: PositiveFloat
    snapshot_hz: PositiveInt


# --- windows ----------------------------------------------------------------


class WindowConfig(_Model):
    start: time
    end: time
    warmup_min: Annotated[int, Field(ge=2, le=5)]
    """Minutes to wait after the window opens before trading (decision record: 2-5)."""

    @field_validator("start", "end", mode="before")
    @classmethod
    def _require_hh_mm_string(cls, value: Any) -> Any:
        # Unquoted 19:30 in YAML 1.1 is the base-60 integer 1170. Insist on a
        # quoted "HH:MM" so that mistake is caught here, not at 19:30.
        if not isinstance(value, str | time):
            # ValueError, not TypeError: pydantic only reports ValueError as a validation error.
            raise ValueError('times must be quoted "HH:MM" strings')  # noqa: TRY004
        return value

    @model_validator(mode="after")
    def _non_empty(self) -> Self:
        if self.start == self.end:
            raise ValueError("window start and end must differ")
        return self


class WindowsConfig(_Model):
    asia: WindowConfig
    london: WindowConfig
    rth: WindowConfig
    timezone: str

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def by_name(self) -> dict[WindowName, WindowConfig]:
        return {"asia": self.asia, "london": self.london, "rth": self.rth}


# --- signals ----------------------------------------------------------------


class SignalWeights(_Model):
    divergence: Fraction
    absorption: Fraction
    sweep: Fraction
    ofi: Fraction

    @model_validator(mode="after")
    def _sum_to_one(self) -> Self:
        _check_weights_sum_to_one(self)
        return self


SignalSource = Literal["emini", "micro"]


class SignalSources(_Model):
    divergence: SignalSource
    absorption: SignalSource
    sweep: SignalSource
    ofi: SignalSource


class SweepConfig(_Model):
    levels: PositiveInt
    window_ms: PositiveInt
    burst_pctl: Percentile
    print_pctl: Percentile


class AbsorptionConfig(_Model):
    level_vol_pctl: Percentile
    hold_sec: PositiveFloat
    refill_count: PositiveInt
    delta_pctl: Percentile
    max_progress_ticks: NonNegativeInt


class IcebergConfig(_Model):
    hidden_vol_pctl: Percentile
    min_refills: PositiveInt


class OfiConfig(_Model):
    window_sec: PositiveFloat
    veto_pctl: Percentile


class DivergenceConfig(_Model):
    swing_bars: PositiveInt
    scales: list[Annotated[str, Field(pattern=r"^[1-9]\d*m$")]] = Field(min_length=1)


class SignalsConfig(_Model):
    weights: SignalWeights
    sources: SignalSources
    entry_threshold: Fraction
    decay_half_life_sec: PositiveFloat
    percentile_lookback_days: PositiveInt
    sweep: SweepConfig
    absorption: AbsorptionConfig
    iceberg: IcebergConfig
    ofi: OfiConfig
    divergence: DivergenceConfig


# --- selection --------------------------------------------------------------


class SelectionWeights(_Model):
    signal: Fraction
    reward_risk: Fraction
    recent: Fraction

    @model_validator(mode="after")
    def _sum_to_one(self) -> Self:
        _check_weights_sum_to_one(self)
        return self


class SelectionConfig(_Model):
    collect_window_ms: PositiveInt
    weights: SelectionWeights


# --- risk -------------------------------------------------------------------


class EdgeDecayConfig(_Model):
    per_window: bool
    lookback_trades: PositiveInt


class RiskConfig(_Model):
    base_risk_usd: Annotated[float, Field(ge=25, le=50)]
    """Fixed dollar risk per trade; the decision record allows $25-$50."""
    max_trades_per_day: PositiveInt
    loss_cooldown_min: Annotated[float, Field(ge=0)]
    tier1_consecutive_losses: PositiveInt
    tier1_multiplier: Annotated[float, Field(gt=0, le=1)]
    tier2_daily_losses: PositiveInt
    peak_drawdown_pct: Annotated[float, Field(gt=0, lt=100)]
    edge_decay: EdgeDecayConfig
    cost_gate_k: PositiveFloat
    buffer_ticks: dict[Root, NonNegativeInt]
    max_spread_ticks: dict[WindowName, dict[Root, PositiveInt]]
    stale_quote_sec: PositiveFloat
    ofi_veto: bool

    @model_validator(mode="after")
    def _tiers_ordered(self) -> Self:
        if self.tier2_daily_losses < self.tier1_consecutive_losses:
            raise ValueError("tier2_daily_losses must be >= tier1_consecutive_losses")
        missing = set(WINDOW_NAMES) - set(self.max_spread_ticks)
        if missing:
            raise ValueError(f"max_spread_ticks is missing windows: {sorted(missing)}")
        return self


# --- execution --------------------------------------------------------------


class EntryConfig(_Model):
    mode: Literal["hybrid", "market"]
    microprice_lean_frac: Fraction
    limit_timeout_sec: PositiveFloat
    fallback: Literal["market"]


class TrailConfig(_Model):
    delta_stall_sec: PositiveFloat
    opposing_strength: Fraction


class ManagementConfig(_Model):
    breakeven_at_r: PositiveFloat
    trail: TrailConfig
    target_min_r: PositiveFloat


class RollConfig(_Model):
    method: Literal["volume"]


# --- ui / storage / lab -----------------------------------------------------


class UiConfig(_Model):
    chart_hz: PositiveFloat
    table_hz: PositiveFloat


class StorageConfig(_Model):
    record_ticks: bool
    screenshots: bool


class LabConfig(_Model):
    enabled: bool
    horizons_sec: list[PositiveInt] = Field(min_length=1)

    @field_validator("horizons_sec")
    @classmethod
    def _strictly_increasing(cls, value: list[int]) -> list[int]:
        if any(b <= a for a, b in pairwise(value)):
            raise ValueError("horizons_sec must be strictly increasing")
        return value


# --- root -------------------------------------------------------------------


class AceConfig(_Model):
    environment: Literal["demo", "live"]
    symbols: list[Root] = Field(min_length=1)
    signal_pairs: dict[Root, Root]
    feed: FeedConfig
    windows: WindowsConfig
    signals: SignalsConfig
    selection: SelectionConfig
    risk: RiskConfig
    entry: EntryConfig
    management: ManagementConfig
    roll: RollConfig
    ui: UiConfig
    storage: StorageConfig
    lab: LabConfig

    @model_validator(mode="after")
    def _symbols_consistent(self) -> Self:
        symbols = set(self.symbols)
        if len(symbols) != len(self.symbols):
            raise ValueError("symbols contains duplicates")
        if not_tradable := symbols - TRADABLE:
            raise ValueError(f"symbols must be micros, got {sorted(not_tradable)}")

        if set(self.signal_pairs) != symbols:
            raise ValueError("signal_pairs must have exactly one entry per symbol")
        for micro, emini in self.signal_pairs.items():
            if emini not in SIGNAL_ONLY:
                raise ValueError(f"signal_pairs[{micro}] must be an E-mini, got {emini}")

        _require_cover("risk.buffer_ticks", self.risk.buffer_ticks, symbols)
        for window, per_symbol in self.risk.max_spread_ticks.items():
            _require_cover(f"risk.max_spread_ticks.{window}", per_symbol, symbols)
        return self


def _require_cover(name: str, mapping: Mapping[Root, object], symbols: set[Root]) -> None:
    missing = symbols - set(mapping)
    if missing:
        raise ValueError(f"{name} is missing symbols: {sorted(missing)}")


# --- fees -------------------------------------------------------------------


class PerSideFees(_Model):
    commission: Annotated[Decimal, Field(ge=0)]
    exchange: Annotated[Decimal, Field(ge=0)]
    clearing: Annotated[Decimal, Field(ge=0)]
    nfa: Annotated[Decimal, Field(ge=0)]

    @property
    def total(self) -> Decimal:
        return self.commission + self.exchange + self.clearing + self.nfa


class FeesConfig(_Model):
    plan: Literal["free", "monthly", "lifetime"]
    verified: bool
    per_side: dict[Root, PerSideFees]

    def round_trip_usd(self, root: Root) -> Decimal:
        return 2 * self.per_side[root].total

    def round_trip_ticks(self, root: Root) -> Decimal:
        return self.round_trip_usd(root) / INSTRUMENTS[root].tick_value_usd


# --- loading ----------------------------------------------------------------


def _read_yaml(path: Path) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror or exc}") from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc


def _validate[M: BaseModel](model: type[M], path: Path) -> M:
    data = _read_yaml(path)
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{path} failed validation:\n{exc}") from exc


def load_config(path: Path) -> AceConfig:
    return _validate(AceConfig, path)


def load_fees(path: Path) -> FeesConfig:
    return _validate(FeesConfig, path)
