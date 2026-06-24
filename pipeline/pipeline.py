"""四步知识库自动化流水线。

采集 → 分析 → 整理 → 保存 的线性 pipeline，自包含、不依赖 ``src/``，可独立运行：

    python pipeline/pipeline.py --sources github,rss --limit 20
    python pipeline/pipeline.py --sources github --limit 5 --dry-run
    python pipeline/pipeline.py --verbose

设计要点：
    - 采集层用 ``httpx`` 发 HTTP 请求；RSS 用简易正则解析，不引 feedparser。
    - 分析层调用 ``model_client`` 的 ``chat_with_retry()``（需配置 API Key）。
    - 采集数据存入 ``knowledge/raw/``，最终文章存入 ``knowledge/articles/``。
    - 数据源地址一律从 ``config.yaml`` 的 ``pipeline`` 段读取（红线 #2）。

字段对齐 ``AGENTS.md`` 的知识条目 JSON 格式；``source`` 取值 ``github`` / ``rss``。

AGENTS.md 红线：禁止裸 ``print()``（统一 ``logging``）；禁止无错误处理的外部请求；
禁止删除 ``knowledge/`` 下已有文件（只写新文件，不覆盖）。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
import yaml

# model_client 与本文件同目录。直接 ``python pipeline/pipeline.py`` 运行时，
# 脚本目录在 sys.path[0]，可裸导入 ``model_client``；作为包被导入（如 pytest）
# 时，它以 ``pipeline.model_client`` 加载。``model_client`` 自身在加载时会
# 把两个名字登记为同一个模块对象（见 model_client.py 顶部），因此这里的裸
# 导入在两种运行模式下都指向同一份代码，``LLMError`` 类身份也一致。
_THIS_DIR = Path(__file__).resolve().parent
if "model_client" not in sys.modules and "pipeline.model_client" not in sys.modules:
    if str(_THIS_DIR) not in sys.path:
        sys.path.insert(0, str(_THIS_DIR))

from model_client import LLMError, LLMProvider, chat_with_retry, create_provider  # noqa: E402

# --------------------------------------------------------------------------- #
# 常量与默认配置
# --------------------------------------------------------------------------- #

LOG = logging.getLogger("pipeline")

# 北京时区 UTC+8，AGENTS.md 规定采集时间用 ISO 8601
_TZ_BJ = timezone(timedelta(hours=8))

# 支持的来源
SOURCES: tuple[str, ...] = ("github", "rss")

# 合法 category / status 取值（对齐 AGENTS.md）
CATEGORIES: tuple[str, ...] = ("framework", "model", "tool", "paper")
STATUSES: tuple[str, ...] = ("draft", "review", "published", "archived")

# 审核门阈值（对齐 config.yaml 的 review 段）
MIN_RELEVANCE_SCORE = 0.4
MIN_SUMMARY_CHARS = 50
MAX_SUMMARY_CHARS = 300
MIN_TAGS = 1
MAX_TAGS = 5

# HTTP 重试退避基数（秒）：5s, 10s, 20s
RETRY_BACKOFF_SECONDS = (5, 10, 20)

# 指数退避：第 n 次失败后等待 RETRY_BACKOFF_SECONDS[n] 秒（不足则末值）
# LLM 分析单次最大 token
ANALYZE_MAX_TOKENS = 500

# config.yaml 缺失 pipeline 段时的后备配置
DEFAULTS: dict[str, Any] = {
    "github": {
        "search_url": "https://api.github.com/search/repositories",
        "query": "AI LLM agent",
        "sort": "stars",
    },
    "rss": {
        "feeds": ["https://hnrss.org/frontpage"],
    },
    "http_timeout": 15,
    "http_retries": 3,
    "paths": {
        "raw_dir": "knowledge/raw",
        "articles_dir": "knowledge/articles",
    },
}

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


# --------------------------------------------------------------------------- #
# 配置加载
# --------------------------------------------------------------------------- #


def _project_root() -> Path:
    """定位项目根（含 ``config.yaml`` 的目录），找不到则用当前工作目录。"""

    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if (parent / "config.yaml").exists():
            return parent
    return Path.cwd()


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并 override 到 base（override 优先），返回新 dict。"""

    out = deepcopy(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def load_config(config_path: Optional[Path] = None) -> dict[str, Any]:
    """读取 ``config.yaml`` 的 ``pipeline`` 段并与默认值合并。

    Args:
        config_path: 显式指定 yaml 路径；默认查项目根的 ``config.yaml``。

    Returns:
        合并后的配置 dict（保证含全部必需字段）。
    """

    path = config_path or (_project_root() / "config.yaml")
    raw: dict[str, Any] = {}
    if path.exists():
        try:
            full = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            raw = full.get("pipeline", {}) if isinstance(full, dict) else {}
        except (OSError, yaml.YAMLError) as exc:
            LOG.warning("读取 %s 失败，回退默认配置: %s", path, exc)
    else:
        LOG.warning("未找到 %s，pipeline 段使用内置默认值", path)

    merged = _deep_merge(DEFAULTS, raw)
    LOG.debug("pipeline 配置: %s", merged)
    return merged


def _setup_logging(verbose: bool = False) -> None:
    """配置根 logger，格式对齐 AGENTS.md。verbose 切到 DEBUG。"""

    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format=_LOG_FORMAT, datefmt=_LOG_DATEFMT)


