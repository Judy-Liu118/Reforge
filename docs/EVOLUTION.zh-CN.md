# Reforge —— 架构与演进档案

[English](EVOLUTION.md) | 简体中文

> 本文件把 `ARCHITECTURE.md`、`TASKS.md`、`RUNTIME_ARCHITECTURE_REVIEW.md`、
> `docs/ARCHITECTURE_VISION.md`、`docs/RESEARCH_RUNTIME_ROADMAP.md` 和
> `docs/RUNTIME_REVIEW.md` 合并成一份历史档案。当前生效的规范性文档是：
> `README.md`、`CLAUDE.md`、`OWNERSHIP.md`。

---

## 第一部分 —— 架构愿景

Reforge 是一个**执行运行时基座**，不是聊天机器人。核心抽象：

```
LLM + Execution + Reflection + Policy + Runtime Memory
```

它把「执行」当作一等公民级的推理原语。长期方向：

```
执行运行时 → 反思式运行时 → 研究型运行时 → 自主调查基座
```

### 核心运行时分层

| 层 | 概念 | 掌管 |
|---|---|---|
| 执行层 | `exec_state` | 沙箱、stdout/stderr、超时、trace |
| 语义层 | `semantic_state` | 意图、反思、评估、恢复推理 |
| 控制层 | `control_state` | 重试策略、能力门禁、governor 决策 |
| 结局层 | `outcome_state` | 任务结局、恢复状态、完成语义 |

### LangGraph 的角色（受限）

LangGraph 负责：编排、节点转移、条件路由。
LangGraph **不**负责：记忆、反思、重试策略、评估、工具抽象、可观测性、运行时策略。
这些都各自独立实现。

### 非目标

不做聊天 UX，不做通用助手，不做 function-calling 演示，不做 benchmark 跑分框架。
重心放在运行时智能和执行基座的质量上。

---

## 第二部分 —— 阶段史（P1 → P36+）

### 第 1 阶段 —— 自愈运行时地基 ✅

* P1 重试循环 —— 感知执行的恢复能力、基于反思的重新生成
* P2 意图感知的失败语义 —— 任务保真度优先于原始执行成功
* P3 结局解析器 —— SUCCESS / RECOVERED / EXPECTED_FAILURE / DENIED / FAILED
* P4 能力治理 —— 三层安全（请求门禁、AST 守卫、完整性守卫）
* P5 运行时状态分离 —— 嵌套子状态（exec / control / semantic / outcome），带双写兼容
* P6 运行时收敛 —— Governor 掌管决策，workflow 只做路由；新增 5 个沙箱集成测试
* P7 Governor 流水线可组合化 —— RuntimeStage Protocol，4 个独立阶段
* P7 执行记忆 —— 逐次运行的 failure_mode + repair_strategy 落 JSONL，注入 `recall_similar()`

成果：65 个测试通过，governor 决策流统一。

### 第 2 阶段 —— 反思式运行时 ✅

* P8 MemorySubstrate Protocol + TrajectoryStore + 感知召回的规划（88 个测试）
* P9 多步任务分解 —— TaskDecomposer + SubtaskRunner + MultiStepTrajectory（111 个测试）
* P10 异步执行 + 并行子任务 —— AsyncSubtaskRunner + 拓扑调度（129 个测试）
* P11 运行时评估改进 —— retry_drift、output_contains_data、EvaluationFeedback（147 个测试）
* P12 逐次尝试的评估追踪 + 历史查询（164 个测试）

### 第 3 阶段 —— 研究型运行时 ✅

* P13 研究运行时 —— ResearchSession / ResearchPlanner / EvidenceAggregator（209 个测试）
* P14 研究 CLI 集成 + 持久化 ResearchStore（237 个测试）
* P15 自适应研究 —— HypothesisRanker + ResearchMemory + 自适应退出（267 个测试）
* P16 研究质量与导出 —— ResearchReporter + `--export-research` + research_output_quality（303 个测试）

### P-R —— 运行时收敛 ✅

清理了四类积压的债务：

