# 01 · 端到端骨架 happy-path（tracer bullet）

## What to build

用 mock 数据打通整条链路的最小纵向切片：建立 `kb` CLI（typer）、LangGraph 线性图（collector → analyzer → organizer）、`KBState` 状态对象、配置加载（config.yaml + Pydantic Settings）、持久化目录。三个节点先用 stub（返回固定假数据），但要真实地写 `knowledge/raw/` 与 `knowledge/articles/`。

目标：`kb collect` 能跑通并落盘一个 raw 文件，证明 **CLI → 图 → State → 文件** 的端到端骨架成立。这是后续填充真实实现的脚手架。

关键决策（schema 级，inline 自 AGENTS.md，需在实现中遵守）：

```python
class KBState(TypedDict):
    batch_id: str                  # YYYYMMDD
    raw_items: list[dict]
    articles: list[dict]
    distributed: list[dict]
    errors: list[dict]
```

每个节点接收整个 State，只修改自己负责的字段；线性 pipeline 不打回。

## Acceptance criteria

- [ ] `pyproject.toml` 注册 `kb` 入口，`kb --help` 可用
- [ ] `config.yaml` + Pydantic Settings 加载通过，敏感 Key 走 `.env`
- [ ] `kb collect`（全 mock）跑通，`knowledge/raw/` 产出文件
- [ ] `kb analyze`（stub）跑通，`knowledge/articles/` 产出文件
- [ ] `kb digest`（stub）跑通，产出当日 MD 日报文件
- [ ] 一次完整运行贯穿 collector → analyzer → organizer 三节点全部写盘，证明端到端骨架成立
- [ ] `KBState` 在三节点间正确流转，每节点只改自己负责字段
- [ ] 禁止裸 `print()`，统一 `logging`（格式见 AGENTS.md）
- [ ] 单元测试覆盖 State 流转（≥1 测试）

## Blocked by

None — can start immediately（参考 #00 ADR 的 State 字段定义）。
