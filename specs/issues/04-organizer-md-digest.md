# 04 · Organizer：MD 日报（kb digest）

## What to build

将 Organizer stub 替换为真实整理：读 `knowledge/articles/`，按审核规则过滤；两层去重；通过的条目整理成当日 MD 日报，作为「整理 Agent」的产物经 `kb digest` 产出。

**审核规则**（不合格标 `status: review` / `archived` 并写 `review_reason`）：
- relevance_score ≥ 0.4（低于 archived）
- 摘要 50–300 字符
- 标签 1–5 个
- category 在枚举值内（framework / model / tool / paper）

**去重**：source_url 精确重复 → 自动归档保留最早；URL 不同但标题高度相似 → 标 review 人工决定。

> 本切片仅产出 MD 日报（整理产物）。真实渠道推送（Telegram/飞书，对应 AGENTS.md 的 `distributor` 模块与 `kb distribute`）留待 **#08**。已发布条目不可覆盖，需新建 version（version+1）。

## Acceptance criteria

- [ ] `kb digest` 产出当日 MD 日报文件（整理 Agent 产物）；`kb distribute`（渠道推送）留待 #08
- [ ] 四条审核规则全部生效，不合格条目落 status + review_reason
- [ ] 精确重复自动归档，疑似重复标 review
- [ ] 已发布条目不可覆盖，修订走 version+1
- [ ] 审核 + 去重逻辑有单元测试，覆盖 ≥ 80%

## Blocked by

- #01（骨架与 KBState）
