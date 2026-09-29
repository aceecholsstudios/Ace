from __future__ import annotations

from datetime import time
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from ace.core.config import AceConfig, ConfigError, load_config, load_fees
from ace.core.instruments import Root
from tests.conftest import CONFIG_DIR


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "ace.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


@pytest.fixture
def raw(ace_yaml_text: str) -> dict[str, object]:
    data = yaml.safe_load(ace_yaml_text)
    assert isinstance(data, dict)
    return data


def test_shipped_config_is_valid() -> None:
    config = load_config(CONFIG_DIR / "ace.yaml")
    assert config.environment == "demo"
    assert config.symbols == [Root.MES, Root.MNQ, Root.M2K]
    assert config.signal_pairs[Root.MNQ] is Root.NQ
    assert config.windows.asia.start == time(19, 30)
    assert config.windows.rth.end == time(16, 0)
    assert config.feed.schema_ == "mbp-10"


def test_shipped_fees_match_design_estimate() -> None:
    fees = load_fees(CONFIG_DIR / "fees.yaml")
    # Design doc Section 3.1 estimates ~$1.50 per round trip.
    assert fees.round_trip_usd(Root.MES) == Decimal("1.52")
    assert fees.round_trip_ticks(Root.MES) == Decimal("1.216")
    assert fees.verified is False


def _set(data: dict[str, object], dotted: str, value: object) -> None:
    *parents, leaf = dotted.split(".")
    node = data
    for key in parents:
        child = node[key]
        assert isinstance(child, dict)
        node = child
    node[leaf] = value


@pytest.mark.parametrize(
    ("dotted", "value", "message"),
    [
        ("environment", "paper", "environment"),
        ("symbols", ["MES", "ES"], "symbols must be micros"),
        ("symbols", ["MES", "MES"], "duplicates"),
        ("signal_pairs", {"MES": "ES", "MNQ": "NQ"}, "one entry per symbol"),
        ("signal_pairs", {"MES": "ES", "MNQ": "MES", "M2K": "RTY"}, "must be an E-mini"),
        ("signals.weights.ofi", 0.5, "sum to 1.0"),
        ("selection.weights.recent", 0.2, "sum to 1.0"),
        ("risk.base_risk_usd", 60, "less than or equal to 50"),
        ("risk.base_risk_usd", 10, "greater than or equal to 25"),
        ("risk.tier2_daily_losses", 1, "tier2_daily_losses"),
        ("risk.buffer_ticks", {"MES": 2, "MNQ": 4}, "buffer_ticks is missing"),
        ("risk.max_spread_ticks.asia", {"MES": 2}, "max_spread_ticks.asia is missing"),
        ("windows.asia.start", 1170, "quoted"),
        ("windows.asia.warmup_min", 10, "less than or equal to 5"),
        ("windows.timezone", "Mars/Olympus", "unknown timezone"),
        ("lab.horizons_sec", [60, 30], "strictly increasing"),
        ("signals.sweep.burst_pctl", 101, "less than or equal to 100"),
        ("entry.mode", "stop", "entry.mode"),
    ],
)
def test_invalid_values_are_rejected(
    tmp_path: Path, raw: dict[str, object], dotted: str, value: object, message: str
) -> None:
    _set(raw, dotted, value)
    with pytest.raises(ConfigError, match=message):
        load_config(_write(tmp_path, raw))


def test_unknown_keys_are_rejected(tmp_path: Path, raw: dict[str, object]) -> None:
    _set(raw, "risk.base_risk_usdd", 40)
    with pytest.raises(ConfigError, match="base_risk_usdd"):
        load_config(_write(tmp_path, raw))


def test_unquoted_yaml_time_is_caught(tmp_path: Path, ace_yaml_text: str) -> None:
    path = tmp_path / "ace.yaml"
    path.write_text(ace_yaml_text.replace('"19:30"', "19:30"), encoding="utf-8")
    with pytest.raises(ConfigError, match="quoted"):
        load_config(path)


def test_config_is_frozen() -> None:
    config = load_config(CONFIG_DIR / "ace.yaml")
    with pytest.raises(ValueError, match="frozen"):
        config.environment = "live"  # type: ignore[misc]


def test_missing_and_malformed_files(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "nope.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("symbols: [MES\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(bad)


def test_model_round_trips(raw: dict[str, object]) -> None:
    config = AceConfig.model_validate(raw)
    again = AceConfig.model_validate(config.model_dump(mode="json", by_alias=True))
    assert again == config
