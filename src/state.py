"""LangGraph pipeline 的共享状态。

三个 Agent 节点（collector → analyzer → organizer）共享 ``KBState``。
每个节点接收整个 State，只修改自己负责的字段；线性 pipeline 不打回。

形状来自 AGENTS.md → LangGraph Pipeline → State 定义。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TypedDict


class KBState(TypedDict, total=False):
    """pipeline 共享状态。

    Attributes:
        batch_id: 批次标识，格式 ``YYYYMMDD``（UTC 日），幂等键之一。
        raw_items: 采集的原始数据（RawItem dict 列表）。
        articles: 分析后的结构化条目（Article dict 列表）。
        distributed: 已分发的条目。
        errors: 各环节异常记录（ErrorEntry dict 列表）。
    """

    batch_id: str
    raw_items: list[dict]
    articles: list[dict]
    distributed: list[dict]
    errors: list[dict]


def today_batch_id(now: datetime | None = None) -> str:
    """返回当日 UTC 批次 id（``YYYYMMDD``）。

    Args:
        now: 用于测试注入的时间；默认当前 UTC。
    """

    ts = now or datetime.now(timezone.utc)
    return ts.strftime("%Y%m%d")


def new_state(batch_id: str | None = None) -> KBState:
    """构造一个空的初始 State，所有列表字段预置为空列表。

    Args:
        batch_id: 显式批次 id；默认取当日 UTC。
    """

    return KBState(
        batch_id=batch_id or today_batch_id(),
        raw_items=[],
        articles=[],
        distributed=[],
        errors=[],
    )


def record_error(
    state: KBState,
    *,
    stage: str,
    reason: str,
    source: str | None = None,
    source_url: str | None = None,
) -> None:
    """向 ``state['errors']`` 追加一条错误记录（原地修改）。"""

    entry: dict = {"stage": stage, "reason": reason}
    if source is not None:
        entry["source"] = source
    if source_url is not None:
        entry["source_url"] = source_url
    state.setdefault("errors", []).append(entry)
