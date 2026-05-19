// sisoul SDK error hierarchy
//
// DaemonError - daemon 返非 2xx
// NetworkError - fetch 失败 / timeout
// AuthError    - 401/403 子类

export class SisoulError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SisoulError";
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

export class DaemonError extends SisoulError {
  public readonly status: number;
  public readonly path: string;
  public readonly body?: string;

  constructor(status: number, path: string, body?: string) {
    super(`daemon ${path} → ${status}${body ? `: ${body.slice(0, 200)}` : ""}`);
    this.name = "DaemonError";
    this.status = status;
    this.path = path;
    this.body = body;
  }
}

export class AuthError extends DaemonError {
  constructor(status: number, path: string, body?: string) {
    super(status, path, body);
    this.name = "AuthError";
  }
}

export class NetworkError extends SisoulError {
  public readonly cause: unknown;
  constructor(message: string, cause?: unknown) {
    super(message);
    this.name = "NetworkError";
    this.cause = cause;
  }
}

export class TimeoutError extends NetworkError {
  constructor(timeoutMs: number) {
    super(`request exceeded ${timeoutMs}ms timeout`);
    this.name = "TimeoutError";
  }
}

export function classifyHttpError(status: number, path: string, body?: string): DaemonError {
  if (status === 401 || status === 403) return new AuthError(status, path, body);
  return new DaemonError(status, path, body);
}
