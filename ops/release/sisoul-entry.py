"""PyInstaller entry shim.

The shim imports the Typer ``app`` from ``sisoul.cli`` and calls it.

We keep this file deliberately small so that ``--hidden-import`` /
``--collect-submodules`` (driven by ``build-binary.sh``) does the rest of
the dependency collection work.
"""

from __future__ import annotations

import sys

from sisoul.cli import app


def main() -> int:
    try:
        app()
        return 0
    except SystemExit as e:  # typer/click raises SystemExit on --help / errors
        return int(e.code or 0)


if __name__ == "__main__":
    sys.exit(main())
