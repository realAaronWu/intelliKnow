"""Shared authentication dependency for administrative API routes."""

import hmac
from typing import Annotated, Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


def build_admin_auth(password: str) -> Callable[..., None]:
    """Return a bearer-token dependency bound to the configured password."""
    if not password:
        raise ValueError("ADMIN_PASSWORD must be set for administrative routes")

    bearer = HTTPBearer(auto_error=False)

    def require_admin(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> None:
        supplied = credentials.credentials if credentials is not None else ""
        if credentials is None or not hmac.compare_digest(supplied, password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid admin credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return require_admin
