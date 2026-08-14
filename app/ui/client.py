"""HTTP/HTTPS client for the IntelliKnow administration API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx


class APIError(RuntimeError):
    pass


def _verify_for_url(base_url: str) -> bool | str:
    if urlparse(base_url).scheme.lower() != "https":
        return True
    return os.getenv("INTELLIKNOW_CA_CERT") or True


@dataclass(frozen=True)
class APIClient:
    base_url: str
    token: str
    timeout: float = 30.0

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            with httpx.Client(
                base_url=self.base_url.rstrip("/"),
                headers=headers,
                timeout=self.timeout,
                trust_env=False,
                verify=_verify_for_url(self.base_url),
            ) as client:
                response = client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise APIError(
                f"Cannot reach IntelliKnow at {self.base_url}. Check that the API is running."
            ) from exc
        if response.is_error:
            try:
                body = response.json()
                detail = body.get("detail", body) if isinstance(body, dict) else body
            except ValueError:
                detail = response.text or response.reason_phrase
            raise APIError(str(detail))
        return response

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params).json()

    def post(self, path: str, *, json: dict | None = None) -> Any:
        return self._request("POST", path, json=json).json()

    def put(self, path: str, *, json: dict) -> Any:
        return self._request("PUT", path, json=json).json()

    def patch(self, path: str, *, json: dict) -> Any:
        return self._request("PATCH", path, json=json).json()

    def delete(self, path: str) -> Any:
        response = self._request("DELETE", path)
        return response.json() if response.content else {}

    def upload(self, filename: str, content: bytes, content_type: str) -> Any:
        return self._request(
            "POST",
            "/documents",
            files={"file": (filename, content, content_type)},
        ).json()

    def csv(self, path: str, *, params: dict[str, Any] | None = None) -> bytes:
        return self._request("GET", path, params=params).content
