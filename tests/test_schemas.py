"""数据契约（src/schemas）单元测试。"""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from src import schemas
from tests.conftest import clone


# ----------------------------- RawItem ----------------------------- #

def test_raw_item_parses_valid(raw_dict):
    item = schemas.RawItem.model_validate(raw_dict)
    assert item.id == "raw-20260621-001"
    assert item.metadata.score == 1078


def test_raw_item_rejects_unknown_field(raw_dict):
    data = clone(raw_dict)
    data["extra_junk"] = "x"
    with pytest.raises(ValidationError):
        schemas.RawItem.model_validate(data)


def test_raw_item_rejects_bad_id(raw_dict):
    data = clone(raw_dict)
    data["id"] = "20260621-001"
    with pytest.raises(ValidationError):
        schemas.RawItem.model_validate(data)


def test_raw_metadata_allows_source_specific_fields(raw_dict):
    data = clone(raw_dict)
    data["metadata"] = {"stars_today": 120, "language": "Python",
                        "description": "d"}
    item = schemas.RawItem.model_validate(data)
    assert item.metadata.stars_today == 120


# ----------------------------- Article ----------------------------- #

def test_article_parses_valid(article_dict):
    art = schemas.Article.model_validate(article_dict)
    assert art.relevance_score == 0.85
    assert art.status is schemas.Status.published


def test_article_rejects_legacy_highlights(article_dict):
    """历史漂移：highlights 不在规范内，契约应拒绝。"""

    data = clone(article_dict)
    data["highlights"] = ["a", "b"]
    with pytest.raises(ValidationError) as exc:
        schemas.Article.model_validate(data)
    assert any("highlights" in str(e) for e in exc.value.errors())


def test_article_rejects_legacy_score_field(article_dict):
    """历史漂移：score（1-10 整数）不应存在，relevance_score 才是规范。"""

    data = clone(article_dict)
    data["score"] = 8
    with pytest.raises(ValidationError):
        schemas.Article.model_validate(data)


@pytest.mark.parametrize("bad", [-0.01, 1.01, 2.0])
def test_article_relevance_score_range(article_dict, bad):
    data = clone(article_dict)
    data["relevance_score"] = bad
    with pytest.raises(ValidationError):
        schemas.Article.model_validate(data)


def test_article_rejects_bad_category(article_dict):
    data = clone(article_dict)
    data["category"] = "newsletter"
    with pytest.raises(ValidationError):
        schemas.Article.model_validate(data)


def test_article_review_reason_optional(article_dict):
    data = clone(article_dict)
    data["status"] = "archived"
    data["review_reason"] = "相关度低于阈值(0.4)"
    art = schemas.Article.model_validate(data)
    assert art.review_reason is not None


# ----------------------------- 文件名契约 -------------------------- #

def test_raw_filename_pattern():
    name = schemas.raw_filename("20260621", "raw-20260621-001")
    assert schemas.RAW_FILENAME_RE.match(name)
    assert name == "raw_20260621_raw-20260621-001.json"


def test_article_filename_pattern():
    name = schemas.article_filename("20260621", "kb-20260621-001", 1)
    assert schemas.ARTICLE_FILENAME_RE.match(name)
    assert name == "20260621_kb-20260621-001_v1.json"


def test_filename_regex_rejects_drift_names():
    assert not schemas.RAW_FILENAME_RE.match("github-trending-2026-06-21.json")
    assert not schemas.ARTICLE_FILENAME_RE.match(
        "20260621_hacker_news_analysis.json"
    )


# ----------------------------- 导出 / 解析 ------------------------- #

def test_json_schemas_exports_three():
    out = schemas.json_schemas()
    assert set(out) == {"RawItem", "Article", "ErrorEntry"}
    for schema_dict in out.values():
        assert schema_dict["type"] == "object"


def test_parse_raw_items_requires_list():
    with pytest.raises(ValueError):
        schemas.parse_raw_items({"not": "a list"})


def test_parse_articles_requires_list():
    with pytest.raises(ValueError):
        schemas.parse_articles("nope")
