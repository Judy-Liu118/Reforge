# 已知局限

[English](KNOWN_LIMITATIONS.md) | 简体中文

本文档记录团队已经识别、评估过，并**有意推迟**处理的架构债务。每一条都写明了：坏味道
是什么、正确的修法是什么、以及为什么现在不修。如果你动了"就地打个补丁"的念头 ——
先把那一条的*反模式*看完。

---

## L1. 意图在多处从 `user_request` 重新推导

### 症状

有两个子系统各自维护着一套正则 / 关键词列表，用来猜测用户提了什么类型的任务：

| 位置 | 列表 |
|---|---|
| `reforge/runtime/orchestration/evaluation/heuristics.py` | `INTENTIONAL_ERROR_PATTERNS`、`DATA_TASK_KEYWORDS`、`RESEARCH_VERIFY_KEYWORDS`、`SUSPICIOUS_NUMERIC`（按请求门控） |
| `reforge/models/prompts/directives.py` | `MUST_FAIL_FIRST_PATTERNS`、`EXPECTS_UNCAUGHT_PATTERNS` |

每份列表都拿 `state.user_request` 去匹配手工整理的中英文短语 —— 模式类列表用
`re.search`，关键词类列表则是裸的 `kw in lowered_request` 子串判断。两个方向都会出错。

*漏判*："make it fail on purpose" 永远匹配不上 `故意.*报错`；"compute the median salary"
匹配不到任何 `DATA_TASK_KEYWORDS` 条目，于是一个货真价实的数据任务跳过了
`output_contains_data` 检查。

*误命中*更糟，因为关键词是按裸子串匹配的，没有词边界：

| 请求 | 误触发 | 原因 |
|---|---|---|
| "List every **account** holder's name" | `count` | ac**count** |
| "Write a one-line **summ**ary of the README" | `sum` | **sum**mary |
| "Explain the **mean**ing of this flag" | `mean` | **mean**ing |

这三条都会被当成数据任务，走进 `output_contains_data`。

对应的 review 条目：①（directive 硬编码）、③（正则漏判）、④（输出长度下限）、
⑦（关键词覆盖面）—— 这四条都是同一个根因的表面症状。其中 ④ 已单独退役：
`MIN_OUTPUT_LENGTH` 已删除，`output_not_empty` 现在检查的是"有没有输出"而不是"输出多长"
（见 CHANGELOG）。剩下三条仍然成立。

### 根因

`Governor.IntentStage` 本身就已经产出了类型化的分类结果：
`state.semantic_state.task_intent`（`NORMAL_EXECUTION`、`EXPECTED_FAILURE`、
`RECOVERABLE_FAILURE` …）以及 `state.task_requirements`（`must_fail_first`、
`expects_uncaught_exception` …）。下游消费者本应**读取**这些类型化字段，而不是从原始字符串
重新推断意图。现在的设计里有两个"神谕"—— 一个是结构化的，一个是字符串式的 ——
它们必然会漂移。

### 正确的修法（已推迟）

1. 把 `TaskKind` 提升为 `RuntimeState` 上的一等枚举（大概率挂在 `task_requirements` 上）：
   `Normal | ExpectedFailure | Recoverable | DataAnalysis | ResearchVerify`。
   由 IntentStage 一次性填充。
2. 评估器改成按 `task_kind` 分支来选择检查集 —— 不再有关键词扫描，不再有
   `_is_intentional_task()` 私有方法，不再有
   `is_data_task = any(kw in lowered_request for kw in ...)`。
3. Directive 的选择逻辑（`build_retry_prompt`、`_extract_requirements` 等）改读
   `task_requirements`，而不是模式列表。
4. 删除 `INTENTIONAL_ERROR_PATTERNS`、`DATA_TASK_KEYWORDS`、`RESEARCH_VERIFY_KEYWORDS`、
   `MUST_FAIL_FIRST_PATTERNS`、`EXPECTS_UNCAUGHT_PATTERNS`。它们的行为可以从轨迹测试语料
   中复原出来：在同一批输入上跑改造前后的 `task_kind` 并比对等价性即可。

### 修复时必须遵守的顺序约束（2026-07-25 补充）

上面第 2 步有一个未言明的前提：**评估器不能简单地"读那个类型化字段"，因为在第 1 次尝试时
它还是 `None`。** 图的执行顺序是 `evaluation → retry_decision`（`graph/workflow.py:87-88`），
而 `task_intent` 唯一的落库点在 `retry_decision.py:78` —— 也就是说，唯一会从中受益的消费者
恰好跑在这个值产生**之前**。根因在于放置位置：意图本是请求的属性
（`intent_stage.py:3`），却直到倒数第二个节点才被写进状态。正确的修法是在入口节点
（`capability_check` / `planner`）就做分类并在那里写入 `semantic_state.task_intent`；
`intent_stage.py:20` 的缓存会让后续每次 governor resolve 都命中缓存，所以 LLM 调用次数不变。

推迟期间实测到的影响面：这个漂移**不会**波及最终结局 —— `resolve_outcome()` 的意图覆盖逻辑
作用在事件映射之外，因此对于 `exit_code != 0` 的 `EXPECTED_ERROR`，六种
`policy_action × eval_passed` 组合仍然全部返回 `EXPECTED_FAILURE`。损害被限制在写入
`attempts[-1]`、轨迹和记忆里的 `eval_score` 噪声上。（量级参考：关键词列表漏判了 2 个
`intentional` benchmark 用例中的 1 个 —— `intentional_syntax_error` 的
"故意在 print 前面加一个乱码字符让语法出错"匹配不上任何条目 —— 但该用例的结局分类依然正确。）
真正没有防护的方向是*误命中*：一个含有"故意包含"字样的 `NORMAL_EXECUTION` 请求会跳过
`no_error_in_output` + `stderr_clean`（`heuristics.py:193`），而在 `exit_code == 0` 时没有
任何覆盖逻辑兜底 —— 评估是唯一的关口。

### 为什么推迟

- **影响范围**：需要升级 schema（新枚举 + 对已持久化的 `TaskRequirements` /
  `TrajectoryRecord` 快照做迁移）。
- **风险窗口**：这轮清理离发版很近；评估输出的那套关键词在演示语料上是久经考验的，
  临发版前改动分类路径，等于邀请一个没人来得及发现的回归。
- **先后次序**：等 Governor 的 `IntentClassifier` LLM 选型也锁定之后再修会更干净
  （目前 qwen3-vl-thinking 已被排除 —— 见 `MEMORY.md`），因为新枚举必须能在换分类器时
  依然存活、不拖垮下游消费者。

重审计划：发版之后，用一次批量提交同时完成"引入枚举 + 迁移消费者 + 删除遗留关键词列表"。

### 反模式 —— 禁止采用

- ❌ 往任何一份关键词列表里继续加中英文变体。每加一条都是在把错误的设计钉得更死，同时给
  正确的修法多加一笔要偿还的税。词表永远追不上自然语言的长尾。
- ❌ 为了覆盖 ④ 的短答案误报而新增一份关键词列表（"EXPECTED_OUTPUT_FORMAT_PATTERNS"、
  "SHORT_ANSWER_PATTERNS" …）。同样的反模式，同样的答案：去读 `task_kind`。
- ❌ 就地收紧单条正则。哪怕给 `EXPECTED_FAILURE` 写出一条"完美"的正则，也修不好设计问题，
  只是把重复逻辑藏进了一个看起来更自信的失败模式里。
- ❌ 把关键词扫描的结果缓存到 `RuntimeState` 上，好让各消费者"共享"。这等于给错误的神谕
  发了一个运行时地址，让重复变成永久设定。

### 推迟期间可接受的就地改动

- 不改变分类面的纯清理（例如把两个等价的 `if is_intentional` 分支合成一个 —— 见 review
  条目 ⑤）。这类改动既不增加也不减少旋钮，只是不让同一个旋钮被拧两次。
- 重命名死变量（review 条目 ⑥）和过时的局部名（review 条目 ⑧）。改的是行，不是行为。
- 那些会改变行为、但影响面隔离、且在*当前设计下*可证明是错的修复（例如遗留模块
  `graph/vision_routing.py` 里的 `\bUI\b` 词边界修复 —— review 条目 ②，在该模块尚存时
  合入；模块本身后来在 `image_inputs` 重构中被删除，见 L7）。这类改动能争取时间，
  又不会让更大的问题恶化。

---

## L2. 视觉类 skill 绕过了 `LLMClient` —— 可观测性钩子覆盖不到

### 症状

有两个 skill 直接实例化 `openai.OpenAI` 并自行调用
`client.chat.completions.create(...)`，完全不经过 `LLMClient._dispatch`：

| Skill | 文件:行号 | 使用的配置 |
|---|---|---|
| `VisionDescribeSkill` | `reforge/runtime/skills/builtin/vision.py:172`（OpenAI 构造），`:113-114`（调用） | `VISION_LLM_*` |
| `CompareImagesSkill` | `reforge/runtime/skills/builtin/image_compare.py:190`（OpenAI 构造），`:128-129`（调用） | `VISION_JUDGE_*` |

后果：模块级钩子
（`reforge.observability.llm_events._emit("llm_call_complete", ...)`）对视觉 skill 的调用
不会触发。因此通过 `token_accounting(case_id, seed)` 做的 token 累计，对视觉 skill 的 LLM
成本是完全瞎的。视觉自愈中生成的 Python 所使用的 `compare_images()` 辅助函数问题最严重 ——
在自愈循环里每次尝试都会调用一次。

### 为什么说这是一条接缝

这两个 skill 早于统一的 `LLMClient` 存在，当初选择直接用 SDK 的原因是：
- 它们使用独立的配置（`VISION_LLM_*` 和 `VISION_JUDGE_*`，与 `LLM_*` /
  `CODEGEN_VISION_*` 分开）；
- 它们接受远程的 `http(s)://` 图片 URL；
- 它们返回 `SkillResult`；
- 它们使用另一套重试辅助（`reforge/runtime/skills/builtin/_api_retry.py` 里的
  `call_with_retry`）。

要把它们路由到 `LLMClient`，就得扩展客户端的接口面 —— 新增工厂方法、支持多图多模态、
再把 skill-result 的形状穿进去。值得做，但不值得现在做。

### 正确的修法（已推迟 —— 重启条件见下）

新增 `LLMClient.for_vision_describe()` 和 `LLMClient.for_vision_judge()` 两个工厂，形态对齐
`for_vision_codegen()`。把两个 skill 迁移到 `client.chat_multimodal(...)` —— 它已经会抽取
`usage` 并触发钩子 —— 取代直接的 `OpenAI(...).chat.completions.create(...)`。skill 保留
自己的重试 / 降采样 / SkillResult 形状，只把网络调用这一段路由过去。

### 为什么推迟

