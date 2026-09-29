from __future__ import annotations

from pathlib import Path

import pytest

from ace.core.secrets import SecretsError, load_secrets

VALUES = {
    "TRADOVATE_USERNAME": "trader",
    "TRADOVATE_PASSWORD": "hunter2-password",
    "TRADOVATE_CID": "1234",
    "TRADOVATE_SECRET": "tv-secret-value",
    "TRADOVATE_ENV": "demo",
    "DATABENTO_API_KEY": "db-key-value",
}


def _env_file(tmp_path: Path, values: dict[str, str]) -> Path:
    path = tmp_path / ".env"
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return path


def test_loads_from_env_file(tmp_path: Path) -> None:
    secrets = load_secrets(_env_file(tmp_path, VALUES))
    assert secrets.tradovate_password.get_secret_value() == "hunter2-password"
    assert secrets.tradovate_env == "demo"


def test_values_never_appear_in_repr(tmp_path: Path) -> None:
    secrets = load_secrets(_env_file(tmp_path, VALUES))
    shown = repr(secrets) + str(secrets) + str(secrets.model_dump())
    for key, value in VALUES.items():
        if key != "TRADOVATE_ENV":
            assert value not in shown


def test_process_environment_overrides_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRADOVATE_ENV", "live")
    assert load_secrets(_env_file(tmp_path, VALUES)).tradovate_env == "live"


def test_missing_or_empty_values_are_named_not_shown(tmp_path: Path) -> None:
    values = {**VALUES, "TRADOVATE_SECRET": "", "TRADOVATE_ENV": "prod"}
    del values["DATABENTO_API_KEY"]
    with pytest.raises(SecretsError) as info:
        load_secrets(_env_file(tmp_path, values))
    message = str(info.value)
    assert "DATABENTO_API_KEY" in message
    assert "TRADOVATE_SECRET" in message
    assert "TRADOVATE_ENV" in message
    assert "hunter2-password" not in message
    assert info.value.__cause__ is None
