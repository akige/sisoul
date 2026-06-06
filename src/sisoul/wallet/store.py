"""Local-only wallet address store. JSON at $SISOUL_VAULT/wallet.json."""
from __future__ import annotations
import json
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


class WalletError(Exception):
    """Base."""


_TRC20_RE = re.compile(r"^T[A-Za-z0-9]{33}$")
_ERC20_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def _looks_trc20(addr: str) -> bool:
    return bool(_TRC20_RE.match(addr))


def _looks_erc20(addr: str) -> bool:
    return bool(_ERC20_RE.match(addr))


@dataclass
class WalletAddresses:
    """Receive addresses for the user. Empty string = unset."""

    usdt_trc20: str = ""
    usdt_erc20: str = ""
    btc_taproot: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def default_wallet_path() -> Path:
    vault = Path(os.environ.get("SISOUL_VAULT", str(Path.home() / ".sisoul"))).expanduser()
    vault.mkdir(parents=True, exist_ok=True)
    return vault / "wallet.json"


class WalletStore:
    """Local JSON store of the user's RECEIVE-ONLY addresses.

    No private keys. No transaction signing. No chain watching.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else default_wallet_path()
        self._cache: Optional[WalletAddresses] = None

    def get(self) -> WalletAddresses:
        if self._cache is not None:
            return self._cache
        if self.path.exists():
            try:
                d = json.loads(self.path.read_text())
                self._cache = WalletAddresses(
                    usdt_trc20=d.get("usdt_trc20", ""),
                    usdt_erc20=d.get("usdt_erc20", ""),
                    btc_taproot=d.get("btc_taproot", ""),
                )
            except Exception as e:
                raise WalletError(f"failed to read {self.path}: {e}") from e
        else:
            self._cache = WalletAddresses()
        return self._cache

    def set_usdt_trc20(self, address: str) -> None:
        if address and not _looks_trc20(address):
            raise WalletError(
                f"address does not look like a TRC20 T-address (34 chars, "
                f"starts with T): {address!r}"
            )
        current = self.get()
        current.usdt_trc20 = address.strip()
        self._save(current)

    def set_usdt_erc20(self, address: str) -> None:
        if address and not _looks_erc20(address):
            raise WalletError(f"address does not look like an ERC20 0x-address: {address!r}")
        current = self.get()
        current.usdt_erc20 = address.strip()
        self._save(current)

    def clear(self) -> None:
        self._cache = WalletAddresses()
        self._save(self._cache)

    def _save(self, addrs: WalletAddresses) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(addrs.to_dict(), indent=2))
        tmp.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except Exception:
            pass
        self._cache = addrs


__all__ = ["WalletStore", "WalletAddresses", "default_wallet_path", "WalletError"]
