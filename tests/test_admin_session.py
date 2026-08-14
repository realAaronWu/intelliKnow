from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.auth import (
    ADMIN_SESSION_COOKIE,
    ADMIN_SESSION_SECONDS,
    AdminSessionManager,
    build_admin_auth,
    build_admin_session_router,
)


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


def _app(manager: AdminSessionManager) -> FastAPI:
    app = FastAPI()
    app.include_router(build_admin_session_router(manager))

    @app.get("/protected", dependencies=[Depends(build_admin_auth(manager))])
    def protected() -> dict:
        return {"ok": True}

    return app


def test_login_issues_eight_hour_token_accepted_by_admin_auth() -> None:
    clock = Clock()
    client = TestClient(_app(AdminSessionManager("correct horse", clock=clock)))

    assert client.post(
        "/admin/session/login", json={"password": "wrong"}
    ).status_code == 401
    response = client.post(
        "/admin/session/login", json={"password": "correct horse"}
    )

    assert response.status_code == 200
    session = response.json()
    assert session["expires_in_seconds"] == ADMIN_SESSION_SECONDS
    assert session["expires_at"] == int(clock.now) + ADMIN_SESSION_SECONDS
    headers = {"Authorization": f"Bearer {session['token']}"}
    assert client.get("/admin/session", headers=headers).status_code == 200
    assert client.get("/protected", headers=headers).json() == {"ok": True}


def test_token_expires_and_tampering_is_rejected() -> None:
    clock = Clock()
    manager = AdminSessionManager("correct horse", clock=clock)
    client = TestClient(_app(manager))
    token, _, _ = manager.issue()

    assert client.get(
        "/protected", headers={"Authorization": f"Bearer {token}x"}
    ).status_code == 401
    clock.now += ADMIN_SESSION_SECONDS
    assert client.get(
        "/protected", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 401


def test_browser_ticket_is_one_time_and_sets_secure_cookie_attributes() -> None:
    manager = AdminSessionManager("correct horse")
    client = TestClient(_app(manager), base_url="https://kms.example.com")
    session = client.post(
        "/admin/session/login", json={"password": "correct horse"}
    ).json()

    response = client.get(
        "/admin/session/browser", params={"ticket": session["browser_ticket"]}
    )

    cookie = response.headers["set-cookie"]
    assert response.status_code == 200
    assert cookie.startswith(f"{ADMIN_SESSION_COOKIE}=")
    assert f"Max-Age={ADMIN_SESSION_SECONDS}" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Secure" in cookie
    assert response.headers["cache-control"] == "no-store"
    assert client.get(
        "/admin/session/browser", params={"ticket": session["browser_ticket"]}
    ).status_code == 401


def test_browser_ticket_expires_after_handoff_window() -> None:
    clock = Clock()
    manager = AdminSessionManager("correct horse", clock=clock)
    client = TestClient(_app(manager))
    _, ticket, _ = manager.issue()

    clock.now += 60

    assert client.get(
        "/admin/session/browser", params={"ticket": ticket}
    ).status_code == 401


def test_logout_expires_browser_cookie() -> None:
    client = TestClient(_app(AdminSessionManager("correct horse")))

    response = client.get("/admin/session/logout")

    cookie = response.headers["set-cookie"]
    assert response.status_code == 200
    assert cookie.startswith(f"{ADMIN_SESSION_COOKIE}=\"")
    assert "Max-Age=0" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie


def test_password_bearer_remains_compatible_for_existing_clients() -> None:
    client = TestClient(_app(AdminSessionManager("correct horse")))

    response = client.get(
        "/protected", headers={"Authorization": "Bearer correct horse"}
    )

    assert response.status_code == 200
