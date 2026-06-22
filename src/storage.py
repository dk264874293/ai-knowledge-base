"""knowledge/raw 与 knowledge/articles 的落盘与读取。

落盘文件名严格遵守 schemas 契约：

- raw:     ``raw_{date}_{id}.json``
- article: ``{date}_{id}_v{version}.json``

幂等键：``batch_id`` + ``source_url``（见 #00 ADR / #05）。本模块提供
``existing_source_urls`` 供 collector 做本地去重，``load_raw_batch`` /
``load_article_batch`` 供后续节点与 ``kb status`` 复用。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from src import schemas
from src.utils.logging import get_logger

log = get_logger(__name__)


def _project_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "config.yaml").exists():
            return parent
    return Path.cwd()


def knowledge_dir(root: Path | str | None = None) -> Path:
    """返回 ``knowledge`` 根目录（不存在则创建）。"""

    base = Path(root) if root else _project_root()
    kd = base / "knowledge"
    kd.mkdir(parents=True, exist_ok=True)
    return kd


def raw_dir(root: Path | str | None = None) -> Path:
    d = knowledge_dir(root) / "raw"
    d.mkdir(parents=True, exist_ok=True)
    return d


def articles_dir(root: Path | str | None = None) -> Path:
    d = knowledge_dir(root) / "articles"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------- #
# 落盘
# --------------------------------------------------------------------------- #


def write_raw(item: dict[str, Any], root: Path | str | None = None) -> Path:
    """将单条 raw 写入 ``knowledge/raw/``。

    Args:
        item: 合法的 RawItem dict（写前用 schemas.RawItem 校验）。

    Returns:
        写入的文件路径。
    """

    validated = schemas.RawItem.model_validate(item)
    date = validated.collected_at[:10].replace("-", "")
    path = raw_dir(root) / schemas.raw_filename(date, validated.id)
    path.write_text(
        json.dumps(validated.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("wrote raw %s -> %s", validated.id, path.name)
    return path


def write_article(article: dict[str, Any], root: Path | str | None = None) -> Path:
    """将单条 article 写入 ``knowledge/articles/``（新建版本，不覆盖）。"""

    validated = schemas.Article.model_validate(article)
    date = validated.collected_at[:10].replace("-", "")
    path = articles_dir(root) / schemas.article_filename(
        date, validated.id, validated.version
    )
    path.write_text(
        json.dumps(validated.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("wrote article %s v%s -> %s", validated.id, validated.version, path.name)
    return path


# --------------------------------------------------------------------------- #
# 读取 / 扫描
# --------------------------------------------------------------------------- #


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_raw_files(root: Path | str | None = None) -> Iterable[Path]:
    for p in sorted(raw_dir(root).glob("*.json")):
        yield p


def iter_article_files(root: Path | str | None = None) -> Iterable[Path]:
    for p in sorted(articles_dir(root).glob("*.json")):
        yield p


def existing_source_urls(root: Path | str | None = None) -> set[str]:
    """扫描 raw 目录，返回已采集过的 source_url 集合（去重键）。"""

    urls: set[str] = set()
    for p in iter_raw_files(root):
        try:
            data = _load_json(p)
        except (OSError, ValueError):
            continue
        url = data.get("source_url")
        if isinstance(url, str):
            urls.add(url)
    return urls


def load_raw_batch(batch_id: str, root: Path | str | None = None) -> list[dict[str, Any]]:
    """读取某批次（YYYYMMDD）所有 raw。

    匹配规则：文件名以 ``raw_{batch_id}_`` 开头。
    """

    prefix = f"raw_{batch_id}_"
    out: list[dict[str, Any]] = []
    for p in iter_raw_files(root):
        if p.name.startswith(prefix):
            try:
                out.append(_load_json(p))
            except (OSError, ValueError) as e:
                log.warning("skip corrupt raw %s: %s", p.name, e)
    return out


def load_article_batch(
    batch_id: str, root: Path | str | None = None
) -> list[dict[str, Any]]:
    """读取某批次（YYYYMMDD）所有 article。

    匹配规则：文件名以 ``{batch_id}_`` 开头。
    """

    prefix = f"{batch_id}_"
    out: list[dict[str, Any]] = []
    for p in iter_article_files(root):
        if p.name.startswith(prefix):
            try:
                out.append(_load_json(p))
            except (OSError, ValueError) as e:
                log.warning("skip corrupt article %s: %s", p.name, e)
    return out
