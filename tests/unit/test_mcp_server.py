"""mcp_knowledge_server.py 的单元测试。

覆盖三个工具（search_articles / get_article / knowledge_stats）与 JSON-RPC
协议层（initialize / tools/list / tools/call / notification / 错误码）。
所有文件读写隔离到 tmp_path，不触碰真实 knowledge/。
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

# 顶层模块，无包前缀（仿 pipeline 测试用 alias 提高可读性）
from mcp_knowledge_server import (
    ERR_INVALID_PARAMS,
    ERR_METHOD_NOT_FOUND,
    ERR_PARSE_ERROR,
    dispatch_tool,
    get_article,
    handle_message,
    knowledge_stats,
    search_articles,
    serve,
)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


def _article(
    *,
    aid: str,
    title: str,
    source: str = "github",
    summary: str = "",
    tags: list[str] | None = None,
    category: str = "tool",
    score: float = 0.5,
    status: str = "published",
    review_reason: str | None = None,
) -> dict[str, Any]:
    """构造一篇最小可用的文章 dict（schema 与真实 knowledge/articles 对齐）。"""

    art: dict[str, Any] = {
        "id": aid,
        "version": 1,
        "parent_id": None,
        "title": title,
        "source_url": f"https://example.com/{aid}",
        "source": source,
        "collected_at": "2026-06-24T20:59:48+08:00",
        "summary": summary,
        "tags": tags or [],
        "category": category,
        "relevance_score": score,
        "status": status,
        "distributed_to": [],
        "created_at": "2026-06-24T21:02:13+08:00",
        "updated_at": "2026-06-24T21:02:13+08:00",
    }
    if review_reason is not None:
        art["review_reason"] = review_reason
    return art


def _write_article(dir_path: Path, art: dict[str, Any]) -> None:
    """把文章按真实命名约定写盘：{date}_{id}_v{version}.json。"""

    aid = art["id"].split("-")[-1]  # kb-20260624-001 -> 001
    name = f"20260624_{art['id']}_v{art['version']}.json"
    (dir_path / name).write_text(
        json.dumps(art, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@pytest.fixture()
def articles_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离的 articles 目录，预置 3 篇文章 + 1 个坏 JSON 文件。"""

    d = tmp_path / "articles"
    d.mkdir()

    _write_article(
        d,
        _article(
            aid="kb-20260624-001",
            title="NousResearch/hermes-agent",
            summary="Hermes Agent 是一个 AI 代理工具，支持 Claude 与 ChatGPT。",
            tags=["ai-agent", "llm", "tool"],
            category="tool",
            score=0.95,
        ),
    )
    _write_article(
        d,
        _article(
            aid="kb-20260624-002",
            title="langgenius/dify",
            source="rss",
            summary="Dify 是开源 LLM 应用开发平台。",
            tags=["llm", "platform"],
            category="framework",
            score=0.8,
        ),
    )
    _write_article(
        d,
        _article(
            aid="kb-20260624-040",
            title="Show HN: TikZ Editor",
            source="rss",
            summary="TikZ 编辑器，所见即所得。",
            tags=["tikz", "editor", "latex"],
            category="tool",
            score=0.3,
            status="archived",
            review_reason="相关度 0.3 低于阈值 0.4",
        ),
    )
    # 坏 JSON：必须被跳过且不影响其他条目
    (d / "20260624_kb-20260624-099_v1.json").write_text(
        "{不是合法 JSON", encoding="utf-8"
    )

    monkeypatch.setenv("KB_ARTICLES_DIR", str(d))
    return d


# --------------------------------------------------------------------------- #
# search_articles
# --------------------------------------------------------------------------- #


def test_search_hits_title_case_insensitive(articles_dir: Path) -> None:
    """关键词大小写不敏感，命中标题。"""

    result = search_articles("HERMES")
    assert len(result) == 1
    assert result[0]["id"] == "kb-20260624-001"


def test_search_hits_summary(articles_dir: Path) -> None:
    """关键词命中摘要。"""

    result = search_articles("开源")
    assert [r["id"] for r in result] == ["kb-20260624-002"]


