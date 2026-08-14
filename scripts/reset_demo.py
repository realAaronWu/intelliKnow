#!/usr/bin/env python3
"""Reset IntelliKnow content and usage data for a fresh laptop demo."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import _default_intent_spaces, load_config  # noqa: E402


BASELINE_INTENTS = {space.slug for space in _default_intent_spaces()}


def runtime_api_url(root: Path = ROOT) -> str:
    runtime = dotenv_values(root / ".run/laptop-demo/runtime.env")
    scheme = "https" if runtime.get("INTELLIKNOW_HTTPS", "1") != "0" else "http"
    host = runtime.get("INTELLIKNOW_API_HOST", "127.0.0.1")
    port = runtime.get("INTELLIKNOW_API_PORT", "8000")
    return f"{scheme}://{host}:{port}"


def tls_verification(root: Path, api_url: str, ca_cert: Path | None) -> str | bool:
    if not api_url.startswith("https://"):
        return True
    candidate = ca_cert or root / ".run/laptop-demo/tls/rootCA.pem"
    if not candidate.exists():
        raise SystemExit(
            f"HTTPS is enabled but the CA certificate was not found at {candidate}. "
            "Pass --ca-cert with the correct path."
        )
    return str(candidate)


def delete_documents_and_custom_intents(client: httpx.Client) -> tuple[int, list[str]]:
    response = client.get("/documents")
    response.raise_for_status()
    documents: list[dict[str, Any]] = response.json()
    for document in documents:
        result = client.delete(f"/documents/{document['id']}")
        result.raise_for_status()

    response = client.get("/admin/intents")
    response.raise_for_status()
    custom_slugs = [
        intent["slug"]
        for intent in response.json()
        if intent["slug"] not in BASELINE_INTENTS
    ]
    for slug in custom_slugs:
        result = client.delete(f"/admin/intents/{slug}")
        result.raise_for_status()
    return len(documents), custom_slugs


def clear_usage_data(database: Path) -> None:
    if not database.exists():
        raise SystemExit(f"IntelliKnow database not found at {database}")
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM query_log")
        connection.execute("DELETE FROM integration_errors")
        connection.execute(
            """
            UPDATE integrations
               SET last_error = NULL,
                   last_error_at = NULL
            """
        )


def remove_managed_uploads(upload_dir: Path) -> int:
    if not upload_dir.exists():
        return 0
    removed = 0
    for path in upload_dir.iterdir():
        if path.is_file() or path.is_symlink():
            path.unlink()
            removed += 1
    return removed


def database_counts(database: Path) -> dict[str, int]:
    with sqlite3.connect(database) as connection:
        return {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in ("documents", "chunks", "query_log", "integration_errors")
        }


def parse_args() -> argparse.Namespace:
    values = {**dotenv_values(ROOT / ".env"), **os.environ}
    parser = argparse.ArgumentParser(
        description=(
            "Delete all documents, custom intents, query analytics, integration "
            "errors, and managed upload copies while preserving baseline intents "
            "and frontend integration credentials."
        )
    )
    parser.add_argument("--yes", action="store_true", help="confirm destructive reset")
    parser.add_argument("--api-url", default=runtime_api_url())
    parser.add_argument("--admin-password", default=values.get("ADMIN_PASSWORD"))
    parser.add_argument("--ca-cert", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.yes:
        raise SystemExit(
            "Refusing to erase demo data without confirmation. Re-run with --yes."
        )
    if not args.admin_password:
        raise SystemExit("ADMIN_PASSWORD is required in .env or --admin-password")

    config = load_config(ROOT / "config.yaml")
    database = (ROOT / config.storage.sqlite_path).resolve()
    upload_dir = (ROOT / config.storage.upload_dir).resolve()
    verify = tls_verification(ROOT, args.api_url, args.ca_cert)

    try:
        with httpx.Client(
            base_url=args.api_url.rstrip("/"),
            headers={"Authorization": f"Bearer {args.admin_password}"},
            verify=verify,
            timeout=60,
        ) as client:
            document_count, custom_intents = delete_documents_and_custom_intents(client)
    except httpx.HTTPError as exc:
        raise SystemExit(
            f"Reset stopped because the IntelliKnow API could not complete cleanup: {exc}"
        ) from exc

    clear_usage_data(database)
    upload_count = remove_managed_uploads(upload_dir)
    counts = database_counts(database)
    if any(counts.values()):
        detail = ", ".join(f"{name}={count}" for name, count in counts.items())
        raise SystemExit(f"Reset verification failed: {detail}")

    print("IntelliKnow demo reset complete.")
    print(f"  Documents deleted: {document_count}")
    print(f"  Custom intents deleted: {', '.join(custom_intents) or 'none'}")
    print(f"  Managed upload files deleted: {upload_count}")
    print("  Query history, analytics metrics, and integration errors: cleared")
    print("  Baseline intents and frontend integration credentials: preserved")


if __name__ == "__main__":
    main()
