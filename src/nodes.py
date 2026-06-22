"""LangGraph 三节点（collector / analyzer / organizer）。

#01 阶段：节点先用 stub（返回固定假数据），但**真实落盘** ``knowledge/raw``
与 ``knowledge/articles``，证明 CLI → 图 → State → 文件 的端到端骨架成立。
后续切片（#02/#03/#04）把 stub 替换为真实采集 / LLM / 整理实现。
"""

from __future__ import annotations

from datetime import datetime, timezone

from src import storage
from src.state import KBState, new_state, record_error
from src.utils.logging import get_logger

log = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Collector（stub）
# --------------------------------------------------------------------------- #


def collector_node(state: KBState) -> KBState:
    """采集节点（stub）：返回固定假数据并真实落盘 raw。

    仅修改 ``state['raw_items']``；失败写入 ``state['errors']``。
    """

    log.info("collector: start (batch_id=%s)", state.get("batch_id"))
    state.setdefault("raw_items", [])

    seen = storage.existing_source_urls()
    mock_url = "https://github.com/example/mock-repo"
    if mock_url in seen:
        log.info("collector: mock item already collected, skip")
    else:
        raw = {
            "id": "raw-" + state["batch_id"] + "-001",
            "source": "github_trending",
            "source_url": mock_url,
            "title": "example/mock-repo",
            "raw_content": "stub raw content for happy-path",
            "collected_at": _now_iso(),
            "metadata": {
                "stars_today": 42,
                "language": "Python",
                "description": "stub repo for skeleton happy-path",
            },
        }
        try:
            storage.write_raw(raw)
            state["raw_items"].append(raw)
            log.info("collector: collected 1 item")
        except Exception as e:  # noqa: BLE001 — 框架阶段记录所有异常
            record_error(
                state,
                stage="collector",
                reason=f"write raw failed: {e}",
                source="github_trending",
                source_url=mock_url,
            )
            log.error("collector: failed -> %s", e)
    return state


# --------------------------------------------------------------------------- #
# Analyzer（stub）
# --------------------------------------------------------------------------- #


def analyzer_node(state: KBState) -> KBState:
    """分析节点（stub）：对 raw_items 产出固定 article 并真实落盘。

    仅修改 ``state['articles']``；LLM 返回异常记 WARNING 并跳过，不中断。
    """

    log.info("analyzer: start, %d raw items", len(state.get("raw_items", [])))
    state.setdefault("articles", [])

    for raw in state.get("raw_items", []):
        try:
            article = {
                "id": "kb-" + state["batch_id"] + "-001",
                "version": 1,
                "parent_id": None,
                "title": raw["title"],
                "source_url": raw["source_url"],
                "source": raw["source"],
                "collected_at": raw["collected_at"],
                "summary": (
                    "这是骨架阶段的 stub 中文摘要，长度满足 published 门下界 50 字符，"
                    "用于证明 analyzer 节点能正确产出并落盘结构化条目。"
                ),
                "tags": ["agent", "llm"],
                "category": "framework",
                "relevance_score": 0.8,
                "status": "published",
                "distributed_to": [],
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }
            storage.write_article(article)
            state["articles"].append(article)
        except Exception as e:  # noqa: BLE001
            record_error(
                state,
                stage="analyzer",
                reason=f"analyze failed: {e}",
                source=raw.get("source"),
                source_url=raw.get("source_url"),
            )
            log.warning("analyzer: skip %s -> %s", raw.get("source_url"), e)
    log.info("analyzer: produced %d articles", len(state["articles"]))
    return state


# --------------------------------------------------------------------------- #
# Organizer（stub）
# --------------------------------------------------------------------------- #


def organizer_node(state: KBState) -> KBState:
    """整理节点（stub）：把 articles 写成当日 MD 日报（digest）。

    仅修改 ``state['distributed']``（这里以「digest 产物」占位）。
    真实渠道推送（Telegram/飞书）留待 #08。
    """

    log.info("organizer: start, %d articles", len(state.get("articles", [])))
    state.setdefault("distributed", [])

    articles = state.get("articles", [])
    if not articles:
        log.info("organizer: no articles, skip digest")
        return state

    batch_id = state.get("batch_id", "")
    lines = [f"# 🤖 AI 技术日报 - {batch_id}\n"]
    for i, art in enumerate(articles, 1):
        lines.append(f"{i}. [{art['title']}]({art['source_url']})")
        lines.append(art["summary"] + "\n")
    digest = "\n".join(lines)

    digest_dir = storage.knowledge_dir() / "digests"
    digest_dir.mkdir(parents=True, exist_ok=True)
    digest_path = digest_dir / f"digest_{batch_id}.md"
    digest_path.write_text(digest, encoding="utf-8")
    log.info("organizer: wrote digest -> %s", digest_path.name)

    state["distributed"] = [{"batch_id": batch_id, "digest": str(digest_path)}]
    return state
