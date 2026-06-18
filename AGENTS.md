# AGENTS.md — AI 知识库助手

## 项目概述

自动从 GitHub Trending、Hacker News 等渠道采集 AI/LLM/Agent 领域的技术动态，
经大模型分析后结构化存储为 JSON，并支持向 Telegram、飞书等多渠道分发的知识库助手。

## 技术栈

| 类别       | 选型                                                     |
|------------|--------------------------------------------------------|
| 语言       | Python 3.12                                            |
| AI 编排    | OpenCode + 国产大模型（Qwen / GLM / DeepSeek 混合路由）|
| 工作流     | LangGraph（线性 pipeline）                             |
| 技能框架   | OpenClaw（外部 Agent 调用接口）                        |
| CLI        | typer                                                  |
| 分发渠道   | Telegram Bot API、飞书 Webhook                         |

## 编码规范

- 遵循 **PEP 8**；行宽上限 88 字符（与 Black 保持一致）
- 命名：变量/函数 `snake_case`，类 `PascalCase`，常量 `UPPER_SNAKE_CASE`
- 所有公开函数必须附带 **Google 风格 docstring**
- **禁止裸 `print()`**，统一使用 `logging` 模块
  - 日志格式：`2026-06-11 10:30:00 [INFO] collector: Collected 25 items`
  - 默认输出 stdout，日志级别通过 `config.yaml` 的 `log_level` 字段可调（默认 `INFO`）
  - **INFO**：采集/分析/推送完成（条目数统计）
  - **WARNING**：HTTP 请求失败（重试中）、LLM 返回格式异常
  - **ERROR**：重试耗尽、持久化失败
- 导入顺序：标准库 → 第三方库 → 本项目模块，各组之间空一行
- 类型注解：公开 API 必须标注参数和返回值类型
- 禁止提交 `.env`、API Key、Token 等敏感信息

## 项目结构

```
.
├── .opencode/
│   ├── agents/            # Agent 定义（YAML）
│   └── skills/            # OpenClaw 技能
├── src/
│   ├── collector/         # 数据采集模块
│   ├── analyzer/          # AI 分析与结构化模块
│   ├── distributor/       # 多渠道分发模块
│   └── utils/             # 公共工具
├── knowledge/
│   ├── raw/               # 采集到的原始数据
│   └── articles/          # 分析后的结构化 JSON 条目
├── tests/                 # 单元/集成测试
├── config.yaml            # 运行时配置
├── AGENTS.md
└── pyproject.toml
```

## 知识条目 JSON 格式

每条记录存储在 `knowledge/articles/` 下，文件名格式 `{date}_{id}_v{version}.json`：

```json
{
  "id": "kb-20260611-001",
  "version": 1,
  "parent_id": null,
  "title": "LangGraph v0.3 Release Notes",
  "source_url": "https://github.com/langchain-ai/langgraph/releases",
  "source": "github_trending",
  "collected_at": "2026-06-11T10:30:00+08:00",
  "summary": "LangGraph 0.3 引入了 subgraph 持久化与流式 checkpoint ...",
  "tags": ["langgraph", "agent", "workflow"],
  "category": "framework",
  "relevance_score": 0.85,
  "status": "published",
  "distributed_to": ["telegram", "feishu"],
  "created_at": "2026-06-11T10:35:00+08:00",
  "updated_at": "2026-06-11T11:00:00+08:00"
}
```

### 字段说明

| 字段              | 类型          | 必填 | 说明                                           |
|-------------------|---------------|------|------------------------------------------------|
| `id`              | `string`      | ✅    | 唯一标识，格式 `kb-{YYYYMMDD}-{序号}`          |
| `version`         | `int`         | ✅    | 版本号，默认 1，修订时递增                      |
| `parent_id`       | `string/null` | ❌    | 指向被修订的前一版本 id                          |
| `title`           | `string`      | ✅    | 条目标题                                       |
| `source_url`      | `string`      | ✅    | 原文链接                                       |
| `source`          | `string`      | ✅    | 采集来源：`github_trending` / `hacker_news` 等  |
| `collected_at`    | `string`      | ✅    | 采集时间，ISO 8601                              |
| `summary`         | `string`      | ✅    | AI 生成的中文摘要（100-300 字）                 |
| `tags`            | `string[]`    | ✅    | 标签列表（1-5 个）                              |
| `category`        | `string`      | ✅    | 分类：`framework` / `model` / `tool` / `paper`  |
| `relevance_score` | `float`       | ❌    | AI 打分 0-1，表示与 AI/LLM 领域相关度           |
| `status`          | `string`      | ✅    | `draft` / `review` / `published` / `archived`   |
| `distributed_to`  | `string[]`    | ❌    | 已分发的渠道列表                                |
| `created_at`      | `string`      | ✅    | 创建时间，ISO 8601                              |
| `updated_at`      | `string`      | ✅    | 最后更新时间，ISO 8601                          |

