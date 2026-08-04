# 参与 Reforge 开发

[English](CONTRIBUTING.md) | 简体中文

给所有要读代码或提交补丁的人的快速上手指南。

## 环境准备

```bash
git clone <repo> && cd Reforge
python -m venv .venv && .venv\Scripts\activate    # Windows
pip install -e ".[dev]"                            # 可编辑安装 + ruff + pytest
pre-commit install                                  # 一次性安装 hook
cp .env.example .env                                # 填入你的 LLM key
```

跑测试：

```bash
python -m pytest reforge/tests --tb=short
```

整个测试套件用时远低于 2 分钟；具体通过数以 README 里的 CI 徽章为准。

## 硬性规则

这些规则靠 review 保证，并不总有测试兜底。

| 规则 | 原因 |
|---|---|
| `RuntimeState` 已**冻结** —— 不得新增字段 | 新状态进 `ExecutionEvent`，再从日志投影出来。加字段等于把 P26 专门要消灭的上帝状态模式又请回来。 |
| 源码文件里不要出现 emoji | 跨平台编码问题（比如 Windows GBK），何况 PR review 时也会被清掉。 |
| 单文件控制在 400 行以内 | 软性目标；接近这个数就该拆成子包。 |
| memory / tools / orchestration / evaluation 之间不得紧耦合 | 每一块都必须能被独立替换。 |
| `MemorySubstrate` Protocol 的方法名是 `write` / `recall` / `recall_for_planning` | 如果你的测试 mock 用了别的名字（`store`、`get`），测试会通过，但运行时会炸。 |
| 禁止在业务逻辑里硬编码提示词 | 从 `prompts/` 模块里取，这样迭代提示词只是改字符串，而不是改代码。 |
| 每新增一个生命周期动作，都要发出 hook / 事件 | 工具执行、重试、反思、记忆读写都算。 |

## 命名约定

| 种类 | 约定 | 示例 |
|---|---|---|
| 事件工厂函数 | snake_case 动词 | `execution_failed(...)` |
| Protocol 类 | 名词，或名词 + `able` | `CapabilityAware`、`MemorySubstrate` |
| Dataclass（状态） | PascalCase 名词 | `AgentCapability`、`ExecutionContext` |
| 测试文件 | `test_<feature>.py`；分阶段推进的工作用 `test_p<NN>_<feature>.py` | `test_sandbox_backends.py` |
| 测试类 | `Test<Aspect>`（一个类只覆盖一个切面） | `TestDockerBackendCommandShape` |

## 什么代码放哪里

| 你想…… | 就放到…… |
|---|---|
| 新增一个内置 skill | `reforge/runtime/skills/builtin/<name>.py`，并在 `default_skill_registry()` 里注册 |
| 新增一个 MCP server | 在 `reforge/runtime/skills/mcp/discovery.py` 里配置自动注册 |
| 新增一种事件类型 | `reforge/runtime/events/models.py` 加工厂函数 + `EventKind` 字面量 + 更新投影 |
| 新增一个 agent 角色 | `reforge/runtime/agents/<role>.py`，并显式携带 `AgentCapability` |
| 新增一个沙箱后端 | `reforge/runtime/infrastructure/execution/backends/<name>_backend.py`，实现 `SandboxBackend` Protocol |
| 新增一个 benchmark 用例 | `reforge/benchmark/cases.py` —— 保持精选，用例要能说明行为 |

## 测试标准

- **每个新文件都要在同一个 PR 里带上单元测试。**
- 条件允许时**优先真实 I/O 而非 mock** —— 参考
  `reforge/tests/integration/test_sandbox_chain.py` 的写法。
- **失败路径和成功路径同等重要** —— Reforge 一半的价值在于恢复能力，测试就应该覆盖它。
- **给慢速 / 依赖特定环境的测试打 marker**（`@pytest.mark.docker` 等），让 CI 能自行取舍。

## 提交 / PR 风格

- 一个 PR 只解决一件事。两件也行，前提是它们强耦合。
- 提交标题用祈使句，&lt;= 72 字符：写 `add DockerBackend`，不要写 `Added DockerBackend.`。
- 正文回答*为什么*，而不是*做了什么* —— *做了什么*看 diff 就知道了。
- 如果这次提交是在回退一次过度设计，请明确指出依据的是 `OWNERSHIP.md` 里的哪条归属规则。
