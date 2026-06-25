"""独立流水线 pipeline/pipeline.py 的单元测试。

覆盖四步：采集（github/rss）、分析（含降级）、整理（去重+审核门）、保存。
所有外部依赖（httpx / LLM）均 mock，落盘隔离到 tmp_path。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from pipeline.model_client import LLMResponse, Usage
from pipeline import pipeline as P


# --------------------------------------------------------------------------- #
# 配置 fixture
# --------------------------------------------------------------------------- #


@pytest.fixture()
def cfg(tmp_path: Path) -> dict[str, Any]:
    """带 tmp 目录的 pipeline 配置。"""

    return {
        "github": {
            "search_url": "https://api.github.com/search/repositories",
            "query": "AI LLM agent",
            "sort": "stars",
        },
        "rss": {"feeds": ["https://hnrss.org/frontpage"]},
        "http_timeout": 15,
        "http_retries": 3,
        "paths": {
            "raw_dir": str(tmp_path / "raw"),
            "articles_dir": str(tmp_path / "articles"),
        },
    }


# --------------------------------------------------------------------------- #
# Step 1: 采集
# --------------------------------------------------------------------------- #


def _mock_response(*, json_body=None, text=None, url="https://test.local"):
    """构造一个带 request 的 httpx.Response（raise_for_status 需要 request）。"""

    kwargs: dict[str, Any] = {"request": httpx.Request("GET", url)}
    if json_body is not None:
        kwargs["json"] = json_body
    if text is not None:
        kwargs["text"] = text
    return httpx.Response(200, **kwargs)


def test_collect_github_parses_items(cfg, mocker):
    """GitHub Search 响应应正确解析为原始条目。"""

    payload = {
        "items": [
            {
                "full_name": "langchain-ai/langgraph",
                "html_url": "https://github.com/langchain-ai/langgraph",
                "description": "Build resilient language agents as graphs.",
                "stargazers_count": 12000,
                "language": "Python",
                "topics": ["agent", "llm", "workflow"],
            }
        ]
    }
    mocker.patch.object(
        P.httpx, "get", return_value=_mock_response(json_body=payload)
    )

    items = P.collect_github(cfg, limit=5)

    assert len(items) == 1
    it = items[0]
    assert it["source"] == "github"
    assert it["source_url"] == "https://github.com/langchain-ai/langgraph"
    assert it["title"] == "langchain-ai/langgraph"
    assert it["metadata"]["stars"] == 12000
    assert it["metadata"]["language"] == "Python"
    assert "agent" in it["metadata"]["topics"]
    assert "collected_at" in it


def test_collect_github_returns_empty_on_failure(cfg, mocker):
    """重试耗尽后应返回空列表而非抛异常。"""

    mocker.patch.object(
        P.httpx,
        "get",
        side_effect=httpx.ConnectError("connection refused"),
    )
    mocker.patch.object(P.time, "sleep", return_value=None)  # 跳过退避等待

    items = P.collect_github(cfg, limit=5)
    assert items == []


def test_collect_rss_parses_items(cfg, mocker):
    """RSS XML 应被简易正则正确提取。"""

    xml = """<?xml version="1.0"?>
    <rss><channel>
      <item>
        <title><![CDATA[Show HN: New LLM Framework]]></title>
        <link>https://news.ycombinator.com/item?id=42</link>
        <description><![CDATA[<p>A brief description</p>]]></description>
        <pubDate>Mon, 23 Jun 2026 10:00:00 GMT</pubDate>
      </item>
    </channel></rss>"""
    mocker.patch.object(P.httpx, "get", return_value=_mock_response(text=xml))

    items = P.collect_rss(cfg, limit=10)

    assert len(items) == 1
    it = items[0]
    assert it["source"] == "rss"
    assert it["source_url"] == "https://news.ycombinator.com/item?id=42"
    assert "Show HN" in it["title"]
    assert "A brief description" in it["raw_content"]
    assert it["metadata"]["pub_date"] == "Mon, 23 Jun 2026 10:00:00 GMT"


def test_collect_rss_skips_items_without_link(cfg, mocker):
    """缺 <link> 的 item 应被跳过。"""

    xml = """<rss><channel>
      <item><title>No link here</title></item>
      <item>
        <title>Valid</title>
        <link>https://example.com/a</link>
      </item>
    </channel></rss>"""
    mocker.patch.object(P.httpx, "get", return_value=_mock_response(text=xml))

    items = P.collect_rss(cfg, limit=10)
    assert len(items) == 1
    assert items[0]["source_url"] == "https://example.com/a"


def test_collect_dispatches_by_source(cfg, mocker):
    """collect 应按 sources 列表分发到各采集函数。"""

    gh = [{"source": "github", "source_url": "https://github.com/a"}]
    rss = [{"source": "rss", "source_url": "https://example.com/b"}]
    mocker.patch.object(P, "collect_github", return_value=gh)
    mocker.patch.object(P, "collect_rss", return_value=rss)

    out = P.collect(["github", "rss"], cfg, limit=5)
    assert len(out) == 2
    assert out[0]["source"] == "github"
    assert out[1]["source"] == "rss"


# --------------------------------------------------------------------------- #
# Step 2: 分析
# --------------------------------------------------------------------------- #


_GITHUB_RAW = {
    "source": "github",
    "source_url": "https://github.com/x/y",
    "title": "x/y",
    "raw_content": "An AI agent framework with tool calling and memory.",
    "collected_at": "2026-06-24T00:00:00+08:00",
}


def _mock_llm_response(content: str) -> LLMResponse:
    """构造一个固定 content 的 LLMResponse（供 chat_with_retry mock 返回）。"""

    return LLMResponse(
        content=content,
        usage=Usage(),
        model="fake",
        provider="fake",
    )


def test_analyze_item_parses_valid_json(mocker):
    """合法 JSON 应被正确解析并规整。"""

    raw_content = "x" * 80
    raw = {**_GITHUB_RAW, "raw_content": raw_content}
    valid = json.dumps(
        {
            "summary": "这是一个关于 AI agent 框架的中文摘要，长度满足审核门下界要求的字符数。",
            "tags": ["agent", "llm", "framework"],
            "category": "framework",
            "relevance_score": 0.9,
        }
    )
    mocker.patch.object(
        P, "chat_with_retry", return_value=_mock_llm_response(valid)
    )

    result = P.analyze_item(raw, provider=None)

    assert result["category"] == "framework"
    assert result["relevance_score"] == 0.9
    assert "agent" in result["tags"]


def test_analyze_item_strips_markdown_fence(mocker):
    """模型偶尔包裹 ```json ... ```，应仍能提取。"""

    raw = {**_GITHUB_RAW, "raw_content": "x" * 80}
    fenced = '```json\n{"summary":"摘要内容足够长的中文文本用于测试长度门限是否被正确处理。","tags":["llm"],"category":"model","relevance_score":0.7}\n```'
    mocker.patch.object(
        P, "chat_with_retry", return_value=_mock_llm_response(fenced)
    )

    result = P.analyze_item(raw, provider=None)
    assert result["category"] == "model"
    assert result["relevance_score"] == 0.7


def test_analyze_item_falls_back_on_garbled_text(mocker):
    """LLM 返回非 JSON 文本时应走降级默认值。"""

    raw = {**_GITHUB_RAW, "raw_content": "x" * 80}
    mocker.patch.object(
        P, "chat_with_retry", return_value=_mock_llm_response("not json")
    )

    result = P.analyze_item(raw, provider=None)

    assert result["_fallback"] is True
    assert result["category"] == "tool"
    assert result["relevance_score"] == 0.5


def test_analyze_item_falls_back_on_llm_error(mocker):
    """LLM 调用失败时应走降级默认值。"""

    from pipeline.model_client import LLMError

    raw = {**_GITHUB_RAW, "raw_content": "x" * 80}
    mocker.patch.object(P, "chat_with_retry", side_effect=LLMError("boom"))

    result = P.analyze_item(raw, provider=None)
    assert result["_fallback"] is True


def test_coerce_analysis_clamps_out_of_range():
    """越界的 relevance_score 与非法 category 应被规整。"""

    out = P._coerce_analysis(
        {
            "summary": "x" * 60,
            "tags": ["a", "b", "c", "d", "e", "f", "g"],  # 超 5 个
            "category": "UNKNOWN",
            "relevance_score": 1.5,
        }
    )
    assert len(out["tags"]) == 5
    assert out["category"] == "tool"
    assert out["relevance_score"] == 1.0


# --------------------------------------------------------------------------- #
# Step 3: 整理
# --------------------------------------------------------------------------- #


def _article(title, url, summary=None, tags=None, score=0.8, category="tool"):
    """构造一个已分析未规整的 article dict。"""

    return {
        "title": title,
        "source_url": url,
        "source": "github",
        "collected_at": "2026-06-24T00:00:00+08:00",
        "summary": summary or ("x" * 100),
        "tags": tags or ["ai"],
        "category": category,
        "relevance_score": score,
        "raw": {},
    }


def test_dedup_drops_exact_url_duplicate():
    """source_url 相同的条目应被丢弃。"""

    arts = [
        _article("A", "https://github.com/a"),
        _article("B", "https://github.com/a"),  # 同 URL
    ]
    kept = P.dedup(arts)
    assert len(kept) == 1
    assert kept[0]["title"] == "A"


def test_dedup_marks_similar_titles_as_review():
    """标题高度相似的条目应保留但标 review。"""

    arts = [
        _article("LangGraph Agent Framework Tool", "https://github.com/a"),
        _article("LangGraph Agent Framework", "https://github.com/b"),
    ]
    kept = P.dedup(arts)
    assert len(kept) == 2
    assert kept[1]["status"] == "review"
    assert "相似" in kept[1]["review_reason"]


def test_dedup_uses_existing_urls_from_disk():
    """盘上已有的 source_url 也应被视为重复。"""

    arts = [_article("A", "https://github.com/seen")]
    kept = P.dedup(arts, existing_urls={"https://github.com/seen"})
    assert kept == []


def test_validate_and_gate_archives_low_score():
    """relevance_score < 0.4 应标 archived。"""

    arts = [P.normalize([_article("A", "https://a", score=0.2)])[0]]
    out = P.validate_and_gate(arts)
    assert out[0]["status"] == "archived"


def test_validate_and_gate_marks_short_summary_review():
    """摘要过短应标 review。"""

    arts = [P.normalize([_article("A", "https://a", summary="短")])[0]]
    out = P.validate_and_gate(arts)
    assert out[0]["status"] == "review"


def test_validate_and_gate_publishes_valid_article():
    """全部合规的高分条目应标 published。"""

    arts = [P.normalize([_article("A", "https://a", score=0.9)])[0]]
    out = P.validate_and_gate(arts)
    assert out[0]["status"] == "published"
    assert "review_reason" not in out[0]


# --------------------------------------------------------------------------- #
# Step 4: 保存
# --------------------------------------------------------------------------- #


def test_assign_ids_increments_from_existing(tmp_path):
    """ID 序号应从盘上已有最大序号 +1 递增。"""

    # 预置一个已存在的当天 article
    existing = tmp_path / "20260624_kb-20260624-007_v1.json"
    existing.write_text("{}", encoding="utf-8")

    arts = [
        {"id": "", "title": "A"},
        {"id": "", "title": "B"},
    ]
    out = P.assign_ids(arts, "20260624", tmp_path)
    assert out[0]["id"] == "kb-20260624-008"
    assert out[1]["id"] == "kb-20260624-009"


def test_save_articles_writes_one_file_each(tmp_path):
    """每条 article 应落一个独立文件，剔除内部字段。"""

    arts = [
        {
            "id": "kb-20260624-001",
            "version": 1,
            "parent_id": None,
            "title": "A",
            "source_url": "https://github.com/a",
            "source": "github",
            "collected_at": "2026-06-24T00:00:00+08:00",
            "summary": "x" * 100,
            "tags": ["ai"],
            "category": "tool",
            "relevance_score": 0.8,
            "status": "published",
            "distributed_to": [],
            "created_at": "2026-06-24T00:00:00+08:00",
            "updated_at": "2026-06-24T00:00:00+08:00",
            "raw": {"secret": "should_be_stripped"},
        }
    ]
    count = P.save_articles(arts, tmp_path)
    assert count == 1

    files = list(tmp_path.glob("*.json"))
    assert files[0].name == "20260624_kb-20260624-001_v1.json"

    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert "raw" not in data  # 内部字段被剔除
    assert data["status"] == "published"
    assert data["title"] == "A"


def test_save_raw_writes_batch_files(tmp_path):
    """raw 应按 raw_{batch}_{id}.json 命名落盘。"""

    items = [
        {
            "source": "github",
            "source_url": "https://github.com/a",
            "title": "a",
            "raw_content": "x",
            "collected_at": "2026-06-24T00:00:00+08:00",
            "metadata": {},
        }
    ]
    count = P.save_raw(items, "20260624", tmp_path)
    assert count == 1
    files = list(tmp_path.glob("*.json"))
    assert files[0].name == "raw_20260624_raw-20260624-001.json"


# --------------------------------------------------------------------------- #
# 配置加载
# --------------------------------------------------------------------------- #


def test_load_config_merges_with_defaults(tmp_path):
    """yaml 缺失部分字段时应回退默认值。"""

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "pipeline:\n  github:\n    query: 'custom query'\n", encoding="utf-8"
    )
    cfg = P.load_config(cfg_file)
    assert cfg["github"]["query"] == "custom query"  # 覆盖
    assert cfg["github"]["sort"] == "stars"  # 回退默认
    assert cfg["http_timeout"] == 15  # 回退默认


def test_load_config_falls_back_when_file_missing(tmp_path):
    """config.yaml 不存在时应回退完整默认配置（不抛异常）。"""

    cfg = P.load_config(tmp_path / "nonexistent.yaml")
    assert cfg["github"]["search_url"] == P.DEFAULTS["github"]["search_url"]
    assert cfg["rss"]["feeds"] == P.DEFAULTS["rss"]["feeds"]


# --------------------------------------------------------------------------- #
# CLI 解析
# --------------------------------------------------------------------------- #


def test_parse_sources_accepts_valid():
    assert P._parse_sources("github,rss") == ["github", "rss"]
    assert P._parse_sources("github") == ["github"]


def test_parse_sources_rejects_invalid():
    with pytest.raises(SystemExit):
        P._parse_sources("twitter")
