from __future__ import annotations

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import select

from app.channels.store import ChannelStore, CredentialError
from app.db import create_engine_for, init_schema, integrations
from app.secrets import MemorySecretStore, SecretStoreError


@pytest.fixture
def engine(tmp_path):
    value = create_engine_for(tmp_path / "channels.db")
    init_schema(value)
    return value


@pytest.fixture
def key() -> str:
    return Fernet.generate_key().decode("ascii")


def test_credentials_are_externalized_round_trip_and_masked(engine):
    secrets = MemorySecretStore()
    store = ChannelStore(engine, secret_store=secrets)

    store.save_credentials("telegram", {"token": "telegram-secret-7890"})

    with engine.connect() as conn:
        row = conn.execute(
            select(
                integrations.c.credentials_encrypted,
                integrations.c.secret_name,
                integrations.c.active_secret_version,
            ).where(integrations.c.channel == "telegram")
        ).one()
    assert row.credentials_encrypted is None
    assert row.secret_name == "intelliknow-telegram-credentials"
    assert row.active_secret_version
    loaded = store.load_credentials("telegram")
    assert loaded is not None
    assert loaded.values == {"token": "telegram-secret-7890"}
    assert loaded.source == "stored"
    assert store.masked_credentials("telegram") == {
        "token": "****7890",
        "source": "stored",
    }


def test_replacing_credentials_disconnects_stale_connected_state(engine, key):
    store = ChannelStore(engine, key, secret_store=MemorySecretStore())
    store.save_credentials("telegram", {"token": "old-secret"})
    store.set_enabled("telegram", True)
    store.mark_connected("telegram", "chat-id")

    store.save_credentials("telegram", {"token": "new-secret"})

    state = store.get("telegram")
    assert state.enabled is True
    assert state.status == "disconnected"
    assert state.last_reply_ref == "chat-id"


def test_channel_credentials_require_the_platform_specific_fields(engine, key):
    store = ChannelStore(engine, key, secret_store=MemorySecretStore())

    with pytest.raises(CredentialError, match="app_id, app_password"):
        store.save_credentials("teams", {"app_id": "id-only"})


def test_fresh_store_does_not_require_a_legacy_encryption_key(engine):
    store = ChannelStore(engine, secret_store=MemorySecretStore())

    store.save_credentials("telegram", {"token": "secret"})

    assert store.load_credentials("telegram") is not None


def test_invalid_legacy_encryption_key_is_rejected(engine):
    with pytest.raises(CredentialError, match="CREDENTIAL_ENCRYPTION_KEY"):
        ChannelStore(engine, "not-a-fernet-key", secret_store=MemorySecretStore())


def test_environment_credentials_are_ignored_by_default(engine, key):
    store = ChannelStore(
        engine,
        key,
        secret_store=MemorySecretStore(),
        env={"TELEGRAM_BOT_TOKEN": "environment-token-1234"},
    )

    assert store.load_credentials("telegram") is None


def test_environment_fallback_requires_an_explicit_legacy_opt_in(engine, key):
    store = ChannelStore(
        engine,
        key,
        secret_store=MemorySecretStore(),
        env={"TELEGRAM_BOT_TOKEN": "environment-token-1234"},
        allow_environment_fallback=True,
    )

    loaded = store.load_credentials("telegram")
    assert loaded is not None
    assert loaded.values == {"token": "environment-token-1234"}
    assert loaded.source == "environment"
    assert store.masked_credentials("telegram")["source"] == "environment"


def test_first_run_state_does_not_hide_environment_credentials(engine, key):
    store = ChannelStore(
        engine,
        key,
        secret_store=MemorySecretStore(),
        env={"TELEGRAM_BOT_TOKEN": "environment-token-1234"},
        allow_environment_fallback=True,
    )

    store.initialize("telegram", enabled=True)

    assert store.get("telegram").enabled is True
    assert store.load_credentials("telegram").source == "environment"


