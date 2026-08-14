"""Encrypted channel credentials and durable connection state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal, Mapping

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Engine, insert, select, text

from app.db import integration_errors, integrations

CredentialSource = Literal["stored"]

_DISPLAY_NAMES = {
    "telegram": "Telegram",
    "whatsapp": "WhatsApp",
    "teams": "Microsoft Teams",
}
_CREDENTIAL_FIELDS = {
    "telegram": {"token"},
    "whatsapp": {"access_token", "phone_number_id", "app_secret", "verify_token"},
    "teams": {"app_id", "app_password", "tenant_id"},
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


def ensure_no_legacy_secret_references(engine: Engine) -> None:
    """Stop upgrades that still need the one-time Keychain migration."""
    with engine.connect() as conn:
        columns = {
            row.name
            for row in conn.execute(text("PRAGMA table_info(integrations)")).mappings()
        }
        if not {"secret_name", "active_secret_version"} <= columns:
            return
        count = conn.execute(
            text(
                "SELECT count(*) FROM integrations "
                "WHERE credentials_encrypted IS NULL "
                "AND secret_name IS NOT NULL AND active_secret_version IS NOT NULL"
            )
        ).scalar_one()
    if count:
        raise CredentialError(
            "Legacy Keychain credentials require one-time migration. Run: "
            ".venv/bin/python scripts/migrate_keychain_credentials.py"
        )


class ChannelStore:
    def __init__(
        self,
        engine: Engine,
        encryption_key: str,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not encryption_key:
            raise CredentialError("CREDENTIAL_ENCRYPTION_KEY is required")
        try:
            self._fernet = Fernet(encryption_key.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise CredentialError("CREDENTIAL_ENCRYPTION_KEY is invalid") from exc
        self._engine = engine
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

    def initialize(self, channel: str, *, enabled: bool) -> None:
        """Create first-run state without overwriting later admin choices."""
        self._validate_channel(channel)
        now = _iso(self._clock())
        with self._engine.begin() as conn:
            exists = conn.execute(
                select(integrations.c.channel).where(integrations.c.channel == channel)
            ).first()
            if exists is None:
                conn.execute(
                    insert(integrations).values(
                        channel=channel,
                        display_name=_DISPLAY_NAMES[channel],
                        enabled=enabled,
                        status="disconnected",
                        updated_at=now,
                    )
                )

    def _validate_credentials(
        self, channel: str, credentials: Mapping[str, str]
    ) -> dict[str, str]:
        self._validate_channel(channel)
        values = dict(credentials)
        expected = _CREDENTIAL_FIELDS[channel]
        if set(values) != expected:
            names = ", ".join(sorted(expected))
            raise CredentialError(f"{channel} credentials require exactly: {names}")
        if any(not isinstance(value, str) or not value for value in values.values()):
            raise CredentialError("credentials must contain non-empty values")
        return values

    def _decode_credentials(self, channel: str, payload: bytes) -> dict[str, str]:
        try:
            values = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CredentialError("Stored credentials are invalid; re-enter them.") from exc
        if not isinstance(values, dict):
            raise CredentialError("Stored credentials are invalid; re-enter them.")
        return self._validate_credentials(channel, values)

    def save_credentials(self, channel: str, credentials: Mapping[str, str]) -> None:
        values = self._validate_credentials(channel, credentials)
        payload = json.dumps(values, sort_keys=True).encode("utf-8")
        encrypted = self._fernet.encrypt(payload).decode("ascii")
        self._upsert(
            channel,
            credentials_encrypted=encrypted,
            status="disconnected",
            last_error=None,
            last_error_at=None,
        )

    def load_credentials(self, channel: str) -> Credentials | None:
        self._validate_channel(channel)
        with self._engine.connect() as conn:
            row = conn.execute(
                select(integrations.c.credentials_encrypted).where(
                    integrations.c.channel == channel
                )
            ).one_or_none()

        if row is not None and row.credentials_encrypted is not None:
            try:
                payload = self._fernet.decrypt(
                    row.credentials_encrypted.encode("ascii")
                )
                values = self._decode_credentials(channel, payload)
            except (
                InvalidToken,
                ValueError,
                TypeError,
                UnicodeError,
                CredentialError,
            ) as exc:
                message = "Stored credentials cannot be decrypted; re-enter them."
                self.mark_disconnected(channel, message)
                raise CredentialError(message) from exc
            return Credentials(values=values, source="stored")
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
        self._validate_channel(channel)
        reason = str(error)
        occurred_at = _iso(self._clock())
        with self._engine.begin() as conn:
            conn.execute(
                insert(integration_errors).values(
                    channel=channel,
                    created_at=occurred_at,
                    reason=reason,
                )
            )
        self._upsert(
            channel,
            status="disconnected",
            last_error=reason,
            last_error_at=occurred_at,
        )

    def mark_disconnected(self, channel: str, error: str) -> None:
        self.record_error(channel, error)

    def recent_errors(self, channel: str, *, limit: int = 5) -> list[dict[str, object]]:
        self._validate_channel(channel)
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(integration_errors)
                .where(integration_errors.c.channel == channel)
                .order_by(integration_errors.c.id.desc())
                .limit(max(0, limit))
            ).mappings().all()
        return [dict(row) for row in rows]

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
