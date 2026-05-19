import { App, PluginSettingTab, Setting } from "obsidian";
import type SisoulPlugin from "./main";

export interface SisoulSettings {
  daemonUrl: string;
  autoSyncIntervalMin: number;
  showVaultStatus: boolean;
  enableRagSelective: boolean;
  managedSectionAutoUpdate: boolean;
  apiToken: string;
}

export const DEFAULT_SETTINGS: SisoulSettings = {
  daemonUrl: "http://127.0.0.1:9876",
  autoSyncIntervalMin: 15,
  showVaultStatus: true,
  enableRagSelective: false,
  managedSectionAutoUpdate: true,
  apiToken: "",
};

export class SisoulSettingTab extends PluginSettingTab {
  plugin: SisoulPlugin;

  constructor(app: App, plugin: SisoulPlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();

    containerEl.createEl("h2", { text: "Sisoul Meta-Layer Settings" });

    new Setting(containerEl)
      .setName("Daemon URL")
      .setDesc("Local sisoul daemon endpoint. Default 127.0.0.1:9876.")
      .addText((text) =>
        text
          .setPlaceholder("http://127.0.0.1:9876")
          .setValue(this.plugin.settings.daemonUrl)
          .onChange(async (value) => {
            this.plugin.settings.daemonUrl = value.trim() || DEFAULT_SETTINGS.daemonUrl;
            await this.plugin.saveSettings();
            this.plugin.client.setBaseUrl(this.plugin.settings.daemonUrl);
          }),
      );

    new Setting(containerEl)
      .setName("API token")
      .setDesc("Optional bearer token if daemon requires auth. Leave empty if loopback-only.")
      .addText((text) =>
        text
          .setPlaceholder("")
          .setValue(this.plugin.settings.apiToken)
          .onChange(async (value) => {
            this.plugin.settings.apiToken = value.trim();
            await this.plugin.saveSettings();
            this.plugin.client.setToken(this.plugin.settings.apiToken);
          }),
      );

    new Setting(containerEl)
      .setName("Auto-sync interval (minutes)")
      .setDesc("0 disables. Plugin will POST /sisoul/sync on this cadence.")
      .addText((text) =>
        text
          .setPlaceholder("15")
          .setValue(String(this.plugin.settings.autoSyncIntervalMin))
          .onChange(async (value) => {
            const n = Number(value);
            this.plugin.settings.autoSyncIntervalMin = Number.isFinite(n) && n >= 0 ? n : 15;
            await this.plugin.saveSettings();
            this.plugin.rescheduleAutoSync();
          }),
      );

    new Setting(containerEl)
      .setName("Show vault status in status bar")
      .setDesc("Render the bottom status-bar widget (daemon / vault / last-sync).")
      .addToggle((toggle) =>
        toggle.setValue(this.plugin.settings.showVaultStatus).onChange(async (value) => {
          this.plugin.settings.showVaultStatus = value;
          await this.plugin.saveSettings();
          this.plugin.refreshStatusBarVisibility();
        }),
      );

    new Setting(containerEl)
      .setName("Enable selective RAG injection")
      .setDesc(
        "When ON, the plugin annotates managed sections with retrieved sisoul context. Disabled by default.",
      )
      .addToggle((toggle) =>
        toggle.setValue(this.plugin.settings.enableRagSelective).onChange(async (value) => {
          this.plugin.settings.enableRagSelective = value;
          await this.plugin.saveSettings();
        }),
      );

    new Setting(containerEl)
      .setName("Auto-update managed blocks")
      .setDesc(
        "Refresh <!-- sisoul-managed-start --> blocks in current note after each successful sync.",
      )
      .addToggle((toggle) =>
        toggle.setValue(this.plugin.settings.managedSectionAutoUpdate).onChange(async (value) => {
          this.plugin.settings.managedSectionAutoUpdate = value;
          await this.plugin.saveSettings();
        }),
      );
  }
}
