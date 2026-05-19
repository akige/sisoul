import { ItemView, WorkspaceLeaf, Notice, ButtonComponent } from "obsidian";
import { DaemonClient } from "./daemon-client";
import { FriendItem, SkillItem } from "./types";

export const SISOUL_VIEW_TYPE = "sisoul-ribbon-view";

/**
 * Left-side ribbon panel rendering the sisoul identity card:
 *   • current DID
 *   • top friends list (cap 20)
 *   • skill count + top 10 skills
 */
export class SisoulRibbonView extends ItemView {
  private did = "—";
  private friends: FriendItem[] = [];
  private skills: SkillItem[] = [];
  private error: string | null = null;
  private loading = false;

  constructor(leaf: WorkspaceLeaf, private readonly client: DaemonClient) {
    super(leaf);
  }

  getViewType(): string {
    return SISOUL_VIEW_TYPE;
  }

  getDisplayText(): string {
    return "Sisoul";
  }

  getIcon(): string {
    return "shield";
  }

  async onOpen(): Promise<void> {
    this.render();
    await this.refresh();
  }

  async onClose(): Promise<void> {
    // nothing to clean up; data is per-view state
  }

  async refresh(): Promise<void> {
    if (this.loading) return;
    this.loading = true;
    this.error = null;
    this.render();
    try {
      const [health, friendsResp, skillsResp] = await Promise.all([
        this.client.health(),
        this.client.friends(),
        this.client.skills(),
      ]);
      this.did = health.did || "—";
      this.friends = friendsResp.items.slice(0, 20);
      this.skills = skillsResp.items.slice(0, 10);
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
    } finally {
      this.loading = false;
      this.render();
    }
  }

  private render(): void {
    const container = this.containerEl.children[1] as HTMLElement;
    container.empty();
    container.addClass("sisoul-ribbon-view");

    container.createEl("h3", { text: "Sisoul identity" });

    const idSection = container.createDiv({ cls: "sisoul-section" });
    idSection.createEl("div", { text: "DID" });
    idSection.createDiv({ cls: "sisoul-did", text: this.did });

    const friendsSection = container.createDiv({ cls: "sisoul-section" });
    friendsSection.createEl("div", { text: `Friends (${this.friends.length})` });
    const friendsUl = friendsSection.createEl("ul", { cls: "sisoul-friend-list" });
    if (this.friends.length === 0) {
      friendsUl.createEl("li", { text: this.loading ? "loading…" : "no friends yet" });
    } else {
      for (const f of this.friends) {
        const li = friendsUl.createEl("li");
        const alias = f.alias || f.did.slice(0, 16);
        li.createSpan({ text: `${alias} ` });
        li.createSpan({ text: `(trust ${f.trust.toFixed(2)})`, cls: "sisoul-muted" });
      }
    }

    const skillsSection = container.createDiv({ cls: "sisoul-section" });
    skillsSection.createEl("div", { text: `Skills (${this.skills.length})` });
    const skillsUl = skillsSection.createEl("ul", { cls: "sisoul-skill-list" });
    if (this.skills.length === 0) {
      skillsUl.createEl("li", { text: this.loading ? "loading…" : "no skills yet" });
    } else {
      for (const s of this.skills) {
        const li = skillsUl.createEl("li");
        li.createSpan({ text: `${s.name} L${s.level} ` });
        li.createSpan({ text: `[${s.category}]`, cls: "sisoul-muted" });
      }
    }

    if (this.error) {
      container.createDiv({ cls: "sisoul-error", text: `Error: ${this.error}` });
    }

    new ButtonComponent(container)
      .setButtonText(this.loading ? "Refreshing…" : "Refresh")
      .setDisabled(this.loading)
      .onClick(async () => {
        await this.refresh();
        if (!this.error) new Notice("Sisoul: ribbon refreshed");
      })
      .buttonEl.addClass("sisoul-refresh-btn");
  }
}
