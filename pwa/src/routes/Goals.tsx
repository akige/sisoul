// Goals 路由 · 长期目标 + 进度
// daemon endpoint: GET /sisoul/goals/list
import { createResource, For, Show } from "solid-js";
import { listGoals, type Goal } from "../api/daemon";
import { formatDate, formatProgress } from "../utils/format";
import GoalProgressBar from "../components/GoalProgressBar";
import AsyncBoundary from "../components/AsyncBoundary";

function GoalCard(props: { goal: Goal }) {
  const g = () => props.goal;

  return (
    <div
      class="border border-sisoul-border rounded-lg p-4 space-y-3 hover:border-sisoul-accentDim transition-colors"
      data-testid="goal-card"
    >
      <div class="flex items-start justify-between gap-3">
        <h3 class="font-semibold text-sisoul-text">{g().title}</h3>
        <span class="font-mono text-sm text-sisoul-accent shrink-0">
          {formatProgress(g().progress)}
        </span>
      </div>

      <GoalProgressBar progress={g().progress} />

      <Show when={g().deadline}>
        <p class="text-xs text-sisoul-muted">
          截止: <span class="font-mono">{formatDate(g().deadline!)}</span>
        </p>
      </Show>
      <Show when={g().notes}>
        <p class="text-sm text-sisoul-muted leading-relaxed">{g().notes}</p>
      </Show>
    </div>
  );
}

function GoalsContent() {
  const [data] = createResource(() => listGoals());

  return (
    <Show
      when={data()}
      fallback={<div class="text-sisoul-muted text-sm">加载中...</div>}
    >
      {(d) => (
        <Show
          when={d().goals.length > 0}
          fallback={
            <div class="text-sisoul-muted text-sm py-8 text-center">
              暂无目标 · 用 <code class="font-mono text-sisoul-accent">sisoul goal add</code> 添加
            </div>
          }
        >
          <div class="space-y-4">
            <For each={d().goals}>{(goal) => <GoalCard goal={goal} />}</For>
          </div>
        </Show>
      )}
    </Show>
  );
}

export default function Goals() {
  return (
    <div class="space-y-6 max-w-2xl" data-route="goals">
      <div>
        <h1 class="text-xl font-semibold text-sisoul-text">Goals</h1>
        <p class="text-sm text-sisoul-muted mt-1">长期目标追踪 · 进度 + 截止日期</p>
      </div>
      <AsyncBoundary>
        <GoalsContent />
      </AsyncBoundary>
    </div>
  );
}