# --------------------------------------------------------------------------- #
# 时间工具
# --------------------------------------------------------------------------- #


def now_iso() -> str:
    """返回当前北京时间 ISO 8601 字符串。"""

    return datetime.now(timezone.utc).astimezone(_TZ_BJ).isoformat(timespec="seconds")


def today_batch_id() -> str:
    """返回今日批次标识 ``YYYYMMDD``（北京时间）。"""

    return datetime.now(timezone.utc).astimezone(_TZ_BJ).strftime("%Y%m%d")


# --------------------------------------------------------------------------- #
# HTTP 带重试
# --------------------------------------------------------------------------- #


def _http_get_with_retry(
    url: str, *, headers: dict[str, str], timeout: float, retries: int
) -> Optional[httpx.Response]:
    """带指数退避的 GET 请求。

    Args:
        url: 请求地址。
        headers: 请求头。
        timeout: 单次超时秒数。
        retries: 含首次在内的总尝试次数。

    Returns:
        成功（2xx）的 Response；全部失败返回 ``None``。
    """

    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            resp = httpx.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt == retries:
                LOG.error(
                    "HTTP 请求失败（重试耗尽 %d/%d）: %s -> %s",
                    attempt,
                    retries,
                    url,
                    exc,
                )
                return None
            wait = RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
            LOG.warning(
                "HTTP 请求失败，%ds 后重试 (%d/%d): %s -> %s",
                wait,
                attempt,
                retries,
                url,
                exc,
            )
            time.sleep(wait)

    LOG.error("HTTP 请求失败（未知原因）: %s -> %s", url, last_exc)
    return None


# --------------------------------------------------------------------------- #
# Step 1: 采集 Collect
# --------------------------------------------------------------------------- #


