"""Password bootstrap and time-limited sessions for administrative routes."""

import base64
import binascii
import hmac
import json
import secrets
import time
from hashlib import sha256
from typing import Annotated, Callable

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

ADMIN_SESSION_COOKIE = "intelliknow_admin_session"
ADMIN_SESSION_SECONDS = 8 * 60 * 60
_BROWSER_TICKET_SECONDS = 60


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class AdminSessionManager:
    def __init__(self, password: str, *, clock=time.time) -> None:
        if not password:
            raise ValueError("ADMIN_PASSWORD must be set for administrative routes")
        self._password = password
        self._signing_key = sha256(
            b"intelliknow-admin-session-v1\0" + password.encode("utf-8")
        ).digest()
        self._clock = clock
        self._tickets: dict[str, tuple[float, str]] = {}

    def issue(self) -> tuple[str, str, int]:
        expires_at = int(self._clock()) + ADMIN_SESSION_SECONDS
        payload = _b64encode(
            json.dumps(
                {"exp": expires_at, "nonce": secrets.token_urlsafe(12)},
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        signature = _b64encode(
            hmac.digest(self._signing_key, payload.encode("ascii"), "sha256")
        )
        token = f"{payload}.{signature}"
        ticket = secrets.token_urlsafe(32)
        self._tickets[ticket] = (
            self._clock() + _BROWSER_TICKET_SECONDS,
            token,
        )
        self._discard_expired_tickets()
        return token, ticket, expires_at

    def verify_password(self, supplied: str) -> bool:
        return hmac.compare_digest(supplied, self._password)

    def verify_token(self, token: str) -> bool:
        try:
            payload, supplied_signature = token.split(".", 1)
            expected_signature = _b64encode(
                hmac.digest(self._signing_key, payload.encode("ascii"), "sha256")
            )
            if not hmac.compare_digest(supplied_signature, expected_signature):
                return False
            claims = json.loads(_b64decode(payload))
            expires_at = int(claims["exp"])
        except (
            ValueError,
            TypeError,
            KeyError,
            UnicodeDecodeError,
            binascii.Error,
            json.JSONDecodeError,
        ):
            return False
        return expires_at > self._clock()

    def authenticate(self, supplied: str) -> bool:
        return self.verify_password(supplied) or self.verify_token(supplied)

    def consume_ticket(self, ticket: str) -> str | None:
        value = self._tickets.pop(ticket, None)
        if value is None:
            return None
        expires_at, token = value
        return token if expires_at > self._clock() else None

    def _discard_expired_tickets(self) -> None:
        now = self._clock()
        self._tickets = {
            ticket: value
            for ticket, value in self._tickets.items()
            if value[0] > now
        }


class LoginRequest(BaseModel):
    password: str


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid admin credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def build_admin_session_router(manager: AdminSessionManager) -> APIRouter:
    router = APIRouter(prefix="/admin/session")
    require_admin = build_admin_auth(manager)

    @router.get("", dependencies=[Depends(require_admin)])
    def session() -> dict:
        return {"authenticated": True}

    @router.post("/login")
    def login(body: LoginRequest) -> dict:
        if not manager.verify_password(body.password):
            raise _unauthorized()
        token, browser_ticket, expires_at = manager.issue()
        return {
            "token": token,
            "browser_ticket": browser_ticket,
            "expires_at": expires_at,
            "expires_in_seconds": ADMIN_SESSION_SECONDS,
        }

    @router.get("/browser", response_class=HTMLResponse)
    def browser_session(ticket: str, request: Request) -> HTMLResponse:
        token = manager.consume_ticket(ticket)
        if token is None:
            raise _unauthorized()
        response = HTMLResponse("<!doctype html><title>Signed in</title>")
        response.set_cookie(
            ADMIN_SESSION_COOKIE,
            token,
            max_age=ADMIN_SESSION_SECONDS,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'none'"
        return response

    @router.get("/logout", response_class=HTMLResponse)
    def logout() -> HTMLResponse:
        response = HTMLResponse("<!doctype html><title>Signed out</title>")
        response.delete_cookie(
            ADMIN_SESSION_COOKIE,
            httponly=True,
            samesite="strict",
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'none'"
        return response

    return router


def build_admin_auth(
    password: str | AdminSessionManager,
) -> Callable[..., None]:
    """Return a bearer-token dependency bound to the configured password."""
    manager = password if isinstance(password, AdminSessionManager) else AdminSessionManager(password)

    bearer = HTTPBearer(auto_error=False)

    def require_admin(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> None:
        supplied = credentials.credentials if credentials is not None else ""
        if credentials is None or not manager.authenticate(supplied):
            raise _unauthorized()

    return require_admin
