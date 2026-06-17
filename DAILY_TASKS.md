# DAILY_TASKS

## CURRENT

### P36 Event Subscriber / Hook System ✅

**1096 tests passing**（1073 → 1096，+23 新测试）。ExecutionEventLog 具备 pub/sub 能力。

* [x] P36.1 `runtime/events/log.py` — 新增 `SubscriberFn` 类型别名、`SubscriptionHandle` frozen-slot 类（`cancel()`）、`_subscribers` dict + `_next_sub_id`；`subscribe()` / `unsubscribe()` / `_notify_subscribers()`；`append()` 末尾调用 `_notify_subscribers()`（锁外，防死锁）
* [x] P36.2 `runtime/events/persistent_log.py` — `append()` 末尾在 `if not self._loading` 内调用 `_notify_subscribers()`；load 期间不触发订阅（仅重建内存索引）
* [x] P36.3 `events/__init__.py` 导出 `SubscriptionHandle`
* [x] P36.4 `tests/test_p36_event_subscribers.py` — 23 个测试：基本订阅、Handle 生命周期、错误隔离、多订阅者、PersistentLog 集成、load 隔离、并发安全

### 关键设计点
- 订阅者在锁外通知（先快照 `list(self._subscribers.values())`），防止订阅者自身调用 `append()` 时死锁
- 订阅者异常被吞掉（`except Exception: pass`），确保任何回调不影响运行时
- `load()` 重建期间不通知（`_loading=True` 屏蔽磁盘写和通知），旧事件不当成新事件广播
- 两个独立 Handle 互不影响，`cancel()` 幂等

---

### P35 Events CLI Monitoring ✅

**1073 tests passing**（1046 → 1073，+27 新测试）。EventLog 可从 CLI 查看。

* [x] P35.1 `cli/events.py` — `handle_events_list(path?)` / `handle_events_show(sid, path?)` / `handle_events_summary(path?)` 三个处理函数；接受可选 `path` 参数（便于测试）；使用 `PersistentEventLog.load()` + `SessionReplay`
* [x] P35.2 `cli/main.py` 接入：`--events-list` / `--events-show <id>` / `--events-summary` 标志；`_run_task()` 和 multistep sequential runner 改为使用 `PersistentEventLog(DEFAULT_EVENT_LOG_PATH)` 注入 runner
* [x] P35.3 `tests/test_p35_events_cli.py` — 27 个测试，4 类场景：list/show/summary/edge cases

### 关键设计点
- `handle_*` 函数接受 `path: Path | None` 参数，无需 mock，`tmp_path` 直接注入
- `SessionReplay.render()` 复用 P25 的渲染逻辑，无重复
- `DEFAULT_EVENT_LOG_PATH` 定义在 `cli/events.py`，main.py 直接 import，单一来源

---

### P34 PersistentEventLog JSONL Persistence ✅

**1046 tests passing**（1020 → 1046，+26 新测试）。EventLog 持久化就位。

* [x] P34.1 `runtime/events/persistent_log.py` — `PersistentEventLog(ExecutionEventLog)` 子类；`append()` 在同一把锁下完成内存更新 + 磁盘写入（保证并发安全）；JSONL 格式（`dataclasses.asdict` + `json.dumps`）
* [x] P34.2 `PersistentEventLog.load(path)` — 从磁盘重建完整日志；`_loading` 标志防止 load 期间二次写盘；跳过损坏行（resilience）；返回空日志当文件不存在
* [x] P34.3 导出 `PersistentEventLog` 进入 `events/__init__.py`
* [x] P34.4 `tests/test_p34_persistent_event_log.py` — 26 个测试，7 类场景：基础持久化、load 字段完整性、容错、多 session、drop-in 兼容性、并发安全、路径管理

### 关键设计点
- `PersistentEventLog` 是 `ExecutionEventLog` 的 drop-in 子类，不改变任何接口
- 磁盘写在锁内（lock → memory update + disk write 原子性），50 并发线程测试通过
- `load()` 用 `_loading` 标志跳过回写，避免重建时二次追加到文件
- 格式与 `TrajectoryStore` 一致（JSONL，每行一条记录）

---

### P17 Multi-Agent Stub + Research Orchestration ✅

**346 tests passing**（319 → 346，+27 新测试）。Multi-Agent 基础就位。

* [x] P17.1 `runtime/research/orchestrator.py` — `ResearchOrchestrator` 用 ThreadPoolExecutor 并行验证一批独立假设；错误隔离（单个失败不中断整批）；保留输入顺序
* [x] P17.2 `runtime/agents/role.py` — 三个 Protocol（`PlannerAgent` / `VerifierAgent` / `SynthesizerAgent`）+ `SynthesisResult` 返回类型；默认 adapter `RunnerVerifier` 与 `DefaultSynthesizer` 分别在 `agents/verifier.py` 和 `agents/synthesizer.py`
* [x] P17.3 `ResearchSession.__init__` 新增 `verifier` / `synthesizer` 注入与 `parallel_verification: bool = False`、`max_workers: int = 4`；默认行为不变（向后兼容）
* [x] P17.4 `RunnerVerifier(runner_factory=...)` 模式：每次 `verify()` 调用都从 factory 新建独立 `RuntimeRunner`（独立 session_id + 可独立注入 substrate）；与 `runner=...` 互斥
* [x] P17.5 并行集成测试：`test_p17_parallel_research.py` 覆盖 barrier 强制并发 / round metadata / 串行-并行结果一致性 / worker 隔离 / synthesizer 端到端

