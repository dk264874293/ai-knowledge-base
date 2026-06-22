"""LangGraph 线性 pipeline：collector → analyzer → organizer。

节点不打回，每个节点接收整个 State、只改自己负责字段。
"""

from __future__ import annotations

from typing import Callable

from langgraph.graph import END, START, StateGraph

from src.nodes import analyzer_node, collector_node, organizer_node
from src.state import KBState
from src.utils.logging import get_logger

log = get_logger("pipeline")


def build_graph():
    """构造并编译 LangGraph 线性图。"""

    graph = StateGraph(KBState)
    graph.add_node("collector", collector_node)
    graph.add_node("analyzer", analyzer_node)
    graph.add_node("organizer", organizer_node)

    graph.add_edge(START, "collector")
    graph.add_edge("collector", "analyzer")
    graph.add_edge("analyzer", "organizer")
    graph.add_edge("organizer", END)
    return graph.compile()


def run_pipeline(initial: KBState | None = None) -> KBState:
    """跑一遍完整 pipeline。

    Args:
        initial: 初始 State；默认 ``new_state()``。

    Returns:
        运行结束后的最终 State。
    """

    app = build_graph()
    state = initial if initial is not None else _new_state()
    log.info("pipeline: start batch_id=%s", state.get("batch_id"))
    result = app.invoke(state)
    log.info(
        "pipeline: done raw=%d articles=%d distributed=%d errors=%d",
        len(result.get("raw_items", [])),
        len(result.get("articles", [])),
        len(result.get("distributed", [])),
        len(result.get("errors", [])),
    )
    return result


def _new_state() -> KBState:
    # 局部 import 避免循环
    from src.state import new_state

    return new_state()


# 节点单例映射，供 CLI 单阶段调用
NODE_BY_NAME: dict[str, Callable[[KBState], KBState]] = {
    "collector": collector_node,
    "analyzer": analyzer_node,
    "organizer": organizer_node,
}