1. `graph/workflow.py` 从 455 行降到 67 行；8 个节点拆到 `graph/nodes/*.py`，每个 ≤ 100 行
2. 提示词外置到 `models/prompts/directives.py`；约束抽取归 `runtime/requirements.py` 负责
3. 移除 8 个为向后兼容保留的 RuntimeState 扁平字段（retry_count / task_intent /
   task_outcome / outcome_reason / final_answer / execution_status / task_status /
   decision_reason）；ExecutionStatus / TaskStatus 枚举一并删除
4. `MemorySubstrate` Protocol 通过 `build_graph(memory_substrate=...)` 和
   `RuntimeRunner.__init__` 端到端打通
5. 记忆持久化统一收敛到 `data/` 之下；旧路径自动迁移
6. 3 个契约测试文件把不变量冻住

319 个测试通过（303 + 16 个契约测试）。

### 第 4 阶段 —— 多智能体运行时 ✅（P17–P18+）

* P17 多智能体骨架 + 研究编排 —— 基于 Protocol 的 PlannerAgent / VerifierAgent /
  SynthesizerAgent + ResearchOrchestrator（并行校验 + worker 隔离）（346 个测试）
* P18 多智能体转正 —— AgentRegistry + MessageBus + 多校验者共识 + 按 agent 划分的 trace span

### 第 5 阶段 —— 事件溯源运行时（P19 → P36）

* P19–P25 追踪 + 任务图 + Workers + 事件 + 持久化事件日志 + 会话回放
* P26–P33 状态投影 + 一致性校验 + 把 retry_count / eval / policy_decision 迁移到事件日志；
  EventLog 常驻启用；移除节点侧状态改写；一致性校验全面集成
* P34 PersistentEventLog（JSONL 持久化，可直接替换的子类，高负载下线程安全）
* P35 事件 CLI（`--events-list`、`--events-show`、`--events-summary`）
* P36 事件订阅 / Hook 系统 —— `subscribe()` / `unsubscribe()` / `SubscriptionHandle`；
  订阅者在锁外调用，错误相互隔离

归档时（2026 年 6 月）**1317 个测试通过**。

---

## 第三部分 —— 历史检视记录

摘自 `RUNTIME_REVIEW.md`。其中若干条目此后已被解决，或重新划定了范围；保留在这里是为了
留存团队的集体记忆。

### 反复出现的主题

> 「运行时抽象的增长速度，超过了运行时架构的收敛速度。」

这不是功能完备度的问题 —— 是基座稳定性的问题。

### 过往建议（截至 P36 的状态）

| 条目 | 状态 |
|---|---|
| 完成 RuntimeState 的嵌套化迁移 | ✅ 已在 P-R / P26 → P33 完成 |
| 分解逻辑的运行时化（SubtaskRuntimeState） | ✅ 已在 P10 完成 |
| CapabilityPolicy 的命名要诚实（底层仍是正则/关键词） | ✅ 已完成 —— 主类改名为 `SemanticSafetyGuard`，模块 docstring 明确写着「仅基于关键词/正则的启发式。无 LLM，无真正的沙箱隔离。」 |
| 失败指纹的结构化签名 | ✅ 已完成 —— `memory/fingerprint.py` |
| 运行时包分层（domain / orchestration / infrastructure） | ✅ 已完成 |
| CLI main.py 拆解 | ✅ 已在 `cli/commands/` 完成 |
| 运行时级别的集成测试 | ✅ 已完成 —— `tests/integration/test_runtime_chains.py` 等 |
| 停止新增大型子系统，收敛运行时架构 | ⚠️ 持续需要自律 |
| 项目定位为「自愈执行运行时」而非「通用 agent」 | ✅ 已采纳（见 README） |

### 尚未收口的架构方向

剩余的迁移工作（通过新增 `ExecutionEvent` 来推进；`RuntimeState` 已冻结）：

> RuntimeState → 事件投影。
> 步骤：所有新状态进 ExecutionEvent → 图节点在改写状态的同时发出事件 →
> 把状态读取替换为事件查询 → RuntimeState 退化为轻量投影助手。

当前的归属规则见 `OWNERSHIP.md`，以及 CLAUDE.md 中的「RuntimeState —— FROZEN」一节。