### 关键设计点
- 三个 Protocol 是 `@runtime_checkable`，现有 `ResearchPlanner` 直接满足 `PlannerAgent`（无需 adapter）
- `RunnerVerifier` 两种 ownership 模式（`runner=shared` vs `runner_factory=per-call`）互斥，避免歧义
- 并行模式下 `stream()` 仍按 round 顺序 yield，但同一 round 内的 hypothesis 是 batch 完成后一起 yield（不再逐个流式）
- `agents/role.py` 用 `TYPE_CHECKING` 隔离 `research.models` 避免循环 import

---

## COMPLETED

### P-R Runtime Consolidation ✅

P16 完成节点全局架构审计后的工程债收尾。**319 tests passing**（303 + 16 新契约测试）。

* [x] P-R.1 拆分 `graph/workflow.py`（455 → 67 行）到 `graph/nodes/{planner,codegen,execution,reflection,evaluation,retry_decision,final_response,capability}.py`，每个 ≤ 100 行；`build_graph()` 支持 `memory_substrate` 注入
* [x] P-R.2 抽出 `models/prompts/directives.py`（CONSTRAINT_VIOLATION / MUST_FAIL_FIRST_OVERRIDE / EXPECTS_UNCAUGHT_OVERRIDE + regex patterns）；`runtime/requirements.py` 承载 `extract_requirements()` —— graph 不再硬编码 prompt 字符串
* [x] P-R.3 删除 `RuntimeState` 8 个 backward-compat flat 字段（retry_count / task_intent / task_outcome / outcome_reason / final_answer / execution_status / task_status / decision_reason），全代码库 grep 替换为 `state.<nested>.<field>`；删除 `ExecutionStatus` / `TaskStatus` enum
* [x] P-R.4 激活 `MemorySubstrate` Protocol：`build_graph(memory_substrate=...)` → planner/reflection 节点通过闭包注入；`RuntimeRunner.__init__(memory_substrate=...)` 暴露 DI 入口
* [x] P-R.5 Memory 存储统一：`MemoryStore` 默认路径 `data/memory/`，自动从遗留 `<root>/memory/` 一次性迁移；新增 `reforge/memory/README.md` 阐述四种记忆职责（MemoryRecord / ExecutionRecord / TrajectoryRecord / ResearchResult）
* [x] P-R.6 新增契约测试：`test_pr_workflow_split.py`（拆分 + 行数预算）、`test_pr_state_nested_only.py`（flat 字段已删除）、`test_pr_substrate_injection.py`（DI 验证）

---

## COMPLETED

### P16 Research Quality + Export ✅

* [x] ResearchReporter Markdown 渲染
* [x] CLI --export-research + ResearchStore.find_by_id
* [x] question_context + research_output_quality 检查
* [x] 303 tests passing

### P15 Adaptive Research — Evidence-Driven Hypothesis Refinement ✅

* [x] P15.1 `HypothesisRanker`：confirmed 关键词重叠 +2/词、rationale 加分 +1、近重复项直接惩罚（返回固定低分 -3.0），优先测试高潜力假设
* [x] P15.2 `ResearchMemory`：基于 `ResearchStore` 的查询视图，`recall_patterns()` 返回相似研究中的 confirmed/rejected 模式（无独立存储）
* [x] P15.3 `ResearchPlanner` 注入 `ResearchMemory`：规划新假设时注入跨 session 历史模式，避免重复已知结论
* [x] P15.4 自适应退出：`confirmed_exit_threshold=0.7`（可配置），confirmed 比例 ≥ 阈值时在 round 2+ 提前终止；`_should_exit()` 函数独立可测
* [x] P15.5 30 个新测试，共 **267 tests passing**；ranker + 自适应退出同时接入 `run()` 和 `stream()`

---

## COMPLETED

### P15 Adaptive Research ✅

* [x] HypothesisRanker + ResearchMemory + 自适应退出
* [x] ResearchPlanner 跨 session 历史注入
* [x] 267 tests passing

### P14 Research CLI Integration + Persistent Research Store ✅

* [x] P14.1 CLI 自动检测研究类问题（英文 why/what causes/investigate/how does，中文 为什么/研究/调查等）→ `is_research_question()` + `_run_task()` 路由到 `run_research()`
* [x] P14.2 `ResearchStore`（JSONL，`data/research.jsonl`）：`save()` / `list_all()` / `find_by_question()`（关键词重叠评分）；格式错误行自动跳过
* [x] P14.3 CLI `--research-history` 展示历史研究：question / rounds / confirmed / contradictions 表格
* [x] P14.4 `ResearchSession.__init__` 新增 `trajectory_store` 参数 → 注入到 `RuntimeRunner`；自定义 runner 时不覆盖
* [x] P14.5 28 个新测试，共 **237 tests passing**；formatter 新增 `format_research_*` 系列函数；`reforge/cli/research.py` 模块分离保持 main.py 精简

---

## COMPLETED

### P14 Research CLI Integration ✅

* [x] CLI 研究路由 + is_research_question 检测
* [x] ResearchStore JSONL 持久化
* [x] --research-history 历史展示
* [x] ResearchSession + TrajectoryStore 集成
* [x] 237 tests passing

### P13 Research Runtime — Iterative Investigation Loop ✅

