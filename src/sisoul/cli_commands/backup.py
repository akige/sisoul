"""sisoul backup · one-command vault backup (zip + manifest)."""
from __future__ import annotations
import json
import os
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer


def cli_backup(
    out: Optional[Path] = typer.Option(
        None, "--out", "-o",
        help="output zip path (default ~/sisoul-backup-YYYY-MM-DD-HHMM.zip)",
    ),
    include_chat: bool = typer.Option(
        False, "--include-chat",
        help="include chat session state (sensitive, off by default)",
    ),
    include_skills: bool = typer.Option(
        True, "--include-skills/--no-skills",
        help="include installed skills",
    ),
    vault: Optional[Path] = typer.Option(None, "--vault", help="vault dir (default ~/.sisoul)"),
) -> None:
    """One-command backup of sisoul vault (zip with manifest)."""
    vault_dir = (vault or Path(os.environ.get("SISOUL_VAULT", "~/.sisoul"))).expanduser()
    skills_dir = Path(os.environ.get("SISOUL_SKILLS_DIR", "~/.sisoul/skills")).expanduser()

    if not vault_dir.exists():
        typer.echo(f"ERROR: vault not found at {vault_dir}", err=True)
        typer.echo("  run: sisoul init", err=True)
        raise typer.Exit(code=1)

    ts = datetime.now().strftime("%Y-%m-%d-%H%M")
    out_path = out or (Path.home() / f"sisoul-backup-{ts}.zip")
    out_path = out_path.expanduser().resolve()

    typer.echo(f"\n  Backing up vault: {vault_dir}")
    typer.echo(f"  → {out_path}\n")

    manifest = {
        "sisoul_version": "1.0.0-alpha",
        "backup_at": datetime.now().isoformat(timespec="seconds"),
        "vault_src": str(vault_dir),
        "include_chat": include_chat,
        "include_skills": include_skills,
        "files": [],
    }

    skip_patterns = [".tmp/", "__pycache__/", ".DS_Store"]
    if not include_chat:
        skip_patterns.append("chat/sessions/")

    count = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # vault files
        for p in vault_dir.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(vault_dir.parent)
            if any(skip in str(rel) for skip in skip_patterns):
                continue
            zf.write(p, arcname=str(rel))
            manifest["files"].append(str(rel))
            count += 1

        # skills (optional)
        if include_skills and skills_dir.exists():
            for p in skills_dir.rglob("*"):
                if not p.is_file():
                    continue
                rel = "skills/" + str(p.relative_to(skills_dir))
                if any(skip in rel for skip in skip_patterns):
                    continue
                zf.write(p, arcname=rel)
                manifest["files"].append(rel)
                count += 1

        # manifest itself
        zf.writestr("sisoul-backup-manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))

    size_mb = out_path.stat().st_size / 1024 / 1024
    typer.echo(f"  ✓ backed up {count} file(s), {size_mb:.2f} MB")
    typer.echo(f"  ✓ manifest: sisoul-backup-manifest.json (inside zip)\n")
    typer.echo("  Restore (later):")
    typer.echo(f"    sisoul restore --from-zip {out_path}")
    typer.echo("")