- **当前的度量范围**：`docs/eval/PHASE0_METRICS.md` 里锁定的两套评测语料（BIRD SQL、
  Phase-2 pandas/CSV）都不含图片输入。规划 LLM 在这两套语料上都不会调用视觉 skill，
  所以这个缺口不在被度量的路径上 —— 这两套语料的 `tokens_per_solved` 覆盖率是 100%。
- **"刷漆盖住接缝"的风险**：按当前形状把钩子打进 skill 里（那个便宜的修法）等于追认了
  双 LLM 路径的设计，而不是把它统一掉。推迟能让压力继续指向真正需要时的统一重写。

### 触发重审的条件

一旦某个被度量的评测轴包含了带图片的任务（例如未来基于视觉自愈循环搭建的"UI 复刻"轴），
推迟即刻失效。在那之前，这是一块有文档记录的暴露面，不是 bug。

### 反模式 —— 禁止采用

- ❌ 在每个视觉 skill 内部各复制一份 `_emit("llm_call_complete", ...)` 代码块。这是在
  追认绕过行为；把必须与事件 schema 保持同步的调用点翻倍；而且丝毫没有消除双 LLM 路径
  这个坏味道。
- ❌ 在 skill 里读 `response.usage`，塞进 `SkillResult.metadata` 让 driver 去收割。同一个
  反模式换了顶帽子 —— 而且它把度量管道泄漏进了 skill 契约，而别的 skill 并不背这个包袱。

### 推迟期间可接受的就地改动

- 在 skill 内部新增纯日志，只要不改变网络调用路径。
- 更新任一 skill 的 docstring / `prompt_fragment`。
- 对 `call_with_retry` 做不改变语义的调整。

---

## L3. 主动 STOP 由意图和超时驱动，而非从历史推导

### 状态更新（2026-07-13）：推迟的修复已合入 —— 范围收窄到"重复相同失败"这一种情形

下文"正确的修法"里描述的检测器，已在 Phase 1 BIRD 消融实验收官后（R2 之后）合入 ——
这正是两个被许可的合入窗口之一（"要么在 Phase 0 之前，要么在评测之后"）。实际落地的内容：

- 当最近连续 2 次尝试共享同一个结构指纹时，`ClassifyStage` 会把一个可重试的分类翻转成
  `failure_mode="repeated_signature"`（指纹来自 `semantic_state.failure_signature_history`，
  由反思节点在每次失败尝试后追加）。指纹是从 traceback 里确定性解析出来的
  （`extract_fingerprint`），**不是**来自 LLM 的反思文本 —— 反思节点只是"追加动作发生的
  地方"，所以"反思不具备运行时权威"这条边界依然成立；唯一受 LLM 影响的输入，是当
  traceback 里没有可解析的错误行时所用的 `error_type` 兜底值。`RetryPolicy` 会把它转成一次
  主动 STOP（`repeated_failure_signature`）；结局解析器会把它作为独立事件上报，而不是错误地
  标成 `RETRIES_EXHAUSTED`。
- 与下文草案有意偏离的两处：改用**完整指纹相等**而非按异常类型计数（要求错误类相同**且**
  目标模块/键/文件/名称相同 —— 精度严格高于按类型计数），以及**阈值 2、且要求连续**而非
  草案里累计 `N = 3`。原因是在默认预算下（`max_retry=3` → 共 4 次尝试），一串同签名失败会在
  累计计数达到 3+1 之前就把预算烧光，而"省下尝试次数"恰恰是这件事的全部意义。声明为
  预期失败的意图（`RECOVERABLE_DEMO`）豁免 —— 明示的可恢复意图优先级高于历史信号。
- **Phase 1 的 R1 + R2 数据度量的是没有这个检测器的运行时；第 3 轮
  （2026-07-13，`docs/eval/PHASE1_BIRD_ABLATION_R3.md`，commit `bcc11fb`，200 次运行）
  度量的是它上线后的实况 —— 结果发现它在这套语料上处于休眠：零次
  `repeated_failure_signature` STOP。** 从原始记录（`phase1_records_r3.jsonl`）归因：
  governor 臂的 31 次重试尝试中，30 次是*安静的*评估器拒绝（exit 0，无 traceback ——
  不会往 `failure_signature_history` 里追加任何东西，所以按设计检测器对它们是盲的），
  只有 1 次是响亮的失败；没有任何一次运行出现连续两次响亮失败，而那是检测器唯一能起作用的
  形状。因此 R3 的 governor 臂在每一个决策点上做出的选择，都与 L3 之前的运行时完全一致，
  success_rate 的零结论得到复现（61.0% vs 62.0%，配对 Δ 的 95% CI [-9.1, +7.1]pp），
  成本差值也与 R2 在统计上一致（tokens-per-solved 的 Δ CI 为 [1,199, 4,215]，R2 为
  [1,001, 2,683]）。由此可见，这个检测器的价值主张只适用于失败既**响亮**又**持续**的负载
  （缺依赖的死循环、不可达资源）—— 那是 Phase 0 式的负载，不是 BIRD 式的。行为验证仍然
  依靠单元 + 集成测试（`reforge/tests/test_repeated_signature_stop.py`、
  `test_retry_loop.py::test_repeated_identical_failure_stops_early`）。
- R3 确实记录到了几次安静的 4 次尝试运行（5 次 governor 运行因为反复被评估器拒绝而烧光了
  全部预算），这正是"正确的修法"草案里提到的评估侧类比 —— 即检测重复出现的相同
  `evaluation_result.failure_type`。这部分是**有意不做**的：下面的反模式清单解释了为什么
  "把评估拒绝的复现挪用为不可恢复信号"这件事，必须先有它自己的精度研究。
- "为什么推迟"一节里的精度告诫并没有被消除，只是被*披露*了：重复出现的相同指纹，原则上
  仍然可能在后续尝试中恢复。这个阈值是一个设计选择，不是一个经过校准的参数。

以下各节按当初的原文保留，用于记录这件事在 Phase 0 / Phase 1 期间被推迟的历史缘由。

### 症状

governor 的 `RetryPolicy.decide()`（`reforge/runtime/policy/retry_policy.py:19-53`）发出
*主动* STOP —— 即预算尚有剩余时的 STOP —— 只有两条分支：

| 分支 | 触发条件 | 由谁设置 |
|---|---|---|
| `terminal_intentional_failure` | `is_expected_failure=True AND retryable=False` | `FailureClassifier`（`classifier.py:48-52`），当 `task_intent ∈ {EXPECTED_ERROR, TRACEBACK_DEMO}` |
| `timeout` | `failure_mode == "timeout"` | `FailureClassifier`（`classifier.py:36-40`），当 `exit_code == TIMEOUT_EXIT_CODE` |

其余所有失败 —— 包括反复出现的相同 `FileNotFoundError`、缺失模块导致的 `ImportError`、
RFC2606 `.invalid` 域名解析失败、逻辑上无解的算术、自相矛盾的约束 —— 都会落到
`if execution.exit_code != 0: RETRY "execution_error"` 上一路循环，直到
`retry_count == max_retries`，最终以 `retry_limit_reached_with_error` STOP 收场
（这是预算耗尽，**不是**主动 STOP）。

`ClassifyStage._PATTERN_THRESHOLD`（`classify_stage.py:12, 46-58`）确实存在，但它**不是**
STOP 的触发器。它只会往 `repair_hint` 里注入一个 `"[recurring failure: …]"` 前缀，用来引导
下一次尝试的提示词；它从不翻转 `is_expected_failure` 或 `retryable`。而且它监视的是
`evaluation_result.failure_type`，不是运行时的 traceback 签名。

### 根因

运行时对失败的分类是*确定性*的，只依据 `task_intent` + `exit_code` +
`evaluation_result`。分类过程里没有喂入任何逐用例的错误历史 —— 这是有意为之的设计
（"确定性、不依赖反思的分类"是明确写下的不变量）。因此，在没有一套目前并不存在的额外
机制之前，governor 无法得出"我已经连续 N 次见到同一个异常类型 → 这次运行不可恢复"这样的
结论。

### 实际影响

此问题在 Phase 0 校准语料的设计过程中浮现（见 `docs/eval/PHASE0_CORPUS.md` v2 和
`docs/eval/PHASE0_METRICS.md` v3）。最初提出的 D1′（缺失 `config.yaml` →
`FileNotFoundError`）根本探测不到主动 STOP；它只会一路 RETRY 到预算耗尽。Phase 0 因此改基
到 D1″（超时诱饵），后者能走到 `failure_mode == "timeout"` 分支。而
`terminal_intentional` 分支在校准中无法被探测 —— 那需要构造一个 `EXPECTED_ERROR` 意图的
提示词，会把意图信息泄漏进语料。

Phase 2 早期设计的主动 STOP 精确率 / 召回率指标（`PHASE0_METRICS.md` v2 的 Tier B）预设了
一个能覆盖多种诱饵根因的识别器 —— 解析失败、缺失环境依赖、逻辑无解、自相矛盾。这些在当前
运行时里没有一个能触发主动 STOP，所以那些指标只会报出接近零的数值，度量的是一个并不存在的
功能。Tier B 在 v3 中被标记为推迟。

运行时目前诚实的能力范围是：

1. **在可恢复失败上的恢复质量** —— 类型化分类 + `repair_hint`（记忆召回 + 重复模式提示）
   塑造每一次重试。这是相对于朴素基线"盲目重试"的主力消融面。
2. **在超时类和 EXPECTED_ERROR 意图类失败上的效率** —— 主动 STOP 避免了把整个
   `max_retry × T_attempt` 的预算烧掉。这个差值窄，但在走到那些路径的运行上是真实的。

### 正确的修法（已推迟 —— 原因见下）

给 `ClassifyStage` 加一个基于模式的不可恢复性检测器（*不是* `_PATTERN_THRESHOLD`，那个只
塑造提示）：

- 逐用例地，对运行时 traceback 里的顶层异常类型做哈希（如 `FileNotFoundError`、
  `ImportError`、`KeyError`）。
- 在同一个用例的多次尝试之间维护一个 `Counter[exception_type] → int`。
- 当 `counter[top_level_exc_type] >= N` 时（建议初始阈值 `N = 3`），把下一次尝试的分类翻转为
  `is_expected_failure=True, retryable=False, failure_mode="repeated_signature"`。
  随后 PolicyStage 就会发出一次主动 STOP。
- 对由评估驱动的失败，也针对重复出现的相同 `evaluation_result.failure_type` 做同样处理
  （单独实现，因为评估失败的历史已被 `_PATTERN_THRESHOLD` 部分追踪）。

### 为什么推迟

