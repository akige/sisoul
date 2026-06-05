"""sisoul completion · install bash/zsh/fish autocomplete."""
from __future__ import annotations
from pathlib import Path
from typing import Optional

import typer


COMPLETION_BASH = """\
# sisoul bash completion. Source from ~/.bashrc:
#   eval "$(sisoul completion bash)"
_sisoul_complete() {
    local cur prev opts
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    local top_commands="init login ask remember stats debate health demo invite cheatsheet status export restore verify daemon sync case skill chat friend borrow lend goals"

    if [[ ${COMP_CWORD} -eq 1 ]]; then
        COMPREPLY=( $(compgen -W "${top_commands} --version --version-json --help" -- ${cur}) )
    fi

    case "${prev}" in
        case)    COMPREPLY=( $(compgen -W "list search show add" -- ${cur}) ) ;;
        friend)  COMPREPLY=( $(compgen -W "list add request accept qr qr-scan mdns petname" -- ${cur}) ) ;;
        skill)   COMPREPLY=( $(compgen -W "list install run publish" -- ${cur}) ) ;;
        chat)    COMPREPLY=( $(compgen -W "send recv sessions rotate-prekey status" -- ${cur}) ) ;;
        goals)   COMPREPLY=( $(compgen -W "list add progress show" -- ${cur}) ) ;;
        sync)    COMPREPLY=( $(compgen -W "now status check apply" -- ${cur}) ) ;;
    esac

    return 0
}
complete -F _sisoul_complete sisoul
"""

COMPLETION_ZSH = """\
# sisoul zsh completion. Source from ~/.zshrc:
#   eval "$(sisoul completion zsh)"
_sisoul() {
    local -a top_commands sub_commands
    top_commands=(
        'init:5-step wizard'
        'login:LLM provider login'
        'ask:single-LLM ask'
        'remember:teach preferences'
        'stats:local case/skill counters'
        'debate:multi-agent debate'
        'health:verify daemon + v2 endpoints'
        'demo:8-step v2 showcase'
        'invite:friend invite text/QR'
        'cheatsheet:quick reference'
        'status:vault + daemon status'
        'export:ZIP export vault'
        'restore:from ZIP or BIP-39'
        'verify:vault + EAS attest check'
        'daemon:start daemon'
        'sync:sync to 5 LLM tools'
        'case:case retrieval'
        'skill:skill marketplace'
        'chat:E2E chat'
        'friend:friend management'
        'borrow:borrow LLM from friend'
        'lend:lend LLM to friend'
        'goals:long-term goals'
    )
    _describe 'top command' top_commands
}
compdef _sisoul sisoul
"""

COMPLETION_FISH = """\
# sisoul fish completion. Save to ~/.config/fish/completions/sisoul.fish
complete -c sisoul -f
complete -c sisoul -n '__fish_use_subcommand' -a 'init' -d '5-step wizard'
complete -c sisoul -n '__fish_use_subcommand' -a 'login' -d 'LLM provider'
complete -c sisoul -n '__fish_use_subcommand' -a 'ask' -d 'single-LLM ask'
complete -c sisoul -n '__fish_use_subcommand' -a 'stats' -d 'local counters'
complete -c sisoul -n '__fish_use_subcommand' -a 'debate' -d 'multi-agent debate'
complete -c sisoul -n '__fish_use_subcommand' -a 'health' -d 'daemon health'
complete -c sisoul -n '__fish_use_subcommand' -a 'demo' -d '8-step showcase'
complete -c sisoul -n '__fish_use_subcommand' -a 'invite' -d 'friend invite'
complete -c sisoul -n '__fish_use_subcommand' -a 'cheatsheet' -d 'quick reference'
complete -c sisoul -n '__fish_use_subcommand' -a 'daemon' -d 'start daemon'
complete -c sisoul -n '__fish_use_subcommand' -a 'case' -d 'case retrieval'
complete -c sisoul -n '__fish_use_subcommand' -a 'skill' -d 'skill marketplace'
complete -c sisoul -n '__fish_use_subcommand' -a 'chat' -d 'E2E chat'
complete -c sisoul -n '__fish_use_subcommand' -a 'friend' -d 'friend management'
"""


def cli_completion(
    shell: str = typer.Argument(..., help="bash | zsh | fish"),
    install: bool = typer.Option(False, "--install", help="write to standard location"),
) -> None:
    """Print or install shell autocompletion."""
    shell_l = shell.lower()
    if shell_l == "bash":
        script = COMPLETION_BASH
        target = Path.home() / ".bash_completion.d" / "sisoul"
    elif shell_l == "zsh":
        script = COMPLETION_ZSH
        target = Path.home() / ".zsh" / "completions" / "_sisoul"
    elif shell_l == "fish":
        script = COMPLETION_FISH
        target = Path.home() / ".config" / "fish" / "completions" / "sisoul.fish"
    else:
        typer.echo(f"ERROR: unsupported shell '{shell}'. Try: bash, zsh, fish", err=True)
        raise typer.Exit(code=1)

    if install:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(script)
        typer.echo(f"OK installed to {target}")
        typer.echo("  Restart shell or source the file to activate.")
        return

    typer.echo(script)
