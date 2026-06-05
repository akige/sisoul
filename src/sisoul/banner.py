"""sisoul ASCII banner — printed on daemon start."""
from __future__ import annotations

from sisoul import __version__


BANNER = r"""
   ___  _                  _
  / __|(_) ___  ___  _  _ | |
  \__ \| |(_-< / _ \| || || |
  |___/|_|/__/ \___/ \_,_||_|

  decentralized P2P AI agent protocol
"""


def print_banner(host: str = "127.0.0.1", port: int = 9876) -> None:
    """Print banner + daemon endpoint info."""
    print(BANNER)
    print(f"  version : {__version__}")
    print(f"  daemon  : http://{host}:{port}")
    print(f"  health  : http://{host}:{port}/sisoul/health")
    print(f"  metrics : http://{host}:{port}/sisoul/metrics")
    print(f"  docs    : http://{host}:{port}/docs")
    print()
    print("  Quick start:")
    print("    sisoul demo                # 8-step v2.0 showcase")
    print("    sisoul cheatsheet          # all commands one-page")
    print("    sisoul health              # verify daemon ready")
    print()
