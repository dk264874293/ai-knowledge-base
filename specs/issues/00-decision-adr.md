# 00 · 决策对齐 ADR

## What to build

记录 PRD v0.1 中四项开放问题的最终决策，以及 PRD ↔ AGENTS.md 冲突的裁决，作为后续所有切片实现的依据。产出一项轻量 ADR（决策记录），明确：

1. **数据怎么传？** → LangGraph `KBState`（内存对象）+ `knowledge/raw`、`knowledge/articles` 文件双写（状态流转 + 可重放落盘）。
2. **上游失败下游怎么办？** → 不整批中断；已成功部分继续流向下游，失败项写入 `State.errors`，等待人工介入。
3. **重跑策略？** → 以 `batch_id`（YYYYMMDD）+ `source_url` 为幂等键；同日重跑 collect 不产生重复 raw，重跑 analyze 跳过已生成 article 的 raw。
4. **进度追踪？** → `kb status` 基于 batch_id 扫描 raw/articles/distributed/errors 汇总。
5. **规格冲突裁决**：一律以 AGENTS.md 为准（GitHub Top25 + HN Top50；analyzer 三维度 = summary + tags + relevance_score）。

## Acceptance criteria

- [ ] 产出 `docs/adr/0001-pipeline-decisions.md`（或等价文件）
- [ ] 四项开放问题每项给出明确结论 + 简短理由
- [ ] 记录 PRD↔AGENTS.md 冲突的最终裁决（AGENTS.md 胜出），列出受影响字段
- [ ] 标注对后续切片的影响（State 字段、batch_id 规则、errors 结构）
- [ ] ADR 经人工 review 确认

## Blocked by

None — can start immediately.
