"""运行时配置加载。

- 静态结构（频率/上限/模型名/max_tokens）来自 ``config.yaml``
- 敏感 Key（API Key / Token / Webhook URL）来自 ``.env``，不出现在 YAML 或代码中

对外暴露 ``load_settings()`` 返回强类型 ``Settings``。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# --------------------------------------------------------------------------- #
# .env 敏感配置（单独一层，便于测试注入 / mock）
# --------------------------------------------------------------------------- #


class Secrets(BaseSettings):
    """从 .env 读取的敏感配置。测试中可构造空实例。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    dashscope_api_key: str = ""
    bigmodel_api_key: str = ""
    deepseek_api_key: str = ""
    telegram_bot_token: str = ""
    feishu_webhook_url: str = ""


# --------------------------------------------------------------------------- #
# config.yaml 结构模型
# --------------------------------------------------------------------------- #


class SourceConfig(BaseModel):
    frequency: str
    limit: int = Field(ge=1)


class CollectorConfig(BaseModel):
    github_trending: SourceConfig
    hacker_news: SourceConfig


class AnalyzerTaskConfig(BaseModel):
    model: str
    max_tokens: int = Field(gt=0)


class AnalyzerConfig(BaseModel):
    summary: AnalyzerTaskConfig
    tags: AnalyzerTaskConfig
    scoring: AnalyzerTaskConfig


class ChannelToggle(BaseModel):
    enabled: bool = True


class DistributorConfig(BaseModel):
    telegram: ChannelToggle = Field(default_factory=ChannelToggle)
    feishu: ChannelToggle = Field(default_factory=ChannelToggle)
    schedule: str = "0 23 * * *"


class ReviewConfig(BaseModel):
    min_relevance_score: float = 0.4
    min_summary_chars: int = 50
    max_summary_chars: int = 300
    min_tags: int = 1
    max_tags: int = 5


class Settings(BaseModel):
    """全部运行时配置的聚合根。"""

    collector: CollectorConfig
    analyzer: AnalyzerConfig
    distributor: DistributorConfig = Field(default_factory=DistributorConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    log_level: str = "INFO"
    secrets: Secrets = Field(default_factory=Secrets)


# --------------------------------------------------------------------------- #
# 加载入口
# --------------------------------------------------------------------------- #


def _project_root() -> Path:
    """定位项目根（含 ``config.yaml`` 的目录）。"""

    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "config.yaml").exists():
            return parent
    return Path.cwd()


def load_settings(config_path: Path | str | None = None) -> Settings:
    """加载 ``config.yaml`` + ``.env``，返回强类型配置。

    Args:
        config_path: 显式指定 yaml 路径；默认在项目根查找 ``config.yaml``。

    Returns:
        Settings: 聚合后的配置对象。
    """

    path = Path(config_path) if config_path else _project_root() / "config.yaml"
    raw: dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    return Settings.model_validate(raw)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程级缓存的配置单例。"""

    return load_settings()
