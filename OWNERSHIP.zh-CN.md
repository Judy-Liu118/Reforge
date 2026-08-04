
# 子系统职责边界

[English](OWNERSHIP.md) | 简体中文

本文档规定每个子系统**产出什么**、**消费什么**，以及**绝对不能做什么**。违反这些规则会
制造隐式耦合，让回放、测试和后续重构的难度指数级上升。

往任何子系统里加代码之前，先读这份文档。

---

## 职责归属总表

| 子系统 | 产出 | 消费 | 禁止 |
|---|---|---|---|
| **governor** | `PolicyDecision` | `TaskIntent`、`CapabilityPolicy` | 执行代码；生成计划；直接写 `RuntimeState` |
| **evaluation** | `EvaluationResult` | 执行输出（stdout/stderr/exit_code） | 做重试决策；修改任何状态 |
| **reflection** | `PlannerContext` | `EvaluationResult`、`TrajectoryStore` | 执行代码；做策略决策 |
| **research** | `ResearchResult`、`HypothesisRecord` | `RuntimeRunner`、`ResearchStore`、`MessageBus` | 依赖 governor；扩张启发式规则 |
| **tracing** | `TraceEvent`、`SpanContext` | 无（被动观察者） | 改变执行行为；参与决策 |
| **events** | `ExecutionEvent` | 无（由所有子系统发出） | 依赖任何运行时子系统（仅限标准库） |
| **tasks/workers** | `TaskResult`、`WorkerState` | `Task`、`WorkerPool` 路由 | 内含业务逻辑；访问 governor 或 evaluation |
| **agents/bus** | `RuntimeMessage`、`VoterResult` | `VerifierAgent`、`MessageBus`、`ActorContext` | 直接修改 `RuntimeState`；掌管执行生命周期 |
| **skills** | `SkillResult` | `SkillContext`、`SkillRegistry` | 直接修改 `RuntimeState`；做重试/策略决策；绕过 governor |
| **RuntimeState** | 当前执行的快照 | 所有图节点（优先只读） | **继续膨胀** —— 它已被冻结 |

---

## 各子系统详细规则

### governor

- 重试 / 停止 / 接受决策的**唯一权威**。
- 消费来自 evaluation、分类和能力策略的信号。
- 返回 `RuntimeDecision` —— 但**不**由它自己去执行这个决策。
- 不得直接调用 evaluation；evaluation 要么被注入，要么已提前跑完。

### evaluation

- **只提供信号**，不做决策。
- 产出 `EvaluationResult(passed, score, checks, failure_type)`。
- 拿这个信号做什么，由运行时（governor）决定。
- 不得触发重试、修改状态或访问记忆。

### reflection

- **上下文增强器** —— 通过注入历史让下一个计划更好。
- 读取 `TrajectoryStore` 和 `EvaluationResult` 来构建 `PlannerContext`。
- 不执行代码，也不做意图分类。

### research

- **调查运行时** —— 编排多轮假设验证。
- 通过 `MessageBus` 与校验 agent 通信。
- 不得堆积新的启发式规则 —— 要深度，不要广度。
- `ResearchSession` 就是边界：调用方走 session，不碰内部实现。

### tracing / 可观测性

- **被动观察者** —— 只读取和记录。
- `TraceCollector` 和 `ExecutionEventLog` 只接收事件，从不主动推送。
- 不得影响被观察的代码路径。
- `SpanContext` 传播是唯一带"主动"色彩的部分 —— 它必须保持按需开启。

### events（ExecutionEvent）

- **地基层** —— 零运行时依赖（仅限标准库）。
- 任何子系统**都可以**发事件；（目前）没有任何子系统**必须**消费事件。
- 方向：随着 RuntimeState 冻结，事件将成为主要的事实记录。
- `FailureCategory` + `semantic_meaning` 是运行时学习所用的词表。
- **例外**：`emitters.py` 虽然放在 events/ 包里，但会 import `RuntimeState` 来给 `NodeFn`
  做类型标注。它属于图的桥接层，不是事件模型的一部分。"仅限标准库"这条约束适用于
  `models.py`、`log.py`、`replay.py`、`projection.py`、`persistent_log.py`、`observer.py`
  和 `categorizer.py`。

