"""Provider-neutral, versioned secret storage contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol
from uuid import uuid4


class SecretStoreError(RuntimeError):
    """A secret backend operation failed without exposing secret material."""


class SecretNotFoundError(SecretStoreError):
    """The requested secret name/version does not exist or is disabled."""


@dataclass(frozen=True)
class SecretReference:
    name: str
    version: str


class SecretStore(Protocol):
    def put(
        self, name: str, value: bytes, *, tags: Mapping[str, str] | None = None
    ) -> SecretReference: ...

    def get(self, reference: SecretReference) -> bytes: ...

    def disable(self, reference: SecretReference) -> None: ...


class MemorySecretStore:
    """Deterministic process-local provider for tests and dependency injection."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, str], bytes] = {}

    def put(
        self, name: str, value: bytes, *, tags: Mapping[str, str] | None = None
    ) -> SecretReference:
        del tags
        reference = SecretReference(name=name, version=uuid4().hex)
        self._values[(reference.name, reference.version)] = bytes(value)
        return reference

    def get(self, reference: SecretReference) -> bytes:
        try:
            return self._values[(reference.name, reference.version)]
        except KeyError as exc:
            raise SecretNotFoundError("secret version is unavailable") from exc

    def disable(self, reference: SecretReference) -> None:
        self._values.pop((reference.name, reference.version), None)