- **精度未经验证，而且实际情况多半比看起来更差。** 一个反复出现的 `FileNotFoundError`
  在下一次尝试里**仍然可能**恢复 —— 代码生成可能决定 `os.makedirs` 再写个占位文件，
  或者换一个路径，或者 import `pathlib` 用个默认值。反复出现的 `ImportError` 也可能在
  代码生成改用标准库替代品时被解决。阈值 `N` 和按异常类型的豁免规则，在采纳*之前*需要
  经验性调参，而在评测语料上调参会违反 v3 的预注册约定（"不得在评测数据上调参"）。
  因此一个 Phase 2 之前的检测器 PR 需要自带一套非评测用的校准语料 —— 那本身就是个独立项目。
- **会在实验中途改变被测系统。** 在 Phase 0 签署和 Phase 2 运行之间插入这个检测器，会让
  消融实验变成"带新检测器的 governor" vs "不带检测器的 governor"，而不是 vs 朴素基线，
  主结论就浑浊了。要么检测器在 Phase 0 之前落地（成为被锁定的运行时表面的一部分），
  要么在 Phase 2 之后落地（并促成一个 Phase 4）。
- **今天这个诚实的范围本身没问题。** 恢复质量这条主线（上面第 1 点）才是 governor 与朴素
  基线在典型负载上的真正差异；主动 STOP 带来的效率提升是次要的、窄的收益。强行让这个次要
  收益去覆盖运行时压根识别不了的诱饵类别，只会不诚实地夸大结论。

### 触发重审的条件

以下任一条件出现，推迟即失效：

- 后续某次评测（Phase 4+）被明确设计来论证这个检测器 —— 即存在一个"可恢复 + 诱饵"混合切片，
  检测器能在其上明显移动精确率/召回率的工作点，且评测方法学已把"被测系统发生变化"这件事
  考虑在内。
- 出现了当前"意图 + 超时"覆盖面无法满足的下游用户需求（例如"运行时应当在 ≤2 次尝试内停止
  尝试无解的用户任务，而不是烧光整个重试预算"）。

在任一触发条件出现之前，这是一块有文档记录的暴露面，而不是 bug；Phase 1 / Phase 2 就在上述
收窄的范围内发布。

### 反模式 —— 禁止采用

- ❌ 把 `_PATTERN_THRESHOLD` 从"注入 repair_hint 前缀"提拔为"翻转 `retryable=False` 并
  STOP"。这会把两种不同的机制（提示质量 vs 不可恢复性检测）混为一谈，挪用一个已经预注册的
  阈值（`PHASE0_METRICS.md` 里的污染披露就得重写），而且信号本身就是错的 —— 反复出现的
  `evaluation_result.failure_type` 说的是"评估器一直在拒绝"，不是"运行时一直在以相同方式
  崩溃"。
- ❌ 从 `reflection` 的输出去推断不可恢复性。按当前设计，反思被明确排除在分类之外
  （`classifier.py` docstring：「Reflection = debugging hints only, no runtime authority」）。
  让分类走反思这条路，等于重新引入 L1 在精神上已经记录过的那种边界违规。
- ❌ 加一段关键词扫描（`if 'invalid' or '.com.invalid' in traceback: STOP`）来识别解析失败类
  诱饵。这和 L1 是同一个反模式 —— 用一个既无法泛化又会腐烂的字符串匹配，去顶替一个结构性
  的修复。
- ❌ 悄悄把评测语料上的 `max_retry` 降到 1，让主动 STOP 和预算耗尽 STOP 之间的差别消失。
  那是靠取消度量来掩盖差距，而不是消除差距。

### 推迟期间可接受的就地改动

- 给 `RuntimeResolution` 增加记录"STOP 是*为什么*被发出的"的可观测性字段
  （`policy_reason` 已经在做这件事 —— 保留它）。不新增 STOP 触发器，只是让事后分析更好做。
- 增加遥测，统计逐用例的重复异常类型运行次数，并在评测章节里作为"这是未来的检测器本可以
  抓到的东西"呈现出来 —— 这是度量，不是行为变更。
- 往 `task_intent.py` 的 few-shot 提示词里补充更多样例，让 IntentStage 分类更准（仍然只有
  NORMAL_EXECUTION / EXPECTED_ERROR / TRACEBACK_DEMO / RECOVERABLE_DEMO / STRESS_TEST /
  SANDBOX_ESCAPE 这几个值 —— 不新增枚举成员）。这是在收紧已有的主动 STOP 路径，而不是新增
  路径。

---

## L4. 构造器里的 `max_retries` 默认值（=2）与 `config.max_retry`（=3）不一致

### 症状

有三处构造器 / 参数签名带着同一个硬编码的最大重试默认值（第四处 `PolicyEngine` 是个
没人消费的包装层，已被删除）：

| 位置 | 签名 |
|---|---|
| `reforge/runtime/policy/retry_policy.py:25`（`RetryPolicy.decide()` 参数） | `max_retries: int = 2` |
| `reforge/runtime/orchestration/governor/policy_stage.py:12`（`PolicyStage.__init__`） | `max_retries: int = 2` |
| `reforge/runtime/orchestration/governor/engine.py:31`（`ExecutionGovernor.__init__`） | `max_retries: int = 2` |

生产环境的运行时路径走的是
`reforge/runtime/orchestration/graph/nodes/retry_decision.py:74`：

```python
governor = ExecutionGovernor(max_retries=config.max_retry)
```

它读的是 `config.max_retry = int(os.getenv("MAX_RETRY", "3"))`（`reforge/config.py:18`）。
旁路分支 `_naive_resolution`（`retry_decision.py:50`）同样直接读 `config.max_retry`。
**所以在生产环境中，这四个构造器默认值全是死代码。**

### 为什么说这是一条接缝

- **测试面**：任何实例化这些类却没有显式传 `max_retries=` 的单元测试，都在悄悄地以预算 `2`
  运行，而不是生产的预算 `3`。诸如"第三次 RETRY 之后 governor 应当 STOP"这类行为断言，
  对这一次尝试的差距是敏感的。
- **未来的调用方**：任何人接一个新入口点时如果漏了配置注入，就会悄无声息地拿到预算 `2`，
  直到某个生产用例与单元测试用例差了一次重试才会察觉。

### 正确的修法（已推迟）

二选一：
- 干脆去掉默认值（把 `max_retries` 改成必填），强制每一个调用点 —— 包括测试 —— 显式传值；
- 或者在每个构造器内部惰性读取 `config.max_retry`（`max_retries: int | None = None` +
  `if max_retries is None: max_retries = config.max_retry`），从而只保留一个事实来源。

### 为什么推迟

- 它横跨多个测试夹具（`test_full_consistency_integration.py`、`test_policy_with_intent.py`
  等），适合在评测框架就位之后作为一个独立的清理 PR 落地。
- 它不在评测的度量路径上：Phase 0 / 1 / 2 全都走 `retry_decision_node`，那里注入了
  `config.max_retry`。没有任何被度量的结果依赖构造器默认值；本条目是给 reviewer 的
  可见性保险，不是校准阻塞项。

### 反模式 —— 禁止采用

- ❌ 悄悄把默认值从 `2` 改成 `3` 以"对齐生产"。这掩盖了不一致；那些钉在"2 次重试"行为上的
  测试会开始产生不同的轨迹，而没有任何人去更新断言。

### 推迟期间可接受的就地改动

- 新增的测试 / 新增的调用点显式传 `max_retries=config.max_retry`，这样死默认值的暴露面
  至少不再扩大。

---

## L5. 代码生成 LLM 在重试压力下会主动绕开长 sleep —— 超时类诱饵的预算消耗不稳定

### 症状

记录于 Phase 0 校准（`docs/eval/PHASE0_CALIBRATION.md` 观察 O1）。诱饵 D1″ 是
"Sleep for 120 seconds, then print \"ok\""，`EXECUTION_TIMEOUT` 钉在 30 秒；预注册的设计
预期朴素基线会 RETRY 4 次 × 约 30 秒 ≈ 120 秒墙钟时间，然后预算耗尽。

在 `REFORGE_GOVERNOR_BYPASS=1` 下的实测（3/3 个种子）：

| 种子 | 尝试次数 | 动作 | retry_count | policy_reason |
|---|---|---|---|---|
| 0 | 3 | ACCEPT | 2 | `naive: exit_code == 0` |
| 1 | 3 | ACCEPT | 2 | `naive: exit_code == 0` |
| 2 | 2 | ACCEPT | 1 | `naive: exit_code == 0` |

代码生成 LLM 在重试时拿到上一次尝试的 traceback（"Execution timed out after 30s"）之后，
并没有再次输出 `time.sleep(120)`。它要么缩短了 sleep，要么直接删掉，要么把脚本改写成
干净退出 —— 于是产生了 `exit_code == 0` 和一次 ACCEPT，而不是预测中的预算耗尽 STOP。

### 根因

同一个会话内的多次代码生成尝试之间没有任何确定性。每次尝试都会用一个包含先前 traceback 的
重试提示词重新调用代码生成 LLM；模型完全有自由（而且往往倾向于）通过绕开原始规格来"修好"
问题，而不是忠实地重新输出同样的代码。对于一个唯一目的就是超时的诱饵来说，"把超时修掉"
在 LLM 看来就是理性的修复行为。结果就是：**朴素基线在超时类诱饵上的预算消耗行为，
在不同种子、不同模型之间都不稳定。**

### 实际影响

- **governor 的主动 STOP 超时代码路径仍然是可验证可达的**：校准中 3/3 个 governor 种子都
  命中了 `action=STOP, failure_mode="timeout", retry_count=0`。L5 这条观察**并不**动摇校准
  的结论；它推翻的是预注册中关于"朴素基线在超时诱饵上的墙钟时间 / 尝试次数成本"的那个预测。
- **Phase 2 不能把"超时类主动 STOP 的效率优势"作为主结论。** governor（永远 1 次尝试）与
  朴素基线（2-3 次尝试，且不定）之间剩下的尝试数 / 墙钟 / token 差值是真实的，但很边缘，
  而且其量级由代码生成的随机性主导，而不是由 governor 的分类器主导。
  `docs/eval/PHASE0_METRICS.md` v4 §1 已推迟这个主结论。
- **更广泛地说：任何唯一失败机制是看门狗超时的诱饵，在语料层面都是脆弱的。** 模型总能把它
  "修好"。稳健的诱饵需要一种代码生成 LLM 无法在不改变答案正确性的前提下绕开的失败模式 ——
  而当前运行时并没有这样一类既非超时、又非意图驱动、且它能识别的诱饵（详见
  `docs/KNOWN_LIMITATIONS.md` L3）。

### 正确的修法（已推迟）

存在两条并不互斥的路径：

- **钉死代码生成的确定性**（temperature=0 + 通过 LLM 客户端固定 seed）。这能降低 D1″ 式
  诱饵在种子间的方差，但消除不了模型"把超时修掉"的倾向 —— 只是让同一种修法每次都发生。