def test_initialize_never_overwrites_a_saved_enabled_choice(engine, key):
    store = ChannelStore(engine, key, secret_store=MemorySecretStore())
    store.set_enabled("telegram", False)

    store.initialize("telegram", enabled=True)

    assert store.get("telegram").enabled is False


def test_clearing_credentials_disables_and_disconnects_the_channel(engine, key):
    store = ChannelStore(engine, key, secret_store=MemorySecretStore())
    store.save_credentials("telegram", {"token": "secret"})
    store.set_enabled("telegram", True)

    store.clear_credentials("telegram")

    assert store.load_credentials("telegram") is None
    state = store.get("telegram")
    assert state.enabled is False
    assert state.status == "disconnected"


def test_status_error_and_reply_reference_survive_a_fresh_store(engine, key):
    store = ChannelStore(engine, key, secret_store=MemorySecretStore())
    store.set_enabled("teams", True)
    store.record_error("teams", "typing failed")
    store.mark_connected("teams", reply_ref='{"conversation":"abc"}')

    state = ChannelStore(engine, key, secret_store=MemorySecretStore()).get("teams")

    assert state.enabled is True
    assert state.status == "connected"
    assert state.last_ok_at is not None
    assert state.last_error == "typing failed"
    assert state.last_error_at is not None
    assert state.last_reply_ref == '{"conversation":"abc"}'


def test_legacy_ciphertext_is_migrated_once_and_cleared(engine, key):
    encrypted = Fernet(key.encode("ascii")).encrypt(
        b'{"token": "legacy-secret"}'
    ).decode("ascii")
    with engine.begin() as conn:
        conn.execute(
            integrations.insert().values(
                channel="telegram",
                display_name="Telegram",
                enabled=True,
                credentials_encrypted=encrypted,
                status="disconnected",
                updated_at="2026-08-12T00:00:00Z",
            )
        )
    secrets = MemorySecretStore()
    store = ChannelStore(engine, key, secret_store=secrets)

    store.initialize("telegram", enabled=False)

    loaded = store.load_credentials("telegram")
    assert loaded is not None
    assert loaded.values == {"token": "legacy-secret"}
    with engine.connect() as conn:
        row = conn.execute(
            select(
                integrations.c.credentials_encrypted,
                integrations.c.active_secret_version,
            ).where(integrations.c.channel == "telegram")
        ).one()
    assert row.credentials_encrypted is None
    assert row.active_secret_version


def test_failed_legacy_migration_preserves_the_ciphertext(engine, key):
    class FailingSecretStore(MemorySecretStore):
        def put(self, name, value, *, tags=None):
            raise SecretStoreError("offline")

    encrypted = Fernet(key.encode("ascii")).encrypt(
        b'{"token": "legacy-secret"}'
    ).decode("ascii")
    with engine.begin() as conn:
        conn.execute(
            integrations.insert().values(
                channel="telegram",
                display_name="Telegram",
                enabled=True,
                credentials_encrypted=encrypted,
                status="disconnected",
                updated_at="2026-08-12T00:00:00Z",
            )
        )
    store = ChannelStore(engine, key, secret_store=FailingSecretStore())

    with pytest.raises(CredentialError, match="original encrypted value was preserved"):
        store.initialize("telegram", enabled=False)

    with engine.connect() as conn:
        persisted = conn.execute(
            select(integrations.c.credentials_encrypted).where(
                integrations.c.channel == "telegram"
            )
        ).scalar_one()
    assert persisted == encrypted


def test_runtime_read_fails_closed_when_secret_store_is_unavailable(engine):
    class FailingReadSecretStore(MemorySecretStore):
        def get(self, reference):
            raise SecretStoreError("provider response containing sensitive details")

    secrets = FailingReadSecretStore()
    store = ChannelStore(engine, secret_store=secrets, cache_ttl_seconds=0)
    store.save_credentials("telegram", {"token": "private-token"})

    with pytest.raises(CredentialError, match="secret-store access") as raised:
        store.load_credentials("telegram")

    assert "private-token" not in str(raised.value)
    assert "sensitive details" not in str(raised.value)
    assert store.get("telegram").status == "disconnected"
