# 05 · 失败传播 + 幂等重跑

## What to build

把横切的健壮性贯穿全链路：

- **失败传播**：上游节点失败时，已成功部分继续流向下游（不整批中断），失败项写入 `State.errors`，每项含来源与原因，可追溯。
- **幂等重跑**：以 `batch_id`（YYYYMMDD）+ `source_url` 为幂等键 —— 同日重跑 `kb collect` 不产生重复 raw（基于 source_url 跳过）；重跑 `kb analyze` 跳过已生成 article 的 raw。

回答 PRD 开放问题 Q1（上游失败下游）与 Q3（重跑策略），需与 #00 ADR 一致。

## Acceptance criteria

- [ ] 注入 Collector 失败，Analyzer/Organizer 仍对已采集项正常运行
- [ ] 所有失败项带来源与原因进入 `State.errors`
- [ ] 同一 batch_id 重跑 `kb collect` 幂等（不产生重复 raw）
- [ ] 重跑 `kb analyze` 跳过已生成 article 的 raw
- [ ] 失败传播 ≥1 集成测试；幂等重跑 ≥1 集成测试

## Blocked by

- #01（骨架与 KBState）
