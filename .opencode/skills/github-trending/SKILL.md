---
name: github-trending
description: >-
  采集 GitHub Trending 热门仓库（HTML 解析，不调 GitHub API），按 AI / LLM / Agent / ML
  关键词过滤，输出标准化 JSON 数组 [name, url, stars, topics, description]。
  Use when 用户提到以下任一场景：① GitHub Trending / 热门 / 热榜 / 趋势榜 / 排行榜 /
  star 榜 / trending；② 问「最近/今天/这周 有什么火的项目」「有什么好的开源项目」
  「有什么值得 star/关注的」「有什么新项目」「有什么 star 增长快的」「有什么好玩的」
  「有什么值得跟进的」「GitHub 上有什么值得关注的」；③ 想发现 AI/LLM/Agent 开源项目
  （「有什么新的 AI 项目」「最近有什么 LLM 项目/模型/框架」「有什么好的 Agent 项目」
  「AI 开源工具推荐」「有没有好玩的 AI 项目」「AI 圈有什么新东西」）；④ 采集知识库数据
  （「采集 GitHub」「刷一下 GitHub/trending」「补充 GitHub 数据」「搜一下 GitHub 上的 AI 项目」
  「推荐几个 GitHub 项目」「帮我搜集一下 AI 项目」「每日 GitHub 采集」）。
  English: "GitHub trending", "what's hot on GitHub", "trending AI repos",
  "popular open source", "top starred repos", "what's new on GitHub".
  不适用于已采集数据的分析总结（→ tech-summary）。
allowed-tools:
  - Bash
  - Read
  - Write
---

# GitHub Trending 采集技能

从 `github.com/trending` 抓取 Top 50 仓库，过滤出 AI / LLM / Agent / ML 领域项目，
输出标准 JSON 数组。走 HTML 解析（不调 API），失败返回空数组。

## Quick start

```bash
# 采集今日 Top 50（默认 daily，最多返回 AI 相关条目）
python .opencode/skills/github-trending/scripts/scrape_trending.py

# 指定时间范围 & 条数上限
python .opencode/skills/github-trending/scripts/scrape_trending.py --since weekly --limit 30
```

输出直接打到 stdout（JSON 数组），日志走 stderr。

## 执行步骤

### 1. 运行采集脚本

脚本 `scripts/scrape_trending.py` 完成全部工作：

| 步骤       | 说明                                                        |
|------------|-------------------------------------------------------------|
| 抓取 HTML  | `urllib` 请求 `github.com/trending?since=daily`，超时 8s    |
| 解析仓库   | 正则提取每个 `<article class="Box-row">` 中的 5 个字段      |
| AI 过滤    | 按 topics + description + name 匹配 AI 关键词               |
| 排序截断   | 按 stars 降序，截取 Top N                                   |
| 输出 JSON  | stdout 输出 JSON 数组，失败时输出 `[]`                       |

> Trending 页面不展示 topics 标签，`topics` 字段可能为空数组；
> AI 过滤同时检查 description 和 name 中的关键词。

### 2. 校验输出

输出必须为 JSON 数组，每条含 5 个字段：

```json
[
  {
    "name": "owner/repo",
    "url": "https://github.com/owner/repo",
    "stars": 1234,
    "topics": ["llm", "agent"],
    "description": "项目简介"
  }
]
```

### 3. 交给 caller

本技能不做去重、不落盘——只 stdout。后续环节（collector / 整理 Agent）负责持久化。

## AI 关键词（部分）

`ai` `llm` `agent` `ml` `machine-learning` `deep-learning` `nlp` `rag`
`transformer` `gpt` `llama` `qwen` `deepseek` `claude` `gemini` `embedding`
`fine-tuning` `inference` `vllm` `langchain` `langgraph` `autogen` `crewai`
`ollama` `stable-diffusion` `generative-ai` `vector-database` ... 完整列表见
`scripts/scrape_trending.py` → `AI_KEYWORDS`。

## 边界

| 做什么                         | 不做什么                              |
|--------------------------------|---------------------------------------|
| HTML 解析 Trending Top 50      | 不调 GitHub API（rate limit）         |
| 按 AI 关键词过滤              | 不做去重（由 caller 处理）             |
| stdout 输出 JSON 数组          | 不存数据库 / 不落盘                    |
| 失败返回 `[]`，不抛异常        | 不生成中文摘要（→ tech-summary）      |
| 单次执行 < 10s                 | 不修改已有知识库文件                   |

## 参数

| 参数       | 默认值   | 可选值                           | 说明           |
|------------|----------|----------------------------------|----------------|
| `--since`  | `daily`  | `daily` / `weekly` / `monthly`   | 时间范围       |
| `--limit`  | `50`     | 正整数                           | 最大返回条数   |
