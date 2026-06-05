"""sisoul cheatsheet · printable quick-reference for alpha users."""
from __future__ import annotations
import typer


CHEATSHEET = """
  ╭─────────────────────────────────────────────────────────────╮
  │  sisoul v1.0-alpha · Quick Reference (cheatsheet)           │
  ╰─────────────────────────────────────────────────────────────╯

  ▸ FIRST STEPS
    sisoul init                          5-step wizard (Petname/did/provider/daemon/QR)
    sisoul daemon                        start HTTP daemon (foreground)
    sisoul health                        verify daemon + v2 endpoints
    sisoul stats                         local case/skill/friend counters

  ▸ FRIENDS
    sisoul friend list                   show your friends
    sisoul friend add <did:key>          add friend by DID
    sisoul friend qr --out friend.png    generate QR for friends to scan
    sisoul friend qr-scan <image>        decode friend's QR
    sisoul friend mdns scan              find friends on LAN (5s scan)
    sisoul friend petname set <did> <n>  set local nickname
    sisoul invite --did <yours> --petname <yours>   text invite for IM/Slack

  ▸ CASES (knowledge sharing)
    sisoul case list                     all cases in vault
    sisoul case search "<query>"         search (TF-IDF foundation)
    sisoul case show <case-id>           full case detail
    sisoul case add -q <q> -a <a> -d <did>   add case manually

  ▸ ASK / DEBATE
    sisoul ask "<question>"              single-LLM ask
    sisoul debate "<difficult q>"        multi-agent debate (foundation: mock)

  ▸ SKILLS
    sisoul skill list                    installed skills
    sisoul skill install <ipfs-cid>      install from IPFS

  ▸ CHAT (Signal-grade)
    sisoul chat send <peer-did> "<msg>"  E2E encrypted (Double Ratchet + PQXDH)
    sisoul chat recv                     pull messages
    sisoul chat sessions list            active sessions

  ▸ BORROW (LLM sharing)
    sisoul borrow request <peer-did>     request LLM from friend
    sisoul lend approve <req-id>         approve incoming request

  ▸ DEMO / DEBUG
    sisoul demo                          8-step end-to-end showcase
    sisoul --version                     ASCII version + module status
    sisoul --version-json                JSON version info

  ▸ DAEMON URL CONVENTIONS
    http://127.0.0.1:9876/sisoul/health     health check
    http://127.0.0.1:9876/sisoul/metrics    Prometheus format
    http://127.0.0.1:9876/v2/case           17 v2/* endpoints
    http://127.0.0.1:9876/docs              FastAPI Swagger UI

  ▸ PWA (mobile / desktop browser)
    https://sisoul.github.io/sisoul-pwa/
    Routes: /ask /debate /dashboard/v2 /skills/v2 /stats /vault /friends ...

  ▸ DOCS
    README.md                            project overview
    docs/ALPHA-LAUNCH-PLAYBOOK.md        install + use + risks
    ALPHA-LAUNCH-CHECKLIST.md            launch day procedure
    docs/whitepaper/sisoul-whitepaper-v1.0.md   14-ch protocol spec

  ▸ TROUBLESHOOTING
    daemon won't start                   check port: lsof -i :9876
    health says daemon unreachable       sisoul daemon  (foreground, see errors)
    cross-NAT borrow fails               sisoul net status; check kubo running
    chat session lost                    ~/.sisoul/chat/sessions/  inspect
    PQXDH "shim mode" warning            pip install kyber-py  (real mode)

  ╭─────────────────────────────────────────────────────────────╮
  │  Need more? sisoul <command> --help  shows full options.    │
  ╰─────────────────────────────────────────────────────────────╯
"""


def cli_cheatsheet() -> None:
    """Print quick-reference cheatsheet."""
    typer.echo(CHEATSHEET)
