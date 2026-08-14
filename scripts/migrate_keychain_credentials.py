"""Migrate credentials written by the former macOS Keychain implementation."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import yaml
from cryptography.fernet import Fernet
from dotenv import dotenv_values

EXPECTED_FIELDS = {
    "telegram": {"token"},
    "teams": {"app_id", "app_password"},
}


def _load_settings(config_path: Path, env_path: Path) -> tuple[Path, Fernet]:
    config = yaml.safe_load(config_path.read_text()) or {}
    database = Path(config.get("storage", {}).get("sqlite_path", "./data/intelliknow.db"))
    if not database.is_absolute():
        database = config_path.parent / database
    values = {**dotenv_values(env_path), **os.environ}
    key = values.get("CREDENTIAL_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY is missing")
    try:
        return database.resolve(), Fernet(str(key).encode("ascii"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY is invalid") from exc


def _read_keychain(service: str, account: str) -> bytes:
    result = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Keychain credential is unavailable for account {account}")
    return result.stdout.rstrip(b"\n")


def _delete_keychain(service: str, account: str) -> None:
    subprocess.run(
        ["security", "delete-generic-password", "-s", service, "-a", account],
        check=False,
        capture_output=True,
    )


def migrate(database: Path, fernet: Fernet, *, service: str) -> list[str]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    migrated: list[tuple[str, str]] = []
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(integrations)")}
        if not {"secret_name", "active_secret_version"} <= columns:
            return []
        rows = connection.execute(
            "SELECT channel, secret_name, active_secret_version "
            "FROM integrations WHERE credentials_encrypted IS NULL "
            "AND secret_name IS NOT NULL AND active_secret_version IS NOT NULL"
        ).fetchall()
        for row in rows:
            channel = row["channel"]
            account = f'{row["secret_name"]}:{row["active_secret_version"]}'
            payload = _read_keychain(service, account)
            try:
                values = json.loads(payload)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"Stored {channel} credentials are invalid") from exc
            if (
                channel not in EXPECTED_FIELDS
                or not isinstance(values, dict)
                or set(values) != EXPECTED_FIELDS[channel]
                or not all(isinstance(value, str) and value for value in values.values())
            ):
                raise RuntimeError(f"Stored {channel} credentials are invalid")
            ciphertext = fernet.encrypt(
                json.dumps(values, sort_keys=True).encode("utf-8")
            ).decode("ascii")
            result = connection.execute(
                "UPDATE integrations SET credentials_encrypted = ?, "
                "secret_name = NULL, active_secret_version = NULL, "
                "previous_secret_version = NULL, pending_secret_version = NULL "
                "WHERE channel = ? AND credentials_encrypted IS NULL "
                "AND secret_name = ? AND active_secret_version = ?",
                (
                    ciphertext,
                    channel,
                    row["secret_name"],
                    row["active_secret_version"],
                ),
            )
            if result.rowcount != 1:
                raise RuntimeError(f"The {channel} record changed during migration")
            migrated.append((channel, account))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    for _, account in migrated:
        _delete_keychain(service, account)
    return [channel for channel, _ in migrated]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--service", default="IntelliKnow")
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise RuntimeError("The legacy credential provider existed only on macOS")
    database, fernet = _load_settings(args.config.resolve(), args.env.resolve())
    channels = migrate(database, fernet, service=args.service)
    if channels:
        print("Migrated credential(s): " + ", ".join(channels))
    else:
        print("No legacy Keychain credentials require migration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
