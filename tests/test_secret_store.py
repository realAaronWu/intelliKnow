from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.secrets import MemorySecretStore, SecretNotFoundError, SecretReference
from app.secrets.azure_key_vault import AzureKeyVaultSecretStore


def test_memory_store_versions_are_independent_and_disable_is_idempotent():
    store = MemorySecretStore()
    first = store.put("integration", b"first")
    second = store.put("integration", b"second")

    assert first.version != second.version
    assert store.get(first) == b"first"
    assert store.get(second) == b"second"

    store.disable(first)
    store.disable(first)
    with pytest.raises(SecretNotFoundError):
        store.get(first)
    assert store.get(second) == b"second"


@dataclass
class _Properties:
    version: str


@dataclass
class _Secret:
    value: str
    properties: _Properties


class _FakeAzureClient:
    def __init__(self):
        self.values = {}
        self.disabled = []

    def set_secret(self, name, value, *, tags):
        assert tags == {"channel": "telegram"}
        self.values[(name, "version-1")] = value
        return _Secret(value, _Properties("version-1"))

    def get_secret(self, name, version):
        return _Secret(self.values[(name, version)], _Properties(version))

    def update_secret_properties(self, name, version, *, enabled):
        self.disabled.append((name, version, enabled))


def test_azure_store_round_trips_binary_values_and_exact_versions():
    client = _FakeAzureClient()
    store = AzureKeyVaultSecretStore("https://unused.vault.azure.net", client=client)

    reference = store.put("integration", b"\x00secret\xff", tags={"channel": "telegram"})

    assert reference == SecretReference("integration", "version-1")
    assert store.get(reference) == b"\x00secret\xff"
    store.disable(reference)
    assert client.disabled == [("integration", "version-1", False)]
