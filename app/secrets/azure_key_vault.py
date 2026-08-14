"""Azure Key Vault provider using workload identity or managed identity."""

from __future__ import annotations

import base64
from typing import Mapping

from app.secrets.base import SecretNotFoundError, SecretReference, SecretStoreError


class AzureKeyVaultSecretStore:
    def __init__(self, vault_url: str, *, client=None) -> None:
        if client is None:
            try:
                from azure.identity import DefaultAzureCredential
                from azure.keyvault.secrets import SecretClient
            except ImportError as exc:  # pragma: no cover - packaging guard
                raise SecretStoreError(
                    "Azure Key Vault dependencies are not installed"
                ) from exc
            client = SecretClient(
                vault_url=vault_url,
                credential=DefaultAzureCredential(),
            )
        self._client = client

    def put(
        self, name: str, value: bytes, *, tags: Mapping[str, str] | None = None
    ) -> SecretReference:
        encoded = base64.b64encode(value).decode("ascii")
        try:
            result = self._client.set_secret(name, encoded, tags=dict(tags or {}))
            version = result.properties.version
        except Exception as exc:
            raise SecretStoreError("Azure Key Vault write failed") from exc
        if not version:
            raise SecretStoreError("Azure Key Vault returned no secret version")
        return SecretReference(name=name, version=version)

    def get(self, reference: SecretReference) -> bytes:
        try:
            result = self._client.get_secret(reference.name, reference.version)
            return base64.b64decode(result.value, validate=True)
        except Exception as exc:
            # Azure exceptions deliberately stay behind this boundary so
            # endpoint responses and logs cannot expose provider internals.
            raise SecretNotFoundError(
                "Azure Key Vault secret version is unavailable"
            ) from exc

    def disable(self, reference: SecretReference) -> None:
        try:
            self._client.update_secret_properties(
                reference.name, reference.version, enabled=False
            )
        except Exception as exc:
            raise SecretStoreError("Azure Key Vault disable failed") from exc