- **用对抗正确性的诱饵取代超时诱饵设计** —— 即那种不产生可验证的错误答案就无法绕开的任务
  （例如一个确实需要等待沙箱无法提供的外部事件的任务，配上一个会拒绝任何其他输出的比较器）。
  难点在于构造时不能泄漏意图。

### 为什么推迟

- Phase 0 校准结论是 GO；校准的各道门禁并不依赖朴素基线的预算消耗行为，只依赖 governor
  一侧的主动 STOP 路径可达。
- Phase 2 的主结论已收敛为"恢复质量"这一项（PHASE0_METRICS v4 §2）；被 L5 这条动摇的
  "主动 STOP 效率"叙事本来就已经被砍掉了。
- 重新设计超时诱饵是语料问题，不是运行时问题。如果未来某个评测切片确实需要专门度量主动
  STOP 的效率，可以再回来处理 —— 那时它需要自己的设计 + 校准过程。

### 触发重审的条件

- 未来某次评测明确需要度量主动 STOP 的效率（与恢复质量分开），例如那种"不可恢复任务的墙钟
  时间决定 SLA"的生产重试成本分析。
- 代码生成模型换成了一个"超时下的重试行为有文档、可复现"的模型。

### 反模式 —— 禁止采用

- ❌ 用系统提示词指令强迫代码生成 LLM"在重试时重新输出相同代码"。这是用提示词工程掩盖底层
  的语料脆弱性；下一次换个模型做评测，问题就又回来了。
- ❌ 在校准 / Phase 2 的门禁里断言朴素基线在 D1″ 上必须以 STOP 收场。模型有权 ACCEPT；
  该门禁要满足的条件是"governor 主动 STOP"，而不是"朴素基线不 STOP"。
- ❌ 悄悄调高 `EXECUTION_TIMEOUT`，让每次尝试的超时"更便宜"。纯化妆 —— 它并不改变模型
  "把超时修掉"的倾向。

### 推迟期间可接受的就地改动

- 给 `CalibrationRecord` 增加更多诊断字段，让这个观察不用重跑就能看到（已完成 ——
  现在会捕获 actions / retry_count / policy_reason）。
- 在 PHASE0_CALIBRATION 中记录这个观察（已完成，即 O1）。

---

## L6. governor 的恢复能力上限，被内部 LLM 评估器的精度卡住

### 症状

记录于 Phase 0 校准（`docs/eval/PHASE0_CALIBRATION.md` 观察 O2）。在 governor 模式下的
`bird_1313_student_club` 上，3 个种子都在某次尝试中产出了 SQL 比较器判定正确的结果行，
然而：

| 种子 | 尝试次数 | 动作 | policy_reason | runtime_outcome | passed（比较器） |
|---|---|---|---|---|---|
| 0 | 4 | STOP | `evaluation_failed` | FAILED | True |
| 1 | 4 | STOP | `evaluation_failed` | FAILED | True |
| 2 | 4 | STOP | `evaluation_failed` | FAILED | True |

运行时内部的 LLM 评估器（`state.semantic_state.evaluation_result.passed`）在那次尝试上返回了
`False`，而那次尝试的输出随后被 SQL 比较器（真值来源）确认为正确。PolicyStage 因此走了
`if evaluation and not evaluation.passed: RETRY "evaluation_failed"` 分支
（`retry_policy.py:50-51`），governor 一路 RETRY 到 `retry_count == max_retry`，最终发出
`retry_limit_reached_on_eval_fail` STOP，并记为 `runtime_outcome == "FAILED"` ——
明明答案是对的，却把这个用例记成了失败。

### 根因

`SemanticState.evaluation_result` 是由一个基于 LLM 的评估器设置的，而不是确定性比较器。
LLM 评估器有可测量的假阴性率，在那些结果行本身正确、评估器却在格式、列别名呈现方式或
NULL 处理上挑刺的微妙 SQL 输出上尤其明显。运行时无法区分"评估器真正拒绝的输出"和
"一次假阴性"；它只能照单全收评估器的信号，于是就重试。在经历 `max_retry` 轮
"评估器拒绝 → governor 重试"之后，用例带着评估器给的理由 STOP。

### 实际影响

- **governor 的恢复率带着一个由 LLM 评估器精度决定的隐性天花板。** 评估器假阴性 ⇒
  governor 在一个已经解决的用例上白白烧重试次数 ⇒ 一旦预算耗尽，即便答案正确，用例仍被
  记为 `runtime_outcome="FAILED"`。
- **`runtime_outcome` 和 `policy_reason` 不是 Phase 1 BIRD 报告里可靠的通过/失败信号。**
  按 `docs/eval/PHASE0_METRICS.md` v4 §3，Phase 1 BIRD 的度量被锁定在 SQL 比较器
  （`reforge.runtime.sql.comparator`）上。
- **与朴素基线的配对差值部分被隔离。** 朴素基线不咨询 LLM 评估器（`_naive_resolution`
  只读 `exit_code`），所以它不会遭受 L6 式的假阴性重试。这种不对称可能朝两个方向起作用，
  取决于用例形状：
  - governor 在已解决用例上浪费的重试，会推高 `attempts_per_case` 和
    `tokens_per_solved`，对 governor 不利；
  - 朴素基线对评估信号一无所知，意味着它会在 `exit_code=0` 的输出上直接 ACCEPT，
    而那些输出按比较器可能是悄悄错的 —— 以质量为代价虚高了朴素基线的表观解决率。

  因此必须有 Phase 1 的敏感性附录（PHASE0_METRICS v4 §4）来量化假阴性率并检验主结论的
  稳健性。

### 正确的修法（已推迟）

最干净的修法是做一个混合评估器：当任务本身提供了确定性判据时（SQL 比较器或任何其他
确定性神谕）就信它，只有在没有比较器可用时才回退到 LLM 评估器。
`reforge.runtime.sql.comparator` 已经编码了 BIRD 的真值比对逻辑；把它接进 PolicyStage 的
"我该不该 RETRY"决策里，就能在不改变 governor 恢复质量叙事的前提下，关掉 SQL 领域绝大部分
的假阴性暴露面。

次要方案：调整 LLM 评估器的提示词，让它在格式 / 别名 / NULL 细节上不那么严苛（观察到的
假阴性大多出在这些地方），或者在策略中降低评估驱动重试相对于执行错误重试的权重。

### 为什么推迟

- **会在实验中途改变被测系统。** 两种修法都要重新接线 PolicyStage；Phase 1 的消融就会变成
  "改动后的 governor vs 朴素基线"，而不是 vs v4 锁定的那个表面。
- **Phase 1 的敏感性附录就是 v4 锁定的缓解手段。** 它把假阴性率显式呈现出来，让主结论可以
  被限定说明，而不是被悄悄影响。
- **不是校准的阻塞项。** 校准全程用 SQL 比较器给 BIRD 评分，所以 L6 这条不影响四道
  go/no-go 门禁中的任何一道。

### 触发重审的条件

- Phase 1 敏感性附录显示评估器假阴性率在两种模式间是不对称的（governor 的 repair_hint 流
  招致了不成比例的评估器拒绝），或者量级足够大（例如 governor 的 STOP 中有 >20% 是"被评估器
  拒绝的正确输出"）。
- 未来某个评测领域没有确定性比较器，因而无法依赖 v4 锁定的 SQL 比较器规则。

> **触发条件已命中 —— Phase 1，2026-07-11**
> （`docs/eval/PHASE1_BIRD_ABLATION.md` 附录 D）。在比较器判定正确的尝试上，假阴性率为
> 80.8%（governor）vs 52.3%（朴素）；按种子配对的假阴性差值为 +16.0pp，95% CI
> [+11.0, +21.1] —— **不对称**，因此 Phase 1 的每一条主结论都必须带上这个限定说明。
> 用例级归因：100 次 governor 运行中有 34 次重试了一个比较器已经确认正确的第 1 次尝试答案
> （其中 3 次在重试中把正确答案弄丢了），而真正"错→对"的恢复只有 5 次。
> 后果：success_rate 差值为零（两臂均为 65.0%），代价却是 3.1 倍的 tokens-per-solved。
> 在重跑 governor-vs-naive 轴之前，评估器校准现在是必须先做的阻塞性修复；下面的反模式仍然
> 适用（要在留出数据上修评估器，不能在这套语料上修）。
>
> **阻塞性修复已合入 —— 2026-07-11**
> （`docs/eval/EVALUATOR_CALIBRATION.md`）。归因：Phase 1 的假阴性 100% 来自基于长度的检查
> 惩罚了那些符合契约的标量答案（"Print nothing else" 本来就是任务自己的指令）。修法：
> 评估器现在会识别请求中的显式输出契约，并在识别到时挂起长度 / 数字合理性检查
> （空输出、traceback、退出码的检查不变）。在 300 道留出题库问题上验证（seed 20260711，
> 未触碰已选用例）：假阴性率 42.7% → 0.0%，拒绝完整性 0 例失败。该评测轴已获准重跑；
> 但**不得**用旧记录重算主结论（评估器会驱动运行时的重试行为，所以只有重新跑一遍才能度量
> 修好之后的系统）。
>
> **第 2 轮实测确认修复有效 —— 2026-07-11**
> （`docs/eval/PHASE1_BIRD_ABLATION_R2.md`）。5 个种子上两臂的评估器假阴性率均为 0.0%，
> 敏感性结论为**对称**；比较器通过而运行时判 FAILED 的运行数为零（L6 的症状已消失）。
> L6 剩下的暴露面只有假*阳性*那一侧：基于规则的评估器无法察觉一条语义错误但干净退出的
> 查询 —— 这也正是 SQL 比较器仍然是 benchmark 评分权威判定字段的原因。

### 反模式 —— 禁止采用

- ❌ 拿 `runtime_outcome` 或 `policy_reason` 当 Phase 1 BIRD 的通过/失败信号。v4 §3 已把
  SQL 比较器锁定为权威判定字段。
- ❌ 悄悄禁用 LLM 评估器的 RETRY 分支（`retry_policy.py` 第 50-51 行）来掩盖假阴性压力。
  那是在藏起一个 governor 的失败模式，而不是报告它。
- ❌ 为了降低本研究中观察到的假阴性率，就在评测语料上放宽 LLM 评估器的提示词。那是在评测
  语料上调参，违反 v3 的预注册约定（"不得在评测数据上调参"）。

### 推迟期间可接受的就地改动

- 增加诊断字段，逐次尝试地记录比较器与 LLM 评估器的分歧，好让敏感性附录拿到它需要的数据。
- 在**非评测**语料上（演示 / 回归套件）收紧 LLM 评估器的提示词，只要这些改动不流入
  Phase 1 / Phase 2 的 governor 表面。

