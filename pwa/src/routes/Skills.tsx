// Skills 路由 · AI 技能管理 (owned / borrowed / available)
// daemon endpoints: GET /sisoul/skill/list, POST /sisoul/skill/borrow
import { createResource, createSignal, For, Show } from "solid-js";
import { listSkills, type SkillItem } from "../api/daemon";
import { normalizeVersion, truncateDid } from "../utils/format";
import AsyncBoundary from "../components/AsyncBoundary";

type TabKey = "owned" | "borrowed" | "available";

// §28 §3.6 — borrow 模态 duration 3 档 (30 分钟 / 60 分钟 / 120 分钟)
export const DURATION_OPTIONS = [30, 60, 120];

function SkillCard(props: { skill: SkillItem; tab: TabKey }) {
  const s = () => props.skill;

  return (
    <div
      class="border border-sisoul-border rounded-lg p-4 space-y-2 hover:border-sisoul-accentDim transition-colors"
      data-testid="skill-card"
    >
      <div class="flex items-center justify-between gap-2">
        <span class="font-semibold text-sisoul-text">{s().name}</span>
        <span class="text-xs font-mono text-sisoul-muted">
          {normalizeVersion(s().version)}
        </span>
      </div>
      <p class="text-xs font-mono text-sisoul-muted">
        by {truncateDid(s().owner_did)}
      </p>

      <div class="flex items-center gap-2 pt-1">
        <Show when={s().source === "owned"}>
          <span class="text-xs px-2 py-0.5 rounded bg-sisoul-success/20 text-sisoul-success font-mono">
            owned
          </span>
        </Show>
        <Show when={s().source === "borrowed"}>
          <span class="text-xs px-2 py-0.5 rounded bg-sisoul-accentDim text-sisoul-accent font-mono">
            borrowed
          </span>
        </Show>
        <Show when={props.tab === "available"}>
          <button class="text-xs px-3 py-1 rounded bg-sisoul-accentDim text-sisoul-accent font-mono hover:bg-sisoul-accent hover:text-sisoul-bg transition-colors">
            Borrow
          </button>
        </Show>
      </div>
    </div>
  );
}

function SkillsContent() {
  const [data] = createResource(() => listSkills());
  const [tab, setTab] = createSignal<TabKey>("owned");

  const owned = (): SkillItem[] =>
    (data()?.owned ?? []).map((s) => ({ ...s, source: "owned" as const }));
  const borrowed = (): SkillItem[] =>
    (data()?.owned ?? [])
      .filter((s) => s.source === "borrowed")
      .map((s) => ({ ...s, source: "borrowed" as const }));
  const available = (): SkillItem[] =>
    (data()?.available_to_borrow ?? []).map((s) => ({
      ...s,
      source: "available" as const,
    }));

  const currentList = () => {
    switch (tab()) {
      case "owned":
        return owned();
      case "borrowed":
        return borrowed();
      case "available":
        return available();
    }
  };

  return (
    <Show
      when={data() !== undefined}
      fallback={<div class="text-sisoul-muted text-sm">加载技能列表...</div>}
    >
      <div class="space-y-4">
        {/* Tabs */}
        <div class="flex gap-1 border-b border-sisoul-border">
          {(["owned", "borrowed", "available"] as TabKey[]).map((t) => {
            const counts: Record<TabKey, () => number> = {
              owned: () => owned().length,
              borrowed: () => borrowed().length,
              available: () => available().length,
            };
            return (
              <button
                class="px-4 py-2 text-sm font-mono border-b-2 transition-colors -mb-px"
                classList={{
                  "border-sisoul-accent text-sisoul-accent": tab() === t,
                  "border-transparent text-sisoul-muted hover:text-sisoul-text":
                    tab() !== t,
                }}
                onClick={() => setTab(t)}
              >
                {t} ({counts[t]()})
              </button>
            );
          })}
        </div>

        {/* List */}
        <Show
          when={currentList().length > 0}
          fallback={
            <div class="text-sisoul-muted text-sm py-8 text-center">
              {tab() === "owned"
                ? "暂无已安装技能"
                : tab() === "borrowed"
                  ? "暂无借用技能"
                  : "暂无可用技能"}
            </div>
          }
        >
          <div class="grid gap-3 sm:grid-cols-2">
            <For each={currentList()}>
              {(skill) => <SkillCard skill={skill} tab={tab()} />}
            </For>
          </div>
        </Show>
      </div>
    </Show>
  );
}

export default function Skills() {
  return (
    <div class="space-y-6 max-w-3xl" data-route="skills">
      <div>
        <h1 class="text-xl font-semibold text-sisoul-text">Skills</h1>
        <p class="text-sm text-sisoul-muted mt-1">
          AI 技能管理 · 已安装 / 借用中 / 可用 (IPFS 加密分发)
        </p>
      </div>
      <AsyncBoundary>
        <SkillsContent />
      </AsyncBoundary>
    </div>
  );
}
