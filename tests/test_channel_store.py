from __future__ import annotations

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import select

from app.channels.store import ChannelStore, CredentialError
from app.channels.store import ensure_no_legacy_secret_references
from app.db import create_engine_for, init_schema, integrations


@pytest.fixture
def engine(tmp_path):
    value = create_engine_for(tmp_path / "channels.db")
    init_schema(value)
    return value


@pytest.fixture
def key() -> str:
    return Fernet.generate_key().decode("ascii")


def test_credentials_are_encrypted_round_trip_and_masked(engine, key):
    store = ChannelStore(engine, key)

    store.save_credentials("telegram", {"token": "telegram-secret-7890"})

    with engine.connect() as conn:
        encrypted = conn.execute(
            select(integrations.c.credentials_encrypted).where(
                integrations.c.channel == "telegram"
            )
        ).scalar_one()
    assert encrypted is not None
    assert "telegram-secret-7890" not in encrypted
    assert Fernet(key.encode("ascii")).decrypt(encrypted.encode("ascii")) == (
        b'{"token": "telegram-secret-7890"}'
    )
    loaded = store.load_credentials("telegram")
    assert loaded is not None
    assert loaded.values == {"token": "telegram-secret-7890"}
    assert loaded.source == "stored"
    assert store.masked_credentials("telegram") == {
        "token": "****7890",
        "source": "stored",
    }


def test_encryption_key_is_required_and_validated(engine):
    with pytest.raises(CredentialError, match="required"):
        ChannelStore(engine, None)
    with pytest.raises(CredentialError, match="invalid"):
        ChannelStore(engine, "not-a-fernet-key")


def test_legacy_keychain_reference_fails_with_migration_command(engine):
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE integrations ADD COLUMN secret_name VARCHAR")
        conn.exec_driver_sql(
            "ALTER TABLE integrations ADD COLUMN active_secret_version VARCHAR"
        )
        conn.execute(
            integrations.insert().values(
                channel="telegram",
                display_name="Telegram",
                enabled=False,
                status="disconnected",
                updated_at="2026-08-13T00:00:00Z",
            )
        )
        conn.exec_driver_sql(
            "UPDATE integrations SET secret_name = ?, active_secret_version = ? "
            "WHERE channel = ?",
            ("intelliknow-telegram-credentials", "legacy-version", "telegram"),
        )

    with pytest.raises(CredentialError, match="migrate_keychain_credentials.py"):
        ensure_no_legacy_secret_references(engine)


def test_wrong_encryption_key_fails_closed_without_leaking_secret(engine, key):
    ChannelStore(engine, key).save_credentials(
        "telegram", {"token": "private-token"}
    )
    other_key = Fernet.generate_key().decode("ascii")

    with pytest.raises(CredentialError, match="cannot be decrypted") as raised:
        ChannelStore(engine, other_key).load_credentials("telegram")

    assert "private-token" not in str(raised.value)
    assert ChannelStore(engine, key).get("telegram").status == "disconnected"


def test_corrupted_ciphertext_returns_a_sanitized_error(engine, key):
    store = ChannelStore(engine, key)
    store.initialize("telegram", enabled=False)
    with engine.begin() as conn:
        conn.execute(
            integrations.update()
            .where(integrations.c.channel == "telegram")
            .values(credentials_encrypted="not-ciphertext-\N{SNOWMAN}")
        )

    with pytest.raises(CredentialError, match="cannot be decrypted") as raised:
        store.load_credentials("telegram")

    assert "not-ciphertext" not in str(raised.value)


def test_replacing_credentials_disconnects_stale_connected_state(engine, key):
    store = ChannelStore(engine, key)
    store.save_credentials("telegram", {"token": "old-secret"})
    store.set_enabled("telegram", True)
    store.mark_connected("telegram", "chat-id")

    store.save_credentials("telegram", {"token": "new-secret"})

    state = store.get("telegram")
    assert state.enabled is True
    assert state.status == "disconnected"
    assert state.last_reply_ref == "chat-id"


def test_channel_credentials_require_platform_specific_fields(engine, key):
    store = ChannelStore(engine, key)

    with pytest.raises(CredentialError, match="app_id, app_password, tenant_id"):
        store.save_credentials("teams", {"app_id": "id-only"})
    with pytest.raises(CredentialError, match="non-empty"):
        store.save_credentials("telegram", {"token": ""})


def test_initialize_never_overwrites_a_saved_enabled_choice(engine, key):
    store = ChannelStore(engine, key)
    store.set_enabled("telegram", False)

    store.initialize("telegram", enabled=True)

    assert store.get("telegram").enabled is False


def test_clearing_credentials_disables_and_disconnects_the_channel(engine, key):
    store = ChannelStore(engine, key)
    store.save_credentials("telegram", {"token": "secret"})
    store.set_enabled("telegram", True)

    store.clear_credentials("telegram")

    assert store.load_credentials("telegram") is None
    state = store.get("telegram")
    assert state.enabled is False
    assert state.status == "disconnected"


def test_status_error_and_reply_reference_survive_a_fresh_store(engine, key):
    store = ChannelStore(engine, key)
    store.set_enabled("teams", True)
    store.record_error("teams", "typing failed")
    store.mark_connected("teams", reply_ref='{"conversation":"abc"}')

    state = ChannelStore(engine, key).get("teams")

    assert state.enabled is True
    assert state.status == "connected"
    assert state.last_ok_at is not None
    assert state.last_error == "typing failed"
    assert state.last_error_at is not None
    assert state.last_reply_ref == '{"conversation":"abc"}'
