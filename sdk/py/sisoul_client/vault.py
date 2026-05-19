"""Vault API - daemon preferences endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .types import ListPreferencesResponse, Preference, VaultGetResponse

if TYPE_CHECKING:
    from .client import SisoulClient


class VaultAPI:
    def __init__(self, client: SisoulClient) -> None:
        self._c = client

    def list(self) -> list[Preference]:
        raw = self._c.get("/preferences/list")
        return ListPreferencesResponse.model_validate(raw).items

    def get(self, key: str) -> str | None:
        if not key:
            raise ValueError("vault.get: key required")
        raw = self._c.get("/preferences/get", params={"key": key})
        return VaultGetResponse.model_validate(raw).value

    def set(self, key: str, value: str) -> None:
        if not key:
            raise ValueError("vault.set: key required")
        self._c.post("/preferences/set", {"key": key, "value": value})

    def delete(self, key: str) -> None:
        if not key:
            raise ValueError("vault.delete: key required")
        self._c.post("/preferences/delete", {"key": key})

    def multi_get(self, keys: list[str]) -> dict[str, str | None]:
        return {k: self.get(k) for k in keys}
