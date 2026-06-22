#!/usr/bin/env python3
"""校验知识条目 JSON 文件的脚本。

支持单文件与通配符多文件输入，检查 JSON 解析、必填字段、ID 格式、
status 枚举、URL 格式、摘要长度、标签数量及可选字段取值范围。

用法:
    python hooks/validate_json.py <json_file> [json_file2 ...]
    python hooks/validate_json.py "knowledge/articles/*.json"

退出码:
    0 - 全部校验通过
    1 - 存在校验失败的文件
"""

import json
import re
import sys
from pathlib import Path
from typing import Callable

REQUIRED_FIELDS: dict[str, type] = {
    "id": str,
    "title": str,
    "source_url": str,
    "summary": str,
    "tags": list,
    "status": str,
}

VALID_STATUSES = {"draft", "review", "published", "archived"}
VALID_AUDIENCES = {"beginner", "intermediate", "advanced"}

ID_PATTERN = re.compile(r"^[a-z0-9_]+-\d{8}-\d{3}$")
URL_PATTERN = re.compile(r"^https?://.+")

MIN_SUMMARY_LEN = 20
MIN_TAGS_COUNT = 1
SCORE_MIN = 1
SCORE_MAX = 10


def _check_id(data: dict) -> str | None:
    """校验 id 字段格式是否符合 {source}-{YYYYMMDD}-{NNN}。"""
    value = data.get("id")
    if not isinstance(value, str) or not ID_PATTERN.match(value):
        return (
            f"id 格式非法（期望 {{source}}-{{YYYYMMDD}}-{{NNN}}，如 "
            f"github-20260317-001），实际值: {value!r}"
        )
    return None


def _check_status(data: dict) -> str | None:
    """校验 status 是否在合法枚举内。"""
    value = data.get("status")
    if not isinstance(value, str) or value not in VALID_STATUSES:
        return (
            f"status 非法（期望 {sorted(VALID_STATUSES)} 之一），"
            f"实际值: {value!r}"
        )
    return None


def _check_url(data: dict) -> str | None:
    """校验 source_url 是否为合法的 http(s) 链接。"""
    value = data.get("source_url")
    if not isinstance(value, str) or not URL_PATTERN.match(value):
        return f"source_url 格式非法（期望 https?://...），实际值: {value!r}"
    return None


def _check_summary(data: dict) -> str | None:
    """校验 summary 最少 20 字符。"""
    value = data.get("summary")
    if not isinstance(value, str):
        return None
    if len(value) < MIN_SUMMARY_LEN:
        return (
            f"summary 过短（最少 {MIN_SUMMARY_LEN} 字符），"
            f"实际长度: {len(value)}"
        )
    return None


def _check_tags(data: dict) -> str | None:
    """校验 tags 至少 1 个且元素为字符串。"""
    value = data.get("tags")
    if not isinstance(value, list):
        return None
    if len(value) < MIN_TAGS_COUNT:
        return f"tags 数量不足（至少 {MIN_TAGS_COUNT} 个），实际数量: {len(value)}"
    if not all(isinstance(tag, str) for tag in value):
        return "tags 包含非字符串元素"
    return None


def _check_score(data: dict) -> str | None:
    """校验可选字段 score 是否在 1-10 范围内。"""
    if "score" not in data:
        return None
    value = data["score"]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return f"score 类型非法（期望数字），实际值: {value!r}"
    if not (SCORE_MIN <= value <= SCORE_MAX):
        return (
            f"score 超出范围（{SCORE_MIN}-{SCORE_MAX}），实际值: {value}"
        )
    return None


def _check_audience(data: dict) -> str | None:
    """校验可选字段 audience 是否在合法枚举内。"""
    if "audience" not in data:
        return None
    value = data["audience"]
    if not isinstance(value, str) or value not in VALID_AUDIENCES:
        return (
            f"audience 非法（期望 {sorted(VALID_AUDIENCES)} 之一），"
            f"实际值: {value!r}"
        )
    return None


# 各规则检查函数，按顺序执行
RULE_CHECKS: list[Callable[[dict], str | None]] = [
    _check_id,
    _check_status,
    _check_url,
    _check_summary,
    _check_tags,
    _check_score,
    _check_audience,
]


def validate_file(path: Path) -> list[str]:
    """校验单个 JSON 文件，返回错误信息列表（空列表表示通过）。

    Args:
        path: JSON 文件路径。

    Returns:
        错误信息列表，每个元素为一条错误描述。
    """
    errors: list[str] = []
    prefix = f"[{path}]"

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"{prefix} 文件读取失败: {exc}")
        return errors

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        errors.append(f"{prefix} JSON 解析失败: {exc}")
        return errors

    if not isinstance(data, dict):
        errors.append(f"{prefix} 顶层结构应为 JSON 对象，实际类型: {type(data).__name__}")
        return errors

    # 必填字段：存在性 + 类型
    for field, expected_type in REQUIRED_FIELDS.items():
        if field not in data:
            errors.append(f"{prefix} 缺少必填字段: {field}")
        elif not isinstance(data[field], expected_type):
            actual_type = type(data[field]).__name__
            errors.append(
                f"{prefix} 字段 {field} 类型错误（期望 "
                f"{expected_type.__name__}，实际 {actual_type}）"
            )

    # 逐条规则检查（仅在字段存在/类型正确时有意义，统一执行）
    for check in RULE_CHECKS:
        message = check(data)
        if message:
            errors.append(f"{prefix} {message}")

    return errors


def expand_paths(args: list[str]) -> list[Path]:
    """展开命令行参数为待校验文件列表，支持通配符。

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
            # 非通配符或未匹配到时，保留原路径以便报错
            files.add(pattern)
    return sorted(files)


def main(argv: list[str]) -> int:
    """脚本主入口。

    Args:
        argv: 命令行参数列表（不含脚本名）。

    Returns:
        退出码，0 表示全部通过，1 表示存在失败。
    """
    if not argv:
        print(
            "用法: python hooks/validate_json.py <json_file> [json_file2 ...]",
            file=sys.stderr,
        )
        return 1

    paths = expand_paths(argv)
    all_errors: list[str] = []
    checked = 0
    passed = 0

    for path in paths:
        if not path.exists():
            all_errors.append(f"[{path}] 文件不存在")
            continue
        if not path.is_file():
            all_errors.append(f"[{path}] 不是文件")
            continue

        checked += 1
        errors = validate_file(path)
        if errors:
            all_errors.extend(errors)
        else:
            passed += 1
            print(f"[OK] {path}")

    print("\n" + "=" * 60)
    print("校验汇总")
    print("=" * 60)
    print(f"  检查文件数: {checked}")
    print(f"  通过: {passed}")
    print(f"  失败: {checked - passed}")

    if all_errors:
        print("\n错误明细:")
        for error in all_errors:
            print(f"  - {error}")
        return 1

    print("\n全部校验通过 ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
