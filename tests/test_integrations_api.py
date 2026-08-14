from __future__ import annotations

import asyncio

from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.auth import build_admin_auth
from app.api.integrations import build_integrations_router
from app.channels.store import ChannelStore
from app.channels.tester import ChannelTestResult
from app.db import create_engine_for, init_schema, integrations
from app.secrets import MemorySecretStore


class FakeTester:
    def __init__(self):
        self.calls = []
        self.result = ChannelTestResult(
            "telegram", True, "success", "complete", 234, None
        )

    async def run(self, channel, question):
        self.calls.append((channel, question))
        return self.result


def _app(tmp_path):
    engine = create_engine_for(tmp_path / "api.db")
    init_schema(engine)
    store = ChannelStore(
        engine,
        Fernet.generate_key().decode("ascii"),
        secret_store=MemorySecretStore(),
    )
    store.initialize("telegram", enabled=False)
    store.initialize("teams", enabled=False)
    tester = FakeTester()
    app = FastAPI()
    app.include_router(
        build_integrations_router(store, tester),
        dependencies=[Depends(build_admin_auth("admin-secret"))],
    )
    return app, engine, store, tester


def _client(app):
    return TestClient(app, headers={"Authorization": "Bearer admin-secret"})


def test_all_integration_routes_require_admin_bearer(tmp_path):
    app, _, _, _ = _app(tmp_path)

    assert TestClient(app).get("/admin/integrations").status_code == 401
    assert (
        TestClient(app)
        .put(
            "/admin/integrations/telegram",
            json={"credentials": {"token": "secret"}},
        )
        .status_code
        == 401
    )
    assert (
        TestClient(app)
        .post("/admin/integrations/telegram/test", json={})
        .status_code
        == 401
    )


def test_save_returns_masked_credential_and_persists_only_a_reference(tmp_path):
    app, engine, _, _ = _app(tmp_path)
    client = _client(app)

    response = client.put(
        "/admin/integrations/telegram",
        json={"credentials": {"token": "telegram-secret-7890"}, "enabled": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["configured"] is True
    assert body["credentials"] == {"token": "****7890", "source": "stored"}
    assert "telegram-secret-7890" not in response.text
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


def test_list_never_returns_the_reply_reference_or_plaintext(tmp_path):
    app, _, store, _ = _app(tmp_path)
    store.save_credentials("telegram", {"token": "private-token"})
    store.set_enabled("telegram", True)
    store.mark_connected("telegram", "private-chat-id")

    response = _client(app).get("/admin/integrations")

    assert response.status_code == 200
    assert "private-token" not in response.text
    assert "private-chat-id" not in response.text
    telegram = response.json()[0]
    assert telegram["has_reply_destination"] is True


def test_enable_requires_credentials_and_disable_retains_them(tmp_path):
    app, _, store, _ = _app(tmp_path)
    client = _client(app)

    missing = client.patch(
        "/admin/integrations/telegram/enabled", json={"enabled": True}
    )
    assert missing.status_code == 400

    store.save_credentials("telegram", {"token": "private-token"})
    store.set_enabled("telegram", True)
    disabled = client.patch(
        "/admin/integrations/telegram/enabled", json={"enabled": False}
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert store.load_credentials("telegram") is not None


def test_teams_can_be_enabled_without_credentials_for_loopback_emulator(tmp_path):
    app, _, store, _ = _app(tmp_path)

    response = _client(app).patch(
        "/admin/integrations/teams/enabled", json={"enabled": True}
    )

    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["configured"] is False
    assert store.is_enabled("teams") is True


def test_clear_removes_stored_credentials_and_destination(tmp_path):
    app, _, store, _ = _app(tmp_path)
    store.save_credentials("telegram", {"token": "private-token"})
    store.set_enabled("telegram", True)
    store.mark_connected("telegram", "chat-id")

    response = _client(app).delete("/admin/integrations/telegram")

    assert response.status_code == 200
    assert response.json()["configured"] is False
    assert response.json()["has_reply_destination"] is False
    assert store.load_credentials("telegram") is None


def test_test_endpoint_returns_stage_and_latency(tmp_path):
    app, _, _, tester = _app(tmp_path)

    response = _client(app).post(
        "/admin/integrations/telegram/test",
        json={"question": "What is the policy?"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "channel": "telegram",
        "ok": True,
        "status": "success",
        "stage": "complete",
        "latency_ms": 234,
        "error": None,
    }
    assert tester.calls == [("telegram", "What is the policy?")]


def test_test_endpoint_rejects_a_blank_question(tmp_path):
    app, _, _, tester = _app(tmp_path)

    response = _client(app).post(
        "/admin/integrations/telegram/test", json={"question": "   "}
    )

    assert response.status_code == 422
    assert tester.calls == []


def test_unknown_channel_is_404_and_wrong_credential_shape_is_400(tmp_path):
    app, _, _, _ = _app(tmp_path)
    client = _client(app)

    assert client.get("/admin/integrations/slack").status_code == 404
    response = client.put(
        "/admin/integrations/teams",
        json={"credentials": {"app_id": "id-only"}},
    )
    assert response.status_code == 400
    assert "app_password" in response.json()["detail"]
