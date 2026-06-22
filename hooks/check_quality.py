#!/usr/bin/env python3
"""知识条目 5 维度质量评分脚本。

对 knowledge/articles 下的 JSON 条目做加权质量评分（满分 100），输出可视化
进度条、各维度得分与 A/B/C 等级，并以退出码反映是否存在 C 级条目。

评分维度（加权总分 100）:
    - 摘要质量    25 分：长度分档 + 技术关键词奖励
    - 技术深度    25 分：score 字段（1-10）映射到 0-25
    - 格式规范    20 分：id/title/source_url/status/时间戳 各 4 分
    - 标签精度    15 分：1-3 个合法标签最佳，命中标准库有奖励
    - 空洞词检测  15 分：不含中英空洞词得满分，命中按次扣分

等级标准: A >= 80, B >= 60, C < 60。

用法:
    python hooks/check_quality.py <json_file> [json_file2 ...]
    python hooks/check_quality.py "knowledge/articles/*.json"

退出码:
    0 - 不存在 C 级条目
    1 - 存在 C 级条目（或发生错误）
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

SUMMARY_FULL_LEN = 50
SUMMARY_BASE_LEN = 20
SCORE_MIN = 1
SCORE_MAX = 10

# 格式规范的必填字段（时间戳单独成项）
FORMAT_FIELDS: tuple[str, ...] = ("id", "title", "source_url", "status")
TIME_FIELDS: tuple[str, ...] = ("collected_at", "created_at", "updated_at")

# 摘要奖励用的技术关键词（中英文混合，英文一律小写匹配）
TECH_KEYWORDS: tuple[str, ...] = (
    "大模型", "大语言模型", "智能体", "微调", "推理", "训练", "向量", "嵌入",
    "检索增强", "知识库", "工作流", "编排", "多模态", "幻觉", "提示词",
    "上下文", "神经网络", "深度学习", "强化学习", "量化", "蒸馏", "函数调用",
    "记忆", "反思", "规划", "agent", "llm", "gpt", "rag", "transformer",
    "embedding", "fine-tuning", "fine-tune", "inference", "mcp", "copilot",
    "tts", "asr", "prompt", "chatbot", "open-source", "tokenizer",
)

# 标准标签库（小写存储，匹配时统一小写）
STANDARD_TAGS: frozenset[str] = frozenset(
    {
        "agent", "llm", "rag", "mcp", "workflow", "framework", "tool",
        "model", "paper", "platform", "open-source", "ai", "ml", "gpt",
        "claude", "claude-code", "embedding", "fine-tuning", "inference",
        "prompt", "tts", "asr", "vision", "multimodal", "transformer",
        "chatbot", "copilot", "voice-clone", "ai-audio", "ai-tools",
        "skills", "training", "quantization", "distillation", "tokenizer",
        "evaluation", "benchmark", "dataset", "server", "api",
    }
)

# 空洞词黑名单
BUZZWORDS_ZH: tuple[str, ...] = (
    "赋能", "抓手", "闭环", "打通", "全链路", "底层逻辑", "颗粒度",
    "对齐", "拉通", "沉淀", "强大的", "革命性的",
)
BUZZWORDS_EN: tuple[str, ...] = (
    "groundbreaking", "revolutionary", "game-changing", "game changing",
    "cutting-edge", "cutting edge", "state-of-the-art", "state of the art",
    "next-generation", "next generation", "world-class", "industry-leading",
    "disruptive", "seamless", "best-in-class",
)


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #


@dataclass
class DimensionScore:
    """单个评分维度的结果。

    Attributes:
        name: 维度名称。
        score: 实际得分。
        max_score: 该维度满分。
        detail: 得分说明（供展示）。
    """

    name: str
    score: float
    max_score: float
    detail: str

    @property
    def percentage(self) -> float:
        """该维度得分占比（0-100）。"""
        return self.score / self.max_score * 100 if self.max_score else 0.0


@dataclass
class QualityReport:
    """单文件的质量评分报告。

    Attributes:
        file_path: 被评分的文件路径。
        dimensions: 各维度得分列表。
        error: 加载失败时的错误信息，成功时为 None。
    """

    file_path: Path
    dimensions: list[DimensionScore] = field(default_factory=list)
    error: str | None = None

    @property
    def total_score(self) -> float:
        """加权总分（各维度得分之和）。"""
        return sum(d.score for d in self.dimensions)

    @property
    def max_score(self) -> float:
        """满分（各维度满分之和）。"""
        return sum(d.max_score for d in self.dimensions)

    @property
    def grade(self) -> str:
        """质量等级：A(>=80) / B(>=60) / C(<60)。"""
        ratio = self.total_score / self.max_score * 100 if self.max_score else 0.0
        if ratio >= 80:
            return "A"
        if ratio >= 60:
            return "B"
        return "C"

    @property
    def is_c_grade(self) -> bool:
        """是否为 C 级（含加载失败的情况）。"""
        return self.error is not None or self.grade == "C"


# --------------------------------------------------------------------------- #
# 维度评分函数
# --------------------------------------------------------------------------- #


def _score_summary(data: dict) -> DimensionScore:
    """摘要质量评分（满分 25）。

    长度分档为基础分，命中技术关键词按个奖励，总分不超过 25。
    """
    summary = data.get("summary", "")
    if not isinstance(summary, str) or not summary:
        return DimensionScore("摘要质量", 0.0, 25, "summary 缺失或为空")

    length = len(summary)
    if length >= SUMMARY_FULL_LEN:
        base = 20.0
        tier = f">= {SUMMARY_FULL_LEN} 字"
    elif length >= SUMMARY_BASE_LEN:
        base = 12.0
        tier = f">= {SUMMARY_BASE_LEN} 字"
    else:
        base = 4.0
        tier = f"< {SUMMARY_BASE_LEN} 字"

    lower = summary.lower()
    matched = [kw for kw in TECH_KEYWORDS if kw in lower]
    bonus = min(len(matched), 5)  # 每个关键词 +1，最多 +5
    score = min(base + bonus, 25.0)
    detail = f"长度 {length} 字（{tier}），命中 {len(matched)} 个技术关键词"
    return DimensionScore("摘要质量", score, 25, detail)


def _score_depth(data: dict) -> DimensionScore:
    """技术深度评分（满分 25）。

    基于 score 字段（1-10）线性映射到 0-25，缺失记 0 分。
    """
    value = data.get("score")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return DimensionScore("技术深度", 0.0, 25, "未提供 score 字段，计 0 分")

    clamped = max(SCORE_MIN, min(SCORE_MAX, value))
    score = round(clamped / SCORE_MAX * 25, 1)
    detail = f"score={value}，映射 {score}/25"
    return DimensionScore("技术深度", score, 25, detail)


def _score_format(data: dict) -> DimensionScore:
    """格式规范评分（满分 20）。

    id、title、source_url、status、时间戳五项各 4 分。
    """
    parts: list[str] = []
    points = 0.0
    for name in FORMAT_FIELDS:
        value = data.get(name)
        ok = isinstance(value, str) and value.strip() != ""
        points += 4.0 if ok else 0.0
        parts.append(f"{name}{'✓' if ok else '✗'}")

    missing = [
        t
        for t in TIME_FIELDS
        if not (isinstance(data.get(t), str) and data.get(t).strip())
    ]
    time_ok = not missing
    points += 4.0 if time_ok else 0.0
    parts.append(f"时间戳{'✓' if time_ok else f'✗(缺 {missing})'}")

    return DimensionScore("格式规范", points, 20, "、".join(parts))


def _score_tags(data: dict) -> DimensionScore:
    """标签精度评分（满分 15）。

    按数量给基础分，命中标准标签库的标签按个奖励。
    """
    tags = data.get("tags")
    if not isinstance(tags, list):
        return DimensionScore("标签精度", 0.0, 15, "tags 缺失或非列表")

    count = len(tags)
    if 1 <= count <= 3:
        base = 9.0
    elif 4 <= count <= 5:
        base = 5.0
    else:
        base = 2.0

    valid = [
        t
        for t in tags
        if isinstance(t, str) and t.lower() in STANDARD_TAGS
    ]
    bonus = min(len(valid), 3) * 2  # 每个命中 +2，最多 +6
    score = min(base + bonus, 15.0)
    detail = (
        f"{count} 个标签，{len(valid)} 个命中标准库"
        f"（基础 {base:.0f} + 奖励 {bonus}）"
    )
    return DimensionScore("标签精度", score, 15, detail)


def _score_buzzwords(data: dict, text: str) -> DimensionScore:
    """空洞词检测评分（满分 15）。

    不含空洞词满分，每命中一个扣 5 分，最低 0 分。
    """
    lower = text.lower()
    found_zh = [w for w in BUZZWORDS_ZH if w in text]
    found_en = [w for w in BUZZWORDS_EN if w in lower]
    found = found_zh + found_en
    penalty = len(found) * 5
    score = max(15 - penalty, 0)
    if found:
        detail = f"命中 {len(found)} 个空洞词：{'、'.join(found)}（扣 {penalty} 分）"
    else:
        detail = "未命中空洞词"
    return DimensionScore("空洞词检测", float(score), 15, detail)


# --------------------------------------------------------------------------- #
# 报告生成与渲染
# --------------------------------------------------------------------------- #


def _build_search_text(data: dict) -> str:
    """拼接用于空洞词检索的文本（title + summary + highlights）。"""
    chunks: list[str] = []
    for key in ("title", "summary"):
        value = data.get(key)
        if isinstance(value, str):
            chunks.append(value)
    highlights = data.get("highlights")
    if isinstance(highlights, list):
        chunks.extend(h for h in highlights if isinstance(h, str))
    return "\n".join(chunks)


def score_file(path: Path) -> QualityReport:
    """对单个 JSON 文件做质量评分，返回报告。

    Args:
        path: JSON 文件路径。

    Returns:
        该文件的质量评分报告。
    """
    report = QualityReport(file_path=path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        report.error = f"文件读取失败: {exc}"
        return report
    except json.JSONDecodeError as exc:
        report.error = f"JSON 解析失败: {exc}"
        return report
    if not isinstance(data, dict):
        report.error = f"顶层结构应为 JSON 对象，实际: {type(data).__name__}"
        return report

    search_text = _build_search_text(data)
    report.dimensions = [
        _score_summary(data),
        _score_depth(data),
        _score_format(data),
        _score_tags(data),
        _score_buzzwords(data, search_text),
    ]
    return report


def _render_bar(ratio: float, width: int = 20) -> str:
    """渲染文本进度条（width 个块）。"""
    filled = round(max(0.0, min(1.0, ratio)) * width)
    return "█" * filled + "░" * (width - filled)


def render_report(report: QualityReport) -> str:
    """将单个报告渲染为多行可读字符串。"""
    lines: list[str] = []
    lines.append("━" * 60)
    lines.append(f"📄 {report.file_path.name}")

    if report.error is not None:
        lines.append(f"  ❌ 加载失败：{report.error}")
        lines.append("  等级: C（错误）")
        return "\n".join(lines)

    total = report.total_score
    full = report.max_score
    ratio = total / full if full else 0.0
    lines.append(
        f"  总评: {_render_bar(ratio)} {total:.1f}/{full:.0f}  等级: {report.grade}"
    )
    for dim in report.dimensions:
        bar = _render_bar(dim.percentage / 100, width=20)
        lines.append(
            f"    {dim.name:<6} {dim.score:>4.1f}/{dim.max_score:<2.0f} "
            f"{bar}  {dim.detail}"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


def expand_paths(args: list[str]) -> list[Path]:
    """展开命令行参数为待评分文件列表，支持通配符。

    Args:
        args: 命令行传入的路径或通配符模式。

    Returns:
        去重并排序后的 Path 列表。
    """
    files: set[Path] = set()
    for arg in args:
        pattern = Path(arg)
        matched = sorted(pattern.parent.glob(pattern.name))
        if matched:
            files.update(matched)
        else:
            files.add(pattern)
    return sorted(files)


def main(argv: list[str]) -> int:
    """脚本主入口。

    Args:
        argv: 命令行参数列表（不含脚本名）。

    Returns:
        退出码，0 表示不存在 C 级条目，1 表示存在 C 级或发生错误。
    """
    if not argv:
        print(
            "用法: python hooks/check_quality.py <json_file> [json_file2 ...]",
            file=sys.stderr,
        )
        return 1

    paths = expand_paths(argv)
    reports: list[QualityReport] = []
    checked = 0

    for path in paths:
        if not path.exists():
            report = QualityReport(file_path=path, error="文件不存在")
            reports.append(report)
            continue
        if not path.is_file():
            report = QualityReport(file_path=path, error="不是文件")
            reports.append(report)
            continue
        reports.append(score_file(path))
        checked += 1

    for report in reports:
        print(render_report(report))

    # 汇总
    grade_count = {"A": 0, "B": 0, "C": 0}
    for report in reports:
        if report.error is not None:
            grade_count["C"] += 1
        else:
            grade_count[report.grade] += 1

    print("\n" + "=" * 60)
    print("评分汇总")
    print("=" * 60)
    print(f"  检查文件数: {checked}")
    print(
        f"  等级分布: A={grade_count['A']}  B={grade_count['B']}  "
        f"C={grade_count['C']}"
    )

    if grade_count["C"] > 0:
        print(f"\n⚠ 存在 {grade_count['C']} 个 C 级条目")
        return 1

    print("\n全部条目质量达标（无 C 级）✔")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
