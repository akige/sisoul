"""sisoul SDK error hierarchy."""

from __future__ import annotations


class SisoulError(Exception):
    """Base."""


class DaemonError(SisoulError):
    """Daemon returned non-2xx."""

    def __init__(self, status: int, path: str, body: str | None = None) -> None:
        snippet = f": {body[:200]}" if body else ""
        super().__init__(f"daemon {path} → {status}{snippet}")
        self.status = status
        self.path = path
        self.body = body


class AuthError(DaemonError):
    """401 / 403."""


class NetworkError(SisoulError):
    """Transport-level failure."""

    def __init__(self, message: str, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.cause = cause


class TimeoutError(NetworkError):  # noqa: A001 - 故意覆盖 builtin 跟 TS SDK 对齐
    """Request exceeded configured timeout."""


def classify_http_error(status: int, path: str, body: str | None = None) -> DaemonError:
    if status in (401, 403):
        return AuthError(status, path, body)
    return DaemonError(status, path, body)
