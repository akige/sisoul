import { App, MarkdownView, Notice, TFile } from "obsidian";
import { DaemonClient } from "./daemon-client";

const START_MARKER = "<!-- sisoul-managed-start -->";
const END_MARKER = "<!-- sisoul-managed-end -->";
const META_RE = /<!--\s*sisoul:(\{[^}]*\})\s*-->/;

export interface ManagedBlockMeta {
  kind?: "identity" | "friends" | "skills" | "goals" | "preferences" | "sync";
  limit?: number;
}

interface ParsedBlock {
  start: number; // index of START_MARKER line in lines[]
  end: number; // index of END_MARKER line in lines[]
  meta: ManagedBlockMeta;
}

/**
 * Manages `<!-- sisoul-managed-start -->...<!-- sisoul-managed-end -->` blocks
 * inside vault markdown notes. User content above/below the markers is
 * preserved exactly. Each block may carry a meta directive:
 *
 *   <!-- sisoul-managed-start -->
 *   <!-- sisoul:{"kind":"friends","limit":5} -->
 *   ... auto-generated body ...
 *   <!-- sisoul-managed-end -->
 */
export class ManagedSection {
  constructor(private readonly app: App, private readonly client: DaemonClient) {}

  /** Update all managed blocks in the currently-active note (if any). */
  async updateActiveNote(): Promise<number> {
    const view = this.app.workspace.getActiveViewOfType(MarkdownView);
    if (!view || !view.file) return 0;
    return this.updateFile(view.file);
  }

  /** Update all managed blocks across the whole vault. Returns # files touched. */
  async updateVault(): Promise<number> {
    const files = this.app.vault.getMarkdownFiles();
    let touched = 0;
    for (const f of files) {
      try {
        const n = await this.updateFile(f);
        if (n > 0) touched++;
      } catch (err) {
        console.warn("sisoul: failed to update", f.path, err);
      }
    }
    if (touched > 0) new Notice(`Sisoul: refreshed managed blocks in ${touched} note(s)`);
    return touched;
  }

  /** Update all managed blocks in a single file. Returns # blocks rewritten. */
  async updateFile(file: TFile): Promise<number> {
    const orig = await this.app.vault.read(file);
    if (!orig.includes(START_MARKER)) return 0;
    const lines = orig.split("\n");
    const blocks = this.parseBlocks(lines);
    if (blocks.length === 0) return 0;

    // walk blocks back-to-front so line indices stay valid as we splice
    blocks.sort((a, b) => b.start - a.start);

    let rewrites = 0;
    for (const blk of blocks) {
      const body = await this.renderBody(blk.meta);
      const replacement = this.buildBlock(blk.meta, body);
      lines.splice(blk.start, blk.end - blk.start + 1, ...replacement);
      rewrites++;
    }

    const next = lines.join("\n");
    if (next !== orig) {
      await this.app.vault.modify(file, next);
    }
    return rewrites;
  }

  // --- parsing / rendering -----------------------------------------------

  private parseBlocks(lines: string[]): ParsedBlock[] {
    const out: ParsedBlock[] = [];
    let i = 0;
    while (i < lines.length) {
      if (lines[i].trim() === START_MARKER) {
        const start = i;
        let end = -1;
        let meta: ManagedBlockMeta = {};
        for (let j = i + 1; j < lines.length; j++) {
          if (lines[j].trim() === END_MARKER) {
            end = j;
            break;
          }
          const m = lines[j].match(META_RE);
          if (m && Object.keys(meta).length === 0) {
            try {
              meta = JSON.parse(m[1]) as ManagedBlockMeta;
            } catch {
              meta = {};
            }
          }
        }
        if (end > start) {
          out.push({ start, end, meta });
          i = end + 1;
          continue;
        }
      }
      i++;
    }
    return out;
  }

  private buildBlock(meta: ManagedBlockMeta, body: string[]): string[] {
    const metaLine = `<!-- sisoul:${JSON.stringify(meta || {})} -->`;
    return [START_MARKER, metaLine, ...body, END_MARKER];
  }

  private async renderBody(meta: ManagedBlockMeta): Promise<string[]> {
    const kind = meta.kind ?? "identity";
    const limit = Math.max(1, Math.min(meta.limit ?? 10, 200));
    const stamp = `_synced ${new Date().toISOString()}_`;
    try {
      switch (kind) {
        case "identity": {
          const h = await this.client.health();
          return [
            `**DID**: \`${h.did}\``,
            `**Daemon**: ${h.status} · v${h.version}`,
            `**Vault**: ${h.vault_encrypted ? "encrypted" : "plain"}`,
            "",
            stamp,
          ];
        }
        case "friends": {
          const r = await this.client.friends();
          const rows = r.items.slice(0, limit).map((f) => {
            const alias = f.alias || f.did.slice(0, 16);
            return `- ${alias} — trust ${f.trust.toFixed(2)} \`${f.did}\``;
          });
          return rows.length ? [...rows, "", stamp] : ["_no friends_", "", stamp];
        }
        case "skills": {
          const r = await this.client.skills();
          const rows = r.items
            .slice(0, limit)
            .map((s) => `- **${s.name}** L${s.level} (${s.category}) — ${s.evidence_count} evidence`);
          return rows.length ? [...rows, "", stamp] : ["_no skills_", "", stamp];
        }
        case "goals": {
          const r = await this.client.goals();
          const rows = r.items
            .slice(0, limit)
            .map((g) => `- [${g.status === "done" ? "x" : " "}] **${g.title}** (p${g.priority})`);
          return rows.length ? [...rows, "", stamp] : ["_no goals_", "", stamp];
        }
        case "preferences": {
          const r = await this.client.preferences();
          const rows = r.items.slice(0, limit).map((p) => `- \`${p.key}\` = ${p.value}`);
          return rows.length ? [...rows, "", stamp] : ["_no preferences_", "", stamp];
        }
        case "sync": {
          const s = await this.client.syncStatus();
          return [
            `**Last sync**: ${s.last_sync_at ?? "never"}`,
            `**In progress**: ${s.in_progress}`,
            `**Peers**: ${s.peers_synced}`,
            `**Pending**: ${s.pending_objects}`,
            s.last_error ? `**Last error**: ${s.last_error}` : "",
            "",
            stamp,
          ].filter((x) => x !== "");
        }
        default:
          return [`_unknown kind: ${kind}_`, "", stamp];
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return [`_sisoul-managed render failed: ${msg}_`, "", stamp];
    }
  }
}
