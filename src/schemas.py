"""数据契约（Pydantic 模型）。

对应 ``specs/schemas/*.json`` 的可执行契约，字段命名 / 取值 / 文件名规则一律
以 AGENTS.md 为准。本模块是 collector → analyzer → organizer 三节点共享的
数据形状，所有落盘 JSON 必须能被这里的模型解析通过。

对外暴露：

- ``RawItem`` / ``Article`` / ``ErrorEntry`` —— Pydantic 模型
- ``Status`` / ``Category`` / ``Source`` —— 枚举
- ``raw_filename`` / ``article_filename`` —— 文件名构造
- ``RAW_FILENAME_RE`` / ``ARTICLE_FILENAME_RE`` —— 文件名校验正则
- ``json_schemas`` / ``parse_raw_items`` / ``parse_articles`` —— 导出与解析
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# --------------------------------------------------------------------------- #
# 枚举
# --------------------------------------------------------------------------- #


class Source(str, Enum):
    """采集来源。"""

    github_trending = "github_trending"
    hacker_news = "hacker_news"


class Category(str, Enum):
    """条目分类。"""

    framework = "framework"
    model = "model"
    tool = "tool"
    paper = "paper"


class Status(str, Enum):
    """条目状态机。"""

    draft = "draft"
    review = "review"
    published = "published"
    archived = "archived"


class Channel(str, Enum):
    """分发渠道。"""

    telegram = "telegram"
    feishu = "feishu"


# --------------------------------------------------------------------------- #
# RawItem
# --------------------------------------------------------------------------- #


class RawMetadata(BaseModel):
    """来源特有的扩展字段。

    不同 ``source`` 携带不同字段（GitHub: stars_today/language/description；
    HN: score/num_comments/author），全部可选，由 collector 按来源填充。
    """

    model_config = ConfigDict(extra="allow")

    # GitHub Trending
    stars_today: int | None = Field(default=None, ge=0)
    language: str | None = None
    description: str | None = None
    # Hacker News
    score: int | None = Field(default=None, ge=0)
    num_comments: int | None = Field(default=None, ge=0)
    author: str | None = None


class RawItem(BaseModel):
    """采集层单条原始记录（对应 ``knowledge/raw/raw_{date}_{id}.json``）。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^raw-\d{8}-\d+$")
    source: Source
    source_url: str = Field(pattern=r"^https?://\S+$")
    title: str = Field(min_length=1)
    raw_content: str
    collected_at: str
    metadata: RawMetadata

    @field_validator("source", mode="before")
    @classmethod
    def _coerce_source(cls, v: Any) -> Any:
        return v


# --------------------------------------------------------------------------- #
# Article
# --------------------------------------------------------------------------- #


class Article(BaseModel):
    """分析层单条结构化条目（对应 ``knowledge/articles/{date}_{id}_v{version}.json``）。

    严格遵守 AGENTS.md 字段表，历史漂移字段（``highlights`` / ``score``）一律拒绝。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^kb-\d{8}-\d+$")
    version: int = Field(default=1, ge=1)
    parent_id: str | None = None
    title: str = Field(min_length=1)
    source_url: str = Field(pattern=r"^https?://\S+$")
    source: Source
    collected_at: str
    summary: str = Field(min_length=1)
    tags: list[str] = Field(min_length=1, max_length=5)
    category: Category
    relevance_score: float = Field(ge=0, le=1)
    status: Status = Status.draft
    review_reason: str | None = None
    distributed_to: list[Channel] = Field(default_factory=list)
    created_at: str
    updated_at: str

    @model_validator(mode="after")
    def _published_gate(self) -> "Article":
        """#04 审核门：published 状态必须四条全过。

        - relevance_score >= 0.4
        - 摘要 50-300 字符

        tags(1-5) 与 category(enum) 已由字段约束强制；此处补 published 的两道门。
        """

        if self.status is Status.published:
            if self.relevance_score < 0.4:
                raise ValueError(
                    "published 条目要求 relevance_score >= 0.4"
                )
            if not (50 <= len(self.summary) <= 300):
                raise ValueError(
                    "published 条目摘要长度需在 50-300 字符之间"
                )
        return self


# --------------------------------------------------------------------------- #
# ErrorEntry（State.errors 单项结构）
# --------------------------------------------------------------------------- #


class ErrorEntry(BaseModel):
    """pipeline 各环节异常记录，等待人工介入。"""

    model_config = ConfigDict(extra="forbid")

    stage: str
    source: Source | None = None
    source_url: str | None = None
    reason: str


# --------------------------------------------------------------------------- #
# 文件名契约
# --------------------------------------------------------------------------- #

RAW_FILENAME_RE = re.compile(r"^raw_\d{8}_raw-\d{8}-\d+\.json$")
ARTICLE_FILENAME_RE = re.compile(r"^\d{8}_kb-\d{8}-\d+_v\d+\.json$")


def raw_filename(date: str, raw_id: str) -> str:
    """构造 raw 文件名：``raw_{date}_{id}.json``。"""

    return f"raw_{date}_{raw_id}.json"


def article_filename(date: str, article_id: str, version: int) -> str:
    """构造 article 文件名：``{date}_{id}_v{version}.json``。"""

    return f"{date}_{article_id}_v{version}.json"


# --------------------------------------------------------------------------- #
# 导出 / 解析
# --------------------------------------------------------------------------- #


def json_schemas() -> dict[str, dict[str, Any]]:
    """导出三个模型的 JSON Schema（对齐 ``specs/schemas/*.json``）。"""

    return {
        "RawItem": RawItem.model_json_schema(),
        "Article": Article.model_json_schema(),
        "ErrorEntry": ErrorEntry.model_json_schema(),
    }


def parse_raw_items(data: Any) -> list[RawItem]:
    """解析 raw 列表；非列表抛 ``ValueError``。"""

    if not isinstance(data, list):
        raise ValueError("raw 数据必须是列表")
    return [RawItem.model_validate(item) for item in data]


def parse_articles(data: Any) -> list[Article]:
    """解析 article 列表；非列表抛 ``ValueError``。"""

    if not isinstance(data, list):
        raise ValueError("article 数据必须是列表")
    return [Article.model_validate(item) for item in data]