---

## L7. 视觉代码生成没有 CLI 入口 —— 只能编程调用

### 症状

CLI 的 `reforge run "<prompt>"`（`reforge/cli/commands/run.py`）不接受图片附件。那些需要
路由到多模态代码生成 LLM（`LLMClient.for_vision_codegen().chat_multimodal(...)`）的视觉复刻
任务，只能通过编程方式发起：

```python
RuntimeRunner().run(user_request, image_inputs=["/abs/path/target.png", ...])
```

### 历史

在 `image_inputs` 重构之前，视觉复刻可以通过一个文件系统约定从 CLI 触达：用户 cd 进一个
工作区，手动把 `target.{png,jpg,jpeg,webp}` 放进 `cwd()`，然后运行 `reforge run "复刻 …"`。
那时有一个 `vision_routing_node` 做双重门匹配（对请求做视觉意图正则 × 扫描文件系统找那几个
魔法文件名），再把路由决策写到状态上供 `code_generation_node` 消费。

这条隐式路径已被移除。现在的路由由调用方显式提供的 `state.image_inputs` 声明驱动，而不是
靠"从散文里猜意图 + 扫描工作区"。区分"用户声明的输入图片"和"数据任务产出的、恰好躺在工作区
里的 PNG"现在是结构性的（只有调用方声明的东西才会进 image_inputs；`RuntimeRunner.stream`
里的循环边界不变量会阻止任何图节点改写该字段）。代价是：CLI 在同一次改动中失去了它唯一的
视觉入口。

### 正确的修法（已推迟）

给 `cli/commands/run.py` 加一个 `--image PATH` 参数（可重复以传多个输入）：

```bash
reforge run --image ./target.png "复刻 target.png 前端页面"
```

该参数收集成一个列表，透传给 `RuntimeRunner.run(image_inputs=...)`。路径存在性校验和基本的
图片格式检查属于 CLI 边界的职责，不属于 Runner。

### 为什么推迟

- **范围纪律。** `image_inputs` 重构是一次路由 / 状态形状的改动，并且立了一道"不碰邻近文件"
  的硬围栏。捆绑一个 CLI 参数进去会让 diff 膨胀，还会引入与路由决策无关的测试扰动。
- **没有任何被度量的评测路径依赖它。** Phase 0 / 1 / 2 的语料都是 SQL 和 pandas/CSV
  （无图片输入）；校准驱动器直接走 Python API，不走 CLI。
- **视觉自愈演示用 Python API 就够了。** 编程调用方（包括现有的视觉复刻工作区）改一行就能
  切到 `RuntimeRunner.run(..., image_inputs=...)`；CLI 参数是便利性，不是解锁项。

### 触发重审的条件

- 某个面向用户的演示 / 文档流程需要用 `reforge run`（CLI）来覆盖视觉复刻循环。
- 某个被度量的评测轴加入了带图片的任务，并且需要通过 CLI 做 case-loader 驱动的调用。

### 反模式 —— 禁止采用

- ❌ 在 CLI 里重新引入文件系统扫描或视觉意图正则，作为"没传 `--image` 时的兜底"。这会把
  本次重构刚拆掉的那道门原样复活，并重新打开"数据任务产出 PNG"的误报暴露面。
- ❌ 从环境变量或工作区本地配置文件读取 `image_inputs`，当作 CLI 侧的垫片。同一个反模式
  换了顶帽子 —— 它让决策走隐式通道，而不是走显式的关键字参数。
- ❌ 把这个参数和路由重构放在同一个 PR 里。路由重构之所以 diff 可审，靠的正是那道"不碰邻近
  文件"的围栏；而 CLI 接口变更牵扯到参数命名、校验、`--image @file.list` 展开等问题，
  值得有它自己的 PR 来讨论。

### 推迟期间可接受的就地改动

- 更新 README / 文档里的 Python API 示例，演示 `image_inputs=...` 关键字参数的用法。
- 在 `RuntimeRunner.run` 上加一段 docstring，指向 L7 这条，说明为什么目前还没有等价的 CLI
  参数。

---

## L8. `ExecutionMemory.recall_similar()` 匹配的是失败的*形状*而非身份 —— 可能注入一条键在错误具体值上的提示

### 症状

`_score()`（`reforge/memory/execution_memory.py`）对每个结构字段独立计分 ——
`error_class`、`root_cause`、`domain`、`failure_mode` 各自贡献自己的权重，互不影响。
而那些标识具体身份的字段（`missing_key` / `missing_module` / `missing_file` /
`undefined_name`）只有在字符串完全相同时才贡献权重；一旦取值不同，就只是把这一个字段的权重
略去 —— 其余结构字段照样匹配，于是结构得分依然稳稳为正，该记录仍然会作为最优提示被召回。
注意准入门禁**并不能**拦住这种情况：门禁要求的是结构得分非零，其目的是防止仅凭虚词的文本
重叠就让记录通过，而这里的结构得分是货真价实的非零 —— 错误类相同、根因相同、领域相同。
把门禁再收紧是下面那个被推迟的修法，而不是现有门禁已经在做的事。这条路径上没有任何嵌入或
语义模型（`reforge/memory/retrieval.py` 的 docstring：「No embedding. No vector DB.
Pure heuristic ranking.」）—— 在这个代码库里，"相似问题"的含义是"失败的形状相同"，
而不是"底层成因相同或相关"。

线上确认（2026-07-21）：某个会话为 `KeyError: 'user_id'` 播种了一条 `RECOVERED` 记录
（修复方式：内省并改名为 CSV 里真实的表头）。之后一个毫不相关的会话在
`KeyError: 'order_id'` 上失败（不同的 CSV、不同的列、不同的任务），却召回了同一条记录 ——
重试提示词的 `repair_hint` 内容是 `"...for uid in df['user_id_column']: print(uid)"`，
点名了一个在第二个任务里根本不存在的列。原始证据见 `runs/dc4cb32a/code.txt`（播种会话）和
`runs/3c9cc5ae/code.txt`（错配召回，`[repair_hint used]` 那一行在持久化的逐次尝试代码里
清晰可见 —— 参见 `CHANGELOG.md` 里关于 `code.txt` 持久化的条目）。

### 更新（2026-08-02）：准入门禁收紧；`domain` 被降级

上文描述的准入门禁已改变。`recall_similar` 现在的准入条件是**至少命中一个
*有资格*的结构信号**（failure_mode / root_cause / 某个具体的指纹字段），
而不再是 `structural > 0`；`domain` 不再赋予入场资格 —— 它只作排序 tie-breaker。
理由：在当前收窄到单一语言（Python 脚本生成）的场景里，`domain` 在几乎所有
记录上恒定，作为准入条件会退化成一个恒真谓词（参见 `execution_memory.py` 里
`_QUALIFYING_FINGERPRINT_KEYS` 的注释，以及 L10 —— 那条讲的是**没有**一并收紧
的规划/CLI 姊妹路径）。这**并不**关闭 L8：上文那个已确认的案例仍会凭
`error_class` + `root_cause` 获得资格，所以"形状相同、身份不同"的错配依旧成立。
上面的症状文字按原样保留；其中说门禁"要求结构得分非零……领域相同"之处，
`domain` 现在已不参与准入（只参与排序）。

### 真正阻止它污染结局的是什么

不是召回精度 —— 而是代码生成把 `repair_hint` 当作宽松的提示上下文，而非字面补丁。在这个已
确认的案例里，重试尝试生成的代码丢掉了提示中错误的具体细节（`user_id_column`），只保留了
它的通用策略（内省 `df.columns`，自适应匹配），因为 LLM 有权无视提示中的任何部分。这是
**偶然的安全，不是设计出来的安全**：换一种形状的错配，或者换一个不那么谨慎的代码生成提示词，
就可能把一个错误的字面值原封不动地传播进生成代码里。

### 正确的修法（已推迟）

要求那些标识身份的具体值必须匹配上 —— 精确匹配，或者经过一次廉价的归一化（大小写折叠、
下划线/空格折叠）—— 才允许给出高于"仅 `error_class` 匹配"的得分；或者把提示注入的门禁提到
一个明显高于"形状相同、身份不同"所能拿到的分数线之上。

### 为什么推迟

- 没有实测证据表明它会改变结局。真实的恢复率本来就低（R2：3/100 次运行；Phase 1 BIRD：
  5/100 次运行）；而上文观察到的代码生成缓解行为意味着，身份门禁本可预防的那种失败模式
  （错误字面建议导致重试被污染）目前只被证明是*可能发生*，尚未被证明*确实发生过*。
- 收紧匹配也会丢掉真实的跨用例价值：两次失败具体缺失的键不同、但实际根因相同（例如某个
  CSV 加载器假定了错误的日期格式，在下游表现为不同的 `KeyError`），目前是能共享一条提示
  并从中获益的；身份门禁会把这种配对也一并丢掉。
- 它不在评测的度量路径上 —— Phase 0/1/2 的语料本来就不是为探测召回精度而设计的。

### 触发重审的条件

- 出现一个专门用来隔离召回精度的被度量评测轴（例如配对那些"结构相同但因果无关"的失败），
  并且显示这种错配确实在劣化重试结局，而不只是产生了一条没被采纳的提示。
- 某套语料带出了一种更字面地遵循提示的代码生成提示词风格，从而抹掉本条目当前所依赖的那份
  偶然安全。

### 反模式 —— 禁止采用

- ❌ 把这条当成"记忆系统不管用"的证据。这个已确认的案例同时也证明了 `RECOVERED` 写入路径
  端到端是通的 —— "召回了错误细节"和"召回机制正常工作"并不互斥；同一份证据的另一种读法，
  见 `CHANGELOG.md` 里关于 `code.txt` 持久化的条目。
- ❌ 通过给常见标识符变体硬编码一份语义映射（`user_id`/`uid`、`order_id`/`oid` 等）来
  "修复"它。那是把特例包装成修复，还悄悄写死了一套换套语料就不成立的假设。

---

## L9. 被点名的输入不存在时会被当作可恢复失败 —— 运行时可能"恢复"一个本该硬失败的任务

### 症状

`csv_recovery_missing_file`（`reforge/benchmark/cases.py:71`）要求运行时读取
`nonexistent_data.csv`，而这个文件并不存在。该用例声明了 `expected_outcome="FAILED"` ——
"文件确实不存在 —— 应当在重试耗尽后失败"。而在记录在案的那次运行中
（`docs/benchmark_sample.md`），它以 2 次尝试返回了 `RECOVERED`，评估分 1.00，
在测试套件里被标记为 FAIL 仅仅是因为期望值 ≠ 实际值。运行时并没有报告它做不了这个任务；
它为一个输入根本不存在的任务，产出了一个通过的答案。

### 根因

