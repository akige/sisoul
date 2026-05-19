// @sisoul/client - 主入口
export { SisoulClient, DEFAULT_BASE_URL, DEFAULT_TIMEOUT_MS } from "./client.js";
export { VaultAPI } from "./vault.js";
export { GoalsAPI } from "./goals.js";
export { FriendsAPI } from "./friends.js";
export { SkillsAPI } from "./skills.js";
export { AttestAPI } from "./attest.js";
export {
  SisoulError,
  DaemonError,
  AuthError,
  NetworkError,
  TimeoutError,
} from "./errors.js";
export * from "./types.js";
