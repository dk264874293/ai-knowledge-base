"""日志初始化。

AGENTS.md 红线：禁止裸 ``print()``，统一 ``logging``。

格式：``2026-06-11 10:30:00 [INFO] collector: Collected 25 items``
默认输出 stdout，级别由 ``config.yaml`` 的 ``log_level`` 可调（默认 INFO）。
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(level: str | int = "INFO", force: bool = False) -> None:
    """初始化根 logger。

    Args:
        level: 日志级别（字符串如 ``"INFO"`` 或整数）。
        force: 已配置后是否强制重新配置（测试场景用）。
    """

    global _configured
    if _configured and not force:
        return

    numeric_level = (
        getattr(logging, str(level).upper())
        if isinstance(level, str)
        else int(level)
    )
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))

    root = logging.getLogger()
    # 清掉既有 handler，避免重复输出（测试 force=True 时尤其重要）
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(numeric_level)
    _configured = True


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """便捷获取 logger，自动确保 logging 已初始化。"""

    if not _configured:
        setup_logging()
    return logging.getLogger(name)
