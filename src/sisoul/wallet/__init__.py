"""sisoul wallet — local-only address store for receiving USDT-TRC20 micropay.

Per docs/INCENTIVE-DESIGN.md, sisoul does NOT custody funds. This module only
stores YOUR OWN TRC20 receiving address (and optionally other chains) in your
vault, so that when you set `incentive_mode: micropay` on a per-friend
permission, the daemon can quote a borrower the right address to pay.

We deliberately do not:
- generate new wallets on the user's behalf (use Trust/TronLink/SafePal)
- hold private keys (zero custody)
- watch the chain for inbound payments (alpha v1.0; tronscan link in CLI is enough)
"""
from .store import WalletStore, default_wallet_path, WalletError

__all__ = ["WalletStore", "default_wallet_path", "WalletError"]
