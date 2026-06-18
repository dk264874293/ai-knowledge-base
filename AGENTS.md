# AGENTS.md — AI 知识库助手

## 项目概述

自动从 GitHub Trending、Hacker News 等渠道采集 AI/LLM/Agent 领域的技术动态，
经大模型分析后结构化存储为 JSON，并支持向 Telegram、飞书等多渠道分发的知识库助手。

## 技术栈

| 类别       | 选型                                |
|------------|-------------------------------------|
| 语言       | Python 3.12                         |
| AI 编排    | OpenCode + 国产大模型（Qwen 等）    |
| 工作流     | LangGraph                           |
| 技能框架   | OpenClaw                            |
| 分发渠道   | Telegram Bot API、飞书 Webhook      |

## 编码规范

- 遵循 **PEP 8**；行宽上限 88 字符（与 Black 保持一致）
- 命名：变量/函数 `snake_case`，类 `PascalCase`，常量 `UPPER_SNAKE_CASE`
- 所有公开函数必须附带 **Google 风格 docstring**
- **禁止裸 `print()`**，统一使用 `logging` 模块
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
├── AGENTS.md
└── pyproject.toml
```

## 知识条目 JSON 格式

每条记录存储在 `knowledge/articles/` 下，文件名格式 `{date}_{id}.json`：

```json
{
  "id": "kb-20260611-001",
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

| 字段              | 类型       | 必填 | 说明                                           |
|-------------------|------------|------|------------------------------------------------|
| `id`              | `string`   | ✅    | 唯一标识，格式 `kb-{YYYYMMDD}-{序号}`          |
| `title`           | `string`   | ✅    | 条目标题                                       |
| `source_url`      | `string`   | ✅    | 原文链接                                       |
| `source`          | `string`   | ✅    | 采集来源：`github_trending` / `hacker_news` 等  |
| `collected_at`    | `string`   | ✅    | 采集时间，ISO 8601                              |
| `summary`         | `string`   | ✅    | AI 生成的中文摘要（100-300 字）                 |
| `tags`            | `string[]` | ✅    | 标签列表                                       |
| `category`        | `string`   | ✅    | 分类：`framework` / `model` / `tool` / `paper`  |
| `relevance_score` | `float`    | ❌    | AI 打分 0-1，表示与 AI/LLM 领域相关度           |
| `status`          | `string`   | ✅    | `draft` / `review` / `published` / `archived`   |
| `distributed_to`  | `string[]` | ❌    | 已分发的渠道列表                                |
| `created_at`      | `string`   | ✅    | 创建时间，ISO 8601                              |
| `updated_at`      | `string`   | ✅    | 最后更新时间，ISO 8601                          |

## Agent 角色概览

| 角色           | 职责                         | 触发方式           | 数据来源                | 输出                        |
|----------------|------------------------------|--------------------|-------------------------|-----------------------------|
| **采集 Agent** | 定时抓取 GitHub Trending 和 Hacker News | 定时任务 / 手动触发 | 网页 API               | `knowledge/raw/*.json`      |
| **分析 Agent** | 对原始数据进行摘要、打标签、评分 | 新 raw 文件产生时   | `knowledge/raw/`        | `knowledge/articles/*.json` |
| **整理 Agent** | 审核、去重、格式化后分发到各渠道 | 手动触发 / 阈值触发 | `knowledge/articles/`   | Telegram / 飞书消息          |

## 红线 🚫

以下操作**绝对禁止**：

1. **禁止提交密钥** — 不得将 API Key、Token、密码提交到 Git 仓库；必须使用 `.env` 或环境变量
2. **禁止硬编码 URL** — 数据源地址必须从配置文件读取，不得写死在代码中
3. **禁止绕过审核直接发布** — 知识条目标记为 `published` 前必须经过整理 Agent 审核流程
4. **禁止直接修改已发布条目** — 已分发的条目如需修改，应新建版本而非覆盖原文件
5. **禁止无错误处理的外部请求** — 所有 HTTP 请求必须包含超时和异常处理，禁止静默失败
6. **禁止删除 `knowledge/` 下已有文件** — 只能归档（`status: archived`），不可物理删除
7. **禁止未经测试即合并** — 新增/修改的模块必须附带对应测试用例
