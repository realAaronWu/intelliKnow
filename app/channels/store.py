"""Secret-backed channel credentials and durable connection state."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal, Mapping

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Engine, insert, select

from app.db import integration_errors, integrations
from app.secrets import SecretReference, SecretStore, SecretStoreError

CredentialSource = Literal["stored", "environment"]

_DISPLAY_NAMES = {"telegram": "Telegram", "teams": "Microsoft Teams"}
_ENV_FIELDS = {
    "telegram": {"token": "TELEGRAM_BOT_TOKEN"},
    "teams": {"app_id": "TEAMS_APP_ID", "app_password": "TEAMS_APP_PASSWORD"},
}
_CREDENTIAL_TYPES = {"telegram": "bot-token", "teams": "client-secret"}


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
        encryption_key: str | None = None,
        *,
        secret_store: SecretStore,
        env: Mapping[str, str] | None = None,
        allow_environment_fallback: bool = False,
        cache_ttl_seconds: int = 300,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._fernet: Fernet | None = None
        if encryption_key:
            try:
                self._fernet = Fernet(encryption_key.encode("ascii"))
            except (ValueError, TypeError) as exc:
                raise CredentialError("CREDENTIAL_ENCRYPTION_KEY is invalid") from exc
        self._engine = engine
        self._secret_store = secret_store
        self._env = env or {}
        self._allow_environment_fallback = allow_environment_fallback
        self._cache_ttl_seconds = min(max(cache_ttl_seconds, 0), 300)
        self._cache: dict[str, tuple[float, str, Credentials]] = {}
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
        self._migrate_legacy_credentials(channel)

    def _validate_credentials(
        self, channel: str, credentials: Mapping[str, str]
    ) -> dict[str, str]:
        self._validate_channel(channel)
        values = {str(key): str(value) for key, value in credentials.items()}
        expected = set(_ENV_FIELDS[channel])
        if set(values) != expected:
            names = ", ".join(sorted(expected))
            raise CredentialError(f"{channel} credentials require exactly: {names}")
        if any(not value for value in values.values()):
            raise CredentialError("credentials must contain non-empty values")
        return values

    @staticmethod
    def _secret_name(channel: str) -> str:
        return f"intelliknow-{channel}-credentials"

    def _decode_secret(self, channel: str, payload: bytes) -> dict[str, str]:
        try:
            values = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CredentialError("Stored credentials are invalid; re-enter them.") from exc
        if not isinstance(values, dict):
            raise CredentialError("Stored credentials are invalid; re-enter them.")
        return self._validate_credentials(channel, values)

    def _migrate_legacy_credentials(self, channel: str) -> None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(
                    integrations.c.credentials_encrypted,
                    integrations.c.active_secret_version,
                ).where(integrations.c.channel == channel)
            ).one_or_none()
        if (
            row is None
            or row.credentials_encrypted is None
            or row.active_secret_version is not None
        ):
            return
        if self._fernet is None:
            raise CredentialError(
                "Legacy integration credentials require CREDENTIAL_ENCRYPTION_KEY "
                "for one-time migration"
            )
        try:
            payload = self._fernet.decrypt(row.credentials_encrypted.encode("ascii"))
            values = self._decode_secret(channel, payload)
        except (InvalidToken, ValueError, TypeError, CredentialError) as exc:
            raise CredentialError(
                "Legacy credentials cannot be decrypted; re-enter them."
            ) from exc

        try:
            reference = self._secret_store.put(
                self._secret_name(channel),
                json.dumps(values, sort_keys=True).encode("utf-8"),
                tags={"application": "intelliknow", "channel": channel},
            )
        except SecretStoreError as exc:
            raise CredentialError(
                "Legacy credential migration could not reach the secret store; "
                "the original encrypted value was preserved."
            ) from exc
        now = _iso(self._clock())
        try:
            with self._engine.begin() as conn:
                result = conn.execute(
                    integrations.update()
                    .where(
                        integrations.c.channel == channel,
                        integrations.c.credentials_encrypted
                        == row.credentials_encrypted,
                        integrations.c.active_secret_version.is_(None),
                    )
                    .values(
                        credentials_encrypted=None,
                        secret_name=reference.name,
                        active_secret_version=reference.version,
                        credential_type=_CREDENTIAL_TYPES[channel],
                        credential_status="unverified",
                        credential_configured_at=now,
                        updated_at=now,
                    )
                )
            if result.rowcount != 1:
                self._secret_store.disable(reference)
        except Exception:
            self._secret_store.disable(reference)
            raise

    def save_credentials(self, channel: str, credentials: Mapping[str, str]) -> None:
        values = self._validate_credentials(channel, credentials)
        payload = json.dumps(values, sort_keys=True).encode("utf-8")
        try:
            reference = self._secret_store.put(
                self._secret_name(channel),
                payload,
                tags={"application": "intelliknow", "channel": channel},
            )
        except SecretStoreError as exc:
            raise CredentialError(
                "Credential storage is unavailable; the submitted credential was not saved."
            ) from exc

        now = _iso(self._clock())
        try:
            with self._engine.begin() as conn:
                current = conn.execute(
                    select(integrations.c.active_secret_version).where(
                        integrations.c.channel == channel
                    )
                ).scalar_one_or_none()
                existing = conn.execute(
                    select(integrations.c.channel).where(
                        integrations.c.channel == channel
                    )
                ).first()
                values_to_store = dict(
                    credentials_encrypted=None,
                    secret_name=reference.name,
                    active_secret_version=reference.version,
                    previous_secret_version=current,
                    pending_secret_version=None,
                    credential_type=_CREDENTIAL_TYPES[channel],
                    credential_status="unverified",
                    credential_configured_at=now,
                    credential_verified_at=None,
                    credential_verification_error=None,
                    status="disconnected",
                    last_error=None,
                    last_error_at=None,
                    updated_at=now,
                )
                if existing is None:
                    conn.execute(
                        insert(integrations).values(
                            channel=channel,
                            display_name=_DISPLAY_NAMES[channel],
                            enabled=False,
                            **values_to_store,
                        )
                    )
                else:
                    conn.execute(
                        integrations.update()
                        .where(integrations.c.channel == channel)
                        .values(**values_to_store)
                    )
        except Exception:
            self._secret_store.disable(reference)
            raise
        self._cache.pop(channel, None)

    def load_credentials(self, channel: str) -> Credentials | None:
        self._validate_channel(channel)
        with self._engine.connect() as conn:
            row = conn.execute(
                select(
                    integrations.c.secret_name,
                    integrations.c.active_secret_version,
                    integrations.c.credentials_encrypted,
                ).where(integrations.c.channel == channel)
            ).one_or_none()

        if row is not None and row.credentials_encrypted is not None:
            self._migrate_legacy_credentials(channel)
            return self.load_credentials(channel)

        if row is not None and row.secret_name and row.active_secret_version:
            cached = self._cache.get(channel)
            now = time.monotonic()
            if (
                cached is not None
                and cached[0] > now
                and cached[1] == row.active_secret_version
            ):
                return cached[2]
            reference = SecretReference(row.secret_name, row.active_secret_version)
            try:
                values = self._decode_secret(channel, self._secret_store.get(reference))
            except (SecretStoreError, CredentialError) as exc:
                message = (
                    "Stored credentials are unavailable; retry after restoring "
                    "secret-store access."
                )
                self.mark_disconnected(channel, message)
                raise CredentialError(message) from exc
            result = Credentials(values=values, source="stored")
            if self._cache_ttl_seconds > 0:
                self._cache[channel] = (
                    now + self._cache_ttl_seconds,
                    row.active_secret_version,
                    result,
                )
            return result

        if self._allow_environment_fallback:
            env_fields = _ENV_FIELDS[channel]
            values = {
                name: self._env.get(variable, "") for name, variable in env_fields.items()
            }
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
        self._validate_channel(channel)
        with self._engine.connect() as conn:
            row = conn.execute(
                select(
                    integrations.c.secret_name,
                    integrations.c.active_secret_version,
                    integrations.c.previous_secret_version,
                    integrations.c.pending_secret_version,
                ).where(integrations.c.channel == channel)
            ).one_or_none()
        self._upsert(
            channel,
            credentials_encrypted=None,
            secret_name=None,
            active_secret_version=None,
            previous_secret_version=None,
            pending_secret_version=None,
            credential_type=None,
            credential_status="unconfigured",
            external_identity=None,
            credential_configured_at=None,
            credential_verified_at=None,
            credential_verification_error=None,
            enabled=False,
            status="disconnected",
            last_reply_ref=None,
        )
        self._cache.pop(channel, None)
        if row is not None and row.secret_name:
            for version in {
                row.active_secret_version,
                row.previous_secret_version,
                row.pending_secret_version,
            } - {None}:
                self._secret_store.disable(SecretReference(row.secret_name, version))

    def set_enabled(self, channel: str, enabled: bool) -> None:
        values = {"enabled": enabled}
        if not enabled:
            values["status"] = "disconnected"
        self._upsert(channel, **values)
        if not enabled:
            self._cache.pop(channel, None)

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
