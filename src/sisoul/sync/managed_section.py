"""sisoul-managed 段标记 + 增量替换工具.

核心约束 (§28 §1.1 模块 4 跨工具 sync):
- sisoul 只覆盖 `<!-- sisoul-managed-start -->` ... `<!-- sisoul-managed-end -->` 之间的内容
- 用户手写段 (markers 前后) 一律保留
- 标记 corrupted (只有 start 没有 end / 反过来 / 多个 start) → raise, 不写

不同格式 (markdown / plain text / yaml) 用不同 marker comment 语法:
- markdown / plain text: HTML 注释 (兼容 markdown renderer + plaintext)
- yaml: `# sisoul-managed-start` / `# sisoul-managed-end`
"""

from __future__ import annotations

from dataclasses import dataclass

# 默认 markdown / plain text 段标记 (HTML 注释)
START_MARKER = "<!-- sisoul-managed-start -->"
END_MARKER = "<!-- sisoul-managed-end -->"

# YAML 段标记 (yaml 不认 HTML 注释, 用 # 注释)
YAML_START_MARKER = "# sisoul-managed-start"
YAML_END_MARKER = "# sisoul-managed-end"


class ManagedSectionError(ValueError):
    """sisoul-managed 段标记 corrupted (start 多个 / 缺一边 / 顺序反)."""


@dataclass(frozen=True)
class MarkerPair:
    """一对 start/end marker (按格式定制)."""

    start: str
    end: str

    @classmethod
    def default(cls) -> "MarkerPair":
        return cls(start=START_MARKER, end=END_MARKER)

    @classmethod
    def yaml(cls) -> "MarkerPair":
        return cls(start=YAML_START_MARKER, end=YAML_END_MARKER)


def extract_managed_section(
    file_content: str,
    markers: MarkerPair | None = None,
) -> str | None:
    """返回 markers 之间的内容 (不含 marker 行本身).

    无 marker → None.
    marker corrupted → raise ManagedSectionError.
    """
    m = markers or MarkerPair.default()
    start_count = file_content.count(m.start)
    end_count = file_content.count(m.end)

    if start_count == 0 and end_count == 0:
        return None

    if start_count != end_count:
        raise ManagedSectionError(
            f"sisoul-managed 段 marker 不配对: start={start_count}, end={end_count} "
            f"(start='{m.start}', end='{m.end}')"
        )

    if start_count > 1:
        raise ManagedSectionError(
            f"sisoul-managed 段 marker 出现 {start_count} 次, 应只 1 对"
        )

    start_idx = file_content.find(m.start)
    end_idx = file_content.find(m.end)

    if end_idx < start_idx:
        raise ManagedSectionError(
            f"sisoul-managed 段 end marker 出现在 start 之前 "
            f"(start@{start_idx}, end@{end_idx})"
        )

    inner_start = start_idx + len(m.start)
    # 去掉首尾换行让结果更干净
    return file_content[inner_start:end_idx].strip("\n")


def insert_or_replace(
    file_content: str,
    managed_content: str,
    markers: MarkerPair | None = None,
) -> str:
    """把 managed_content 包在 markers 里, 插入或替换进 file_content.

    - 文件不存在 (空 content) → 只写 managed 段
    - 已有 markers → 仅替换 markers 之间的内容, marker 行不动
    - 没 markers → append 到文件末尾 (前空一行)
    - markers corrupted → raise ManagedSectionError

    managed_content 不含 marker 行 (本函数加).
    """
    m = markers or MarkerPair.default()
    start_count = file_content.count(m.start)
    end_count = file_content.count(m.end)

    if start_count != end_count or start_count > 1:
        raise ManagedSectionError(
            f"sisoul-managed marker corrupted: start={start_count}, end={end_count}"
        )

    managed_block = f"{m.start}\n{managed_content.strip()}\n{m.end}"

    if start_count == 0:
        # 首次 sync: append
        if not file_content.strip():
            return managed_block + "\n"
        # 已有内容: 隔一空行 append
        sep = "" if file_content.endswith("\n\n") else ("\n" if file_content.endswith("\n") else "\n\n")
        return file_content + sep + managed_block + "\n"

    # 替换: 用字符串切片精确替换
    start_idx = file_content.find(m.start)
    end_idx = file_content.find(m.end)
    if end_idx < start_idx:
        raise ManagedSectionError("end marker 出现在 start 之前")

    before = file_content[:start_idx]
    after = file_content[end_idx + len(m.end):]
    return before + managed_block + after
