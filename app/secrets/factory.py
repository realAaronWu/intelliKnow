"""Secret-store composition from validated application configuration."""

from __future__ import annotations

from app.config import SecretStoreConfig
from app.secrets.azure_key_vault import AzureKeyVaultSecretStore
from app.secrets.base import SecretStore
from app.secrets.macos_keychain import MacOSKeychainSecretStore


def build_secret_store(cfg: SecretStoreConfig) -> SecretStore:
    if cfg.provider == "macos-keychain":
        return MacOSKeychainSecretStore(service=cfg.keychain_service)
    if cfg.provider == "azure-key-vault":
        assert cfg.azure_vault_url is not None
        return AzureKeyVaultSecretStore(cfg.azure_vault_url)
    raise ValueError(f"unsupported secret store provider: {cfg.provider!r}")