* [x] P13.1 `ResearchPlanner`：LLM 将开放性问题分解为 2-3 个可执行假设（hypothesis + rationale + verification_request），支持 prior_findings 注入
* [x] P13.2 `HypothesisRecord`：结构化假设存储（hypothesis_id, hypothesis, status: pending/confirmed/rejected/inconclusive, evidence, confidence, round_number）
* [x] P13.3 `EvidenceAggregator`：启发式 status 更新（exit_code + output 长度 + error 关键词）；关键词重叠 ≥3 词的 confirmed/rejected 对自动标记为矛盾
* [x] P13.4 `ResearchSession`：max_rounds 多轮调查循环，round N confirmed 证据注入 round N+1 prior_findings；全轮无 pending/inconclusive 时提前退出；支持 `stream()` 逐假设迭代
* [x] P13.5 45 个新测试（模型/聚合器/规划器/会话），共 **209 tests passing**；`RESEARCH_PLANNER_SYSTEM` prompt 加入 templates.py

---

## COMPLETED

### P13 Research Runtime ✅

* [x] ResearchPlanner + ResearchSession + EvidenceAggregator
* [x] HypothesisRecord (pending/confirmed/rejected/inconclusive)
* [x] 矛盾检测 + prior findings 跨轮注入
* [x] 209 tests passing

### P12 Per-Attempt Evaluation Tracking + History Query ✅

* [x] AttemptRecord eval 字段 + evaluation_node 全时段运行
* [x] CLI eval 趋势显示 + TrajectoryStore 评估模式查询
* [x] ClassifyStage 评估历史学习闭环
* [x] 164 tests passing

### P12 Per-Attempt Evaluation Tracking + History Query ✅

* [x] AttemptRecord eval 字段 + evaluation_node 全时段运行
* [x] CLI eval 趋势显示 + TrajectoryStore 评估模式查询
* [x] ClassifyStage 评估历史学习闭环
* [x] 164 tests passing

### P11 Runtime Evaluation Improvements ✅

* [x] P11.1 HeuristicEvaluator 新增 `retry_drift` 检查（同错误跨尝试重复触发 retry drift 警告）
* [x] P11.1 HeuristicEvaluator 新增 `output_contains_data` 检查（数据任务输出过短）
* [x] P11.2 `AttemptStep` 新增 `eval_score` / `eval_failure_type` 字段，`from_final_state` 填充最后尝试评估结果
* [x] P11.3 新建 `runtime/evaluation/feedback.py`：10 个 check → 可操作修复指令的精准映射
* [x] P11.4 `retry_context.py` 改用 `format_eval_feedback()`（由 terse 字符串升级为分项指令）
* [x] P11.5 18 个新测试，共 147 tests passing；`evaluation/__init__.py` 导出补全

---

## COMPLETED

### P11 Runtime Evaluation Improvements ✅

* [x] retry_drift + output_contains_data 检查
* [x] AttemptStep 评估分数追踪
* [x] EvaluationFeedback 精准指令映射
* [x] 147 tests passing

### P10 Async Execution + Parallel Subtasks ✅

* [x] P10.1 AsyncSubtaskRunner（ThreadPoolExecutor，并行独立子任务）
* [x] P10.2 _group_by_levels 拓扑排序（支持菱形依赖、线性链、混合图）
* [x] P10.3 _enrich_subtask 上下文传播（前驱 final_answer 注入依赖子任务 prompt）
* [x] P10.4 DECOMPOSER_SYSTEM + _parse_response 支持 depends_on 字段
* [x] P10.4 CLI 并行感知：检测并行级别，显示 "[ Parallel ]" 指示，并行任务用 AsyncSubtaskRunner
* [x] P10.5 18 个新测试（依赖图、上下文传播、AsyncSubtaskRunner），共 129 tests passing
* [x] 清理：修复双重 decompose LLM 调用 bug，修复 reflection/__init__.py 导出，TrajectoryStore 多步路径隔离

---

## COMPLETED

### P10 Async Execution + Parallel Subtasks ✅

* [x] AsyncSubtaskRunner + 依赖图拓扑排序
* [x] SubtaskResult 上下文传播
* [x] CLI 并行执行集成
* [x] 129 tests passing

### P9 Multi-Step Task Decomposition ✅

* [x] P9.1 TaskDecomposer（启发式正则 + LLM 分类，无信号时零 LLM 开销）
* [x] P9.2 SubtaskRunner（顺序执行子任务，stream_all / run_all 双接口）
* [x] P9.3 MultiStepTrajectory + TrajectoryStore.save_multistep()（聚合轨迹）
* [x] P9.4 CLI 分解感知：_run_task 自动路由，_run_multistep_task 显示逐步进度
* [x] P9.5 23 个新测试，共 111 tests passing
* [x] DECOMPOSER_SYSTEM prompt 加入 templates.py

---

## COMPLETED

### P9 Multi-Step Task Decomposition ✅

* [x] TaskDecomposer + SubtaskRunner + MultiStepTrajectory
* [x] CLI 分解感知执行（自动路由单步/多步）
* [x] 111 tests passing

### P8 Reflective Runtime Foundation ✅

* [x] P8.0 修复 memory/models.py MemoryRecord 重复字段 Bug（problem_signature 被 Pydantic 静默丢弃）
* [x] P8.1 Memory Substrate Protocol（MemorySubstrate + CompositeMemorySubstrate）
* [x] P8.2 Execution Trajectory Store（TrajectoryRecord / AttemptStep / TrajectoryStore，JSONL）
* [x] P8.2.5 RuntimeRunner 集成 TrajectoryStore（final_response 节点自动保存轨迹）
* [x] P8.3 ExecutionMemory 召回改进（加权评分替换硬过滤，新增 problem_signature 字段）
* [x] P8.4 PlannerMemoryContext + 检索增强规划（_planner_node 注入历史经验上下文）
* [x] 88 tests passing

