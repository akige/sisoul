// Friends API - daemon /friend/* endpoints + lend/borrow

import type { SisoulClient } from "./client.js";
import type {
  Friend,
  FriendAddRequest,
  FriendBorrowRequest,
  FriendLendRequest,
  ListFriendsResponse,
} from "./types.js";

export class FriendsAPI {
  constructor(private readonly client: SisoulClient) {}

  async list(): Promise<Friend[]> {
    const resp = await this.client.get<ListFriendsResponse>("/friend/list");
    return resp.friends ?? [];
  }

  async add(req: FriendAddRequest): Promise<Friend> {
    if (!req.did) throw new Error("friends.add: did required");
    return this.client.post<Friend>("/friend/add", req);
  }

  async remove(did: string): Promise<void> {
    if (!did) throw new Error("friends.remove: did required");
    await this.client.post<{ ok: boolean }>("/friend/remove", { did });
  }

  async lend(req: FriendLendRequest): Promise<{ lease_id: string; expires_at: number }> {
    if (!req.friend_did) throw new Error("friends.lend: friend_did required");
    if (!req.resource_id) throw new Error("friends.lend: resource_id required");
    return this.client.post("/friend/lend", req);
  }

  async borrow(req: FriendBorrowRequest): Promise<{ lease_id: string; expires_at: number }> {
    if (!req.owner_did) throw new Error("friends.borrow: owner_did required");
    if (!req.resource_id) throw new Error("friends.borrow: resource_id required");
    return this.client.post("/friend/borrow", req);
  }

  /** 信任度 ≥ threshold 的好友过滤 */
  async strongTies(threshold = 0.7): Promise<Friend[]> {
    const all = await this.list();
    return all.filter((f) => f.trust_level >= threshold);
  }
}