### tasks / workers

- **纯执行基座** —— 不含业务逻辑。
- `TaskScheduler` 和 `WorkerOrchestrator` 对 agent 和 research 一无所知。
- `WorkerPool` 仅按类型字符串路由，不带任何语义理解。
- 结果是 `TaskResult` dataclass；后处理是调用方的事。

### agents / bus

- **协调层** —— 路由消息、汇总投票、包装 agent。
- `MessageBus` 只懂路由规则，不懂语义内容。
- `VerifierVoter` 用严格多数汇总结果 —— 不调用 LLM。
- `AgentRegistry` 把 `(role, variant)` 映射到具体实现 —— 不含策略逻辑。

### skills

- **能力包装层** —— 每个 Skill 封装一项类型化能力（沙箱、文件读取、网页搜索、MCP 调用）。
- 同一个 Protocol 支持两种调用范式：
  * 代码即行动：LLM 生成的 Python 把 skill 当库来 import；
  * 工具即行动：LLM 发出 OpenAI function-call，运行时派发到
    `SkillRegistry.get(name).invoke(params, ctx)`。
- `SkillContext` 是**唯一**传入的对象：没有 `RuntimeState`，没有 governor 句柄，没有事件
  日志写入器。副作用只能通过 `SkillResult` 向外流出；由运行时层负责把这次调用用事件包起来。
- `SkillRegistry` 只做查找 + 导出 OpenAI schema，不含策略逻辑。
- 一个 skill **可以**抛异常、**可以**做 I/O、**可以**耗时（但要遵守 `context.timeout_s`）。
  它**绝不能**决定是否重试、修改任何状态，或调用 governor。

---

## RuntimeState —— 已冻结

`RuntimeState` 不得继续膨胀。

**现有字段即最终形态。** 任何新的执行状态都**必须**进入发往 `ExecutionEventLog` 的
`ExecutionEvent`。

### 为什么

`RuntimeState` 最初只是一块小小的工作流黑板。如今它已有 16 个顶层字段和 4 个嵌套子状态。
继续膨胀会导致：

- 归属模糊：说不清哪个节点"拥有"哪个字段；
- 隐式耦合：节点 A 通过共享对象读取节点 B 写入的状态；
- 回放困难：想复原当时发生了什么，得把整个对象读一遍；
- 测试摩擦：为了构造出待测状态，测试不得不去设置一堆无关字段。

### 方向：事件溯源运行时

`RuntimeState` 应当变成一个**事件投影** —— 由有序的 `ExecutionEvent` 事实流重建出来的
派生视图。

中间步骤（不要冒进）：
1. 所有新状态 → `ExecutionEvent`（当前所处阶段）
2. 图节点在改写状态的同时发出事件
3. 逐步把状态读取替换为事件查询
4. 最终 `RuntimeState` 只是一个轻量投影助手，不再是首要事实来源

### 投影 vs 同源镜像 vs 双写

"顶层禁止双写扁平字段"这条契约（`test_state_no_flat_fields.py`）管的是 RuntimeState 的
**顶层**。而**子状态内部**那些同样携带了事件日志中已有值的字段，在本规则之前一直没有分类。
一旦一个字段的值也存在于事件日志里，它必属于以下三级之一 —— 只有第一级才是合法的派生字段。

**一个派生字段合法（属于_投影_）当且仅当：**

0. **（前置）真相源存在。** 投影性质**不是**字段的固有属性，而是由运行时的**装配方式**赋予的。
   经 `RuntimeRunner` 装配时真相源（事件日志）存在；此前提不成立时，下面三条全部不适用。
   **真相源是否存在由装配路径决定，不由字段定义决定。**
