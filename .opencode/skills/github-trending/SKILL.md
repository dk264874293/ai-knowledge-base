---
name: github-trending
description: >-
  抓取 GitHub Trending Top 50 并按 AI / LLM / Agent 关键词过滤，生成结构化中文摘要。
  Use when collecting GitHub trending repos, or when user asks
  "GitHub 上最近有什么火的项目".
allowed-tools:
  - Read
  - Grep
  - Glob
  - WebFetch
  - Write
  - Bash
---

# GitHub Trending 采集技能

从 GitHub Trending 抓取 Top 50 仓库，过滤出 AI / LLM / Agent 领域项目，
生成中文摘要并输出标准 JSON。

## 使用场景

- 每日定时采集 GitHub Trending
- 快速了解近期最受关注的 AI 开源项目
- 用户主动询问近期 GitHub 热门 AI 项目
- 为知识库补充 GitHub 渠道的技术动态

## 执行步骤

### 1. 抓取 Trending 页面

使用 WebFetch 请求 GitHub Trending，获取 Top 50：

| 参数       | 值                                   |
|------------|--------------------------------------|
| URL        | `https://github.com/trending?since=daily` |
| 备用 URL   | `https://github.com/trending?since=weekly` |
| 超时       | 15s                                  |
| 重试       | 3 次，指数退避 5s → 10s → 20s       |

> WebFetch 超时或异常时，降级用 GitHub Search API（`sort=stars`）获取替代数据。

### 2. 提取核心字段

从每个仓库提取：

| 字段        | 来源                                   |
|-------------|----------------------------------------|
| `name`      | 仓库全名 `owner/repo`                  |
| `url`       | 仓库地址                                |
| `stars`     | 总 star 数                             |
| `language`  | 主语言                                  |
| `topics`    | 仓库 Topics 标签                        |
| `description` | README 摘要 / description（用于写中文摘要） |

### 3. AI 相关性过滤

**纳入条件**（满足任一）：

- topics / description 含以下关键词（不区分大小写）：
  `ai`、`llm`、`agent`、`rag`、`transformer`、`llama`、`qwen`、`gpt`、
  `chatgpt`、`claude`、`gemini`、`deepseek`、`embedding`、`fine-tuning`、
  `inference`、`vllm`、`langchain`、`autogen`、`crewai`、`ollama`、
  `stable-diffusion`、`diffusion`、`vector-database`、`machine-learning`、
  `deep-learning`、`nlp`
- 属于 `machine-learning`、`deep-learning`、`nlp`、`llm`、`ai`、
  `generative-ai` 等 GitHub Topic

**排除条件**（命中即剔除）：

- 名称为 `awesome-xxx` / `xxx-list` / `xxx-resources` 等纯聚合列表
- 与 AI / LLM / Agent 领域明显无关（纯前端 UI、纯运维脚本等）
- 已归档（archived）或长期未更新（> 2 年无提交）

### 4. 本地去重

采集前先扫描 `knowledge/raw/` 下已有的 `github-trending-*.json`，
对比 `url` 字段，已存在则跳过；同一批次内 `url` 完全相同的仅保留一条。

### 5. 生成中文摘要

对每个保留项目生成中文摘要，遵循公式：

```
项目名 + 做什么 + 为什么值得关注
```

- **项目名**：仓库全名或常用简称
- **做什么**：一句话说明核心功能 / 解决的问题
- **为什么值得关注**：技术亮点、热度趋势或实用价值
- **长度**：100–300 字，客观准确，不夸大

### 6. 排序取 Top 50

- 按 `stars` 降序排列
- 截取前 **50** 条
- 不足 50 条时按实际数量输出，不凑数、不编造

### 7. 写入 JSON

将结果写入：

```
knowledge/raw/github-trending-YYYY-MM-DD.json
```

## 注意事项

- 真实数据：所有信息来自实际搜索结果，禁止编造
- 中文优先：摘要统一中文；项目名、URL 保留原文
- 聚焦 AI：严格围绕 AI / LLM / Agent，排除纯聚合仓库
- 去重前置：采集前必须扫描本地已有数据

## 输出格式

```json
{
  "source": "github_trending",
  "skill": "github-trending",
  "collected_at": "2026-06-22T00:00:00+08:00",
  "items": [
    {
      "name": "langchain-ai/langgraph",
      "url": "https://github.com/langchain-ai/langgraph",
      "summary": "langgraph 是 LangChain 团队推出的图式 Agent 编排框架，支持将语言 Agent 构建为可持久化的有状态图。引入 subgraph 持久化、流式 checkpoint 等能力，适合构建复杂多步推理与工具调用流程。",
      "stars": 18000,
      "language": "Python",
      "topics": ["llm", "agent", "langchain", "workflow"]
    }
  ]
}
```

### 字段说明

| 字段           | 位置  | 类型       | 说明                                  |
|----------------|-------|------------|---------------------------------------|
| `source`       | 顶层  | `string`   | 固定值 `github_trending`              |
| `skill`        | 顶层  | `string`   | 固定值 `github-trending`              |
| `collected_at` | 顶层  | `string`   | 采集时间，ISO 8601（北京时间）         |
| `items`        | 顶层  | `array`    | 仓库条目数组（Top 50）                 |
| `name`         | item  | `string`   | 仓库全名 `owner/repo`                 |
| `url`          | item  | `string`   | 仓库地址                               |
| `summary`      | item  | `string`   | 中文摘要（100–300 字）                 |
| `stars`        | item  | `number`   | star 数                               |
| `language`     | item  | `string`   | 主语言                                 |
| `topics`       | item  | `string[]` | Topics 标签（1–5 个）                  |
