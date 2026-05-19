import { Notice, Plugin, WorkspaceLeaf } from "obsidian";
import { DaemonClient } from "./daemon-client";
import { registerCommands } from "./commands";
import { ManagedSection } from "./managed-section";
import { SISOUL_VIEW_TYPE, SisoulRibbonView } from "./ribbon-view";
import { DEFAULT_SETTINGS, SisoulSettings, SisoulSettingTab } from "./settings";
import { StatusBar } from "./status-bar";

export default class SisoulPlugin extends Plugin {
  settings: SisoulSettings = { ...DEFAULT_SETTINGS };
  client!: DaemonClient;
  managed!: ManagedSection;
  statusBar: StatusBar | null = null;
  private statusBarRoot: HTMLElement | null = null;
  private autoSyncTimer: number | null = null;
  private statusRefreshTimer: number | null = null;

  async onload(): Promise<void> {
    await this.loadSettings();

    this.client = new DaemonClient({
      baseUrl: this.settings.daemonUrl,
      token: this.settings.apiToken,
    });
    this.managed = new ManagedSection(this.app, this.client);

    // settings tab
    this.addSettingTab(new SisoulSettingTab(this.app, this));

    // ribbon icon (left toolbar)
    this.addRibbonIcon("shield", "Sisoul identity", () => this.activateRibbonView());

    // ribbon view registration
    this.registerView(SISOUL_VIEW_TYPE, (leaf) => new SisoulRibbonView(leaf, this.client));

    // status bar
    this.refreshStatusBarVisibility();

    // command palette
    registerCommands(this);

    // initial probe (non-blocking)
    this.probeDaemonOnStartup();

    // schedules
    this.rescheduleAutoSync();
    this.scheduleStatusRefresh();
  }

  async onunload(): Promise<void> {
    if (this.autoSyncTimer !== null) {
      window.clearInterval(this.autoSyncTimer);
      this.autoSyncTimer = null;
    }
    if (this.statusRefreshTimer !== null) {
      window.clearInterval(this.statusRefreshTimer);
      this.statusRefreshTimer = null;
    }
    this.statusBar?.detach();
    this.statusBar = null;
  }

  async loadSettings(): Promise<void> {
    const data = (await this.loadData()) as Partial<SisoulSettings> | null;
    this.settings = { ...DEFAULT_SETTINGS, ...(data ?? {}) };
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
  }

  refreshStatusBarVisibility(): void {
    if (this.settings.showVaultStatus) {
      if (!this.statusBar) {
        this.statusBarRoot = this.addStatusBarItem();
        this.statusBar = new StatusBar(this.client, async () => {
          // click-to-sync
          try {
            this.statusBar?.setInProgress(true);
            await this.client.triggerSync();
            new Notice("Sisoul: sync triggered");
          } catch {
            /* toast already raised by client */
          } finally {
            this.statusBar?.setInProgress(false);
            await this.statusBar?.refresh();
          }
        });
        this.statusBar.attach(this.statusBarRoot);
        void this.statusBar.refresh();
      }
    } else {
      this.statusBar?.detach();
      this.statusBar = null;
      this.statusBarRoot = null;
    }
  }

  rescheduleAutoSync(): void {
    if (this.autoSyncTimer !== null) {
      window.clearInterval(this.autoSyncTimer);
      this.autoSyncTimer = null;
    }
    const min = this.settings.autoSyncIntervalMin;
    if (min <= 0) return;
    this.autoSyncTimer = window.setInterval(async () => {
      try {
        this.statusBar?.setInProgress(true);
        await this.client.triggerSync();
        if (this.settings.managedSectionAutoUpdate) {
          await this.managed.updateActiveNote();
        }
      } catch {
        /* toast already surfaced */
      } finally {
        this.statusBar?.setInProgress(false);
        await this.statusBar?.refresh();
      }
    }, min * 60_000);
    this.registerInterval(this.autoSyncTimer);
  }

  private scheduleStatusRefresh(): void {
    if (this.statusRefreshTimer !== null) {
      window.clearInterval(this.statusRefreshTimer);
    }
    this.statusRefreshTimer = window.setInterval(() => {
      void this.statusBar?.refresh();
    }, 30_000);
    this.registerInterval(this.statusRefreshTimer);
  }

  private async probeDaemonOnStartup(): Promise<void> {
    try {
      const h = await this.client.health();
      new Notice(`Sisoul daemon online (v${h.version}, ${h.status})`);
    } catch {
      // client itself surfaces a toast; nothing extra here
    }
  }

  async activateRibbonView(): Promise<void> {
    const { workspace } = this.app;
    let leaf: WorkspaceLeaf | null = null;
    const existing = workspace.getLeavesOfType(SISOUL_VIEW_TYPE);
    if (existing.length > 0) {
      leaf = existing[0];
    } else {
      leaf = workspace.getLeftLeaf(false);
      if (leaf) {
        await leaf.setViewState({ type: SISOUL_VIEW_TYPE, active: true });
      }
    }
    if (leaf) workspace.revealLeaf(leaf);
  }
}