## Raw 数据 JSON 格式

每条原始记录存储在 `knowledge/raw/` 下，文件名格式 `raw_{date}_{id}.json`：

```json
{
  "id": "raw-20260611-001",
  "source": "github_trending",
  "source_url": "https://github.com/langchain-ai/langgraph",
  "title": "langchain-ai/langgraph",
  "raw_content": "原始 HTML 片段 / API 响应内容",
  "collected_at": "2026-06-11T00:00:00+00:00",
  "metadata": {
    "stars_today": 120,
    "language": "Python",
    "description": "Build resilient language agents as graphs."
  }
}
```

`metadata` 为来源特有的扩展字段，不同来源内容不同：
- **GitHub Trending**：`stars_today`、`language`、`description`
- **Hacker News**：`score`、`num_comments`、`author`

## Agent 角色概览

| 角色           | 职责                                    | 触发方式           | 数据来源              | 输出                            |
|----------------|-----------------------------------------|--------------------|-----------------------|---------------------------------|
| **采集 Agent** | 定时抓取 GitHub Trending 和 Hacker News | 定时任务 / 手动触发 | 网页 API              | `knowledge/raw/*.json`          |
| **分析 Agent** | 对原始数据进行摘要、打标签、评分        | 新 raw 文件产生时   | `knowledge/raw/`      | `knowledge/articles/*.json`     |
| **整理 Agent** | 审核、去重、格式化后分发到各渠道        | 每日 07:00 批量触发 | `knowledge/articles/` | Telegram / 飞书消息              |

## LangGraph Pipeline

三个 Agent 构成线性 pipeline，不打回：

```
采集 Agent → 分析 Agent → 整理 Agent
```

### State 定义

```python
class KBState(TypedDict):
    batch_id: str                  # 批次标识（格式：YYYYMMDD）
    raw_items: list[dict]          # 采集的原始数据
    articles: list[dict]           # 分析后的结构化条目
    distributed: list[dict]        # 已分发的条目
    errors: list[dict]             # 各环节异常记录
```

每个节点接收整个 State，只修改自己负责的字段。

## 模型配置

采用多厂商混合路由，通过 `src/utils/llm_client.py` 的 `LLMClient` 封装：

| 任务            | 模型        | 提供方           | max_tokens |
|-----------------|-------------|------------------|-----------|
| summary 生成    | Qwen-Max    | 阿里云 DashScope | 500       |
| tags 提取       | GLM-5.1     | 智谱 BigModel    | 100       |
| relevance_score | DeepSeek V3 | DeepSeek Platform| 50        |

`LLMClient` 对外暴露统一接口，分析 Agent 不感知底层厂商：

```python
class LLMClient:
    def generate_summary(self, content: str) -> str: ...
    def extract_tags(self, content: str) -> list[str]: ...
    def score_relevance(self, content: str) -> float: ...
```

## 采集频率

| 来源            | 频率                | 范围      | 策略                          |
|-----------------|---------------------|-----------|-------------------------------|
| GitHub Trending | 每日 1 次（UTC 00:00）| Top 25  | 全量采集，去重由整理 Agent 处理 |
| Hacker News     | 每日 1 次（UTC 00:00）| Top 50  | 同上                          |

## 审核与去重

### 审核标准

整理 Agent 自动校验以下规则，校验失败的条目标记为 `status: review` 并写入 `review_reason`：

| 规则               | 条件                                      |
|--------------------|------------------------------------------|
| 相关度过滤         | `relevance_score >= 0.4`（低于则 archived）|
| 摘要长度           | 50-300 字符                               |
| 标签数量           | 1-5 个                                    |
| 分类有效性         | 必须在枚举值内                             |

### 去重规则

两层检测：

| 层级     | 判定条件          | 处理                         |
|----------|-------------------|------------------------------|
| 精确重复 | `source_url` 完全相同 | 自动归档，保留最早的一条   |
| 疑似重复 | URL 不同但标题高度相似 | 标记 `review`，人工决定   |

## 分发

### 推送时间

每日北京时间 **07:00**（UTC 23:00）批量推送当天所有 `status: published` 条目。

### 消息模板