---

## COMPLETED

### P8 Reflective Runtime Foundation ✅

* [x] MemorySubstrate Protocol（统一内存接口）
* [x] TrajectoryStore（跨尝试语义轨迹追踪）
* [x] Pattern-Based Memory Recall（problem_signature 加权召回）
* [x] Retrieval-Aware Planning（检索增强规划）
* [x] 88 tests passing

### P7 Governor Pipeline + Execution Memory ✅

* [x] P7.1 Clear RuntimeState duplicate fields
* [x] P7.2 Governor Pipeline with 4 stages
* [x] P7.3 ExecutionMemory record + recall
* [x] 65 tests passing

### P6 Runtime Consolidation ✅

* [x] Consolidate governor decision flow
* [x] Remove remaining flat-field writes
* [x] Add real sandbox integration tests
* [x] Improve trace readability for nested runtime state

---

## NEXT

### P18 Multi-Agent Runtime — Stage 4 起点

P17 已提供 `AgentRole` Protocol 与并行 orchestrator。下一步把 agent 角色提升为 first-class runtime concept。

**实现约束（所有子任务均适用）：**
- `workflow.py` 不允许重新膨胀；orchestration 保持 thin
- message bus 只承担路由 / 分发 / 投递，不承担 retry / evaluation / synthesis / semantic arbitration
- P17 parallel isolation 不允许破坏（worker 之间无共享可变状态）
- 模块大小：≤ 200 LOC preferred，避免 giant orchestrator 回归

---

* [x] P18.0 **Actor Identity + Message Contract（foundation）** ✅

  - `runtime/agents/identity.py` — `ActorContext(actor_id, actor_role, session_scope)`，frozen dataclass，`create()` 工厂方法生成 UUID actor_id
  - `runtime/agents/message.py` — `RuntimeMessage` Pydantic frozen model，`create()` 自动填充 `correlation_id` 和 `timestamp`
  - `tests/test_p18_actor_identity.py` — 24 个测试，覆盖 identity uniqueness / message contract / scoped isolation
  - 附带修复：`research/__init__.py` 移除 `session.py` 导出（编排层不属于包核心导出，避免 `agents → research → agents` 循环导入）
  - **370 tests，369 passing**（1 个 P17 已有 flaky test：`id()` 在全量套件下偶发 GC 复用，单独运行全过）

* [x] P18.1 `AgentRegistry` ✅：按 `actor_role` 注册 / 查找 agent；`(role, variant)` 双键支持 default / experimental / mock 运行时切换；`create_actor()` 同时返回 `ActorContext` + 实现（P18.0 bridge）；`RegistryKeyError` 清晰错误提示；35 个测试（**405 tests passing**）

* [x] P18.2 **Message bus** ✅：`MessageBus` 路由层，`send`（单分发：actor_id 精确匹配 → role 首注册）/ `send_all`（广播：role 全部 handler → actor_id fallback）；`BusRoutingError`；`correlation_id` 全程不变；不承担 cognition；29 个测试（**434 tests passing**）

* [x] P18.3 **多 verifier 协作** ✅：`VerifierVoter`（纯投票：strict majority，平均 confidence，聚合去重 evidence）+ `BusVerifier`（满足 `VerifierAgent` Protocol，`bus.send_all` 广播 + voter 共识）+ `make_verifier_handler`（`VerifierAgent` → bus handler 序列化桥）；`correlation_id` 贯穿所有 verifier 响应；31 个测试（**465 tests passing**）

* [x] P18.4 **Agent-level tracing** ✅：`AgentSpan` context manager（`observability/tracing/agent_span.py`），`from_actor(collector, ctx, action, correlation_id)` 工厂；`__enter__` 发 `AGENT_ACTION_STARTED`，`__exit__` 发 `COMPLETED`/`FAILED`；metadata 携带 `actor_id / actor_role / session_scope / action / correlation_id`；异常自动重新抛出；`EventType` 新增三个 variant；31 个测试（**496 tests passing**）

* [x] P18.5 **端到端** ✅：`build_bus_research_session(verifier_agents, session_scope, *, collector, planner, synthesizer, max_rounds)` 工厂函数（`agents/multi_agent.py`）组装完整 P18 栈；`_make_traced_handler` 把 `AgentSpan` 嵌入每个 verifier handler；分歧解决（2/3 confirmed → confirmed，1+1+1 → inconclusive）通过全量 `session.run()` 验证；36 个测试（**532 tests passing**）

---

**测试要求（P18 全程）：**
- actor identity tests：`actor_id` 唯一，`session_scope` 正确绑定
- message contract tests：所有必填字段存在，`correlation_id` 可追踪
- scoped memory isolation tests：无跨 actor mutation
- delegation orchestration tests：routing 正确，correlation 完整，isolation 稳定

---

## NEXT — P19 Distributed Tracing

P18 建立了扁平的 AgentSpan（actor_id + correlation_id）。P19 引入 **span 树**：每个 span 携带 trace_id / span_id / parent_span_id，多 agent 调用可组装成可视化层级树。

**实现约束（全子任务适用）：**
- `AgentSpan` 向后兼容：不传 `SpanContext` 时行为不变
- `TraceTree` 只消费 TraceEvent 列表，不修改 TraceCollector 内部
- `SpanContext` 为 frozen dataclass，不可变
- render 结果为纯文本，不引入外部依赖

---