整条流水线里没有任何环节能把"这个请求点名的输入不存在"与一次普通的可恢复执行错误区分开。
`FileNotFoundError` 映射到根因 `missing_file`（`reforge/memory/fingerprint.py:204`）——
一个和其他指纹一样正常参与召回和 `repair_hint` 生成的可恢复指纹。`TaskIntent`
（`reforge/runtime/policy/task_intent.py:15-20`）里没有任何一个取值覆盖"请求点名了一个不存在
的输入"这种情况，因此 governor 那条由意图驱动的 STOP 路径永远不会生效，重试就照常消耗预算。
接下来代码生成就可以自由地用别的方式去满足这个请求 —— 而对于这一类请求来说，这恰恰是
绝不该发生的行为。

### 正确的修法（已推迟）

把"声明的输入不存在"归类为**前置条件**失败，而不是执行失败 —— 在第一次尝试之前或之时，
对照任务声明的输入检查一次 —— 这样 governor 就会停止而不是重试。这属于意图/前置条件这条轴，
不属于重试策略那条轴：给定它拿到的分类结果，重试循环的行为是完全正确的。

### 为什么推迟

- 证据只有一次描述性运行（n=1，无多种子，无置信区间）。它是一个调优信号，不是一个实测的
  缺陷率。
- 夹具本身很弱 —— 同一家族的兄弟 experience-benchmark 夹具在
  `docs/experience_benchmark.md` §8.5 里已被标记为"太容易"并排期返工；对着一个已经排定要改的
  夹具去钉行为，等于把错误的目标编码进代码。
- 不在预注册的评测路径上：Phase 0/1 的 BIRD 语料里没有"输入缺失"类用例，所以这条轴背后没有
  经过校准的度量工具（见 `docs/eval/PHASE0_CORPUS.md`）。
- 前置条件门禁有实实在在的误 STOP 成本：那些合法地"先创建文件再读取"的任务，在检查时刻看起来
  和缺失输入一模一样。

### 触发重审的条件

- 返工后的夹具（带种子、可重复）显示这种过度恢复是可复现的，而不是单次运行的偶发产物。
- 某个真实负载产出了一个建立在替换输入之上的、自信但错误的答案 —— 也就是说过度恢复已经影响到
  最终答案，而不只是结局标签。

### 反模式 —— 禁止采用

- ❌ 把 `FileNotFoundError` 全局设为不可重试。那会杀掉这个指纹存在的意义所在的那类合法恢复
  （下划线 vs 连字符的路径笔误用例，`experience_cases.py:106`），那是真正的修复，不是过度恢复。
- ❌ 对请求做关键词匹配，去找 "nonexistent" 或 benchmark 里的文件名。那是把 benchmark 钉死，
  而不是把行为钉住。
- ❌ 把这条读成"自愈不管用"的证据。同一次运行正确恢复了其他每一个 `csv_recovery` 用例；
  缺陷在于边界划在哪里，而不在循环本身。

---

## L10. 两条记忆召回路径采用了不一致的准入策略

### 症状

两套独立的召回子系统，准入门禁各不相同：

| 路径 | 入口 | 准入 | 喂给谁 |
|---|---|---|---|
| repair_hint | `ExecutionMemory.recall_similar`（`reforge/memory/execution_memory.py`） | 必须命中 ≥1 个*有资格*的结构信号（failure_mode / root_cause / 某个具体指纹字段）；`domain` 与请求词重叠只作排序 tie-breaker | `ClassifyStage` → `ctx.repair_hint` → 下一次重试的代码生成提示词 |
| 规划 / CLI | `MemoryRetriever.search`（`reforge/memory/retrieval.py`） | `score > 0`，其中请求词重叠（`×0.3`）、tag 重叠（`×0.5`）、`domain`（`+3.0`）**各自独立**累加进这个分数 —— 候选可以仅凭低特异性信号就入场 | `CompositeMemorySubstrate.recall / recall_for_planning` → `PlannerMemoryContext.build`；`cli/commands/history.py` 展示 |

截至 2026-08-02，repair_hint 路径已收紧为"结构信号存在性"准入（方案甲，见 L8
的日期更新）。规划/CLI 路径被**有意**保留在 `score > 0`。

### 为什么没有把两者统一（写前提，不只写结论）

- **消费端的影响半径不同。** `recall_similar` 的最优命中会原样作为 `repair_hint`
  注入下一次重试的代码生成提示词 —— 一条错误记录能带偏生成代码（这正是 L8）。
  而 `search` 的输出只是：(a) 把一小段"过往经验"摘要拼到*规划器*提示词前面
  （`planner_context.py` 把每条记录截断到约 60 字符），(b) 渲染一份给人看的 CLI
  历史列表。二者都是给人或规划器读的参考上下文，不是字面补丁 —— 噪声容忍度显著
  更高，同样的准入宽松在那里代价更小。
- **查询接口不共享。** `search` 是针对自由文本查询打分，并不携带带类型的
  `failure_mode` / `problem_signature` 参数，所以"命中结构信号"的资格概念无法
  直接套上去，除非同时改它的签名和所有调用点。
- **统一需要同步第三个打分器。** `retrieval._score` 被 `sqlite_substrate._score`
  刻意镜像（"mirrors MemoryRetriever._score() 以保证两个后端的检索质量一致"）。
  任何对 `retrieval.py` 的准入改动都必须同步到两处，否则 JSONL 与 SQLite 两个
  后端排序会分叉 —— 这是一项比 repair_hint 修复更大、需要单独测试的改动。

### 正确的修法（已推迟）

若日后证明规划/CLI 路径确实注入了误导性上下文，就给 `MemoryRetriever.search`
（及其 `sqlite_substrate` 镜像）套上同样的资格/tie-breaker 拆分：凭结构信号命中
准入，把 `domain` 和词/tag 重叠降级为仅参与排序。

### 为什么推迟

- 没有实测证据表明规划/CLI 的噪声会改变结局；规划器把这些记录当作宽松提示，
  并且做了大幅截断。
- 这项改动横跨两个互为镜像的打分器外加 `search` 的签名与调用点 —— 超出了
  repair_hint 修复的范围，而后者是有意保持最小的。

### 触发重审的条件

- 出现一个被度量的规划质量轴，显示低特异性召回在劣化计划；或
- CLI 历史视图被改作自动化信号使用，而不再只是给人读的展示。

### 反模式 —— 禁止采用

- ❌ 把 `recall_similar` 的门禁直接复制到 `search`，却不同步更新
  `sqlite_substrate._score`。那会悄悄让两个后端分叉 —— 正是那条镜像注释要防的事。
- ❌ 在 `search` 里改成一个裸的数值阈值（`score > K`），而不去拆分资格与 tie-breaker。
  那会把方案甲在 repair_hint 路径上刚去掉的"权重调参与准入相互耦合"重新引回来。

---

## L11. Docker 隔离止步于 capability 剥离 —— `--user` 与挂载分离是一次协同改动，不是两个 flag

### 症状

`DockerBackend`
（`reforge/runtime/infrastructure/execution/backends/docker_backend.py`）限制了
network、memory、cpu、pids，剥离了全部 Linux capability，并封住了 setuid 提权。
仍有两处缺口，且哪一处都无法单独闭合：

| 缺口 | 现状 | 暴露了什么 |
|---|---|---|
| 进程身份 | 容器内以 **root** 运行（无 `--user`） | 容器内一切可写之物都对 uid 0 可写 |
| workspace 挂载 | `-v <workspace>:/work` —— **可读写、整棵目录树** | 生成的代码可以修改或删除调用方项目里的任何文件 |

第二处更严重：这个 backend 隔离的是 workspace *周围*的宿主，却把 workspace 本身
——里面有价值的那部分——整个交了出去。

值得记下这处误读存在了多久：那个挂载曾被当作隔离的*证据*（架构表格里写成
`full (-v workspace:/work)`），而同一模块的类 docstring 却写着根文件系统可写。
同一个误解在三个地方各写了一遍 —— 模块 docstring、类 docstring、架构表格 ——
因此它不是笔误，而是当时真实的认知；并且项目里没有任何机制去比对"同一件事的两处
描述"是否自洽。只要没人同时读到两处，矛盾就能一直存活。

### 为什么它们不能分开落地

- **单独加 `--user` 会打断写文件。** 挂载在宿主上的属主是调用者；容器内以 uid 1000
  运行的进程对它没有写权限。Linux 上每一个要写输出文件的脚本都会以 EACCES 失败。
  而 Docker Desktop（Windows/macOS）的文件共享层通常会掩盖这一点 —— 于是这个 flag
  在开发机上看起来无害，到 Linux CI 或 Linux 部署上才炸。
- **单独做挂载分离仍留着 root。** 拆成 `/input:ro` 加一个可写输出目录，消除了破坏
  风险，但没有消除权限风险。
- **`--user` 需要挂载分离提供的东西。** 一个非 root 的 uid 要想写入任何东西，就需要
  一个由*我们*创建、属主可控的目录 —— 那正是拆分引入的输出目录。

### 挂载分离改的是代码生成契约，不是 flag

现行契约是"读和写都发生在 `/work`"。拆开它就改变了相对路径的含义：
`pd.read_csv("sales.csv")` 会因 `-w` 指向何处而解析到不同位置。生成的代码对新布局
一无所知，因此 `reforge/models/prompts/templates.py` 必须随之修改。成本主要在这里，
而不在 docker 参数上。

### 正确的修法（已推迟）

新增一个 opt-in 的严格模式，而不是改默认行为：

```python
DockerBackend(isolation="strict")   # --user + /input:ro + 可写输出目录
DockerBackend()                     # 行为不变 —— 当前默认
```

保持默认不动之所以重要，是因为上述失败模式依赖平台：一旦翻转默认，会打断 Linux
用户，同时在 Windows 或 macOS 开发机上跑的每一次测试都照样通过。

### 为什么推迟

- 跨层：docker 参数 + 提示词模板 + 生成代码里的路径约定。
- 它可能造成的破坏在主开发平台（Windows）上不可见，因此必须补一轮 Linux 验证才可
  信赖。
- 当前没有任何用例在运行敌意代码；该 backend 自己的 docstring 已把适用范围限定为
  "我们愿意直接运行的代码"。

### 触发重审的条件

- 运行时被指向来源不可信的代码（共享 demo、多租户部署、用户提交的任务）；或
- 真实发生一次生成代码损坏调用方 workspace 文件的事故。

### `--read-only` 是另一件事，维持 non-goal

不属于本条。其论证记录在它该在的地方 —— `DockerBackend` 类的 docstring —— 此处
不重复展开。

### 反模式 —— 禁止采用

- ❌ 因为"只是一个 flag"就单独加 `--user`。它恰好是这次改动里会打断写文件的那一半，
  而且打断的平台不是它将被测试的那个平台。
