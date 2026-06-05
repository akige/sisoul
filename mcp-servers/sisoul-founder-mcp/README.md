# sisoul-founder-mcp

MCP server that exposes the sisoul founder-agent (`@founder`) to any
MCP-capable AI client: paseo+claude, Claude Desktop, codex, gemini-cli.

## Why

Lets you summon the founder-agent inside any AI assistant session.

```
@founder why does sisoul refuse to issue a token?
```

The client (Claude / GPT / Gemini) sees `@founder`, calls the
`founder_ask` tool exposed by this MCP server, which routes to the local
sisoul daemon's `/v1/founder/chat` endpoint, which loads vault + LLM and
returns the answer.

## Install

```bash
cd mcp-servers/sisoul-founder-mcp
npm install
```

## Configure your AI client

### Claude Code / paseo+claude (`~/.claude/mcp.json`)

```json
{
  "mcpServers": {
    "sisoul-founder": {
      "command": "node",
      "args": ["/absolute/path/to/sisoul/mcp-servers/sisoul-founder-mcp/server.mjs"],
      "env": {
        "SISOUL_DAEMON_BASE": "http://127.0.0.1:9876"
      }
    }
  }
}
```

### Codex (similar)

### Generic stdio MCP client

```
node /path/to/server.mjs
```

Speaks JSON-RPC over stdio per MCP protocol.

## Tools exposed

| Tool | Purpose |
|---|---|
| `founder_ask(question)` | Chat with founder-agent (returns LLM answer or retrieval fallback) |
| `founder_recall(query, top_k=3)` | Query case-graph directly, no LLM |
| `founder_status` | Vault size + provider chain + RSI state |
| `founder_cases_list` | All loaded cases |
| `founder_lessons_list` | All loaded lessons |

## Requirements

- Node.js 20+
- sisoul daemon running at `SISOUL_DAEMON_BASE` (default `http://127.0.0.1:9876`)
- Founder vault initialized: `sisoul founder init --from vault-template/founder/`

## Troubleshooting

`founder-mcp error: ... fetch failed`
- Check `sisoul daemon status` — daemon running?
- Check `curl http://127.0.0.1:9876/v1/founder/status` works directly

`MCP server crashed on startup`
- Check Node version (`node --version`) is 20+
- Run `npm install` again

## License

Apache-2.0 (same as parent sisoul project).