* [x] P19.0 `SpanContext` ✅ — `trace_id + span_id + parent_span_id` frozen dataclass；`root()` 工厂（自动生成 trace_id）；`child()` 继承 trace_id 并生成新 span_id；`is_root` 属性

* [x] P19.1 Extend `AgentSpan` ✅ — 接受可选 `span_context: SpanContext`；`from_actor` 新增 `span_context` 参数；`_append` 把 `span_id / parent_span_id / trace_id` 写入 metadata；无 SpanContext 时行为不变（向后兼容）

* [x] P19.2 `TraceTree` + `render_trace_tree` ✅ — 从 `list[TraceEvent]` 重建 span 树；`TraceNode(span_id, parent_span_id, trace_id, actor_id, actor_role, action, children)`；`all_nodes()` 深度优先展开；`trace_ids()` 返回所有 trace；`render_trace_tree` 输出缩进文本

* [x] P19.3 端到端 ✅ — 3 verifier × 2 hypothesis = 6 child spans，全部链接到 root trace_id；`render_trace_tree` 正确输出缩进层级；42 个测试（**574 tests passing**）

---

## NEXT — P20 Task Graph Scheduling

P9/P10 的 `AsyncSubtaskRunner` 按拓扑层级批量执行；P20 实现真正的 DAG 调度器：任务完成立即解锁后继，支持 priority，failed dep 自动 skip 后继。

**实现约束：**
- `TaskScheduler` 是通用调度器，不绑定 research/agent 逻辑
- 不依赖 LangGraph；用 `ThreadPoolExecutor` + `concurrent.futures.wait`
- 文件 ≤ 200 LOC；纯 dataclass，不用 Pydantic（`fn: Callable` 不可序列化）

---

* [x] P20.0 `Task` + `TaskResult` ✅ — `task_id / fn / deps(frozenset) / priority`；`execute_task` 包装器（捕获所有异常 → failed）；`TaskResult(status: completed/failed/skipped, output, error, duration_ms)`

* [x] P20.1 `TaskGraph` ✅ — `add(task)` / `ready(completed)` 按 priority 降序；`validate()` DFS 白灰黑染色 → `CycleError(path)`；外部 dep（未注册）视为已完成

* [x] P20.2 `TaskScheduler` ✅ — `run(graph) → dict[task_id, TaskResult]`；`ThreadPoolExecutor + wait(FIRST_COMPLETED)`；failed dep 自动 skip 后继（传递闭包）；`_apply_skips` + `_submit_ready` 分离职责

* [x] P20.3 测试覆盖 ✅ — chain / diamond / parallel / 失败传播 / transitive skip / priority 顺序 / 环检测 / 外部 dep；46 个测试（**620 tests passing**）

---

## NEXT — P21 Worker Orchestration ✅

P20 的 TaskScheduler 使用匿名 ThreadPoolExecutor；P21 引入具名、类型路由的 Worker 系统，
连接 P18 agent 层与 P20 TaskGraph。

**实现约束：**
- `WorkerPool` 只承担路由 / 分发 / 状态追踪，不承担 cognition
- `WorkerOrchestrator` 与 `TaskScheduler` 互相独立（不继承），独立可替换
- `Task.worker_type` 为可选字段，空字符串保持向后兼容
- 文件 ≤ 200 LOC；dataclass，不用 Pydantic

---

* [x] P21.0 **`WorkerSpec` + `WorkerState`** ✅ — `WorkerSpec(worker_id, worker_type, capacity)`；`WorkerState` 追踪 `active/completed/failed/stopped`；`capacity=0` 在构造时报错
* [x] P21.1 **`WorkerPool`** ✅ — `register / stop / submit / state / all_states / shutdown`；按 `worker_type` 路由（空字符串=任意 worker）；least-loaded 选择；capacity 限制；`WorkerUnavailableError`；done callback 维护状态；lock 保护所有状态读写
* [x] P21.2 **`WorkerOrchestrator`** ✅ — `run(graph)` 使用 `WorkerPool.submit` 替代匿名线程池；`WorkerUnavailableError` 不失败任务（defer 到 worker 空闲后重试）；`_finalize` 两阶段：先标记 unroutable→failed，再传递性 skip；向后兼容 P20 TaskGraph 语义
* [x] P21.3 **测试覆盖** ✅ — WorkerSpec/WorkerState / WorkerPool routing+capacity+stop / WorkerOrchestrator chain+diamond+fail propagation+typed routing+unroutable finalization / e2e multi-type DAG；52 个测试（**672 tests passing**）

---

## NEXT — P22 Execution Event Model ✅（架构检视响应）

外部架构检视指出核心风险：feature expansion > architecture consolidation。
P22 作为架构响应，建立 ExecutionEvent 基础层，冻结 RuntimeState，并明确子系统边界。

**实现约束：**
- `reforge/runtime/events/` 零 runtime 依赖（仅 stdlib）
- `RuntimeState` 不允许继续扩张（CLAUDE.md 硬约束）
- 所有新状态 → `ExecutionEvent`，不进 `RuntimeState`

---

* [x] P22.0 **`ExecutionEvent` 模型** ✅ — 7 种 `EventKind`、6 种 `FailureCategory`；工厂函数强制 payload 契约（`execution_failed` 要求 `category/recoverable/error`）；frozen dataclass，零 runtime 依赖

* [x] P22.1 **`ExecutionEventLog`** ✅ — 线程安全 append-only log；`query(kind, session_id)` AND 语义过滤；`replay()` 全序快照；`sessions()` 集合；所有读写加锁

