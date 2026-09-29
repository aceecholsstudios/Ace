"""Credentials from ``.env`` (design doc Section 6.3).

Values load into ``SecretStr`` so they never show up in logs, reprs, or the
dashboard. Moving to Windows Credential Manager later only means replacing
:func:`load_secrets`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

SECRET_ENV_VARS: tuple[str, ...] = (
    "TRADOVATE_USERNAME",
    "TRADOVATE_PASSWORD",
    "TRADOVATE_CID",
    "TRADOVATE_SECRET",
    "DATABENTO_API_KEY",
)


class SecretsError(Exception):
    """Credentials are missing or malformed. The message never contains values."""


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        case_sensitive=True,
    )

    tradovate_username: SecretStr = Field(alias="TRADOVATE_USERNAME", min_length=1)
    tradovate_password: SecretStr = Field(alias="TRADOVATE_PASSWORD", min_length=1)
    tradovate_cid: SecretStr = Field(alias="TRADOVATE_CID", min_length=1)
    tradovate_secret: SecretStr = Field(alias="TRADOVATE_SECRET", min_length=1)
    tradovate_env: Literal["demo", "live"] = Field(alias="TRADOVATE_ENV")
    databento_api_key: SecretStr = Field(alias="DATABENTO_API_KEY", min_length=1)


def load_secrets(env_file: Path | None) -> Secrets:
    """Load credentials from the process environment, then ``env_file`` if given.

    Process environment variables win over the file, which lets CI or a test
    inject values without writing a ``.env``.
    """
    try:
        return Secrets(_env_file=env_file)
    except ValidationError as exc:
        # Report which variables are wrong, never what they contain.
        problems = sorted({f"{err['loc'][0]}: {err['msg']}" for err in exc.errors()})
        raise SecretsError("invalid credentials: " + "; ".join(problems)) from None
