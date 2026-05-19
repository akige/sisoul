import { createMemo } from "solid-js";
import { formatProgress } from "../utils/format";

interface Props {
  progress: number; // 0-100
  label?: string;
  class?: string;
}

export default function GoalProgressBar(props: Props) {
  const clamped = createMemo(() => Math.max(0, Math.min(100, props.progress)));
  const label = createMemo(() => props.label ?? formatProgress(clamped()));

  return (
    <div class={`space-y-1 ${props.class ?? ""}`} data-testid="goal-progress-bar">
      <div class="flex justify-between text-xs text-sisoul-muted">
        <span>Progress</span>
        <span class="font-mono">{label()}</span>
      </div>
      <div
        class="h-2 bg-sisoul-border rounded-full overflow-hidden"
        role="progressbar"
        aria-valuenow={clamped()}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          class="h-full rounded-full transition-all duration-300"
          classList={{
            "bg-sisoul-success": clamped() >= 70,
            "bg-sisoul-warn": clamped() >= 30 && clamped() < 70,
            "bg-sisoul-danger": clamped() < 30,
          }}
          style={{ width: `${clamped()}%` }}
        />
      </div>
    </div>
  );
}
