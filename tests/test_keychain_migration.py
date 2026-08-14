from __future__ import annotations

import json
import sqlite3

from cryptography.fernet import Fernet
import pytest

from scripts import migrate_keychain_credentials as migration


def _legacy_database(path):
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE integrations (
            channel TEXT PRIMARY KEY,
            credentials_encrypted TEXT,
            secret_name TEXT,
            active_secret_version TEXT,
            previous_secret_version TEXT,
            pending_secret_version TEXT
        )
        """
    )
    connection.execute(
        "INSERT INTO integrations "
        "(channel, secret_name, active_secret_version) VALUES (?, ?, ?)",
        ("telegram", "intelliknow-telegram-credentials", "version-1"),
    )
    connection.commit()
    connection.close()


def test_migration_commits_ciphertext_before_deleting_keychain_item(
    tmp_path, monkeypatch
):
    database = tmp_path / "legacy.db"
    _legacy_database(database)
    key = Fernet.generate_key()
    deleted = []
    monkeypatch.setattr(
        migration,
        "_read_keychain",
        lambda service, account: b'{"token": "telegram-secret"}',
    )
    monkeypatch.setattr(
        migration,
        "_delete_keychain",
        lambda service, account: deleted.append((service, account)),
    )

    channels = migration.migrate(database, Fernet(key), service="IntelliKnow")

    connection = sqlite3.connect(database)
    row = connection.execute(
        "SELECT credentials_encrypted, secret_name, active_secret_version "
        "FROM integrations WHERE channel = 'telegram'"
    ).fetchone()
    connection.close()
    assert channels == ["telegram"]
    assert json.loads(Fernet(key).decrypt(row[0].encode("ascii"))) == {
        "token": "telegram-secret"
    }
    assert row[1:] == (None, None)
    assert deleted == [
        ("IntelliKnow", "intelliknow-telegram-credentials:version-1")
    ]


def test_invalid_legacy_value_rolls_back_and_keeps_keychain_item(
    tmp_path, monkeypatch
):
    database = tmp_path / "legacy.db"
    _legacy_database(database)
    deleted = []
    monkeypatch.setattr(
        migration,
        "_read_keychain",
        lambda service, account: b'{"wrong": "value"}',
    )
    monkeypatch.setattr(
        migration,
        "_delete_keychain",
        lambda service, account: deleted.append((service, account)),
    )

    with pytest.raises(RuntimeError, match="credentials are invalid"):
        migration.migrate(
            database, Fernet(Fernet.generate_key()), service="IntelliKnow"
        )

    connection = sqlite3.connect(database)
    row = connection.execute(
        "SELECT credentials_encrypted, secret_name, active_secret_version "
        "FROM integrations WHERE channel = 'telegram'"
    ).fetchone()
    connection.close()
    assert row == (None, "intelliknow-telegram-credentials", "version-1")
    assert deleted == []
