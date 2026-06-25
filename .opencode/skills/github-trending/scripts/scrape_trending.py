#!/usr/bin/env python3
"""GitHub Trending 采集脚本。

走 HTML 解析（不调 GitHub API），抓取 Trending Top 50，
过滤出 AI / LLM / Agent / ML 相关项目，输出 JSON 数组到 stdout。

设计约束（对齐 specs/github-trending-skill.md）：
  - 单次执行 < 10s
  - 失败时输出空数组 []，不抛异常
  - 只 stdout JSON，日志走 stderr
  - 不做去重（由 caller 处理）

Usage:
    python scrape_trending.py [--since daily|weekly|monthly] [--limit 50]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from urllib.error import HTTPError, URLError

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

TRENDING_URL = "https://github.com/trending"
TIMEOUT_SEC = 8
DEFAULT_LIMIT = 50
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

AI_KEYWORDS: set[str] = {
    "ai",
    "artificial-intelligence",
    "llm",
    "large-language-models",
    "large-language-model",
    "agent",
    "agents",
    "ai-agent",
    "ai-agents",
    "autonomous-agents",
    "multi-agent",
    "ml",
    "machine-learning",
    "deep-learning",
    "nlp",
    "natural-language-processing",
    "rag",
    "retrieval-augmented-generation",
    "transformer",
    "transformers",
    "gpt",
    "chatgpt",
    "llama",
    "qwen",
    "deepseek",
    "claude",
    "gemini",
    "embedding",
    "embeddings",
    "fine-tuning",
    "fine-tune",
    "inference",
    "vllm",
    "langchain",
    "langgraph",
    "autogen",
    "crewai",
    "ollama",
    "stable-diffusion",
    "diffusion",
    "diffusion-model",
    "vector-database",
    "vector-search",
    "generative-ai",
    "prompt-engineering",
    "openai",
    "anthropic",
    "huggingface",
    "pytorch",
    "tensorflow",
    "text-to-speech",
    "speech-to-text",
    "computer-vision",
    "reinforcement-learning",
    "neural-network",
    "chatbot",
    "copilot",
}

# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


def fetch_html(url: str) -> str:
    """请求 GitHub Trending 页面 HTML。

    Args:
        url: 完整的 Trending URL。

    Returns:
        HTML 字符串。

    Raises:
        任何网络异常都向上抛，由 ``main()`` 兜底输出空数组。
    """

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
        return resp.read().decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# HTML 解析
# --------------------------------------------------------------------------- #

_ARTICLE_SPLIT_RE = re.compile(r'<article[^>]*class="[^"]*Box-row', re.IGNORECASE)
_REPO_LINK_RE = re.compile(r'<h2[^>]*>[\s\S]*?<a[^>]*href="(/[^"]+)"', re.IGNORECASE)
_STARS_RE = re.compile(r'/stargazers"[^>]*>[\s\S]*?([\d,]+)\s*</a>', re.IGNORECASE)
_TOPIC_RE = re.compile(
    r'class="[^"]*topic-tag[^"]*"[^>]*>\s*([^<]+?)\s*<', re.IGNORECASE
)
_DESC_RE = re.compile(
    r'<p\s+class="col-9[^"]*"[^>]*>\s*([\s\S]*?)</p>', re.IGNORECASE
)
_LANG_RE = re.compile(
    r'itemprop="programmingLanguage"[^>]*>\s*([^<]+)', re.IGNORECASE
)


def _parse_int(text: str) -> int:
    """把 ``"1,234"`` 解析为 ``1234``。"""

    cleaned = re.sub(r"[^\d]", "", text)
    return int(cleaned) if cleaned else 0


def parse_repos(html: str) -> list[dict]:
    """从 Trending HTML 解析仓库列表。

    Args:
        html: GitHub Trending 页面的完整 HTML。

    Returns:
        仓库字典列表，每个含 ``name`` / ``url`` / ``stars`` / ``topics`` /
        ``description`` 五个字段。
    """

    repos: list[dict] = []
    blocks = _ARTICLE_SPLIT_RE.split(html)

    for block in blocks[1:]:
        repo: dict = {
            "name": "",
            "url": "",
            "stars": 0,
            "topics": [],
            "description": "",
        }

        m = _REPO_LINK_RE.search(block)
        if not m:
            continue
        path = m.group(1).split("?")[0].strip("/")
        if path.count("/") != 1:
            continue
        repo["name"] = path
        repo["url"] = f"https://github.com/{path}"

        m = _STARS_RE.search(block)
        if m:
            repo["stars"] = _parse_int(m.group(1))

        topics = _TOPIC_RE.findall(block)
        repo["topics"] = [t.strip().lower() for t in topics if t.strip()]

        m = _DESC_RE.search(block)
        if m:
            repo["description"] = re.sub(r"\s+", " ", m.group(1)).strip()

        repos.append(repo)

    return repos


# --------------------------------------------------------------------------- #
# AI 过滤
# --------------------------------------------------------------------------- #


def is_ai_related(repo: dict) -> bool:
    """判断仓库是否与 AI / LLM / Agent / ML 相关。

    检查 ``topics`` 和 ``description`` + ``name`` 中是否含 AI 关键词。

    Args:
        repo: 解析出的仓库字典。

    Returns:
        ``True`` 表示相关。
    """

    for topic in repo.get("topics", []):
        if topic in AI_KEYWORDS:
            return True

    haystack = " ".join(
        [
            repo.get("name", ""),
            repo.get("description", ""),
        ]
    ).lower()

    for kw in AI_KEYWORDS:
        if kw in haystack:
            return True

    return False


def filter_ai_repos(repos: list[dict]) -> list[dict]:
    """过滤出 AI 相关仓库并按 stars 降序排列。"""

    filtered = [r for r in repos if is_ai_related(r)]
    filtered.sort(key=lambda r: r.get("stars", 0), reverse=True)
    return filtered


# --------------------------------------------------------------------------- #
# 输出校验
# --------------------------------------------------------------------------- #

REQUIRED_FIELDS = ("name", "url", "stars", "topics", "description")


def validate_repo(repo: dict) -> bool:
    """校验单条仓库字段完整性（jsonschema 等效检查）。"""

    return all(
        field in repo
        and (
            field in ("stars",)
            and isinstance(repo[field], int)
            or field == "topics"
            and isinstance(repo[field], list)
            or field in ("name", "url", "description")
            and isinstance(repo[field], str)
        )
        for field in REQUIRED_FIELDS
    )


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def main() -> None:
    """入口：抓取 → 解析 → 过滤 → 输出 JSON 到 stdout。"""

    parser = argparse.ArgumentParser(description="GitHub Trending 采集")
    parser.add_argument(
        "--since",
        choices=["daily", "weekly", "monthly"],
        default="daily",
        help="时间范围（默认 daily）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"最大返回条数（默认 {DEFAULT_LIMIT}）",
    )
    args = parser.parse_args()

    url = f"{TRENDING_URL}?since={args.since}"

    try:
        html = fetch_html(url)
        repos = parse_repos(html)
        ai_repos = filter_ai_repos(repos)[: args.limit]
        output = [r for r in ai_repos if validate_repo(r)]
    except (HTTPError, URLError, OSError, ValueError) as e:
        print(f"[scrape_trending] error: {e}", file=sys.stderr)
        output = []
    except Exception as e:  # noqa: BLE001 — 兜底，不抛异常
        print(f"[scrape_trending] unexpected: {e}", file=sys.stderr)
        output = []

    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
