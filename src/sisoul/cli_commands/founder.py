"""sisoul founder CLI — chat / status / recall / cases / lessons / init.

Examples:
    sisoul founder status                            # vault size + provider chain
    sisoul founder chat "why does sisoul refuse to issue a token?"
    sisoul founder recall "rsi safety boundary" --top-k 3
    sisoul founder cases                             # list all loaded cases
    sisoul founder lessons                           # list all loaded lessons
    sisoul founder init --from vault-template/founder
"""
from __future__ import annotations
import json
import shutil
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

cli_founder = typer.Typer(
    name="founder",
    help="sisoul founder-agent (@founder) — protocol's first user.",
    no_args_is_help=True,
)
console = Console()


@cli_founder.command("status")
def founder_status_cmd():
    """Print founder-agent status (vault size, provider, RSI state)."""
    from sisoul.founder.agent import FounderAgent

    agent = FounderAgent()
    st = agent.status()
    console.print(f"[bold]Vault root:[/bold] {st['vault_root']}")
    console.print(
        f"[bold]Size:[/bold] cases={st['vault_size']['cases']}, "
        f"lessons={st['vault_size']['lessons']}, "
        f"eval={st['vault_size']['eval_prompts']}, "
        f"system_prompt={'yes' if st['vault_size']['has_system_prompt'] else 'NO'}"
    )
    console.print(f"[bold]Config:[/bold] {json.dumps(st['config'])}")


@cli_founder.command("chat")
def founder_chat_cmd(
    question: str = typer.Argument(..., help="Question for @founder"),
    record: bool = typer.Option(True, "--record/--no-record", help="Log to vault/founder/chat/log.jsonl"),
):
    """Chat with @founder. Retrieval-only if no LLM adapter configured."""
    from sisoul.founder.agent import FounderAgent

    agent = FounderAgent()
    result = agent.chat(question, adapter=None, record=record)
    console.print(f"\n[bold cyan]@founder[/bold cyan] ({result['provider']}, {result['mode']}):\n")
    console.print(result["answer"])
    if result["cases_recalled"]:
        console.print(
            f"\n[dim]Recalled cases: {', '.join(result['cases_recalled'])}[/dim]"
        )


@cli_founder.command("recall")
def founder_recall_cmd(
    query: str = typer.Argument(..., help="Search query"),
    top_k: int = typer.Option(3, "--top-k", "-k", help="Max results"),
):
    """Query the founder case-graph directly without LLM."""
    from sisoul.founder.vault import FounderVault

    vault = FounderVault()
    matches = vault.recall(query, top_k=top_k)
    if not matches:
        console.print(f"[yellow]No cases matched '{query}'.[/yellow]")
        raise typer.Exit(code=0)
    table = Table(title=f"Recall: {query}")
    table.add_column("id", style="cyan")
    table.add_column("score", style="green", justify="right")
    table.add_column("question", style="white")
    table.add_column("tags", style="dim")
    for case, score in matches:
        table.add_row(case.id, f"{score:.2f}", case.question[:80], ",".join(case.tags))
    console.print(table)


@cli_founder.command("cases")
def founder_cases_cmd():
    """List all loaded cases."""
    from sisoul.founder.vault import FounderVault

    vault = FounderVault()
    all_cases = vault.all_cases()
    if not all_cases:
        console.print("[yellow]No cases loaded. Run `sisoul founder init`.[/yellow]")
        raise typer.Exit(code=0)
    table = Table(title=f"{len(all_cases)} cases")
    table.add_column("id", style="cyan")
    table.add_column("question", style="white")
    table.add_column("tags", style="dim")
    for c in all_cases:
        table.add_row(c.id, c.question[:80], ",".join(c.tags))
    console.print(table)


@cli_founder.command("lessons")
def founder_lessons_cmd():
    """List all loaded lessons."""
    from sisoul.founder.vault import FounderVault

    vault = FounderVault()
    lessons = vault.all_lessons()
    if not lessons:
        console.print("[yellow]No lessons loaded.[/yellow]")
        raise typer.Exit(code=0)
    for l in lessons:
        console.print(f"\n[bold cyan]{l.id}[/bold cyan]: {l.principle}")
        console.print(f"  [dim]applies: {', '.join(l.applies_to)}[/dim]")
        if l.context:
            console.print(f"  [dim]context: {l.context}[/dim]")


@cli_founder.command("init")
def founder_init_cmd(
    from_path: Path = typer.Option(
        ..., "--from", help="Path to vault-template/founder/ seed directory"
    ),
    force: bool = typer.Option(
        False, "--force", help="Overwrite existing founder vault"
    ),
):
    """Initialize the founder vault from a seed directory."""
    from sisoul.founder.vault import founder_dir

    target = founder_dir()
    if target.exists() and not force:
        console.print(f"[red]Already initialized at {target}. Use --force to overwrite.[/red]")
        raise typer.Exit(code=1)
    if not from_path.exists():
        console.print(f"[red]Seed dir not found: {from_path}[/red]")
        raise typer.Exit(code=1)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(from_path, target)
    console.print(f"[green]Founder vault initialized at {target}[/green]")
    console.print(f"  copied from: {from_path}")
    console.print(f"  Try: [bold]sisoul founder status[/bold]")


@cli_founder.command("history")
def founder_history_cmd(
    last: int = typer.Option(10, "--last", "-n", help="Last N chat entries"),
):
    """Show recent founder chat history."""
    from sisoul.founder.vault import founder_dir

    log_file = founder_dir() / "chat" / "log.jsonl"
    if not log_file.exists():
        console.print("[yellow]No chat history yet.[/yellow]")
        raise typer.Exit(code=0)
    lines = log_file.read_text().splitlines()[-last:]
    table = Table(title=f"Last {len(lines)} founder chats")
    table.add_column("ts", style="dim")
    table.add_column("mode", style="yellow")
    table.add_column("provider", style="cyan")
    table.add_column("question", style="white")
    for line in lines:
        try:
            entry = json.loads(line)
            table.add_row(
                entry.get("ts", "")[:19],
                entry.get("mode", ""),
                entry.get("provider", ""),
                entry.get("user", "")[:60],
            )
        except Exception:
            continue
    console.print(table)


__all__ = ["cli_founder"]
