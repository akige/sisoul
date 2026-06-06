"""Standalone runner for the sisoul prekey directory.

Usage:
    python ops/run_prekey_directory.py [--host 0.0.0.0] [--port 8765]

Or behind systemd / launchd, env-vars:
    SISOUL_PREKEY_DATA   data directory (default /var/lib/sisoul-prekey or ~/.sisoul-prekey)
    SISOUL_PREKEY_HOST   bind address (default 127.0.0.1)
    SISOUL_PREKEY_PORT   port (default 8765)
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default=os.environ.get("SISOUL_PREKEY_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("SISOUL_PREKEY_PORT", "8765")))
    p.add_argument("--data", default=os.environ.get("SISOUL_PREKEY_DATA",
                   str(Path.home() / ".sisoul-prekey")))
    args = p.parse_args()
    os.environ["SISOUL_PREKEY_DATA"] = args.data
    Path(args.data).mkdir(parents=True, exist_ok=True)
    from sisoul.prekey_directory.server import create_prekey_directory_app
    import uvicorn
    app = create_prekey_directory_app()
    print(f"sisoul prekey directory: data={args.data} bind={args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
