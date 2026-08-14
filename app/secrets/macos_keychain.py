"""macOS Keychain secret provider for laptop deployments."""

from __future__ import annotations

import ctypes
import sys
from ctypes.util import find_library
from typing import Mapping
from uuid import uuid4

from app.secrets.base import SecretNotFoundError, SecretReference, SecretStoreError

_ERR_SEC_ITEM_NOT_FOUND = -25300


class MacOSKeychainSecretStore:
    def __init__(self, *, service: str = "IntelliKnow", security=None) -> None:
        if security is None:
            if sys.platform != "darwin":
                raise SecretStoreError("macOS Keychain is available only on macOS")
            path = find_library("Security")
            if not path:
                raise SecretStoreError("macOS Security framework is unavailable")
            security = ctypes.CDLL(path)
        self._service = service.encode("utf-8")
        self._security = security
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        void_p = ctypes.c_void_p
        uint32_p = ctypes.POINTER(ctypes.c_uint32)
        void_pp = ctypes.POINTER(void_p)
        self._security.SecKeychainAddGenericPassword.argtypes = [
            void_p,
            ctypes.c_uint32,
            void_p,
            ctypes.c_uint32,
            void_p,
            ctypes.c_uint32,
            void_p,
            void_pp,
        ]
        self._security.SecKeychainAddGenericPassword.restype = ctypes.c_int32
        self._security.SecKeychainFindGenericPassword.argtypes = [
            void_p,
            ctypes.c_uint32,
            void_p,
            ctypes.c_uint32,
            void_p,
            uint32_p,
            void_pp,
            void_pp,
        ]
        self._security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        self._security.SecKeychainItemFreeContent.argtypes = [void_p, void_p]
        self._security.SecKeychainItemFreeContent.restype = ctypes.c_int32
        self._security.SecKeychainItemDelete.argtypes = [void_p]
        self._security.SecKeychainItemDelete.restype = ctypes.c_int32

    @staticmethod
    def _account(reference: SecretReference) -> bytes:
        return f"{reference.name}:{reference.version}".encode("utf-8")

    def put(
        self, name: str, value: bytes, *, tags: Mapping[str, str] | None = None
    ) -> SecretReference:
        del tags
        reference = SecretReference(name=name, version=uuid4().hex)
        account = self._account(reference)
        status = self._security.SecKeychainAddGenericPassword(
            None,
            len(self._service),
            self._service,
            len(account),
            account,
            len(value),
            value,
            None,
        )
        if status != 0:
            raise SecretStoreError(f"macOS Keychain write failed (status {status})")
        return reference

    def _find(
        self, reference: SecretReference, *, include_item: bool = False
    ) -> tuple[ctypes.c_void_p | None, bytes]:
        account = self._account(reference)
        length = ctypes.c_uint32()
        data = ctypes.c_void_p()
        item = ctypes.c_void_p()
        status = self._security.SecKeychainFindGenericPassword(
            None,
            len(self._service),
            self._service,
            len(account),
            account,
            ctypes.byref(length),
            ctypes.byref(data),
            ctypes.byref(item) if include_item else None,
        )
        if status == _ERR_SEC_ITEM_NOT_FOUND:
            raise SecretNotFoundError("Keychain secret version is unavailable")
        if status != 0:
            raise SecretStoreError(f"macOS Keychain read failed (status {status})")
        try:
            value = ctypes.string_at(data, length.value)
        finally:
            self._security.SecKeychainItemFreeContent(None, data)
        return (item if include_item else None), value

    def get(self, reference: SecretReference) -> bytes:
        _, value = self._find(reference)
        return value

    def disable(self, reference: SecretReference) -> None:
        try:
            item, _ = self._find(reference, include_item=True)
        except SecretNotFoundError:
            return
        assert item is not None
        status = self._security.SecKeychainItemDelete(item)
        if status not in (0, _ERR_SEC_ITEM_NOT_FOUND):
            raise SecretStoreError(f"macOS Keychain delete failed (status {status})")
