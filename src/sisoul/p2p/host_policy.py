"""Host policy — where sisoul may run an embedded P2P (kubo / GossipSub) node.

User red line (decentralisation policy §10.3 + §1):

> 完全去中心化的 GossipSub 很占内存的话，不要在任何 aws 机器上跑，
> 只允许在 mac/wsl/win11 上跑。
> 除了 sisoul 通用客户端，不允许 aws 机器有任何 sisoul 的中心化服务和端口在跑。

kubo + GossipSub are memory-heavy and must only run on the user's own laptop
(mac / wsl / win). ``aws-*`` boxes are dev workstations only.

This module is the **single source of truth** for that decision. Every code path
that would spawn an embedded ``ipfs daemon`` (daemon startup hook, chat-send
GossipSub transport, skill pinning) calls :func:`cloud_refusal_reason` first and
refuses when it returns a reason.

WSL2 note: WSL reports DMI vendor ``Microsoft Corporation``; that must **not** be
treated as cloud — the user explicitly wants kubo to run on WSL2. WSL is detected
and allowed before the cloud-vendor check.

Override (discouraged; only for the user's own non-cloud machine if detection
false-positives): ``export SISOUL_ALLOW_CLOUD_P2P=1``.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

#: env override — user knowingly allows embedded P2P on a flagged host.
ALLOW_CLOUD_P2P_ENV = "SISOUL_ALLOW_CLOUD_P2P"

#: hostname prefixes that mark a cloud / shared box.
#: (EC2 instance ids start with ``i-``; the user's boxes are ``aws-us`` / ``aws-hk``.)
_CLOUD_HOSTNAME_PREFIXES = ("aws-", "aws_", "i-")

#: DMI vendor substrings that mark a cloud VM. Deliberately excludes "microsoft"
#: so WSL2 (DMI vendor "Microsoft Corporation") is never caught here.
_CLOUD_DMI_VENDORS = (
    "amazon",
    "ec2",
    "google compute",
    "googlecloud",
    "digitalocean",
    "alibaba",
    "tencent",
    "oraclecloud",
)


def _hostname() -> str:
    """Lower-cased hostname, or "" if it cannot be read."""
    try:
        return (socket.gethostname() or "").strip().lower()
    except OSError:
        return ""


def _is_wsl() -> bool:
    """True on Windows Subsystem for Linux (an allowed user laptop)."""
    for p in ("/proc/sys/kernel/osrelease", "/proc/version"):
        try:
            txt = Path(p).read_text(errors="ignore").lower()
        except OSError:
            continue
        if "microsoft" in txt or "wsl" in txt:
            return True
    return False


def _etc_cloud_present() -> bool:
    """True if ``/etc/cloud`` exists (cloud-init image — AWS/GCP/Azure/DO/…)."""
    return Path("/etc/cloud").exists()


def _dmi_vendor() -> str:
    """First non-empty DMI vendor/product string (Linux only), lower-cased."""
    for p in (
        "/sys/class/dmi/id/sys_vendor",
        "/sys/class/dmi/id/product_name",
        "/sys/class/dmi/id/board_vendor",
    ):
        try:
            txt = Path(p).read_text(errors="ignore").strip().lower()
        except OSError:
            continue
        if txt:
            return txt
    return ""


def cloud_refusal_reason() -> str | None:
    """Reason string if this host must NOT run an embedded P2P node, else ``None``.

    ``None`` == embedded kubo / GossipSub is allowed (the user's own laptop).
    """
    # explicit, knowing opt-in wins over every signal.
    if os.environ.get(ALLOW_CLOUD_P2P_ENV) == "1":
        return None

    host = _hostname()
    for pref in _CLOUD_HOSTNAME_PREFIXES:
        if host.startswith(pref):
            return f"hostname {host!r} matches cloud pattern {pref!r}*"

    # WSL2 is an allowed user laptop even though its DMI vendor is "Microsoft".
    if _is_wsl():
        return None

    if _etc_cloud_present():
        return "/etc/cloud present (cloud-init host — refusing embedded P2P)"

    vendor = _dmi_vendor()
    for needle in _CLOUD_DMI_VENDORS:
        if needle in vendor:
            return f"DMI vendor {vendor!r} is a cloud provider"

    return None


def p2p_allowed() -> bool:
    """True when this host may run an embedded kubo / GossipSub P2P node."""
    return cloud_refusal_reason() is None


__all__ = [
    "ALLOW_CLOUD_P2P_ENV",
    "cloud_refusal_reason",
    "p2p_allowed",
]