* [x] P22.2 **`OWNERSHIP.md`** ✅ — 定义 9 个子系统的 produces/consumes/MUST NOT；反模式清单；RuntimeState FROZEN 声明和 event-sourced 路线图

* [x] P22.3 **`CLAUDE.md` 更新** ✅ — `RuntimeState FROZEN` 硬约束；Event-Sourced Runtime 方向；子系统 ownership 规则引用

* [x] P22.4 **测试覆盖** ✅ — ExecutionEvent 不变性/自动字段；7 种工厂函数 payload 验证；EventLog append/query/replay/sessions/线程安全；50 个测试（**722 tests passing**）

---

## NEXT — P23 Event-Sourced Runtime Integration ✅

P22 建立了 ExecutionEvent 词汇表；P23 把它接入现有的 runtime 图节点，
四个关键节点现在会在生命周期转换点发射事件。

**实现约束：**
- 四个 `wrap_*_node` 高阶函数：`event_log=None` 时返回原始函数（零开销，零改动）
- 不修改任何现有节点函数签名
- `session_id` 在 `RuntimeRunner.__init__` 生成，通过 `build_graph` → 闭包传递
- `RuntimeState FROZEN` 原则：session_id 不加入 RuntimeState

---

* [x] P23.0 **`FailureCategorizer`** ✅ — 纯函数 `categorize_failure(exit_code, stderr) → (FailureCategory, semantic_meaning)`；优先级排序：dependency > syntax > timeout > policy_blocked > runtime_error；大小写不敏感匹配

* [x] P23.1 **`emitters.py`** ✅ — 4 个 `wrap_*_node` 高阶函数；`wrap_execution_node`（started + succeeded/failed）、`wrap_evaluation_node`（evaluation_completed）、`wrap_reflection_node`（reflection_generated，仅有 traceback 时）、`wrap_retry_decision_node`（policy_decided + recovery_attempted on RETRY）

* [x] P23.2 **`workflow.py` + `runner.py` 扩展** ✅ — `build_graph(event_log, session_id)` 参数；`RuntimeRunner(event_log)` 参数；`runner.event_log` 属性；全程向后兼容

* [x] P23.3 **测试覆盖** ✅ — FailureCategorizer 15 个 / 各 wrap 函数单元测试 / RuntimeRunner DI 验证；49 个测试（**771 tests passing**）

---

## NEXT — P24 Parallel Execution Runtime ✅

把 P21 WorkerOrchestrator 与 RuntimeRunner 真正连接起来：
多个 runner 实例按 DAG 并行执行，完全隔离，按 worker 类型路由。

**实现约束：**
- `runner_factory` 每次执行调用一次（session 隔离）
- `ParallelRuntime` 不知道 LangGraph / LLM，只知道 RuntimeTask 接口
- 失败语义继承自 WorkerOrchestrator（failed dep → skip 后继）

---

* [x] P24.0 **`RuntimeTask` + `RuntimeOutput` + `RuntimeResult`** ✅ — `RuntimeTask(task_id/user_request/runner_factory/deps/priority/worker_type)`；`RuntimeOutput(state/session_id/event_log)`；`RuntimeResult(status/final_answer/session_id/output)`；deps 归一化为 frozenset

* [x] P24.1 **`ParallelRuntime`** ✅ — `run(tasks) → dict[str, RuntimeResult]`；`_build_graph` 将 RuntimeTask 转为 Task；`_make_fn` 创建捕获 runner_factory 的闭包；`_to_result` 从 RuntimeOutput 提取 final_answer；`WorkerOrchestrator` 负责调度

* [x] P24.2 **测试覆盖** ✅ — 模型 / 单任务 / 并行 / chain / diamond / 失败传播 / transitive skip / 路由 / 优先级 / factory 隔离 / 并发性能（timing）/ 端到端管道；42 个测试（**813 tests passing**）

---

## NEXT — P25 Session Replay ✅（Event-Sourced 读取侧）

从 ExecutionEventLog 投影重建 session 历史，不触碰任何现有节点。
这是 event-sourced 架构的读取侧（projection layer）。

**实现约束：**
- 零 runtime 依赖（仅 events/ 内部）
- AttemptSummary / SessionSummary 均为 frozen dataclass（不可变）
- 算法：状态机逐事件处理，POLICY_DECIDED 关闭当前 attempt bucket

---

* [x] P25.0 **`AttemptSummary` + `SessionSummary`** ✅ — frozen dataclass；attempt_number / execution_outcome / failure_category / semantic_meaning / eval_score / reflection_summary / policy_decision

* [x] P25.1 **`SessionReplay`** ✅ — `summarize(session_id)` / `all_summaries()` / `render(session_id)`；`_build_summary` 状态机（EXECUTION_STARTED 开桶 → 各事件注入字段 → POLICY_DECIDED 关桶）；partial session（无 POLICY_DECIDED）→ in_progress

* [x] P25.2 **`render_summary`** ✅ — 缩进文本时间线；含 session_id / outcome / Recoveries / per-attempt 标签（outcome + category + semantic）/ eval / reflection / policy

* [x] P25.3 **测试覆盖** ✅ — 单成功 / 单失败 / retry+成功 / 多次重试+失败 / partial session / 多 session 隔离 / 渲染内容验证；31 个测试（**844 tests passing**）

---

## NEXT — P33 Full Consistency Integration Test ✅（Event-Sourced Migration 封印）

