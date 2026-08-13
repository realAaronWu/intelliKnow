from __future__ import annotations

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import select

from app.channels.store import ChannelStore, CredentialError
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
        raw = conn.execute(
            select(integrations.c.credentials_encrypted).where(
                integrations.c.channel == "telegram"
            )
        ).scalar_one()
    assert "telegram-secret-7890" not in raw
    loaded = store.load_credentials("telegram")
    assert loaded is not None
    assert loaded.values == {"token": "telegram-secret-7890"}
    assert loaded.source == "stored"
    assert store.masked_credentials("telegram") == {
        "token": "****7890",
        "source": "stored",
    }


def test_channel_credentials_require_the_platform_specific_fields(engine, key):
    store = ChannelStore(engine, key)

    with pytest.raises(CredentialError, match="app_id, app_password"):
        store.save_credentials("teams", {"app_id": "id-only"})


@pytest.mark.parametrize("key", ["", "not-a-fernet-key"])
def test_missing_or_invalid_encryption_key_is_rejected(engine, key):
    with pytest.raises(CredentialError, match="CREDENTIAL_ENCRYPTION_KEY"):
        ChannelStore(engine, key)


def test_environment_credentials_are_used_only_when_nothing_is_stored(engine, key):
    store = ChannelStore(
        engine,
        key,
        env={"TELEGRAM_BOT_TOKEN": "environment-token-1234"},
    )

    loaded = store.load_credentials("telegram")

    assert loaded is not None
    assert loaded.values == {"token": "environment-token-1234"}
    assert loaded.source == "environment"
    assert store.masked_credentials("telegram")["source"] == "environment"


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


def test_undecryptable_credentials_disconnect_without_crashing_the_store(engine, key):
    store = ChannelStore(engine, key)
    store.save_credentials("telegram", {"token": "secret"})
    with engine.begin() as conn:
        conn.execute(
            integrations.update()
            .where(integrations.c.channel == "telegram")
            .values(credentials_encrypted="corrupt")
        )

    with pytest.raises(CredentialError, match="re-enter"):
        store.load_credentials("telegram")

    assert store.get("telegram").status == "disconnected"
