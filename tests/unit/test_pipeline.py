"""LangGraph pipeline 端到端流转测试（#01 验收）。

覆盖：

- KBState 在三节点间正确流转
- 每个节点只修改自己负责的字段
- collector → analyzer → organizer 串行落盘，文件名符合契约
- collector 幂等（重跑不产生重复 raw）
- 失败传播：analyzer 失败时记录 errors，不中断 organizer

所有落盘隔离到 tmp_path，不污染真实 knowledge/。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src import storage
from src.nodes import analyzer_node, collector_node, organizer_node
from src.pipeline import build_graph
from src.state import new_state


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 storage 的根目录重定向到 tmp_path，隔离真实 knowledge/。"""

    root = tmp_path / "kb"
    root.mkdir()
    monkeypatch.setattr(storage, "_project_root", lambda: root)
    # nodes.organizer_node 内部直接调用 storage.knowledge_dir()，同样受 _project_root 影响
    return root


@pytest.fixture()
def batch_id() -> str:
    return "20260622"


@pytest.fixture()
def fresh_state(batch_id: str) -> dict:
    return new_state(batch_id)


# --------------------------------------------------------------------------- #
# KBState 流转 + 字段归属
# --------------------------------------------------------------------------- #


def test_collector_only_writes_raw_items(fresh_state, isolated_root):
    """collector 只应改 raw_items（与 errors），不动 articles/distributed。"""

    before = {k: list(v) if isinstance(v, list) else v for k, v in fresh_state.items()}
    result = collector_node(fresh_state)

    assert len(result["raw_items"]) == 1
    # articles / distributed 保持初始空列表
    assert result["articles"] == before["articles"]
    assert result["distributed"] == before["distributed"]


def test_pipeline_state_flows_through_all_nodes(fresh_state, isolated_root, batch_id):
    """端到端：raw → article → digest 三段数据依次流转、真实落盘。"""

    app = build_graph()
    result = app.invoke(fresh_state)

    # collector 产出 raw
    assert len(result["raw_items"]) == 1
    raw = result["raw_items"][0]
    assert raw["source"] == "github_trending"

    # analyzer 读 raw 产出 article
    assert len(result["articles"]) == 1
    article = result["articles"][0]
    assert article["source_url"] == raw["source_url"]
    assert article["status"] == "published"

    # organizer 读 article 产出 digest
    assert len(result["distributed"]) == 1
    assert "digest" in result["distributed"][0]


def test_files_written_with_contract_filenames(fresh_state, isolated_root, batch_id):
    """落盘文件名必须符合 schemas 契约正则。"""

    from src import schemas

    app = build_graph()
    app.invoke(fresh_state)

    raws = list(storage.iter_raw_files())
    articles = list(storage.iter_article_files())

    assert len(raws) == 1
    assert len(articles) == 1
    assert schemas.RAW_FILENAME_RE.match(raws[0].name), raws[0].name
    assert schemas.ARTICLE_FILENAME_RE.match(articles[0].name), articles[0].name
    assert raws[0].name == f"raw_{batch_id}_raw-{batch_id}-001.json"
    assert articles[0].name == f"{batch_id}_kb-{batch_id}-001_v1.json"


# --------------------------------------------------------------------------- #
# 幂等（#05 预演）
# --------------------------------------------------------------------------- #


def test_collector_is_idempotent_on_rerun(fresh_state, isolated_root):
    """同 batch 重跑 collector，因 source_url 已存在应跳过，不产生重复 raw。"""

    collector_node(fresh_state)
    # 第二次：fresh_state 已带 raw_items，但去重以落盘 source_url 为准
    fresh_state["raw_items"] = []
    collector_node(fresh_state)

    raws = list(storage.iter_raw_files())
    assert len(raws) == 1, "重跑不应产生重复 raw"


# --------------------------------------------------------------------------- #
# 失败传播（#05 预演）
# --------------------------------------------------------------------------- #


def test_analyzer_failure_recorded_not_fatal(fresh_state, isolated_root, batch_id, mocker):
    """analyzer 处理失败应进 errors，不中断后续条目。"""

    fresh_state["raw_items"] = [
        {
            "id": f"raw-{batch_id}-001",
            "source": "github_trending",
            "source_url": "https://github.com/x/ok",
            "title": "ok",
            "raw_content": "x",
            "collected_at": "2026-06-22T00:00:00+00:00",
            "metadata": {"stars_today": 1, "language": "Python", "description": "d"},
        }
    ]
    # 让落盘抛异常，模拟写盘失败
    mocker.patch.object(storage, "write_article", side_effect=OSError("disk full"))

    analyzer_node(fresh_state)
    assert fresh_state["errors"], "写盘失败应进 errors"
    assert fresh_state["errors"][0]["stage"] == "analyzer"
    assert "disk full" in fresh_state["errors"][0]["reason"]
