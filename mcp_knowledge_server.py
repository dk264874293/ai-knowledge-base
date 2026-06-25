"""本地知识库 MCP Server（纯标准库实现）。

通过 JSON-RPC 2.0 over stdio 暴露三个只读工具，让 AI 工具（Claude Desktop、
Cline、OpenCode 等）可以搜索本地 ``knowledge/articles/`` 目录下的结构化知识条目::

    - search_articles(keyword, limit=5)  按关键词搜索标题/摘要/标签
    - get_article(article_id)            按 id 取完整文章
    - knowledge_stats()                  文章总数、来源/分类/标签分布

零第三方依赖，仅用 Python 标准库手写 JSON-RPC 2.0 + MCP 协议。运行::

    python mcp_knowledge_server.py                      # 用默认 knowledge/articles
    python mcp_knowledge_server.py --articles-dir DIR   # 覆盖目录
    KB_ARTICLES_DIR=DIR python mcp_knowledge_server.py  # 同上，用环境变量

客户端配置示例（Claude Desktop ``claude_desktop_config.json`` / Cline）::

    {
      "mcpServers": {
        "kb": {
          "command": "python",
          "args": ["/绝对路径/mcp_knowledge_server.py"]
        }
      }
    }

协议说明：
    - stdout 只承载 JSON-RPC 响应（每行一条 JSON），绝不写日志，以免污染协议；
    - 所有调试/运行日志一律走 stderr —— 这是豁免"禁止裸 print"红线的唯一例外，
      因为 stdout 的写入本质上是协议输出，而非调试信息；
    - 其余遵守 AGENTS.md 红线：不硬编码路径（目录由项目根/环境变量解析）、
      不静默失败（坏数据记 WARNING）、不修改 ``knowledge/`` 下任何文件（只读）。
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

# --------------------------------------------------------------------------- #
# 常量与日志
# --------------------------------------------------------------------------- #

LOG = logging.getLogger("mcp_server")

SERVER_NAME = "kb-knowledge-server"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"

# JSON-RPC 2.0 标准错误码
ERR_PARSE_ERROR = -32700  # JSON 解析失败
ERR_INVALID_REQUEST = -32600  # 不是合法的 JSON-RPC 请求对象
ERR_METHOD_NOT_FOUND = -32601  # 方法不存在或不可用
ERR_INVALID_PARAMS = -32602  # 方法参数非法
ERR_INTERNAL_ERROR = -32603  # 服务端内部错误

# search_articles 的 limit 边界（防御性夹值）
MIN_LIMIT = 1
MAX_LIMIT = 50
DEFAULT_LIMIT = 5
# search 返回的 summary 预览长度上限（字符），避免单条响应过长
SUMMARY_PREVIEW_LEN = 200
# knowledge_stats 热门标签 Top N
TOP_TAGS_N = 10

# 环境变量：覆盖 articles 目录（红线 #2：路径不写死）
ENV_ARTICLES_DIR = "KB_ARTICLES_DIR"


class MCPError(Exception):
    """JSON-RPC 错误，携带标准错误码与可选 data。

    Attributes:
        code: JSON-RPC 错误码（见 ``ERR_*`` 常量）。
        message: 人类可读的错误摘要。
        data: 可选的附加信息（如非法参数名）。
    """

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        """初始化。

        Args:
            code: JSON-RPC 错误码。
            message: 错误摘要。
            data: 可选附加信息。
        """

        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def to_payload(self) -> dict[str, Any]:
        """返回 JSON-RPC ``error`` 字段所需的 dict。"""

        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            payload["data"] = self.data
        return payload


# --------------------------------------------------------------------------- #
# 知识库读取（纯标准库，不依赖 src.storage，避免拉入 pydantic/yaml）
# --------------------------------------------------------------------------- #


def _project_root() -> Path:
    """从本文件向上查找 ``config.yaml`` 定位项目根目录。

    Returns:
        项目根目录；找不到 config.yaml 时回退到本文件所在目录。
    """

    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if (parent / "config.yaml").exists():
            return parent
    return here.parent


def _articles_dir() -> Path:
    """返回 articles 目录。

    优先级：环境变量 ``KB_ARTICLES_DIR`` > ``{project_root}/knowledge/articles``。

    Returns:
        articles 目录 Path。
    """

    override = os.getenv(ENV_ARTICLES_DIR, "").strip()
    if override:
        return Path(override).expanduser()
    return _project_root() / "knowledge" / "articles"


def _load_json(path: Path) -> dict[str, Any]:
    """读取单个 JSON 文件。

    Args:
        path: 文件路径。

    Returns:
        解析后的 dict。

    Raises:
        OSError: 文件读取失败。
        ValueError: JSON 解析失败（``json.JSONDecodeError`` 的父类）。
    """

    return json.loads(path.read_text(encoding="utf-8"))


def _load_articles(directory: Path | None = None) -> list[dict[str, Any]]:
    """读取目录下所有 ``*.json`` 文章。

    单文件解析失败不会拖垮整体：记 WARNING 后跳过（红线 #5：不静默失败，
    但单条坏数据不影响其余条目）。

    Args:
        directory: articles 目录；``None`` 时用 ``_articles_dir()``。

    Returns:
        文章 dict 列表，按文件名排序。
    """

    directory = directory or _articles_dir()
    if not directory.is_dir():
        LOG.warning("articles 目录不存在: %s", directory)
        return []

    out: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = _load_json(path)
        except OSError as exc:
            LOG.warning("读取文章失败，跳过 %s: %s", path.name, exc)
            continue
        except ValueError as exc:
            LOG.warning("文章 JSON 解析失败，跳过 %s: %s", path.name, exc)
            continue
        if not isinstance(data, dict):
            LOG.warning("文章顶层不是对象，跳过 %s", path.name)
            continue
        out.append(data)
    return out


# --------------------------------------------------------------------------- #
# 工具实现
# --------------------------------------------------------------------------- #


def _as_text(value: Any) -> str:
    """把任意值安全转成小写字符串，用于大小写不敏感匹配。"""

    if value is None:
        return ""
    if isinstance(value, str):
        return value.lower()
    return str(value).lower()


def _preview(text: str, limit: int = SUMMARY_PREVIEW_LEN) -> str:
    """截断 summary 预览，超长追加省略号。"""

    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def search_articles(
    keyword: str, limit: int = DEFAULT_LIMIT
) -> list[dict[str, Any]]:
    """按关键词搜索文章（标题 / 摘要 / 标签），返回精简视图。

    匹配为大小写不敏感的子串匹配。空 keyword 时返回 ``relevance_score``
    最高的前 N 条（记 WARNING）。结果按 ``relevance_score`` 降序。

    Args:
        keyword: 搜索关键词。
        limit: 返回上限，会被夹到 ``[MIN_LIMIT, MAX_LIMIT]``。

    Returns:
        精简字段列表，每项含
        ``id / title / source / category / relevance_score / tags / summary``。
    """

    limit = max(MIN_LIMIT, min(MAX_LIMIT, int(limit)))
    articles = _load_articles()

    kw = (keyword or "").strip()
    if not kw:
        LOG.warning("search_articles: keyword 为空，返回得分最高的 %d 条", limit)
        matched = articles
    else:
        kw_lower = kw.lower()
        matched = [
            art
            for art in articles
            if kw_lower in _as_text(art.get("title"))
            or kw_lower in _as_text(art.get("summary"))
            or any(kw_lower in _as_text(tag) for tag in art.get("tags") or [])
        ]

    matched.sort(key=lambda a: float(a.get("relevance_score") or 0.0), reverse=True)
    matched = matched[:limit]

    return [
        {
            "id": art.get("id"),
            "title": art.get("title"),
            "source": art.get("source"),
            "category": art.get("category"),
            "relevance_score": art.get("relevance_score"),
            "tags": art.get("tags") or [],
            "summary": _preview(str(art.get("summary") or "")),
        }
        for art in matched
    ]


def get_article(article_id: str) -> dict[str, Any] | None:
    """按 ``id`` 字段精确匹配，返回完整文章。

    Args:
        article_id: 文章 id，如 ``kb-20260624-001``。

    Returns:
        完整文章 dict；找不到返回 ``None``。
    """

    article_id = (article_id or "").strip()
    if not article_id:
        return None
    for art in _load_articles():
        if art.get("id") == article_id:
            return art
    return None


def knowledge_stats() -> dict[str, Any]:
    """统计知识库概况。

    Returns:
        含 ``total_articles``、按 ``source`` / ``category`` / ``status`` 的
        分布、以及 Top ``TOP_TAGS_N`` 热门标签的 dict。
    """

    articles = _load_articles()
    by_source = Counter(art.get("source") or "unknown" for art in articles)
    by_category = Counter(art.get("category") or "unknown" for art in articles)
    by_status = Counter(art.get("status") or "unknown" for art in articles)

    tag_counter: Counter[str] = Counter()
    for art in articles:
        for tag in art.get("tags") or []:
            if isinstance(tag, str) and tag:
                tag_counter[tag] += 1

    return {
        "total_articles": len(articles),
        "by_source": dict(by_source),
        "by_category": dict(by_category),
        "by_status": dict(by_status),
        "top_tags": tag_counter.most_common(TOP_TAGS_N),
    }


# --------------------------------------------------------------------------- #
# MCP 工具表
# --------------------------------------------------------------------------- #

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_articles",
        "description": (
            "按关键词搜索知识库文章，匹配标题、摘要与标签（大小写不敏感）。"
            "返回相关度最高的若干条精简信息。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "搜索关键词；留空时返回得分最高的条目。",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回上限，默认 5，范围 1-50。",
                    "default": DEFAULT_LIMIT,
                    "minimum": MIN_LIMIT,
                    "maximum": MAX_LIMIT,
                },
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "get_article",
        "description": "按文章 id（如 kb-20260624-001）获取完整内容。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "article_id": {
                    "type": "string",
                    "description": "文章唯一标识，形如 kb-YYYYMMDD-NNN。",
                }
            },
            "required": ["article_id"],
        },
    },
    {
        "name": "knowledge_stats",
        "description": "返回知识库统计信息：文章总数、来源/分类/状态分布、热门标签。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _tool_result(text: str, is_error: bool = False) -> dict[str, Any]:
    """构造 MCP ``tools/call`` 结果。"""

    return {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }


def dispatch_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """分发执行单个 MCP 工具，返回 ``tools/call`` 结果。

    Args:
        name: 工具名。
        arguments: 工具参数。

    Returns:
        MCP 格式的工具结果（``content`` + ``isError``）。

    Raises:
        MCPError: 工具名未知（``ERR_METHOD_NOT_FOUND``）或参数非法
            （``ERR_INVALID_PARAMS``）。
    """

    arguments = arguments or {}

    if name == "search_articles":
        keyword = arguments.get("keyword")
        if not isinstance(keyword, str):
            raise MCPError(ERR_INVALID_PARAMS, "参数 keyword 必须是字符串")
        limit = arguments.get("limit", DEFAULT_LIMIT)
        try:
            limit_int = int(limit)  # noqa: PLW2901 - 允许传入数字字符串
        except (TypeError, ValueError) as exc:
            raise MCPError(
                ERR_INVALID_PARAMS, "参数 limit 必须是整数"
            ) from exc
        result = search_articles(keyword, limit_int)
        return _tool_result(
            json.dumps(result, ensure_ascii=False, indent=2)
            if result
            else "未找到匹配的文章。",
            is_error=False,
        )

    if name == "get_article":
        article_id = arguments.get("article_id")
        if not isinstance(article_id, str):
            raise MCPError(ERR_INVALID_PARAMS, "参数 article_id 必须是字符串")
        article = get_article(article_id)
        if article is None:
            return _tool_result(
                f"找不到 id 为 {article_id} 的文章。", is_error=True
            )
        return _tool_result(
            json.dumps(article, ensure_ascii=False, indent=2), is_error=False
        )

    if name == "knowledge_stats":
        result = knowledge_stats()
        return _tool_result(
            json.dumps(result, ensure_ascii=False, indent=2), is_error=False
        )

    raise MCPError(ERR_METHOD_NOT_FOUND, f"未知工具: {name}")


# --------------------------------------------------------------------------- #
# JSON-RPC 2.0 协议层
# --------------------------------------------------------------------------- #


def _make_response(
    msg_id: Any, result: Any = None, error: dict[str, Any] | None = None
) -> dict[str, Any]:
    """构造 JSON-RPC 2.0 响应对象。"""

    resp: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        resp["error"] = error
    else:
        resp["result"] = result
    return resp


def _is_request(msg: Any) -> bool:
    """判断消息是否为需要回响应的 JSON-RPC request（有 ``method`` 字段）。"""

    return (
        isinstance(msg, dict)
        and msg.get("jsonrpc") == "2.0"
        and isinstance(msg.get("method"), str)
    )


def _is_notification(msg: Any) -> bool:
    """判断消息是否为 notification（有 method 但无 id）。"""

    return (
        isinstance(msg, dict)
        and msg.get("jsonrpc") == "2.0"
        and isinstance(msg.get("method"), str)
        and "id" not in msg
    )


def handle_message(msg: Any) -> dict[str, Any] | None:
    """处理单条已解析的 JSON-RPC 消息。

    Args:
        msg: 已解析为 Python 对象的消息。

    Returns:
        需要回写的响应 dict；notification 或非法消息返回 ``None``（不回包）。
    """

    # notification：有 method、无 id，不回响应
    if _is_notification(msg):
        method = msg.get("method")
        LOG.info("收到 notification: %s（不回包）", method)
        return None

    if not _is_request(msg):
        # 不是合法请求对象，但若携带了 id 仍尝试回 invalid_request 错误
        msg_id = msg.get("id") if isinstance(msg, dict) else None
        if msg_id is None:
            LOG.warning("收到无法识别的消息，且无 id，丢弃")
            return None
        return _make_response(
            msg_id,
            error=MCPError(
                ERR_INVALID_REQUEST, "不是合法的 JSON-RPC 2.0 请求对象"
            ).to_payload(),
        )

    msg_id = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}

    try:
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            if not isinstance(params, dict) or "name" not in params:
                raise MCPError(
                    ERR_INVALID_PARAMS, "tools/call 需要 params.name"
                )
            result = dispatch_tool(params["name"], params.get("arguments") or {})
        else:
            raise MCPError(ERR_METHOD_NOT_FOUND, f"未知方法: {method}")
    except MCPError as exc:
        LOG.warning("处理方法 %s 失败 (code=%d): %s", method, exc.code, exc.message)
        return _make_response(msg_id, error=exc.to_payload())
    except Exception as exc:  # noqa: BLE001 - 兜底，确保协议不崩
        LOG.exception("处理方法 %s 时发生未预期错误", method)
        return _make_response(
            msg_id,
            error=MCPError(
                ERR_INTERNAL_ERROR, f"内部错误: {exc}"
            ).to_payload(),
        )

    LOG.info("方法 %s 处理成功", method)
    return _make_response(msg_id, result=result)


def serve(in_stream: Iterable[str], out_stream: Any) -> None:
    """主循环：逐行读取请求、回写响应。

    Args:
        in_stream: 按行产出字符串的可迭代对象（通常是 ``sys.stdin``）。
        out_stream: 写出响应的类文件对象（通常是 ``sys.stdout``）。
    """

    LOG.info("MCP server 启动，articles 目录: %s", _articles_dir())
    for line in in_stream:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError as exc:
            # 整行 JSON 解析失败：无法获知 id，只能回 parse error（id=null）
            LOG.warning("JSON 解析失败: %s", exc)
            resp = _make_response(
                None,
                error=MCPError(
                    ERR_PARSE_ERROR, f"JSON 解析失败: {exc}"
                ).to_payload(),
            )
            _write_response(out_stream, resp)
            continue

        resp = handle_message(msg)
        if resp is not None:
            _write_response(out_stream, resp)


def _write_response(out_stream: Any, resp: dict[str, Any]) -> None:
    """把响应以单行 JSON 写到输出流并 flush。"""

    out_stream.write(json.dumps(resp, ensure_ascii=False) + "\n")
    out_stream.flush()


# --------------------------------------------------------------------------- #
# 自测入口
# --------------------------------------------------------------------------- #

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""

    parser = argparse.ArgumentParser(
        prog="mcp_knowledge_server.py",
        description="本地知识库 MCP Server（JSON-RPC 2.0 over stdio，纯标准库）",
    )
    parser.add_argument(
        "--articles-dir",
        default=None,
        help=(
            "articles 目录路径（默认 {project_root}/knowledge/articles，"
            "也可用环境变量 KB_ARTICLES_DIR 覆盖）"
        ),
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "INFO"),
        help="日志级别（默认 INFO，与 config.yaml 的 log_level 对齐）",
    )
    return parser


def _main(argv: list[str] | None = None) -> int:
    """命令行入口：解析参数、配置日志、启动服务循环。

    Args:
        argv: 命令行参数；``None`` 时取 ``sys.argv[1:]``。

    Returns:
        进程退出码：0 正常结束。
    """

    args = _build_parser().parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format=_LOG_FORMAT,
        datefmt=_LOG_DATEFMT,
        stream=sys.stderr,  # 关键：日志走 stderr，stdout 只留给 JSON-RPC 响应
    )

    if args.articles_dir:
        os.environ[ENV_ARTICLES_DIR] = str(Path(args.articles_dir).expanduser())

    try:
        serve(sys.stdin, sys.stdout)
    except KeyboardInterrupt:
        LOG.info("收到中断信号，退出")
        return 0
    except Exception:  # noqa: BLE001
        LOG.exception("server 异常退出")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