1. **真相源单向：** 事件先写，投影后更 —— 这条成立本身依赖条件 0。
2. **可重建性：** 字段能从真相源完整重建，反向不成立。
3. **裁决确定：** 不一致时答案是确定的 —— 以事件为准。*该确定性目前由 emitter 每次全量重算
   的路径保证、并由测试验证，而**非**由运行时仲裁器执行；`check_state_consistency` 只报告
   不一致，从不纠正。*

| 级别 | 定义 | 成员 | 风险 / 裁决 |
|---|---|---|---|
| **投影**（合法） | 可从源事件完整重建；满足全部三条件 | `control_state.retry_count` —— 登记于 `tests/test_projected_fields.py::PROJECTED_FIELDS` | 可丢弃缓存；不一致时以事件为准 |
| **同源镜像**（受限） | 由**同一函数内同一局部变量**同时写入事件与字段；**不可**从日志重建 | `control_state.retry_decision_action`（`emitters.py:327`）；`semantic_state.reflection_summary`（`emitters.py:273-282`）；`semantic_state.evaluation_result` 的 score/passed（`emitters.py:196-233`） | 一致性靠"写两遍"保证；**写入点出错时无法靠重建检测** —— 这正是它区别于投影之处 |
| **双写**（禁止） | 两个共同权威各自独立写，无单向真相源 | 无（由 `test_state_no_flat_fields.py` 的 deny-list 拦截） | 真相源不唯一，不可恢复 |

**为什么三条件必须合起来 —— 缺一不可。** 去掉可重建性，字段退化为**同源镜像**：happy-path
上仍一致，但写错了无法检测，因为没有东西可供重建。去掉单向性，它退化为**双写**：两个权威之间
无从裁决。只有三条合起来，才使字段成为事件日志随时可以推翻的可丢弃缓存 —— 而这恰恰就是投影
区别于双写的地方。

**装配边界（前提在哪里被保证）。** 经 `RuntimeRunner` 装配时，`retry_count` 是事件日志的投影；
走 legacy 路径 `build_graph(event_log=None)` 时，它退化为节点本地计数，条件 1–3 不适用。这个保证
在 `RuntimeRunner.__init__`（`runner.py:46` —— always-active log），**不在图层**。`build_graph`
与 emitter **有意**支持 `event_log=None`（由 `test_always_active_event_log.py` 及
`test_emitter_policy_decision.py` 的 legacy 测试覆盖），因此**不**在图层加运行时硬断言 —— 断言会
砸掉一个被有意支持的模式。该不变式改由本文档记录、并从 `runner.py:46` 处交叉引用。

**流程要求 —— 新增子状态派生字段时。** 先判断它属于三级中的哪一级：

- **投影**：**必须**登记进 `PROJECTED_FIELDS`，并通过重建性测试后方可合入；
- **同源镜像**：**必须**在该字段的写入点注明其事件同源关系，并说明为何不可重建；
- **双写**：禁止。

**范围诚实说明（这是一道闸，不是免责声明）。** 契约测试只对**已登记**字段上锁，无法自动探测
"新增了一个未登记的投影形态字段" —— 那需要语义判断。这道闸由上述流程要求与 code review 承担，
**不**由测试承担。不要把契约测试跑绿理解为"不存在未登记的投影字段"。

---

## 需要避免的反模式

| 反模式 | 危害 |
|---|---|
| 子系统 A 调用子系统 B 的内部函数 | 制造隐式耦合；破坏独立可替换性 |
| 往 `RuntimeState` 上加字段 | 上帝对象继续膨胀；职责归属变得不清 |
| 让 evaluation 做重试决策 | 把信号生产和策略权威混为一谈 |
| 让 research 堆积关键词启发式 | 规则一锅粥；复杂度涨了，智能没涨 |
| 追踪代码改变执行行为 | 观察者效应；可观测性的目的被自己抵消 |
| 在 `WorkerPool` / `TaskScheduler` 里写业务逻辑 | 基座应当与策略无关 |
