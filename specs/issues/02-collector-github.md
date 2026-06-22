# 02 · Collector：GitHub Trending 真实采集

## What to build

将骨架里的 Collector stub 替换为真实采集：WebFetch 抓取 GitHub Trending，按 AI/LLM/Agent 关键词过滤，按 AGENTS.md 取 **Top 25**，写 `knowledge/raw/`（格式符合 AGENTS.md raw 规范）。复用已存在的 `.opencode/skills/github-trending/SKILL.md` 逻辑。

HTTP 超时 15s，指数退避重试 3 次（5s / 10s / 20s）；采集前先扫描本地 raw 做 source_url 去重；重试耗尽写入 `State.errors`。

## Acceptance criteria

- [ ] `kb collect --source github_trending` 抓取真实数据并落盘 raw JSON，字段符合 AGENTS.md raw 规范
- [ ] 仅保留 AI/LLM/Agent 相关项，排除 `awesome-*` 等聚合列表
- [ ] 采集量受 Top 25 上限约束
- [ ] 本地 source_url 去重生效（已存在则跳过）
- [ ] HTTP 失败时指数退避重试，耗尽后写入 `State.errors`
- [ ] Collector 测试 mock 所有外部依赖，覆盖率 ≥ 80%

## Blocked by

- #01（骨架与 KBState）
