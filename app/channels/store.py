"""Encrypted channel credentials and durable connection state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal, Mapping

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Engine, insert, select

from app.db import integrations

CredentialSource = Literal["stored", "environment"]

_DISPLAY_NAMES = {"telegram": "Telegram", "teams": "Microsoft Teams"}
_ENV_FIELDS = {
    "telegram": {"token": "TELEGRAM_BOT_TOKEN"},
    "teams": {"app_id": "TEAMS_APP_ID", "app_password": "TEAMS_APP_PASSWORD"},
}


class CredentialError(RuntimeError):
    pass


@dataclass(frozen=True)
class Credentials:
    values: dict[str, str]
    source: CredentialSource


@dataclass(frozen=True)
class ChannelState:
    channel: str
    display_name: str
    enabled: bool
    status: Literal["connected", "disconnected"]
    last_ok_at: str | None
    last_error: str | None
    last_error_at: str | None
    last_reply_ref: str | None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ChannelStore:
    def __init__(
        self,
        engine: Engine,
        encryption_key: str,
        *,
        env: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not encryption_key:
            raise CredentialError("CREDENTIAL_ENCRYPTION_KEY is required")
        try:
            self._fernet = Fernet(encryption_key.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise CredentialError("CREDENTIAL_ENCRYPTION_KEY is invalid") from exc
        self._engine = engine
        self._env = env or {}
        self._clock = clock

    def _validate_channel(self, channel: str) -> None:
        if channel not in _DISPLAY_NAMES:
            raise ValueError(f"unsupported channel: {channel!r}")

    def _upsert(self, channel: str, **values) -> None:
        self._validate_channel(channel)
        now = _iso(self._clock())
        with self._engine.begin() as conn:
            exists = conn.execute(
                select(integrations.c.channel).where(integrations.c.channel == channel)
            ).first()
            if exists is None:
                initial = {
                    "channel": channel,
                    "display_name": _DISPLAY_NAMES[channel],
                    "enabled": False,
                    "status": "disconnected",
                    "updated_at": now,
                    **values,
                }
                conn.execute(
                    insert(integrations).values(**initial)
                )
            else:
                conn.execute(
                    integrations.update()
                    .where(integrations.c.channel == channel)
                    .values(updated_at=now, **values)
                )

    def save_credentials(self, channel: str, credentials: Mapping[str, str]) -> None:
        self._validate_channel(channel)
        values = {str(key): str(value) for key, value in credentials.items()}
        expected = set(_ENV_FIELDS[channel])
        if set(values) != expected:
            names = ", ".join(sorted(expected))
            raise CredentialError(f"{channel} credentials require exactly: {names}")
        if not values or any(not value for value in values.values()):
            raise CredentialError("credentials must contain non-empty values")
        payload = json.dumps(values, sort_keys=True).encode("utf-8")
        encrypted = self._fernet.encrypt(payload).decode("ascii")
        self._upsert(channel, credentials_encrypted=encrypted)

    def load_credentials(self, channel: str) -> Credentials | None:
        self._validate_channel(channel)
        with self._engine.connect() as conn:
            row = conn.execute(
                select(integrations.c.credentials_encrypted).where(
                    integrations.c.channel == channel
                )
            ).one_or_none()

        if row is not None:
            if row.credentials_encrypted is None:
                return None
            try:
                decrypted = self._fernet.decrypt(
                    row.credentials_encrypted.encode("ascii")
                )
                values = json.loads(decrypted)
            except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as exc:
                message = "Stored credentials cannot be decrypted; re-enter them."
                self.mark_disconnected(channel, message)
                raise CredentialError(message) from exc
            if not isinstance(values, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in values.items()
            ):
                message = "Stored credentials are invalid; re-enter them."
                self.mark_disconnected(channel, message)
                raise CredentialError(message)
            return Credentials(values=values, source="stored")

        env_fields = _ENV_FIELDS[channel]
        values = {name: self._env.get(variable, "") for name, variable in env_fields.items()}
        if all(values.values()):
            return Credentials(values=values, source="environment")
        return None

    def masked_credentials(self, channel: str) -> dict[str, str]:
        credentials = self.load_credentials(channel)
        if credentials is None:
            return {}
        masked = {name: f"****{value[-4:]}" for name, value in credentials.values.items()}
        masked["source"] = credentials.source
        return masked

    def clear_credentials(self, channel: str) -> None:
        self._upsert(
            channel,
            credentials_encrypted=None,
            enabled=False,
            status="disconnected",
            last_reply_ref=None,
        )

    def set_enabled(self, channel: str, enabled: bool) -> None:
        values = {"enabled": enabled}
        if not enabled:
            values["status"] = "disconnected"
        self._upsert(channel, **values)

    def is_enabled(self, channel: str) -> bool:
        return self.get(channel).enabled

    def mark_connected(self, channel: str, reply_ref: str) -> None:
        self._upsert(
            channel,
            status="connected",
            last_ok_at=_iso(self._clock()),
            last_reply_ref=reply_ref,
        )

    def record_error(self, channel: str, error: str) -> None:
        self._upsert(
            channel,
            status="disconnected",
            last_error=str(error),
            last_error_at=_iso(self._clock()),
        )

    def mark_disconnected(self, channel: str, error: str) -> None:
        self.record_error(channel, error)

    def get(self, channel: str) -> ChannelState:
        self._validate_channel(channel)
        with self._engine.connect() as conn:
            row = conn.execute(
                select(integrations).where(integrations.c.channel == channel)
            ).one_or_none()
        if row is None:
            return ChannelState(
                channel=channel,
                display_name=_DISPLAY_NAMES[channel],
                enabled=False,
                status="disconnected",
                last_ok_at=None,
                last_error=None,
                last_error_at=None,
                last_reply_ref=None,
            )
        return ChannelState(
            channel=row.channel,
            display_name=row.display_name,
            enabled=bool(row.enabled),
            status="connected" if row.status == "connected" else "disconnected",
            last_ok_at=row.last_ok_at,
            last_error=row.last_error,
            last_error_at=row.last_error_at,
            last_reply_ref=row.last_reply_ref,
        )
