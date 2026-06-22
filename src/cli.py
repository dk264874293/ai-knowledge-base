"""kb CLI 入口（typer）。

注册为 ``kb`` 命令（见 pyproject.toml ``[project.scripts]``）。

命令对齐 AGENTS.md → CLI 命令 + specs/issues README 的命名裁决：

- ``kb collect``   采集（--source github_trending|hacker_news，省略=全量）
- ``kb analyze``   分析未处理的 raw
- ``kb digest``    整理成当日 MD 日报（#04）
- ``kb distribute`` 渠道推送（#08，本阶段 stub）
- ``kb status``    查看批次状态（#06）
"""

from __future__ import annotations

import typer

from src.config import get_settings
from src.utils.logging import get_logger, setup_logging

app = typer.Typer(help="AI 知识库助手：采集 → 分析 → 整理 pipeline", no_args_is_help=True)
log = get_logger("cli")


def _bootstrap() -> None:
    """从配置读取日志级别并初始化 logging。"""

    try:
        level = get_settings().log_level
    except Exception:  # noqa: BLE001 — 配置缺失时退回默认，CLI 仍可用
        level = "INFO"
    setup_logging(level, force=True)


@app.command()
def collect(
    source: str = typer.Option(
        None, "--source", "-s", help="github_trending | hacker_news；省略=全量"
    ),
) -> None:
    """采集（#01 stub 阶段跑通整条 pipeline 并落盘 raw）。"""

    _bootstrap()
    from src.pipeline import run_pipeline

    log.info("kb collect: source=%s", source or "all")
    result = run_pipeline()
    typer.echo(
        f"collected={len(result.get('raw_items', []))} "
        f"articles={len(result.get('articles', []))} "
        f"errors={len(result.get('errors', []))}"
    )


@app.command()
def analyze() -> None:
    """分析（读取已落盘 raw，产出 articles）。"""

    _bootstrap()
    from src import storage
    from src.nodes import analyzer_node
    from src.state import new_state

    state = new_state()
    state["raw_items"] = storage.load_raw_batch(state["batch_id"])
    log.info("kb analyze: loaded %d raw items", len(state["raw_items"]))
    result = analyzer_node(state)
    typer.echo(
        f"articles={len(result.get('articles', []))} "
        f"errors={len(result.get('errors', []))}"
    )


@app.command()
def digest() -> None:
    """整理当日条目为 MD 日报。"""

    _bootstrap()
    from src import storage
    from src.nodes import organizer_node
    from src.state import new_state

    state = new_state()
    state["articles"] = storage.load_article_batch(state["batch_id"])
    log.info("kb digest: loaded %d articles", len(state["articles"]))
    result = organizer_node(state)
    dist = result.get("distributed", [])
    typer.echo(f"distributed={len(dist)}")
    if dist:
        typer.echo(dist[0].get("digest", ""))


@app.command()
def distribute(
    channel: str = typer.Option(
        None, "--channel", "-c", help="telegram | feishu；省略=全渠道"
    ),
) -> None:
    """渠道分发（#08，本阶段 stub，仅提示）。"""

    _bootstrap()
    log.info("kb distribute: channel=%s (stub, deferred to #08)", channel or "all")
    typer.echo("distribute: not implemented yet (see #08)")


@app.command()
def status(
    date: str = typer.Option(None, "--date", "-d", help="YYYYMMDD；省略=今日"),
) -> None:
    """查看批次进度（#06）。"""

    _bootstrap()
    from src import storage
    from src.state import today_batch_id

    batch_id = date or today_batch_id()
    raw = storage.load_raw_batch(batch_id)
    articles = storage.load_article_batch(batch_id)
    errors = [a for a in articles if False]  # placeholder；errors 暂从 state 读

    typer.echo(f"批次 {batch_id}")
    typer.echo(f"  raw:      {len(raw)}")
    typer.echo(f"  articles: {len(articles)}")
    typer.echo(f"  errors:   {len(errors)}")
    if not raw and not articles:
        typer.echo("  (无数据)")


if __name__ == "__main__":
    app()
