# 06 · 进度追踪 kb status

## What to build

基于 `batch_id` 实现 `kb status`：扫描当日 `knowledge/raw`、`knowledge/articles`、distributed、`errors`，汇总展示今日批次的采集 / 分析 / 分发数量与失败项摘要。提供 CLI 可读输出。回答 PRD 开放问题 Q4（进度追踪）。

## Acceptance criteria

- [ ] `kb status` 显示当日批次各阶段计数（raw / articles / distributed / errors）
- [ ] 列出失败项摘要与原因（读 errors）
- [ ] 支持 `kb status --date YYYYMMDD` 查看历史批次
- [ ] 无数据时给出明确提示而非报错
- [ ] 输出禁用裸 print，走 logging / typer 标准输出
- [ ] status 汇总逻辑有单元测试，覆盖率 ≥ 80%

## Blocked by

- #01（骨架与 KBState）