**Telegram**（MarkdownV2）：

```
🤖 AI 技术日报 - {日期}

{序号}. [{title}]({source_url})
{summary}

标签: {tag1} {tag2} {tag3}
分类: {category} | 相关度: {relevance_score}
```

**飞书**：Interactive Card（Markdown 元素 + 可点击链接按钮）。

## 错误处理与重试

| 请求类型         | 超时 | 重试次数 | 重试策略                  |
|------------------|------|---------|--------------------------|
| GitHub Trending  | 15s  | 3 次    | 指数退避（5s, 10s, 20s）  |
| Hacker News API  | 10s  | 3 次    | 指数退避                  |
| Telegram Bot API | 10s  | 2 次    | 固定间隔 5s               |
| 飞书 Webhook     | 10s  | 2 次    | 固定间隔 5s               |

重试耗尽仍失败的请求记录到 `errors` 列表，等待人工介入。

## CLI 命令

基于 typer，入口注册为 `kb`：

```bash
kb collect --source github_trending   # 只采 GitHub
kb collect --source hacker_news       # 只采 HN
kb collect                            # 全量采集

kb analyze                            # 分析所有未处理的 raw 文件

kb distribute --channel telegram      # 只推 Telegram
kb distribute --channel feishu        # 只推飞书
kb distribute                         # 全渠道推送

kb status                             # 查看今日批次状态
```

注册方式（`pyproject.toml`）：

```toml
[project.scripts]
kb = "src.cli:app"
```

## OpenClaw 技能

作为外部 Agent 调用接口，暴露以下只读/低风险能力：

| 技能                | 功能             | 暴露给外部 |
|--------------------|------------------|-----------|
| `collect.yaml`     | 触发采集（指定来源）| ✅         |
| `query.yaml`       | 查询知识库        | ✅         |
| `stats.yaml`       | 获取统计信息       | ✅         |
| analyze            | AI 分析           | ❌（受控调度）|
| distribute         | 渠道推送          | ❌（风险较高）|

## 测试策略

| 项目       | 方案                                       |
|-----------|---------------------------------------------|
| 框架       | pytest                                      |
| 覆盖率     | ≥ 80%（collector / analyzer / distributor） |
| 单元测试   | mock 所有外部依赖（pytest-mock + responses） |
| LLM 调用   | mock 返回固定 JSON，验证解析正确性           |
| 集成测试   | 可选，仅 CI 手动触发，用真实 API 验证端到端  |

目录结构：

```
tests/
├── unit/
│   ├── test_collector.py
│   ├── test_analyzer.py
│   └── test_distributor.py
├── integration/
│   └── test_e2e.py
└── conftest.py          # 共享 fixtures（mock LLM 响应等）
```

## 配置文件

使用 `config.yaml` 做运行时配置，Pydantic Settings 读取校验：

```yaml
collector:
  github_trending:
    frequency: "0 0 * * *"
    limit: 25
  hacker_news:
    frequency: "0 0 * * *"
    limit: 50

analyzer:
  summary:
    model: "qwen-max"
    max_tokens: 500
  tags:
    model: "glm-5.1"
    max_tokens: 100
  scoring:
    model: "deepseek-chat"
    max_tokens: 50

distributor:
  telegram:
    enabled: true
  feishu:
    enabled: true
  schedule: "0 23 * * *"

log_level: "INFO"
```

敏感密钥（API Key）通过 `.env` 管理，不出现在 YAML 中：

```env
DASHSCOPE_API_KEY=sk-...
BIGMODEL_API_KEY=...
DEEPSEEK_API_KEY=sk-...
TELEGRAM_BOT_TOKEN=...
FEISHU_WEBHOOK_URL=...
```

## 红线 🚫

以下操作**绝对禁止**：

1. **禁止提交密钥** — 不得将 API Key、Token、密码提交到 Git 仓库；必须使用 `.env` 或环境变量
2. **禁止硬编码 URL** — 数据源地址必须从配置文件读取，不得写死在代码中
3. **禁止绕过审核直接发布** — 知识条目标记为 `published` 前必须经过整理 Agent 审核流程
4. **禁止直接修改已发布条目** — 已分发的条目如需修改，应新建版本（`version` +1）而非覆盖原文件
5. **禁止无错误处理的外部请求** — 所有 HTTP 请求必须包含超时和异常处理，禁止静默失败
6. **禁止删除 `knowledge/` 下已有文件** — 只能归档（`status: archived`），不可物理删除
7. **禁止未经测试即合并** — 新增/修改的模块必须附带对应测试用例
