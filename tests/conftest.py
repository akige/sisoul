"""pytest conftest · 兜底注入 src/ 到 sys.path.

editable install (.pth) 在 CJK 路径 + C locale 下偶发失效 (并行 agent 同时改 site-packages
触发 'Resource deadlock avoided'). conftest 兜底用 PYTHONPATH 逻辑直接注入 src/, 保证测试稳定.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