def collect_github(cfg: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    """从 GitHub Search API 采集 AI 相关仓库。

    Args:
        cfg: ``pipeline.github`` 段 + ``http_timeout`` / ``http_retries``。
        limit: 返回条数上限（即 ``per_page``）。

    Returns:
        原始条目列表，每条含 source/source_url/title/raw_content/metadata/collected_at。
    """

    gh = cfg["github"]
    params = f"?q={gh['query'].replace(' ', '+')}&sort={gh['sort']}&per_page={limit}"
    url = f"{gh['search_url']}{params}"

    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        LOG.warning("未设置 GITHUB_TOKEN，匿名调用受 10/min 速率限制")

    resp = _http_get_with_retry(
        url,
        headers=headers,
        timeout=float(cfg["http_timeout"]),
        retries=int(cfg["http_retries"]),
    )
    if resp is None:
        LOG.error("GitHub 采集失败：重试耗尽，返回空列表")
        return []

    try:
        data = resp.json()
    except ValueError as exc:
        LOG.error("GitHub 响应不是合法 JSON: %s", exc)
        return []

    items = data.get("items", []) if isinstance(data, dict) else []
    collected_at = now_iso()
    out: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        full_name = it.get("full_name") or ""
        html_url = it.get("html_url") or ""
        if not html_url:
            continue
        description = it.get("description") or ""
        raw_content = (
            f"{full_name}\n\n{description}\n\nTopics: "
            f"{', '.join(it.get('topics', []) or [])}"
        )
        out.append(
            {
                "source": "github",
                "source_url": html_url,
                "title": full_name or html_url,
                "raw_content": raw_content,
                "metadata": {
                    "stars": it.get("stargazers_count"),
                    "language": it.get("language"),
                    "description": description,
                    "topics": it.get("topics", []),
                },
                "collected_at": collected_at,
            }
        )

    LOG.info("GitHub 采集完成: %d 条", len(out))
    return out


# RSS item 块简易正则（非贪婪，逐项提取）
_RSS_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.DOTALL)
_RSS_TITLE_RE = re.compile(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", re.DOTALL)
_RSS_LINK_RE = re.compile(r"<link>(.*?)</link>", re.DOTALL)
_RSS_DESC_RE = re.compile(
    r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", re.DOTALL
)
_RSS_DATE_RE = re.compile(r"<pubDate>(.*?)</pubDate>")


def _strip_tags(text: str) -> str:
    """粗略去除 HTML 标签，折叠空白。"""

    clean = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", clean).strip()


def collect_rss(cfg: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    """从 RSS 源采集条目，用简易正则解析。

    Args:
        cfg: ``pipeline.rss`` 段 + ``http_timeout`` / ``http_retries``。
        limit: 每个 feed 返回条数上限。

    Returns:
        原始条目列表。
    """

    feeds = cfg["rss"].get("feeds", [])
    timeout = float(cfg["http_timeout"])
    retries = int(cfg["http_retries"])
    collected_at = now_iso()
    out: list[dict[str, Any]] = []

    for feed_url in feeds:
        resp = _http_get_with_retry(
            feed_url, headers={}, timeout=timeout, retries=retries
        )
        if resp is None:
            LOG.error("RSS 采集失败: %s", feed_url)
            continue

        text = resp.text
        items = _RSS_ITEM_RE.findall(text)
        for raw_item in items[:limit]:
            title_m = _RSS_TITLE_RE.search(raw_item)
            link_m = _RSS_LINK_RE.search(raw_item)
            desc_m = _RSS_DESC_RE.search(raw_item)
            date_m = _RSS_DATE_RE.search(raw_item)

            title = _strip_tags(title_m.group(1)) if title_m else ""
            link = (link_m.group(1).strip() if link_m else "").strip()
            if not link:
                continue
            desc = _strip_tags(desc_m.group(1)) if desc_m else ""
            pub_date = date_m.group(1).strip() if date_m else ""

            out.append(
                {
                    "source": "rss",
                    "source_url": link,
                    "title": title or link,
                    "raw_content": f"{title}\n\n{desc}\n\nPublished: {pub_date}",
                    "metadata": {
                        "description": desc,
                        "pub_date": pub_date,
                        "feed": feed_url,
                    },
                    "collected_at": collected_at,
                }
            )

        LOG.info("RSS feed 采集完成: %s -> %d 条", feed_url, len(items[:limit]))

    LOG.info("RSS 采集完成（全部 feed）: %d 条", len(out))
    return out


def collect(
    sources: list[str], cfg: dict[str, Any], limit: int
) -> list[dict[str, Any]]:
    """按 sources 分发采集。

    Args:
        sources: 来源列表，元素需在 ``SOURCES`` 中。
        cfg: 完整 pipeline 配置。
        limit: 每源条数上限。

    Returns:
        合并后的原始条目列表。
    """

    out: list[dict[str, Any]] = []
    for src in sources:
        if src == "github":
            out.extend(collect_github(cfg, limit))
        elif src == "rss":
            out.extend(collect_rss(cfg, limit))
        else:
            LOG.warning("未知来源 %r，已跳过", src)
    LOG.info("采集完成（全部来源 %s）: 共 %d 条", sources, len(out))
    return out


# --------------------------------------------------------------------------- #
# Step 2: 分析 Analyze
# --------------------------------------------------------------------------- #

_ANALYZE_SYSTEM = (
    "你是 AI 技术领域的资深分析师。请对给定内容输出**严格的 JSON**，"
    "不要包含任何解释文字或 markdown 代码块。"
)
_ANALYZE_PROMPT_TMPL = """请分析以下内容，返回严格 JSON（字段如下）：
- summary: 中文摘要，{min_s}-{max_s} 字
- tags: 1-5 个英文标签（小写、简短）
- category: 取值 framework / model / tool / paper
- relevance_score: 与 AI/LLM/Agent 领域的相关度，0.0-1.0（保留两位小数）

只输出 JSON 对象本身，形如：
{{"summary": "...", "tags": ["..."], "category": "tool", "relevance_score": 0.85}}

内容：
{content}
"""


def _build_analyze_prompt(content: str) -> str:
    """构造分析 prompt，填入审核门长度区间。"""

    return _ANALYZE_PROMPT_TMPL.format(
        min_s=MIN_SUMMARY_CHARS, max_s=MAX_SUMMARY_CHARS, content=content
    )


def _fallback_analysis(raw: dict[str, Any], reason: str) -> dict[str, Any]:
    """LLM 解析失败时的降级默认分析。

    Args:
        raw: 原始条目。
        reason: 降级原因（写入日志）。

    Returns:
        默认分析结果 dict。
    """

    content = raw.get("raw_content", "")
    summary = content[:200].strip() or raw.get("title", "")
    return {
        "summary": summary,
        "tags": [raw.get("source", "unknown")],
        "category": "tool",
        "relevance_score": 0.5,
        "_fallback": True,
        "_fallback_reason": reason,
    }


def _coerce_analysis(data: dict[str, Any]) -> dict[str, Any]:
    """规整 LLM 返回的分析结果，修正越界取值。"""

    summary = str(data.get("summary", "")).strip()
    tags = data.get("tags", [])
    if not isinstance(tags, list):
        tags = [str(tags)]
    tags = [str(t).strip().lower() for t in tags if str(t).strip()]
    tags = tags[:MAX_TAGS] or ["unknown"]

    category = str(data.get("category", "tool")).strip().lower()
    if category not in CATEGORIES:
        category = "tool"

    try:
        score = float(data.get("relevance_score", 0.5))
    except (TypeError, ValueError):
        score = 0.5
    score = max(0.0, min(1.0, score))

    return {
        "summary": summary,
        "tags": tags,
        "category": category,
        "relevance_score": round(score, 2),
    }


def analyze_item(raw: dict[str, Any], provider: LLMProvider) -> dict[str, Any]:
    """调用 LLM 分析单条原始内容，返回 summary/tags/category/relevance_score。

    解析失败时返回降级默认值（带 ``_fallback`` 标记）。

    Args:
        raw: 原始条目。
        provider: LLM provider 实例。

    Returns:
        分析结果 dict。
    """

    content = raw.get("raw_content", "")[:2000]
    messages = [
        {"role": "system", "content": _ANALYZE_SYSTEM},
        {"role": "user", "content": _build_analyze_prompt(content)},
    ]

    try:
        resp = chat_with_retry(provider, messages, max_tokens=ANALYZE_MAX_TOKENS)
    except LLMError as exc:
        LOG.warning("LLM 调用失败，降级默认值: %s -> %s", raw.get("source_url"), exc)
        return _fallback_analysis(raw, f"LLM 调用失败: {exc}")

    text = resp.content.strip()
    # 兼容模型偶尔包 ```json ... ``` 的情况
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    payload = fence.group(1) if fence else text
    # 取第一个 {...} 块，丢弃多余文本
    brace = re.search(r"\{.*\}", payload, re.DOTALL)
    if brace:
        payload = brace.group(0)

    try:
        data = json.loads(payload)
    except ValueError as exc:
        LOG.warning(
            "LLM 返回非合法 JSON，降级默认值: %s -> %s",
            raw.get("source_url"),
            exc,
        )
        return _fallback_analysis(raw, f"JSON 解析失败: {exc}")

    return _coerce_analysis(data)


def analyze(
    raw_items: list[dict[str, Any]], provider: LLMProvider
) -> list[dict[str, Any]]:
    """逐条分析原始内容，合并成 article dict 列表。

    Args:
        raw_items: 原始条目列表。
        provider: LLM provider 实例。

    Returns:
        article dict 列表（尚未补全 id / 时间戳，由整理层完成）。
    """

    out: list[dict[str, Any]] = []
    fallback_count = 0
    for raw in raw_items:
        analysis = analyze_item(raw, provider)
        if analysis.get("_fallback"):
            fallback_count += 1
        article = {
            "title": raw.get("title", ""),
            "source_url": raw.get("source_url", ""),
            "source": raw.get("source", "unknown"),
            "collected_at": raw.get("collected_at", now_iso()),
            "summary": analysis["summary"],
            "tags": analysis["tags"],
            "category": analysis["category"],
            "relevance_score": analysis["relevance_score"],
            "raw": raw,
        }
        out.append(article)

    LOG.info(
        "分析完成: %d 条（其中降级默认 %d 条）", len(out), fallback_count
    )
    return out


# --------------------------------------------------------------------------- #
# Step 3: 整理 Organize
# --------------------------------------------------------------------------- #


def _title_tokens(title: str) -> set[str]:
    """把标题切成小写 token 集合（用于 Jaccard 相似度）。"""

    return {tok for tok in re.split(r"\W+", title.lower()) if len(tok) > 1}


def _jaccard(a: set[str], b: set[str]) -> float:
    """两集合的 Jaccard 相似度。"""

    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def dedup(
    articles: list[dict[str, Any]], existing_urls: Optional[set[str]] = None
) -> list[dict[str, Any]]:
    """去重：精确重复（source_url 相同或已在盘上）丢弃；标题高度相似标 review。

    标题相似度用 Jaccard（token 集合交并比），阈值 0.7——即七成以上词汇重合
    即视为"高度相似"。

    Args:
        articles: 待去重的 article 列表。
        existing_urls: 盘上已有的 source_url 集合（``None`` 表示不查盘）。

    Returns:
        去重后的 article 列表（疑似重复的保留但标记 review）。
    """

    seen_urls: set[str] = set(existing_urls or [])
    kept: list[dict[str, Any]] = []
    dropped = 0
    reviewed = 0

    for art in articles:
        url = art.get("source_url", "")
        if url and url in seen_urls:
            dropped += 1
            LOG.info("精确重复，丢弃: %s", url)
            continue
        seen_urls.add(url)

        # 与已保留的条目比标题相似度
        tok = _title_tokens(art.get("title", ""))
        is_similar = False
        for prev in kept:
            if _jaccard(tok, _title_tokens(prev.get("title", ""))) > 0.7:
                is_similar = True
                break
        if is_similar:
            reviewed += 1
            art["status"] = "review"
            art["review_reason"] = "疑似重复（标题高度相似）"
            LOG.info("疑似重复，标记 review: %s", art.get("title"))

        kept.append(art)

    LOG.info("去重完成: 保留 %d 条（丢弃 %d，疑似重复 %d）", len(kept), dropped, reviewed)
    return kept


def normalize(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """补全 article 缺失字段并规整类型。

    填充 version/parent_id/distributed_to/created_at/updated_at；status 默认 draft。

    Args:
        articles: 待规整的 article 列表。

    Returns:
        规整后的 article 列表（原地修改并返回）。
    """

    ts = now_iso()
    for art in articles:
        art.setdefault("version", 1)
        art.setdefault("parent_id", None)
        art.setdefault("distributed_to", [])
        art.setdefault("created_at", ts)
        art.setdefault("updated_at", ts)
        art.setdefault("status", "draft")
    LOG.info("规整完成: %d 条", len(articles))
    return articles


def validate_and_gate(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """审核门校验：违规降级为 review/archived，通过的标 published。

    规则（对齐 AGENTS.md 审核标准）：
        - relevance_score < 0.4 -> archived
        - summary 长度越界 / tags 数量越界 / category 非法 -> review

    Args:
        articles: 待校验的 article 列表。

    Returns:
        校验后的 article 列表（原地修改并返回）。
    """

    for art in articles:
        reasons: list[str] = []

        summary = art.get("summary", "")
        tags = art.get("tags", [])
        category = art.get("category", "")
        score = float(art.get("relevance_score", 0.0))

        if not (MIN_SUMMARY_CHARS <= len(summary) <= MAX_SUMMARY_CHARS):
            reasons.append(
                f"摘要长度 {len(summary)} 不在 {MIN_SUMMARY_CHARS}-{MAX_SUMMARY_CHARS}"
            )
        if not (MIN_TAGS <= len(tags) <= MAX_TAGS):
            reasons.append(f"标签数量 {len(tags)} 不在 {MIN_TAGS}-{MAX_TAGS}")
        if category not in CATEGORIES:
            reasons.append(f"分类 {category!r} 非法")

        if reasons:
            art["status"] = "review"
            art["review_reason"] = "；".join(reasons)
            continue

        # 通过基础校验后过相关度门
        if score < MIN_RELEVANCE_SCORE:
            art["status"] = "archived"
            art["review_reason"] = (
                f"相关度 {score} 低于阈值 {MIN_RELEVANCE_SCORE}"
            )
        else:
            art["status"] = "published"
            art.pop("review_reason", None)

    published = sum(1 for a in articles if a.get("status") == "published")
    LOG.info(
        "审核完成: published=%d, review=%d, archived=%d",
        published,
        sum(1 for a in articles if a.get("status") == "review"),
        sum(1 for a in articles if a.get("status") == "archived"),
    )
    return articles


def organize(
    articles: list[dict[str, Any]], existing_urls: Optional[set[str]] = None
) -> list[dict[str, Any]]:
    """整理三件套：去重 → 规整 → 校验审核门。"""

    deduped = dedup(articles, existing_urls)
    normalized = normalize(deduped)
    return validate_and_gate(normalized)


# --------------------------------------------------------------------------- #
# Step 4: 保存 Save
# --------------------------------------------------------------------------- #


def _max_article_seq(dir_path: Path, batch_id: str) -> int:
    """扫描 articles 目录，返回当天批次已用的最大序号。

    Args:
        dir_path: articles 目录。
        batch_id: ``YYYYMMDD`` 批次标识。

    Returns:
        最大序号（无则 0）。
    """

    pattern = re.compile(rf"^{batch_id}_kb-\d{{8}}-(\d+)_v\d+\.json$")
    max_seq = 0
    for p in dir_path.glob("*.json"):
        m = pattern.match(p.name)
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    return max_seq


def assign_ids(
    articles: list[dict[str, Any]], batch_id: str, articles_dir: Path
) -> list[dict[str, Any]]:
    """为每条 article 分配唯一 id（``kb-{YYYYMMDD}-{NNN}``），NNN 递增不覆盖。

    Args:
        articles: 待分配的 article 列表。
        batch_id: ``YYYYMMDD`` 批次标识。
        articles_dir: articles 目录（用于扫描已有序号）。

    Returns:
        已赋 id 的 article 列表。
    """

    seq = _max_article_seq(articles_dir, batch_id)
    for art in articles:
        seq += 1
        art["id"] = f"kb-{batch_id}-{seq:03d}"
    LOG.info("ID 分配完成: %d 条（起始序号 %d）", len(articles), seq - len(articles) + 1)
    return articles


def _ensure_dir(path: Path) -> None:
    """确保目录存在。"""

    path.mkdir(parents=True, exist_ok=True)


def save_raw(
    raw_items: list[dict[str, Any]], batch_id: str, raw_dir: Path
) -> int:
    """把原始条目写入 ``knowledge/raw/``，文件名 ``raw_{batch_id}_{NNN}.json``。

    Args:
        raw_items: 原始条目列表。
        batch_id: ``YYYYMMDD`` 批次标识。
        raw_dir: raw 目录。

    Returns:
        成功写入的文件数。
    """

    _ensure_dir(raw_dir)
    count = 0
    for idx, raw in enumerate(raw_items, start=1):
        raw_id = f"raw-{batch_id}-{idx:03d}"
        payload = {**raw, "id": raw_id}
        path = raw_dir / f"raw_{batch_id}_{raw_id}.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        count += 1
        LOG.info("wrote raw %s -> %s", raw_id, path.name)
    LOG.info("raw 落盘完成: %d 个文件", count)
    return count


def save_articles(articles: list[dict[str, Any]], articles_dir: Path) -> int:
    """把 article 写入 ``knowledge/articles/``，一个文件一条。

    落盘前剔除内部字段（``raw`` / ``_fallback*``），并对齐 AGENTS.md 字段表。

    Args:
        articles: 已赋 id 的 article 列表。
        articles_dir: articles 目录。

    Returns:
        成功写入的文件数。
    """

    _ensure_dir(articles_dir)
    count = 0
    for art in articles:
        batch_id = art["id"][3:11]  # kb-YYYYMMDD-NNN -> YYYYMMDD
        date = batch_id
        version = int(art.get("version", 1))
        path = articles_dir / f"{date}_{art['id']}_v{version}.json"

        # 剔除内部字段，仅保留对外字段
        public_keys = (
            "id",
            "version",
            "parent_id",
            "title",
            "source_url",
            "source",
            "collected_at",
            "summary",
            "tags",
            "category",
            "relevance_score",
            "status",
            "review_reason",
            "distributed_to",
            "created_at",
            "updated_at",
        )
        payload = {k: art[k] for k in public_keys if k in art}
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        count += 1
        LOG.info(
            "wrote article %s v%s -> %s", art["id"], version, path.name
        )
    LOG.info("article 落盘完成: %d 个文件", count)
    return count


# --------------------------------------------------------------------------- #
# 已有 source_url 扫描（去重输入）
# --------------------------------------------------------------------------- #


def scan_existing_source_urls(raw_dir: Path) -> set[str]:
    """扫描 raw 目录，返回已采集过的 source_url 集合。"""

    urls: set[str] = set()
    if not raw_dir.exists():
        return urls
    for p in raw_dir.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        url = data.get("source_url")
        if isinstance(url, str):
            urls.add(url)
    return urls


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_sources(raw: str) -> list[str]:
    """解析 --sources 参数，校验取值。"""

    parts = [s.strip().lower() for s in raw.split(",") if s.strip()]
    invalid = [p for p in parts if p not in SOURCES]
    if invalid:
        raise SystemExit(f"不支持的来源: {invalid}，可选: {list(SOURCES)}")
    if not parts:
        raise SystemExit("--sources 不能为空")
    return parts


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器。"""

    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="四步知识库自动化流水线：采集 → 分析 → 整理 → 保存",
    )
    parser.add_argument(
        "--sources",
        default="github,rss",
        help="数据来源，逗号分隔（可选: github, rss），默认 github,rss",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="每个来源采集条数上限，默认 20",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="干跑模式：跑完 collect+analyze+organize 后只打印统计，不落盘",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="输出 DEBUG 级别详细日志",
    )
    return parser


def run(
    sources: list[str], limit: int, dry_run: bool, verbose: bool = False
) -> dict[str, int]:
    """执行四步流水线。

    Args:
        sources: 来源列表。
        limit: 每源条数上限。
        dry_run: 干跑模式（不落盘）。
        verbose: 是否 DEBUG 日志。

    Returns:
        各步统计 dict。
    """

    _setup_logging(verbose)
    cfg = load_config()

    raw_dir = _project_root() / cfg["paths"]["raw_dir"]
    articles_dir = _project_root() / cfg["paths"]["articles_dir"]
    batch_id = today_batch_id()

    LOG.info(
        "=== 流水线启动 === sources=%s limit=%d dry_run=%s batch_id=%s",
        sources,
        limit,
        dry_run,
        batch_id,
    )

    # Step 1: 采集
    raw_items = collect(sources, cfg, limit)
    if not raw_items:
        LOG.warning("采集结果为空，流水线终止")
        return {"collected": 0}

    # Step 2: 分析
    provider = create_provider()
    articles = analyze(raw_items, provider)

    # Step 3: 整理
    existing = scan_existing_source_urls(raw_dir)
    articles = organize(articles, existing_urls=existing)
    assign_ids(articles, batch_id, articles_dir)

    published = sum(1 for a in articles if a.get("status") == "published")
    review = sum(1 for a in articles if a.get("status") == "review")
    archived = sum(1 for a in articles if a.get("status") == "archived")

    if dry_run:
        LOG.info("=== 干跑模式，不落盘 ===")
        LOG.info(
            "汇总: 采集 %d / 分析 %d / published %d / review %d / archived %d",
            len(raw_items),
            len(articles),
            published,
            review,
            archived,
        )
        return {
            "collected": len(raw_items),
            "analyzed": len(articles),
            "published": published,
            "review": review,
            "archived": archived,
            "written": 0,
        }

    # Step 4: 保存
    raw_written = save_raw(raw_items, batch_id, raw_dir)
    art_written = save_articles(articles, articles_dir)

    LOG.info(
        "=== 流水线完成 === 采集 %d / 分析 %d / published %d / review %d / "
        "archived %d / 写入 raw %d / 写入 article %d",
        len(raw_items),
        len(articles),
        published,
        review,
        archived,
        raw_written,
        art_written,
    )
    return {
        "collected": len(raw_items),
        "analyzed": len(articles),
        "published": published,
        "review": review,
        "archived": archived,
        "written": art_written,
    }


def main(argv: Optional[list[str]] = None) -> int:
    """CLI 入口。

    Args:
        argv: 命令行参数（``None`` 读 ``sys.argv``）。

    Returns:
        进程退出码。
    """

    parser = build_parser()
    args = parser.parse_args(argv)
    sources = _parse_sources(args.sources)
    run(sources, limit=args.limit, dry_run=args.dry_run, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    sys.exit(main())
