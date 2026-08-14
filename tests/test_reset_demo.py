from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

from app.db import create_engine_for, init_schema, integration_errors, integrations, query_log


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/reset_demo.py"
SPEC = importlib.util.spec_from_file_location("reset_demo", SCRIPT)
assert SPEC and SPEC.loader
reset_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reset_demo)


def test_clear_usage_data_preserves_integration_configuration(tmp_path: Path) -> None:
    database = tmp_path / "intelliknow.db"
    engine = create_engine_for(database)
    init_schema(engine)
    with engine.begin() as connection:
        connection.execute(
            query_log.insert().values(
                created_at="2026-08-13T00:00:00Z",
                channel="telegram",
                question="demo",
                fallback_used=False,
                status="answered",
            )
        )
        connection.execute(
            integrations.insert().values(
                channel="telegram",
                display_name="Telegram",
                enabled=True,
                credentials_encrypted="encrypted-token",
                status="error",
                last_error="timeout",
                last_error_at="2026-08-13T00:00:00Z",
                updated_at="2026-08-13T00:00:00Z",
            )
        )
        connection.execute(
            integration_errors.insert().values(
                channel="telegram",
                created_at="2026-08-13T00:00:00Z",
                reason="timeout",
            )
        )

    reset_demo.clear_usage_data(database)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM query_log").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM integration_errors").fetchone()[0] == 0
        row = connection.execute(
            "SELECT enabled, credentials_encrypted, last_error FROM integrations"
        ).fetchone()
    assert row == (1, "encrypted-token", None)


def test_remove_managed_uploads_is_idempotent(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "1.pdf").write_bytes(b"demo")
    nested = uploads / "keep-directory"
    nested.mkdir()

    assert reset_demo.remove_managed_uploads(uploads) == 1
    assert reset_demo.remove_managed_uploads(uploads) == 0
    assert nested.exists()