4 个 emitter wrapper 组成的完整流水线端到端验证：5 个场景（成功 / 失败 / retry+成功 /
max-retry+STOP / 双 session）全部通过 `check_state_consistency()`，7 个字段零 mismatch。

**附带修复：** `wrap_reflection_node` 去掉成功路径 early return——
现在无论是否有 traceback，只要 `error_summary` 非空就发射 REFLECTION_GENERATED 并覆盖 `reflection_summary`。
这修复了 retry 场景中事件 "stale reflection" 的一致性缺口。更新了 P23/P29 中 3 个旧测试。

---

* [x] P33.0 **emitters.py 修复** ✅ — `wrap_reflection_node` 移除 `if not state.traceback: return result` early return；成功路径也发射事件并覆盖
* [x] P33.1 **集成测试** ✅ — `_merge` 辅助函数模拟 LangGraph dict merge；`_pipeline_*` 构建 4 个完整场景；5 个场景类 × 多个断言；24 个测试
* [x] P33.2 **P23/P29 测试更新** ✅ — `test_no_event_when_no_traceback` → 期望 1 个事件；P29 两个 success path 测试同步更新（**1020 tests passing**）

---

## NEXT — P32 Node Mutation Removal ✅（Event-Sourced Migration Phase 2 开始）

从 `retry_decision_node` 删除已迁移字段的冗余 mutation，emitter 成为唯一写入路径。

**删除内容：**
- `"retry_count": state.control_state.retry_count`（无意义的自我复制）
- `"retry_decision_action": resolution.action`（初始 control_state 中）
- `new_count = state.control_state.retry_count + 1`（死变量）
- `control_state_retry = control_state.model_copy(...)` 整个分支（死代码）
- RETRY 返回中的 `"control_state": control_state_retry`（覆盖）

**节点现在只负责：** `policy_reason`、`task_intent`、`clf`、`governor_resolution`、
`retry_decision`、`retry_context`（RETRY only）

---

* [x] P32.0 **retry_decision_node 精简** ✅ — 删除 5 处冗余代码；`retry_decision` 移入 `base` dict；RETRY 分支不再覆盖 control_state
* [x] P32.1 **测试覆盖** ✅ — 裸节点不设置迁移字段（mock governor）/ 非迁移字段正常 / wrapped 节点 emitter 正确填充 / should_retry 路由不变 / consistency 回归；22 个测试（**996 tests passing**）

---

## NEXT — P31 Always-Active EventLog ✅（Event-Sourced Migration 第六步）

`RuntimeRunner` 不再允许 `event_log=None` 的空运行状态。
无论是否注入外部 log，内部始终维护一个 `ExecutionEventLog`，确保 P28-P30 的 emitter 覆盖永不跳过。

**核心变更：** `runner.py` 构造函数：
```python
self._event_log = event_log if event_log is not None else ExecutionEventLog()
```

**语义变化：**
- 旧：`RuntimeRunner().event_log is None` → True
- 新：`RuntimeRunner().event_log is not None` → True（自动创建内部 log）
- 注入外部 log 行为不变（为跨 session 共享场景保留）

---

* [x] P31.0 **runner.py 单行变更** ✅ — `self._event_log = event_log or ExecutionEventLog()`；`build_graph` 收到的 event_log 永远非 None
* [x] P31.1 **P23 旧测试更新** ✅ — `test_runner_default_event_log_is_none` → `test_runner_default_creates_internal_event_log`（断言翻转）
* [x] P31.2 **测试覆盖** ✅ — 自动创建/类型/为空/session 隔离/注入优先/shared log/session_id 不变；15 个测试（**974 tests passing**）

---

## NEXT — P30 retry_decision_action Migration ✅（Event-Sourced Migration 第五步）

`wrap_retry_decision_node` 新增 `retry_decision_action` event-derived 覆盖，
同时将 P28 的 `retry_count` 覆盖重构为单次 `model_copy`（`cs_updates` dict 合并）。

**核心变更：** emitters.py 中 `wrap_retry_decision_node`：
- `cs_updates = {"retry_decision_action": action_str}` 始终设置
- RETRY 分支追加 `cs_updates["retry_count"] = event_count`
- 单次 `cs.model_copy(update=cs_updates)` 完成所有字段覆盖

---

* [x] P30.0 **emitters.py 重构** ✅ — `wrap_retry_decision_node` 合并为 `cs_updates` dict 模式；`retry_decision_action` 在所有决策（ACCEPT/STOP/RETRY）中覆盖；P28 的 `retry_count` 行为保留
* [x] P30.1 **测试覆盖** ✅ — policy 覆盖（三种 action）/ event payload 一致 / P28 回归保护 / consistency / projection；16 个测试（**959 tests passing**）

---

## NEXT — P29 eval_score/passed + reflection_summary Migration ✅（Event-Sourced Migration 第四步）

在 `wrap_evaluation_node` 和 `wrap_reflection_node` 中加入 event-derived 覆盖：
- `evaluation_result.score/passed` ← EVALUATION_COMPLETED payload
- `semantic_state.reflection_summary` ← REFLECTION_GENERATED payload
同一模式，两个字段一个 P-stage 完成。

**核心变更：**
- `wrap_evaluation_node`：发射后 `result["evaluation_result"] = {**ev, "score": score, "passed": passed}`
- `wrap_reflection_node`：发射后 `result["semantic_state"] = sem.model_copy(update={"reflection_summary": summary})`
- 成功路径（无 traceback）不发射、不覆盖，保持原有行为

---

