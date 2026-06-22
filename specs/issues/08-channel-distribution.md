# 08 · 渠道分发：Telegram + 飞书（kb distribute）

## What to build

实现 AGENTS.md 的 `distributor` 模块：将当日 `status: published` 条目批量推送到 Telegram 与飞书。`kb distribute` 支持 `--channel telegram|feishu` 或全渠道；按 AGENTS.md 模板渲染（Telegram 用 MarkdownV2，飞书用 Interactive Card + 可点击链接按钮）。

**幂等与红线**：以 `batch_id` + 条目 id 做推送幂等 —— 已出现在 `distributed_to` 的条目不重复推送（红线 #4 / #6）；推送成功后将渠道名追加进 article 的 `distributed_to` 字段（version 不变，仅更新该字段）。HTTP 超时 10s、固定间隔 5s 重试 2 次；重试耗尽写入 `State.errors`。

> 推送定时（每日 07:00 / UTC 23:00）的 cron 触发不在本切片范围，见 README「延后项」。

## Acceptance criteria

- [ ] `kb distribute --channel telegram` 按 MarkdownV2 模板推送当日 published 条目
- [ ] `kb distribute --channel feishu` 按 Interactive Card 模板推送
- [ ] `kb distribute`（无参数）全渠道推送
- [ ] 推送成功后渠道名写入 article 的 `distributed_to`，不覆盖其它字段
- [ ] 已在 `distributed_to` 的条目不重复推送（幂等）
- [ ] HTTP 失败固定间隔 5s 重试 2 次，耗尽后写入 `State.errors`，不中断其它条目
- [ ] Telegram/飞书调用在测试中 mock，覆盖率 ≥ 80%
- [ ] API Key / Webhook URL 走 `.env`，不出现在代码或 YAML

## Blocked by

- #04（ Organizer 产出 published 条目 + digest）
