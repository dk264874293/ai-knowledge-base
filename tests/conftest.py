"""pytest 共享 fixtures。"""

from __future__ import annotations

import copy
from typing import Any

import pytest


@pytest.fixture()
def raw_dict() -> dict[str, Any]:
    """一份合法的 RawItem 字典。"""

    return {
        "id": "raw-20260621-001",
        "source": "hacker_news",
        "source_url": "https://news.ycombinator.com/item?id=1",
        "title": "Sample",
        "raw_content": "原始内容",
        "collected_at": "2026-06-21T00:00:00+08:00",
        "metadata": {"score": 1078, "num_comments": 576, "author": "thm"},
    }


@pytest.fixture()
def article_dict() -> dict[str, Any]:
    """一份合法的 Article 字典（规范结构，无 highlights/score 漂移字段）。"""

    return {
        "id": "kb-20260621-001",
        "version": 1,
        "parent_id": None,
        "title": "Sample",
        "source_url": "https://news.ycombinator.com/item?id=1",
        "source": "hacker_news",
        "collected_at": "2026-06-21T00:00:00+08:00",
        "summary": "这是一段长度合法的中文摘要示例，用于满足 published 门 50-300 字符的下界要求，可以作为测试 fixture 使用。",
        "tags": ["llm", "agent"],
        "category": "framework",
        "relevance_score": 0.85,
        "status": "published",
        "distributed_to": [],
        "created_at": "2026-06-21T10:35:00+08:00",
        "updated_at": "2026-06-21T11:00:00+08:00",
    }


def clone(d: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(d)
