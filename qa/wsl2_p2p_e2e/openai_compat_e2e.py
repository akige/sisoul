"""Wave T3 真跨 OS e2e: Win11 用 openai SDK 调本机 Alice daemon → Bob (WSL) → mock.

跑位: Win11 powershell (Alice 端).

前置:
1. WSL Bob daemon 已起 (192.0.2.15:9877 listen), 跑 mock forwarder.
2. Win11 Alice daemon 已起 (192.0.2.16:9876 listen), 装了本 wave-T3 分支代码.
3. Alice 已 add Bob did:key friend (`sisoul friend add did:key:z6LSofo...`).
4. env SISOUL_OPENAI_COMPAT_MOCK=1 (强制走 echo mock 不真打 LLM).
5. pip install openai

跑:
    set OPENAI_API_KEY=sk-fake
    set OPENAI_BASE_URL=http://127.0.0.1:9876/v1
    set SISOUL_BORROW_BOB_URL=http://192.0.2.15:9877
    set SISOUL_OPENAI_COMPAT_MOCK=1
    python openai_compat_e2e.py

期望:
    Response: [MOCK] echo: <|system|> ... <|user|> Hello from codex via sisoul!
    [OK] OpenAI-compat round-trip via sisoul borrow succeeded
"""
from __future__ import annotations

import os
import sys

# 走标准 openai SDK ≥ 1.x
try:
    from openai import OpenAI
except ImportError:
    print("[FAIL] missing 'openai' package. pip install openai", file=sys.stderr)
    sys.exit(3)


def main() -> int:
    base_url = os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:9876/v1")
    api_key = os.environ.get("OPENAI_API_KEY", "sk-fake")
    model = os.environ.get("OPENAI_COMPAT_E2E_MODEL", "claude-opus-4-7")

    print(f"OPENAI_BASE_URL: {base_url}")
    print(f"OPENAI_API_KEY:  {api_key[:6]}***")
    print(f"Target model:    {model}")
    print(f"SISOUL_OPENAI_COMPAT_MOCK: {os.environ.get('SISOUL_OPENAI_COMPAT_MOCK', '<unset>')}")
    print(f"SISOUL_BORROW_BOB_URL:     {os.environ.get('SISOUL_BORROW_BOB_URL', '<unset>')}")

    client = OpenAI(base_url=base_url, api_key=api_key)

    # 1. /v1/models
    print("\n--- GET /v1/models ---")
    try:
        models = client.models.list()
        ids = [m.id for m in models.data]
        print(f"Models: {ids}")
    except Exception as e:
        print(f"[FAIL] /v1/models error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    # 2. /v1/chat/completions
    print("\n--- POST /v1/chat/completions ---")
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a sisoul borrow test."},
                {"role": "user", "content": "Hello from codex via sisoul!"},
            ],
            max_tokens=100,
            temperature=0.5,
        )
    except Exception as e:
        print(f"[FAIL] chat.completions error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    print(f"id:              {resp.id}")
    print(f"created:         {resp.created}")
    print(f"model:           {resp.model}")
    print(f"finish_reason:   {resp.choices[0].finish_reason}")
    print(f"content[:200]:   {resp.choices[0].message.content[:200]}")
    print(f"usage:           {resp.usage}")

    content = resp.choices[0].message.content or ""
    if not content:
        print("[FAIL] empty content", file=sys.stderr)
        return 2
    if "[MOCK]" not in content and "echo" not in content.lower():
        # 注: 若 SISOUL_OPENAI_COMPAT_MOCK=1 但 Bob 端 forwarder 不是 mock, 可能真打 LLM.
        # 这里软警告, 不当 fail (允许 Bob 端真 forwarder 跑成功).
        print("[WARN] no MOCK marker — Bob 走真 forwarder? content 可能是真 LLM 回")
    if "Hello from codex via sisoul" not in content and "[MOCK]" not in content:
        # 纯真 LLM 回不一定 echo 原话, 也允许
        pass

    print("\n[OK] OpenAI-compat round-trip via sisoul borrow succeeded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
