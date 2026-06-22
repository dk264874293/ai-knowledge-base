# 07 · 扩展 Hacker News 第二数据源

## What to build

将 GitHub-only 骨架纵向扩展到第二数据源 HN：Collector 增加 HN 抓取（按 AGENTS.md 取 **Top 50**，超时 10s，指数退避重试 3 次）；raw 与 articles 的 `source` 字段区分来源（`hacker_news`）；Analyzer/Organizer 复用既有逻辑（不感知来源），MD 日报可混合展示两源条目。贯穿全部层证明多源抽象成立。

## Acceptance criteria

- [ ] `kb collect --source hacker_news` 抓 HN Top 50 并落盘 raw（格式符合 AGENTS.md raw 规范）
- [ ] raw/articles 正确标注 `source: hacker_news`，metadata 含 score/num_comments/author
- [ ] Analyzer/Organizer 对 HN 条目无需改动即可工作（多源抽象验证）
- [ ] HN 采集有超时（10s）/ 重试，失败进 `State.errors`
- [ ] 多源混合批次有 ≥1 集成测试

## Blocked by

- #02（Collector 抽象）
- #03（Analyzer 不感知来源）
- #04（Organizer 多源日报）