def test_search_hits_tags(articles_dir: Path) -> None:
    """关键词命中标签。"""

    result = search_articles("latex")
    assert [r["id"] for r in result] == ["kb-20260624-040"]


def test_search_sorted_by_score_desc(articles_dir: Path) -> None:
    """结果按 relevance_score 降序。"""

    result = search_articles("llm")
    # 001(0.95) 与 002(0.8) 标签都含 llm
    assert [r["id"] for r in result] == ["kb-20260624-001", "kb-20260624-002"]


def test_search_empty_keyword_returns_top_by_score(
    articles_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """空 keyword 返回得分最高的条目，并记 WARNING。"""

    with caplog.at_level("WARNING"):
        result = search_articles("", limit=2)
    assert [r["id"] for r in result] == ["kb-20260624-001", "kb-20260624-002"]
    assert any("keyword 为空" in rec.message for rec in caplog.records)


def test_search_limit_clamped(articles_dir: Path) -> None:
    """limit 超出范围被夹到 [1, 50]。"""

    assert len(search_articles("", limit=0)) == 1  # 夹到 1
    assert len(search_articles("", limit=999)) <= 50


def test_search_summary_preview_truncated() -> None:
    """summary 预览超长被截断并加省略号。"""

    import mcp_knowledge_server as M

    long_text = "摘要" * 200  # 远超 SUMMARY_PREVIEW_LEN
    assert M._preview(long_text).endswith("…")


def test_search_skips_corrupt_file(articles_dir: Path) -> None:
    """坏 JSON 文件被跳过，不影响正常条目计数。"""

    result = search_articles("", limit=50)
    # 3 篇正常文章，不含坏文件
    assert len(result) == 3


def test_search_result_has_compact_fields(articles_dir: Path) -> None:
    """精简视图只含约定字段。"""

    result = search_articles("hermes")
    keys = set(result[0].keys())
    assert keys == {
        "id",
        "title",
        "source",
        "category",
        "relevance_score",
        "tags",
        "summary",
    }


# --------------------------------------------------------------------------- #
# get_article
# --------------------------------------------------------------------------- #


def test_get_article_found(articles_dir: Path) -> None:
    """按 id 精确命中，返回完整文章。"""

    art = get_article("kb-20260624-040")
    assert art is not None
    assert art["id"] == "kb-20260624-040"
    assert art["status"] == "archived"
    assert art["review_reason"] == "相关度 0.3 低于阈值 0.4"


def test_get_article_not_found(articles_dir: Path) -> None:
    """未命中返回 None。"""

    assert get_article("kb-0000-999") is None


def test_get_article_empty_id(articles_dir: Path) -> None:
    """空 id 返回 None。"""

    assert get_article("") is None
    assert get_article("   ") is None


# --------------------------------------------------------------------------- #
# knowledge_stats
# --------------------------------------------------------------------------- #


def test_knowledge_stats_counts(articles_dir: Path) -> None:
    """统计总数、来源、分类、状态、热门标签。"""

    stats = knowledge_stats()
    assert stats["total_articles"] == 3
    assert stats["by_source"] == {"github": 1, "rss": 2}
    assert stats["by_category"] == {"tool": 2, "framework": 1}
    assert stats["by_status"] == {"published": 2, "archived": 1}
    # llm 出现两次（001、002），应排在最前
    top_tag, top_count = stats["top_tags"][0]
    assert top_tag == "llm"
    assert top_count == 2


def test_knowledge_stats_empty_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """空目录统计全为零值。"""

    d = tmp_path / "empty"
    d.mkdir()
    monkeypatch.setenv("KB_ARTICLES_DIR", str(d))
    stats = knowledge_stats()
    assert stats["total_articles"] == 0
    assert stats["by_source"] == {}
    assert stats["top_tags"] == []


# --------------------------------------------------------------------------- #
# 协议层：initialize / tools/list
# --------------------------------------------------------------------------- #


def test_handle_initialize() -> None:
    """initialize 返回协议版本与 serverInfo。"""

    msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    resp = handle_message(msg)
    assert resp is not None
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    result = resp["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"]


def test_handle_tools_list() -> None:
    """tools/list 返回 3 个工具及其 inputSchema。"""

    resp = handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert resp is not None
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == {"search_articles", "get_article", "knowledge_stats"}
    for tool in resp["result"]["tools"]:
        assert "inputSchema" in tool


def test_notification_no_response() -> None:
    """notification（无 id）不回包。"""

    resp = handle_message(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert resp is None


def test_unknown_method_returns_error() -> None:
    """未知方法返回 method not found 错误码。"""

    resp = handle_message(
        {"jsonrpc": "2.0", "id": 3, "method": "resources/list"}
    )
    assert resp is not None
    assert resp["error"]["code"] == ERR_METHOD_NOT_FOUND
    assert resp["id"] == 3


def test_invalid_request_object() -> None:
    """非 JSON-RPC 2.0 对象回 invalid_request（带 id 时）。"""

    resp = handle_message({"jsonrpc": "1.0", "id": 4, "method": "foo"})
    assert resp is not None
    assert resp["error"]["code"] == -32600


# --------------------------------------------------------------------------- #
# 协议层：tools/call
# --------------------------------------------------------------------------- #


def test_dispatch_unknown_tool() -> None:
    """调用未知工具返回 method not found。"""

    with pytest.raises(Exception) as exc_info:  # noqa: PT011
        dispatch_tool("nope", {})
    assert exc_info.value.code == ERR_METHOD_NOT_FOUND  # type: ignore[attr-defined]


def test_dispatch_search_invalid_params() -> None:
    """search_articles 缺少/类型错的 keyword 返回 invalid params。"""

    with pytest.raises(Exception) as exc_info:  # noqa: PT011
        dispatch_tool("search_articles", {})
    assert exc_info.value.code == ERR_INVALID_PARAMS  # type: ignore[attr-defined]


def test_handle_tools_call_search(articles_dir: Path) -> None:
    """tools/call search_articles 返回 MCP content 结构。"""

    resp = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "search_articles",
                "arguments": {"keyword": "dify", "limit": 3},
            },
        }
    )
    assert resp is not None
    content = resp["result"]["content"]
    assert content[0]["type"] == "text"
    payload = json.loads(content[0]["text"])
    assert payload[0]["id"] == "kb-20260624-002"


