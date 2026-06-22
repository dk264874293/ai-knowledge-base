# 03 · Analyzer：三维度标注

## What to build

将 Analyzer stub 替换为真实分析：读 `knowledge/raw/` 未处理文件，经 `LLMClient` 做三维度标注 —— summary（Qwen-Max）/ tags（GLM-5.1）/ relevance_score（DeepSeek V3），写 `knowledge/articles/`（格式符合 AGENTS.md article 规范）。

`LLMClient` 统一封装多厂商路由，Analyzer 不感知底层厂商：

```python
class LLMClient:
    def generate_summary(self, content: str) -> str: ...
    def extract_tags(self, content: str) -> list[str]: ...
    def score_relevance(self, content: str) -> float: ...
```

## Acceptance criteria

- [ ] `kb analyze` 读 raw 产出 articles JSON，summary/tags/relevance_score 三维度齐全
- [ ] `LLMClient` 暴露统一接口（上述三方法），多厂商路由对 Analyzer 透明
- [ ] 模型名与 max_tokens 从 config.yaml 读，API Key 走 `.env`，不出现在代码或 YAML
- [ ] LLM 调用在测试中 mock 返回固定 JSON，验证解析正确性
- [ ] LLM 返回格式异常记 WARNING 并跳过该条，不中断整批

## Blocked by

- #01（骨架与 KBState）
