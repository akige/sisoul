import { Notice } from "obsidian";
import type SisoulPlugin from "./main";

/**
 * Registers command-palette entries.
 */
export function registerCommands(plugin: SisoulPlugin): void {
  plugin.addCommand({
    id: "sisoul-sync-now",
    name: "Sisoul: Sync now",
    callback: async () => {
      try {
        plugin.statusBar?.setInProgress(true);
        const r = await plugin.client.triggerSync();
        new Notice(`Sisoul: sync triggered (${r.sync_id})`);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        new Notice(`Sisoul: sync failed — ${msg}`);
      } finally {
        plugin.statusBar?.setInProgress(false);
        await plugin.statusBar?.refresh();
        if (plugin.settings.managedSectionAutoUpdate) {
          await plugin.managed.updateActiveNote();
        }
      }
    },
  });

  plugin.addCommand({
    id: "sisoul-show-did",
    name: "Sisoul: Show DID",
    callback: async () => {
      try {
        const h = await plugin.client.health();
        new Notice(`Sisoul DID: ${h.did}`, 10_000);
        try {
          await navigator.clipboard.writeText(h.did);
        } catch {
          /* clipboard may be unavailable in some sandboxes */
        }
      } catch (err) {
        new Notice(`Sisoul: cannot fetch DID — ${err instanceof Error ? err.message : err}`);
      }
    },
  });

  plugin.addCommand({
    id: "sisoul-toggle-rag",
    name: "Sisoul: Toggle RAG inject",
    callback: async () => {
      plugin.settings.enableRagSelective = !plugin.settings.enableRagSelective;
      await plugin.saveSettings();
      new Notice(`Sisoul RAG inject: ${plugin.settings.enableRagSelective ? "ON" : "OFF"}`);
    },
  });

  plugin.addCommand({
    id: "sisoul-update-managed-active",
    name: "Sisoul: Update managed blocks in current note",
    callback: async () => {
      const n = await plugin.managed.updateActiveNote();
      new Notice(`Sisoul: updated ${n} managed block(s) in current note`);
    },
  });

  plugin.addCommand({
    id: "sisoul-update-managed-vault",
    name: "Sisoul: Update managed blocks across vault",
    callback: async () => {
      const n = await plugin.managed.updateVault();
      new Notice(`Sisoul: vault scan complete — ${n} file(s) updated`);
    },
  });

  plugin.addCommand({
    id: "sisoul-open-ribbon",
    name: "Sisoul: Open identity panel",
    callback: () => plugin.activateRibbonView(),
  });
}