* [x] P29.0 **emitters.py 两处变更** ✅ — `wrap_evaluation_node` 覆盖 score/passed；`wrap_reflection_node` 覆盖 reflection_summary（仅 traceback 非空且 summary 非空时）
* [x] P29.1 **测试覆盖** ✅ — eval 发射/payload/覆盖/字段保留/session 隔离 / reflection 发射/覆盖/成功路径/空 summary / consistency / projection；22 个测试（**943 tests passing**）

---

## NEXT — P28 retry_count Event Migration ✅（Event-Sourced Migration 第三步）

将 `retry_count` 从 RuntimeState mutation 迁移为 event-derived，events 成为 source of truth。

**核心变更：** `emitters.py` — `wrap_retry_decision_node` 在发射 `RECOVERY_ATTEMPTED` 后，
用 `len(event_log.query(kind="RECOVERY_ATTEMPTED", session_id=...))` 覆盖 result 的 `retry_count`。
节点函数本身不变，向后兼容保持（event_log=None → identity wrapper）。

**证明：** `test_event_count_is_source_of_truth` — 当 log 已预存 2 个 RECOVERY_ATTEMPTED 事件时，
state-mutation 会给出 0+1=1，但 event-derived 给出 3 → result 的 retry_count=3（事件优先）。

---

* [x] P28.0 **emitters.py 单处变更** ✅ — `wrap_retry_decision_node`：RETRY 分支发射事件后计算 `event_count`，覆盖 result["control_state"].retry_count；`model_copy` + dict 两种 control_state 类型均支持
* [x] P28.1 **测试覆盖** ✅ — legacy/event_log=None / emission / event-derived count / 多重 retry / source_of_truth 证明 / consistency_report / projection 一致性；18 个测试（**921 tests passing**）

---

## NEXT — P27 Event-State Consistency Validator ✅（Event-Sourced Migration 第二步）

在 `runtime/bridge/` 建立一致性验证层，证明 EventLog 和 RuntimeState 携带相同信息。
这是 RuntimeState 字段替换为 event projection 的前提保障。

**实现约束：**
- `reforge/runtime/bridge/` 是跨层桥接模块，可同时依赖 `events/` 和 `state/`
- 零副作用：不修改 state、不发射事件，纯读取比较
- float 字段用相对容差（1e-6）避免 fp 噪声
- `evaluation_result is None` 时跳过 eval 字段检查（执行前阶段）

---

* [x] P27.0 **`FieldMismatch` + `ConsistencyReport`** ✅ — frozen dataclass；`is_consistent` 属性；`mismatch_fields()` 列表
* [x] P27.1 **`check_state_consistency(projection, state)`** ✅ — 7 个字段映射：retry_count / last_policy_decision / last_eval_score / last_eval_passed / last_reflection / last_execution_outcome / current_attempt；None→空字符串归一化；StrEnum.value 处理
* [x] P27.2 **测试覆盖** ✅ — 模型不变性 / 一致场景（空/zero/retry/eval/reflection/exec/attempt）/ 每字段独立 mismatch / float 容差 / None 边界；28 个测试（**903 tests passing**）

---

## NEXT — P26 Runtime State Projection ✅（Event-Sourced Migration 第一步）

从 ExecutionEventLog 推导"当前执行状态"，作为 RuntimeState 迁移的读侧基础。
零 runtime 依赖（stdlib + events/ only）。

**实现约束：**
- `RuntimeStateProjection` 为 frozen dataclass，所有字段来源于事件，不依赖 RuntimeState
- `project_state()` 追踪 LATEST 事件值（与 SessionReplay 的 per-attempt bucket 互补）
- "live state" vs "historical summary" 的功能分工明确

---

* [x] P26.0 **`RuntimeStateProjection`** ✅ — frozen dataclass；12 个字段：`retry_count / current_attempt / last_execution_outcome / last_failure_category / last_failure_semantic / last_eval_score / last_eval_passed / last_reflection / last_policy_decision / is_terminal / outcome`
* [x] P26.1 **`project_state(session_id, event_log)`** ✅ — 单遍事件扫描；latest-wins 语义；RECOVERY_ATTEMPTED 累加；ACCEPT/STOP 设置 is_terminal；零依赖
* [x] P26.2 **测试覆盖** ✅ — 空日志 / 各 kind 单独 / terminal 检测 / latest-wins / retry 序列 / 多 session 隔离；31 个测试（**875 tests passing**）

---

## LATER

### Runtime Architecture

* [ ] Event-sourced RuntimeState migration — 将 RuntimeState 由 source-of-truth 改为 event projection（大体量，高风险，需分批推进）
  - P26 已完成读侧基础（RuntimeStateProjection 可从事件推导关键字段）
  - 下一步：EventStateValidator — 验证 RuntimeState 字段与 projection 一致（证明事件完备性）
  - 再下一步：逐字段替换 RuntimeState mutation 为 event-first（以 retry_count 为突破口）

---

## STABLE SUBSYSTEMS

The following systems are considered stable and should avoid major refactors unless necessary:

* OutcomeResolver
* CapabilityPolicy
* RetryPolicy
* TaskIntent classification
* RuntimeState (P-R: flat fields removed, nested sub-states canonical)
* Governor resolve() single authority
* `graph/nodes/` modular layout (P-R.1)
* `MemorySubstrate` DI path (P-R.4)
* `data/` unified persistence root (P-R.5)

---

## CURRENT ARCHITECTURE PRIORITY

Current focus is shifting from:

* retry heuristics
* execution recovery
* policy stabilization

towards:

* Governor pipeline composability
* execution memory and adaptive retry
* research-oriented runtime