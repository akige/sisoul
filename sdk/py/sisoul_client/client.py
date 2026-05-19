"""SisoulClient - top-level daemon client (httpx)."""

from __future__ import annotations

from typing import Any

import httpx

from .errors import NetworkError, TimeoutError, classify_http_error

DEFAULT_BASE_URL = "http://localhost:8088/sisoul"
DEFAULT_TIMEOUT = 30.0


class SisoulClient:
    """Sync httpx-backed daemon client.

    Usage::

        with SisoulClient(base_url="http://localhost:8088/sisoul") as c:
            prefs = c.vault.list()
            skills = c.skills.owned()
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        merged_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            **(headers or {}),
        }
        self._client = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers=merged_headers,
        )

        # lazy import 防 circular
        from .vault import VaultAPI
        from .goals import GoalsAPI
        from .friends import FriendsAPI
        from .skills import SkillsAPI
        from .attest import AttestAPI

        self.vault = VaultAPI(self)
        self.goals = GoalsAPI(self)
        self.friends = FriendsAPI(self)
        self.skills = SkillsAPI(self)
        self.attest = AttestAPI(self)

    # ─── lifecycle ────────────────────────────────────────────────────────
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SisoulClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ─── low-level ────────────────────────────────────────────────────────
    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        absolute: bool = False,
    ) -> Any:
        url = path if absolute else f"{self.base_url}{path}"
        try:
            resp = self._client.request(method, url, json=json, params=params)
        except httpx.TimeoutException as e:
            raise TimeoutError(f"request to {url} timed out after {self.timeout}s") from e
        except httpx.RequestError as e:
            raise NetworkError(f"request to {url} failed: {e}", e) from e

        if resp.status_code >= 400:
            body: str | None
            try:
                body = resp.text
            except Exception:
                body = None
            raise classify_http_error(resp.status_code, path, body)

        if resp.status_code == 204 or not resp.content:
            return None
        ct = resp.headers.get("content-type", "")
        if "application/json" not in ct:
            return resp.text
        return resp.json()

    def get(self, path: str, *, params: dict[str, Any] | None = None, absolute: bool = False) -> Any:
        return self.request("GET", path, params=params, absolute=absolute)

    def post(self, path: str, json: Any | None = None, *, absolute: bool = False) -> Any:
        return self.request("POST", path, json=json, absolute=absolute)

    def delete(self, path: str, *, absolute: bool = False) -> Any:
        return self.request("DELETE", path, absolute=absolute)
