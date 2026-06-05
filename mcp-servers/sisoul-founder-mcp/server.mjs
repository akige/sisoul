#!/usr/bin/env node
/**
 * sisoul-founder-mcp — MCP server bridging any AI client (paseo+claude / codex / gemini-cli)
 * to the local sisoul daemon's founder-agent endpoints (/v1/founder/*).
 *
 * Tools exposed:
 *   founder.ask(question)         — chat with the founder-agent
 *   founder.recall(query, top_k)  — query the case-graph directly
 *   founder.history(topic)        — list recent founder chat log entries
 *   founder.status                — vault size, provider chain, RSI state
 *
 * Daemon endpoint: env SISOUL_DAEMON_BASE or default http://127.0.0.1:9876.
 *
 * Usage (Claude Code / paseo+claude):
 *   {
 *     "mcpServers": {
 *       "sisoul-founder": {
 *         "command": "node",
 *         "args": ["/path/to/sisoul-founder-mcp/server.mjs"]
 *       }
 *     }
 *   }
 *
 * Then in any session: `@founder why does sisoul refuse to issue a token?`
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";

const DAEMON = process.env.SISOUL_DAEMON_BASE || "http://127.0.0.1:9876";

async function callDaemon(path, init) {
  const url = `${DAEMON}${path}`;
  const resp = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!resp.ok) {
    const errBody = await resp.text().catch(() => "");
    throw new Error(`sisoul daemon ${path} -> ${resp.status} ${resp.statusText} ${errBody}`);
  }
  return resp.json();
}

const TOOLS = [
  {
    name: "founder_ask",
    description:
      "Ask the sisoul founder-agent a question. The agent assembles its persona prompt + relevant cases from vault and routes to an LLM (falls back to retrieval-only if no LLM is configured).",
    inputSchema: {
      type: "object",
      properties: {
        question: {
          type: "string",
          description: "Your question (in any language; agent responds in same language).",
        },
      },
      required: ["question"],
    },
  },
  {
    name: "founder_recall",
    description:
      "Query the founder-agent case-graph directly without LLM inference. Returns the top-k matching cases (sprint history, design decisions, lessons).",
    inputSchema: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "Search query (English or Chinese).",
        },
        top_k: {
          type: "number",
          default: 3,
          minimum: 1,
          maximum: 10,
        },
      },
      required: ["query"],
    },
  },
  {
    name: "founder_status",
    description:
      "Get founder-agent runtime status: vault size (cases/lessons/eval_prompts), LLM provider chain, RSI enabled.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "founder_cases_list",
    description: "List all loaded cases (id, question, answer preview, tags).",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "founder_lessons_list",
    description: "List all loaded lessons (id, principle, applicability).",
    inputSchema: { type: "object", properties: {} },
  },
];

const server = new Server(
  { name: "sisoul-founder-mcp", version: "1.0.0-alpha" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS }));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  try {
    if (name === "founder_ask") {
      const result = await callDaemon("/v1/founder/chat", {
        method: "POST",
        body: JSON.stringify({ question: args.question, record: true }),
      });
      return {
        content: [
          {
            type: "text",
            text: `${result.answer}\n\n— founder-agent · provider=${result.provider} · cases_recalled=${result.cases_recalled.join(",") || "none"} · mode=${result.mode}`,
          },
        ],
      };
    }

    if (name === "founder_recall") {
      const result = await callDaemon("/v1/founder/recall", {
        method: "POST",
        body: JSON.stringify({ query: args.query, top_k: args.top_k || 3 }),
      });
      const lines = result.matches.map(
        (m) => `• [${m.score.toFixed(2)}] ${m.id}: Q: ${m.question}\n  A: ${m.answer.slice(0, 240)}${m.answer.length > 240 ? "..." : ""}`
      );
      return {
        content: [
          {
            type: "text",
            text: lines.length
              ? `Recall for "${result.query}":\n\n${lines.join("\n\n")}`
              : `No matching cases for "${result.query}".`,
          },
        ],
      };
    }

    if (name === "founder_status") {
      const result = await callDaemon("/v1/founder/status");
      return {
        content: [
          {
            type: "text",
            text:
              `founder-agent status:\n` +
              `  vault_root: ${result.vault_root}\n` +
              `  vault_size: ${JSON.stringify(result.vault_size)}\n` +
              `  config: ${JSON.stringify(result.config)}`,
          },
        ],
      };
    }

    if (name === "founder_cases_list") {
      const result = await callDaemon("/v1/founder/cases");
      const lines = result.cases.map((c) => `• ${c.id} [${c.tags.join(",")}]: ${c.question}`);
      return {
        content: [
          { type: "text", text: `${result.count} cases:\n\n${lines.join("\n")}` },
        ],
      };
    }

    if (name === "founder_lessons_list") {
      const result = await callDaemon("/v1/founder/lessons");
      const lines = result.lessons.map(
        (l) => `• ${l.id}: ${l.principle} (applies: ${l.applies_to.join(", ")})`
      );
      return {
        content: [
          { type: "text", text: `${result.count} lessons:\n\n${lines.join("\n")}` },
        ],
      };
    }

    return {
      content: [{ type: "text", text: `unknown tool: ${name}` }],
      isError: true,
    };
  } catch (err) {
    return {
      content: [
        {
          type: "text",
          text:
            `founder-mcp error: ${err.message}\n` +
            `Is sisoul daemon running at ${DAEMON}? Try: sisoul founder daemon`,
        },
      ],
      isError: true,
    };
  }
});

const transport = new StdioServerTransport();
await server.connect(transport);
console.error(`sisoul-founder-mcp ready (daemon=${DAEMON})`);