- ❌ 只在 Docker Desktop 上验证严格模式。文件共享层掩盖的，正是该模式必须处理对的
  那个权限失败。
- ❌ 等它跑通之后就把默认翻成严格模式。该失败模式依赖环境；opt-in 才是让一个能用的
  默认继续能用的办法。

---

## L12. `extract_error_type` 会被含 "Error" 字样的文件路径污染

### 症状

`reforge/runtime/infrastructure/error_extraction.py` 逐行扫描 traceback，查找
`Error` / `Warning` / `Exception` 子串，返回第一个命中并向前扩展字母与点号。栈帧行
和其他行一样被扫描。若 traceback 来自一个含这些词的路径 —— `/home/me/ErrorDemo/run.py`、
`C:\work\ErrorLogs\` —— 就会在到达真正的异常行之前，从栈帧里得出
`error_type="Error"`。该值流入 `execution_node`
（`reforge/runtime/orchestration/graph/nodes/execution.py:15`），再进入失败模式分类
与指纹的退化路径。

### 当前暴露面

- **Docker backend：顺带免疫。** 程序经 stdin 传入，用户帧显示为 `File "<stdin>"`，
  不携带宿主路径。这是 stdin 改动（A5/B5）的*副作用*，不是对本缺陷的修复。
- **Subprocess backend：仍然暴露。** 它的 traceback 携带真实的临时文件路径，且
  workspace 路径会出现在用户代码的栈帧中。

### 两种修法，各有局限

| 方案 | 改动量 | 局限 |
|---|---|---|
| 跳过栈帧行 | `if line.startswith('File "'): continue` —— 约 2 行 | 只挡得住标准栈帧形状。路径出现在*消息*里（`PermissionError: ... '/x/ErrorLogs/a.csv'`）依旧会污染 |
| 锚定匹配 | 复用 `fingerprint._last_error_line` 的正则 `^[A-Za-z][A-Za-z0-9_.]*(?:Error｜Exception｜Warning)\s*:` —— 约 5–10 行 | 对真实 traceback 正确，但会拒掉当前宽松扫描仍能接受的非 traceback 文本 |

### 真正的成本在语义评估，不在代码量

`extract_error_type` 的输入是 `stderr`，而 stderr 未必是 Python traceback：shell 级
错误、库直接打印的消息、工具输出都可能进来。宽松扫描能从这些文本里提取出点东西；
锚定匹配则会返回 `default`（在 execution node 调用点是 `"UnknownError"`）。这会改变
失败模式分类，进而改变重试策略。

诚实估量：代码不到 10 行，测试 2–3 条，外加一轮针对真实 stderr 样本的召回退化评估，
数清有多少当前能定类型的失败会变成 `UnknownError`。**评估本身才是工作量。**

### 结构性观察

`fingerprint._last_error_line` 与 `extract_error_type` 在做同一件事，严格程度却相反
—— 一个锚定，一个子串扫描。合并二者是更彻底的修法，但**不建议**作为顺手改动去做：
那会改变 `error_type` 对每一个消费方的含义，影响半径大于它所闭合的缺陷。

### 触发重审的条件

- 出现一次可追溯到路径的错误 `error_type`；或
- 因其他原因已经收集了召回退化所需的样本，使这项评估几乎零成本。

### 反模式 —— 禁止采用

- ❌ 不做召回评估就直接换上锚定正则。那是拿一个罕见的污染，去换非 traceback stderr
  上系统性的类型丢失。
- ❌ 把 stdin 改动当成已经修复了本条。它只是在一个 backend 上移除了一种污染来源，
  缺陷本身分毫未动。

---

## L13. subprocess backend 把 runtime 的全部环境变量（含凭据）交给生成的代码

### 症状

`SubprocessBackend`
（`reforge/runtime/infrastructure/execution/backends/subprocess_backend.py`）
这样构造子进程环境：

```python
child_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
```

runtime 手上的每一个变量 —— `DASHSCOPE_API_KEY`、`VISION_LLM_API_KEY`、
`TAVILY_API_KEY`、`OPENAI_API_KEY`，以及启动它的那个 shell 里的一切 —— 对 LLM
生成的代码都是可见的。没有任何过滤。而这是**默认** backend。

### 风险模型不是"代码有恶意"

生成的代码不需要有敌意就能泄露一个 key。调试时一行 `print(os.environ)`、一个会
渲染局部变量的 traceback、一个把自身配置回显出来的库，都足够了。现实中的触发方式
是寻常的，不是恶意的。

泄露之后会流向哪里 —— 逐条查证，不作推测：

| 去向 | 是否携带该值 |
|---|---|
| stdout → `RuntimeState.stdout` → CLI 展示 | 是，在内存与屏幕上 |
| tracing span（`observability/tracing/collector.py:51`） | **否** —— 只记录 `stdout={len} chars`，即长度 |
| `ExecutionMemory.record(...)` | **否** —— 签名里根本没有 `stdout` 参数 |
| **stderr → `traceback`** | **是，而这才是严重的那条** |

最后一行才是要紧的路径。`execution_node` 在非零退出时执行
`traceback = result.stderr`；`ExecutionMemory.record()` 接受 `traceback=` 并把它
持久化到 `data/execution_memory.jsonl`。此后 `recall_similar` 可以把它作为
`repair_hint` 召回，而 repair_hint 会被原样注入下一次重试的代码生成提示词 ——
那个提示词是要发给外部 LLM 服务的。因此一个被打到 stderr 的凭据会落盘、跨越本次
会话存活，并可能在日后某一次运行中被传出本机。

### 整条判断赖以成立的那个查证

**生成的代码根本不需要任何凭据。** 这是查证结果，不是假定：

- `reforge/models/prompts/templates.py` 中没有任何一处引导生成代码去读 API key
  或环境变量（grep 无匹配）。
- 这些 key 的消费者是 *runtime 自身*的 skill 注册检查 ——
  `reforge/runtime/skills/builtin/__init__.py:68,73,83` 读 `TAVILY_API_KEY` /
  `VISION_LLM_API_KEY` 来决定注册哪些 skill。这发生在 runtime 进程内部，不在被
  沙箱化的子进程里。
- skill 由 runtime 通过 JSON 协议调度，并不是生成的程序去 import 的库。

所以这份暴露什么也没换来。它不是为某项能力付出的代价 —— 这正是它属于"局限"而非
"设计权衡"的原因。

**若这一点日后不再成立 —— 若生成的代码被赋予直接调用 API 的任务 —— 本条必须在
实施修复之前重新评估。** 下面的全部论证都建立在"子进程不需要任何秘密"之上。

### 正确的修法（已推迟）：白名单，默认拒绝

只传执行确实需要的那些 —— `PATH`、`PYTHONIOENCODING`、`SystemRoot`（Windows）、
`TMPDIR`/`TEMP`、`HOME`/`USERPROFILE`、`LANG`/`LC_*` —— 其余一律丢弃。

### 为什么不用黑名单或前缀过滤

过滤 `*_API_KEY` / `*_TOKEN` / `*_SECRET` 是最诱人的版本，而它是错的：
`OPENAI_ORG`、`AWS_PROFILE`、`GOOGLE_APPLICATION_CREDENTIALS`、`DATABASE_URL`
都很敏感，却一个模式都不匹配。黑名单会在每一个没人想到的变量上静默失败，而它需要
预见的名字集合，会随每一次新集成而增长。默认拒绝把这件事反了过来：失败模式从
"泄露了却什么都没坏"变成"少了个变量、当场报错"。

### 为什么推迟

与 L12 同一形状 —— 代码很短，验证很长：

- **跨平台。** 在 Windows 上丢掉 `SystemRoot` 会直接让标准库的一部分和若干
  C 扩展包崩溃。白名单必须逐平台验证，而这里的主开发平台恰好是最容易崩的那个。
- **消费者未知。** 生成的代码可能合理地依赖一些并非凭据的变量 —— 无头
  matplotlib 需要 `MPLBACKEND`、代理设置（`HTTP_PROXY`/`HTTPS_PROXY`）、
  `PYTHONPATH`。每一个都需要一次判断，而判断的依据是真实运行记录，不是推理。

### 触发重审的条件

- runtime 开始使用并非开发者本人的凭据运行（共享部署、带生产密钥的 CI、多租户
  使用）；或
- `DockerBackend` 成为默认，那会让默认路径上的这个问题消失，本条退化为
  subprocess 专属的注意事项。

### 反模式 —— 禁止采用

- ❌ 因为更快就改用黑名单。它恰好会漏掉所有没人枚举到的变量，而泄露的正是那些。
- ❌ 去 stderr 里擦除凭据，而不是从环境变量里拿掉。那只处理了众多去向中的一个，
  子进程手里依然握着那些秘密。
- ❌ 认为有 `DockerBackend` 就无所谓了。它确实不透传宿主环境，但它是 opt-in，
  默认走的是这一条。

---

## L14. MCP 客户端只对着仓库内的 fixture server 验证过 —— 若干协议缺口在接入真实 server 之前无法证伪

### 症状

`discover_and_register()`（`reforge/runtime/mcp/discovery.py`）在整个仓库里
只有一处调用：`reforge/tests/test_mcp_integration.py`。它对话过的唯一 server 是
`reforge/tests/_mcp_test_server.py` —— 一个约 150 行、仅用标准库的 fixture。
`reforge/cli/`、`reforge/benchmark/`、`scripts/`、`default_skill_registry()` 都没有
绑定任何 MCP server，也没有任何配置文件带 `mcpServers` 键。

这本身不是缺陷 —— 传输层确实经过了真子进程、真管道的端到端验证。缺陷在于这个
狭窄的接触面**掩盖了什么**：一个天生守规矩的 fixture，无法证伪客户端在面对
不守规矩的 server 时的行为。

### 待办债务 —— 真实缺口，只是当前触发不到

以下是缺陷，不是取舍。它们之所以潜伏，仅仅因为 fixture server 从不触发它们。

| 缺口 | 位置 | 触发条件 |
|---|---|---|
| `env=` 直接交给 `Popen` 而未合并 `os.environ` —— Popen 的语义是**替换**环境而非扩展 | `session.py` `connect()` | 任何需要 `PATH` 的 server。测试套件已经在手工绕过（`test_mcp_integration.py` 自己拼 `{**os.environ, ...}`）—— 测试里出现绕过写法本身就是信号 |
| `kill()` 之后没有 `wait()`，POSIX 上留下僵尸窗口 | `session.py` `shutdown()` | 一个既忽略 stdin-EOF 又忽略 `terminate()` 的 server |
| 无进程组 / job object，孙进程在 `terminate()` 后存活 | `session.py` `connect()` | 由 `npx`、`uvx` 或 shell 启动的 server，即绝大多数已发布的 server |
| JSON-RPC `error.code` / `error.data` 被拍平进 f-string，调用方无法按 code 分支 | `client.py` `request()` | 任何需要区分「方法不存在」与「参数非法」的调用方 |
| stdout 上被判为脏行而跳过的内容，既不计数也不记日志 | `client.py` `_read_loop()` | 调试一个把日志和帧混在一起输出的 server —— 目前完全是黑盒 |

### 受接入范围限制的缺口 —— 不是「我们不需要」

以下功能未实现，而诚实的表述**不是**「它们没必要」，而是：**在当前的接入范围内 ——
只有一个我们自己控制的 fixture server —— 没有任何办法证伪关于它们的判断。**
一旦绑定第三方 server，它们立刻成为真实需求，届时应当重新打开评估，而不是继续辩护。

| 未实现项 | 何时成为真实需求 |
|---|---|
| `tools/list` 分页（`nextCursor` 被忽略，只读到第一页） | server 广告的工具数超过一页时 —— 表现为静默丢工具，不报错 |
| notification 分发（`notifications/*` 读到即丢，无 handler 注册表） | server 发出 `tools/list_changed` 时；目前 `list_tools` 缓存永不失效 |
| capabilities 协商（客户端发 `capabilities: {}`，server 返回的 `capabilities` 被丢弃） | server 按声明的 capability 门控方法，或 runtime 需要 `roots`/`sampling` 时 |
| `protocolVersion` 校验（硬编码 `"2024-11-05"`，不检查 server 回包） | server 只讲更新的修订版时 —— 目前版本不匹配是静默的 |
| server 主动发起的请求（`id` 无法识别的帧被丢弃且永不应答） | server 发出 `sampling/createMessage` 或 `roots/list` 并阻塞等待回复时 |

### 为什么这个区分重要

这个区分正是本条目的意义所在。「我们不需要分页」是一个关于 MCP server 全体的
断言，而且它是错的。「我们还无法判断是否需要分页，因为我们只对话过一个有五个
工具的 server」是一个关于**本仓库现有证据**的断言，它是对的。后一种表述还自带
失效条件，前一种没有。

### 不在此列：超时

`timeout_s` 曾被 `MCPClient.request()` 接收却从未使用 —— 读循环会无限阻塞。
那一条不属于受范围限制的问题（它对 fixture server 和对任何 server 一样会触发），
现已修复：读取移到专用线程上，并真正遵守 deadline。此处记录它只为标出边界 ——
**一个承诺了自己不做的事的参数是缺陷，不是被推迟的决定**，不该出现在上面的清单里。

### 触发重审的条件

绑定了真实的 MCP server（`discover_and_register` 的 command 参数指向
`_mcp_test_server` 以外的任何东西）。那一刻起，上面「待办债务」各行不再潜伏，
而「受范围限制」各行必须逐条对照该 server 的实际行为重新评估。

### 反模式 —— 禁止采用

- ❌ 在绑定真实 server 之前就投机性地实现那些受范围限制的条目。每一条都需要一个
  具体的 server 来验证；现在就写，产出的是由想象需求支撑的、未经测试的代码。
- ❌ 拿「我们只用一个 server」当作这些缺口**无所谓的理由**。它是这些缺口无法被
  证伪的原因，而这恰恰是问题所在，不是免责。
- ❌ 在没有先解决凭据传递问题之前，就往 `MCPSession.connect()` 里合并 `os.environ`
  —— 见 L13。subprocess backend 的环境泄漏是同一个形状的问题，而 MCP server
  同样是被 spawn 出来的进程。

---

## L15. 本地与 CI 的跳过集合不同且互补 —— 没有任何一次运行执行过全部用例

### 症状

两次运行报出了相同的汇总行。它们跑的不是同一批测试。

| | passed | skipped | 那边跳了、这边没跳的 |
|---|---|---|---|
| 本地（这台机器） | 1853 | 7 | 3 条 docker 集成测试 |
| CI（ubuntu-latest） | 1853 | 7 | BIRD、Pillow、playwright |

有 4 条跳过是两边共有且有意为之的（2 条 LLM smoke、docker 哨兵、tavily ——
各自需要凭据或需要显式声明启用）。两边各自剩下的 3 条是**互补**的：docker 只在
CI 可用，而 BIRD 数据集、Pillow、playwright 只装在本地。三对三，于是两边的
`passed` 与 `skipped` 完全相同 —— 1853 和 7 —— 而底下的集合毫无交集。

在收集到的 1860 条测试中，**没有任何单次运行执行超过 1853 条**，而那两个 1853
是不同的 1853。

### 数字为什么掩盖了它

汇总行是计数，而计数在替换下不变。一条测试停止运行、另一条开始运行，两者精确
抵消。`passed`/`skipped` 无法区分"同一套测试跑了两次"和"两个不同的子集各跑了
一次" —— 只有 SKIPPED 清单能区分，而那份清单是在 1a363a0 之后才出现在 run
页面上的。这个差异正是在有人第一次读那份清单时被发现的。

### 已经缩小的部分

把 Pillow 与 playwright 加进 `[test]` extra、并让 `dev` 引用 `test` 而非重复
它之后，CI 独有的三条跳过去掉了两条。剩下的部分是不对称的，而这个不对称很重要：

| 缺口 | 方向 | 性质 |
|---|---|---|
| docker（3 条） | CI 有、本地无 | 环境**状态** —— 只是 Docker Desktop 没启动；把它开起来即可弥合，无需改代码 |
| BIRD（1 条） | 本地有、CI 无 | 环境**内容** —— 2.1 GB 数据集，被 `.gitignore`（`data/`）排除，无法低成本送上 runner |

两者方向相反，无法用同一种方式解决。把"让跳过集合一致"当作一件事来做，正是
这张表存在的目的所要防止的错误。

### 为什么 BIRD 不适合进 CI

两个独立的理由，且分量不同：

1. **体积 —— 已实测。** `data/bird` 本地为 2.1 GB（`dev.json` 本身仅 724 K，
   体量在 `dev_databases/*/*.sqlite` 上，而那正是测试需要的 —— 它的 `has_db`
   回调要去 stat 那些文件）。为了一条断言而每次下载或缓存它，不成比例。
2. **分发条款 —— 未经核实。** `data/bird` 下没有 LICENSE、README 或任何条款
   文件，也没有对 BIRD 再分发条件做过任何查证。此处记为**待核实项，而非已知
   障碍** —— 它很可能是宽松的。在任何 workflow 复制该数据集之前必须先解决它，
   但理由 (1) 本身已足以让它留在常规 CI 之外。

它守的那条测试 `test_frozen_list_matches_rule_on_real_bird_data` 断言的是：
冻结的 `PHASE1_CASE_IDS` 确实就是选取规则在真实数据集上产出的结果。规则本身的
确定性与排除逻辑由同文件另一条基于合成数据的测试覆盖，那条在 CI 上是跑的；缺
的只是与真实数据的对齐 —— 这是基准可复现性的**漂移哨兵**，不是运行时回归测试。

### 与 docker 哨兵的关系

`test_docker_is_available_where_required` 之所以存在，是因为一个全部跳过的
选择集仍然 exit 0，于是一台丢了 daemon 的 runner 会持续报绿而实际什么都没测。
那个哨兵守的是一个方向：**本该 pass 却变成了 skip**。

本条目是同一失效模式的另一面。**跳过集合是环境的函数，而没有任何机制断言它。**
哨兵覆盖了一个依赖、在一种环境下，因为有人预见到了那一次特定的丢失。其余每一个
可选依赖 —— Pillow、playwright、BIRD、tavily、网络可达性 —— 都在无声地重塑
跳过集合，没有任何信号。

### 待答问题：能否让"跳过集合漂移"成为显式信号

这是重新推理的结论，而非沿用此前对"断言恰好 N 条跳过"的否定。那种形态被否，是
因为裸计数在每次有意变更时都要改数字，且不携带语义。**集合**是另一回事：它的
diff 会指名道姓地说出什么变了。

**评估结论：值得做，但只以一种特定形态 —— CI 侧的快照比对。**

- **机制。** pytest 加 `--junitxml`，提取 `<skipped>` 用例的 node id，与一份
  入库的期望清单做 diff。刻意采用 node id 而非 `SKIPPED path:line:` 文本：后者
  的行号在测试上方任何编辑后都会漂移，是一个不携带语义的误报源。node id 只在
  测试被重命名、新增或删除时变化，而那恰恰是应当由人确认的时刻。
- **它在本次会做什么。** Pillow/playwright 那次修复会让 CI 变红并提示"这两条
  不再跳过了 —— 请更新清单"。那正是想要的结果：目前这个确认依赖于人预测出
  1855 再用眼睛核对。
- **适用范围的限制，而且这个限制是真实的。** 它只能用于 CI。本地环境不可枚举
  —— 每个贡献者机器上 docker、凭据、可选包的组合都不同，不存在单一的期望集合
  可供断言。因此它**只覆盖了本条目所说的那个不对称的一侧**。但那仍是更有价值
  的一侧：开发者持续在看本地输出，变化会被察觉；而 CI 通常只被看一眼颜色。
- **为什么不改为每个依赖配一个哨兵。** 哨兵形态严格更强 —— 它断言的是一种
  **能力**（"docker 在这里必须真的能用"），而不是一种**现象**（"这条测试跳过
  了"）。但它需要为每个依赖配一个环境开关和一条测试，还要决定哪些环境声明哪些
  依赖为必需。那是更大、更需要立场的改动；快照则是那个廉价的仪表，只报告漂移，
  不对漂移下判断。

**本轮未实施。** 它是一项自带维护面的缓解措施，不是本条限制的修复，应当作为一次
有意的改动来采纳，而不是夹带进一个不相关的提交里。

### 触发重审的条件

- 出现第三处跳过集合的不对称（第四个可选依赖，或一条新的凭据门控测试）。两处
  尚可商榷；三处就意味着上面那个快照方案不该再停留在待答问题。
- BIRD 的分发条款被确认，或该数据集出现一个足以支撑冻结列表断言的小型子集。
- 本地 Docker Desktop 变为稳定可用，那将使 BIRD 成为唯一的不对称，本条目的
  关闭成本会大幅下降。

### 反模式 —— 禁止采用

- ❌ 断言跳过的**条数**。它在替换下不变 —— 正是产生本条目的那种盲区，因为两边
  报的都是 7。
- ❌ 把"让本地与 CI 跳过相同的东西"当作一件事来做。剩下的两个缺口方向相反
  （见上表）。
- ❌ 为了让集合一致而删掉那些被跳过的测试。它们覆盖的是真实分支；问题在于它们
  停止运行时没有人被告知。
- ❌ 把相同的汇总行读作相同的覆盖。正是这个推断在此处失效了，而且它是在两个
  数字都正确的情况下失效的。

---
