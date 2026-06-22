# Backlog — AI 知识库三 Agent Pipeline

来源：PRD v0.1 + AGENTS.md，经 `to-issues` 拆分为纵向切片（tracer bullet）。

## 裁决原则

- **规格冲突一律以 AGENTS.md 为准**：GitHub Top25、HN Top50、analyzer 三维度 = summary + tags + relevance_score。
- 每个切片贯穿 CLI → LangGraph → State → 持久化 → 测试 全部层，可独立演示。
- 切片 1 是关键 tracer bullet（全 mock 打通链路）；2/3/4 完成后可并行；5/6 为横切关注点；7 为第二数据源纵向扩展；8 为渠道分发。
- **CLI 命名对齐**：整理产物走 `kb digest`（MD 日报，#04），渠道推送走 `kb distribute`（Telegram/飞书，#08）。AGENTS.md CLI 列表仅有 `distribute`，此处新增 `digest` 以区分整理产物与渠道推送。

## 切片索引

| #   | 标题                          | Blocked by | 状态 |
|-----|-------------------------------|------------|------|
| 00  | 决策对齐 ADR                  | —          | ☐    |
| 01  | 端到端骨架 happy-path（mock） | —          | ☐    |
| 02  | Collector：GitHub Trending    | 01         | ☐    |
| 03  | Analyzer：三维度标注          | 01         | ☐    |
| 04  | Organizer：MD 日报            | 01         | ☐    |
| 05  | 失败传播 + 幂等重跑           | 01         | ☐    |
| 06  | 进度追踪 kb status            | 01         | ☐    |
| 07  | 扩展 Hacker News 第二数据源   | 02, 03, 04 | ☐    |
| 08  | 渠道分发 Telegram + 飞书      | 04         | ☐    |

## 依赖图

```
00 (ADR) ─┐
          ├─► 01 (骨架) ─┬─► 02 (Collector/GH) ─┐
          │              ├─► 03 (Analyzer)      ├─► 07 (HN 多源)
          │              ├─► 04 (Organizer/digest) ─► 08 (渠道分发)
          │              ├─► 05 (失败/重跑)
          │              └─► 06 (kb status)
```

> 0 与 1 可并行启动（State 形状已在 AGENTS.md 给出，0 仅补决策留痕）。

## issue → Agent 派生

`.opencode/agents/{collector,analyzer,organizer}.md` 由对应 issue 派生，**issue 是职责说明的权威来源**（冲突一律以 issue + AGENTS.md 为准）。

| Agent 配置 | 权威 issue | 备注 |
|------------|-----------|------|
| `.opencode/agents/collector.md` | `02-collector-github.md` + `07-hacker-news-source.md` | GH Top 25 / HN Top 50 |
| `.opencode/agents/analyzer.md` | `03-analyzer-labeling.md` | 三维度：summary / tags / relevance_score |
| `.opencode/agents/organizer.md` | `04-organizer-md-digest.md`（分发边界 → `08`） | `kb digest`；`kb distribute` 归 #08 |

派生约定：

- agent 配置只保留**运行时配置**（front-matter：mode/model/permission）+ **操作要点**（工具/降级/红线）；职责、验收标准、schema 一律引用 issue，不在 agent 文件重复，避免漂移。
- 派生时已消除的历史漂移：`score` 1-10 → `relevance_score` 0-1 浮点；移除 `highlights` 字段；文件命名 `{date}-{source}-{slug}` → `{date}_{id}_v{version}.json`；organizer 状态阈值 `score≥7` → `relevance_score≥0.4`。
- **写入边界**：collector/analyzer 子 agent `edit: deny`，只产出数据；落盘由 LangGraph 节点代码完成（见对应 issue）。organizer `write/edit: allow`，直接写 articles/MD。

## 延后项（deferred）

- **定时调度（cron）**：PRD「每天 UTC 0:00 触发」与 AGENTS.md 采集/推送 cron 表（采集 UTC 0:00、分发北京 07:00）暂不纳入代码切片，计划交给 OS cron / systemd 触发既有 CLI。如后续需内置调度器再单独立切片（候选 #09）。