def test_handle_tools_call_get_article_not_found(
    articles_dir: Path,
) -> None:
    """get_article 未命中时 isError=True。"""

    resp = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "get_article",
                "arguments": {"article_id": "kb-9999-999"},
            },
        }
    )
    assert resp is not None
    assert resp["result"]["isError"] is True


def test_handle_tools_call_missing_name() -> None:
    """tools/call 缺 params.name 返回 invalid params。"""

    resp = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"arguments": {}},
        }
    )
    assert resp is not None
    assert resp["error"]["code"] == ERR_INVALID_PARAMS


# --------------------------------------------------------------------------- #
# 端到端：serve 循环
# --------------------------------------------------------------------------- #


def test_serve_end_to_end(articles_dir: Path) -> None:
    """serve 处理多行请求，stdout 逐行返回合规响应。"""

    lines = [
        json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
        ),
        json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        ),  # 不回包
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "knowledge_stats",
                    "arguments": {},
                },
            }
        ),
    ]
    in_stream = io.StringIO("\n".join(lines) + "\n")
    out_stream = io.StringIO()

    serve(in_stream, out_stream)

    responses = [
        json.loads(line)
        for line in out_stream.getvalue().splitlines()
        if line.strip()
    ]
    # notification 不回包：只应有 3 条响应
    assert len(responses) == 3
    assert responses[0]["id"] == 1
    assert "tools" in responses[1]["result"]
    assert responses[2]["result"]["content"][0]["type"] == "text"


def test_serve_parse_error(articles_dir: Path) -> None:
    """坏 JSON 行返回 parse error（id=null）。"""

    in_stream = io.StringIO("{坏 json\n")
    out_stream = io.StringIO()
    serve(in_stream, out_stream)

    resp = json.loads(out_stream.getvalue().strip())
    assert resp["id"] is None
    assert resp["error"]["code"] == ERR_PARSE_ERROR
