// Goals API - daemon /goals/* endpoints

import type { SisoulClient } from "./client.js";
import type {
  Goal,
  GoalCreateRequest,
  GoalUpdateRequest,
  ListGoalsResponse,
} from "./types.js";

export class GoalsAPI {
  constructor(private readonly client: SisoulClient) {}

  async list(): Promise<Goal[]> {
    const resp = await this.client.get<ListGoalsResponse>("/goals/list");
    return resp.goals ?? [];
  }

  async add(req: GoalCreateRequest): Promise<Goal> {
    if (!req.title) throw new Error("goals.add: title required");
    return this.client.post<Goal>("/goals/add", req);
  }

  async update(req: GoalUpdateRequest): Promise<Goal> {
    if (!req.id) throw new Error("goals.update: id required");
    return this.client.post<Goal>("/goals/update", req);
  }

  async delete(id: string): Promise<void> {
    if (!id) throw new Error("goals.delete: id required");
    await this.client.post<{ ok: boolean }>("/goals/delete", { id });
  }

  /** 进度小步推进辅助. */
  async bumpProgress(id: string, delta: number): Promise<Goal> {
    const goals = await this.list();
    const g = goals.find((x) => x.id === id);
    if (!g) throw new Error(`goal ${id} not found`);
    const next = Math.min(1, Math.max(0, g.progress + delta));
    return this.update({ id, progress: next });
  }
}
