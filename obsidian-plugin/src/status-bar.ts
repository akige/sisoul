import { setIcon } from "obsidian";
import { DaemonClient } from "./daemon-client";

export interface StatusBarState {
  daemonConnected: boolean;
  vaultEncrypted: boolean;
  lastSyncAt: string | null;
  inProgress: boolean;
}

/**
 * Bottom status-bar widget. Shows daemon connectivity, vault encryption flag
 * and last-sync timestamp. Click triggers a sync-now action.
 */
export class StatusBar {
  private el: HTMLElement | null = null;
  private dot: HTMLSpanElement | null = null;
  private label: HTMLSpanElement | null = null;
  private state: StatusBarState = {
    daemonConnected: false,
    vaultEncrypted: false,
    lastSyncAt: null,
    inProgress: false,
  };

  constructor(
    private readonly client: DaemonClient,
    private readonly onClickSync: () => void,
  ) {}

  attach(rawEl: HTMLElement): void {
    this.el = rawEl;
    this.el.empty();
    this.el.addClass("sisoul-status-bar");
    this.el.setAttr("aria-label", "Sisoul daemon status. Click to sync now.");

    this.dot = this.el.createSpan({ cls: "sisoul-dot" });
    const icon = this.el.createSpan();
    setIcon(icon, "shield");
    this.label = this.el.createSpan({ text: "Sisoul: ?" });

    this.el.addEventListener("click", () => this.onClickSync());
    this.render();
  }

  detach(): void {
    this.el?.remove();
    this.el = null;
    this.dot = null;
    this.label = null;
  }

  async refresh(): Promise<void> {
    try {
      const [health, sync] = await Promise.all([this.client.health(), this.client.syncStatus()]);
      this.state = {
        daemonConnected: health.status !== "down",
        vaultEncrypted: !!health.vault_encrypted,
        lastSyncAt: sync.last_sync_at,
        inProgress: !!sync.in_progress,
      };
    } catch {
      this.state = {
        daemonConnected: false,
        vaultEncrypted: false,
        lastSyncAt: this.state.lastSyncAt,
        inProgress: false,
      };
    }
    this.render();
  }

  setInProgress(v: boolean): void {
    this.state.inProgress = v;
    this.render();
  }

  private render(): void {
    if (!this.dot || !this.label) return;
    this.dot.removeClass("ok");
    this.dot.removeClass("warn");
    if (this.state.daemonConnected && this.state.vaultEncrypted) {
      this.dot.addClass("ok");
    } else if (this.state.daemonConnected) {
      this.dot.addClass("warn");
    }
    const parts: string[] = ["Sisoul"];
    parts.push(this.state.daemonConnected ? "live" : "offline");
    if (this.state.vaultEncrypted) parts.push("enc");
    if (this.state.inProgress) {
      parts.push("syncing…");
    } else if (this.state.lastSyncAt) {
      parts.push(`synced ${this.relativeTime(this.state.lastSyncAt)}`);
    } else {
      parts.push("no sync");
    }
    this.label.setText(parts.join(" · "));
  }

  private relativeTime(iso: string): string {
    const t = Date.parse(iso);
    if (!Number.isFinite(t)) return iso;
    const delta = Math.floor((Date.now() - t) / 1000);
    if (delta < 60) return `${delta}s ago`;
    if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
    if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
    return `${Math.floor(delta / 86400)}d ago`;
  }
}
