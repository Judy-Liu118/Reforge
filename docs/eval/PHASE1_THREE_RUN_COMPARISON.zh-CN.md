# Phase 1 BIRD 消融 —— 三轮对照

> **对已有记录的整合,不是新运行。** 把三轮已发布的结果并排放在一起,不引入任何新
> 数据。同一锁定语料(20 个 case,`PHASE1_CORPUS.md`)、同样 2 臂 × 5 seed = 每轮
> 200 次运行、同一 pin 死的模型 `deepseek-v4-pro`。**轮与轮之间变的是被测运行时,
> 不是题目。** 每轮细节见三份源报告;机制说明在 `KNOWN_LIMITATIONS.md` L3 / L6。
> 这一页的目的是一次读完。

## 1. 数据来源

| 轮次 | 日期 | commit | 原始记录 | 报告 | 相比上一轮改了什么 |
|---|---|---|---|---|---|
| **R1** | 2026-07-11 | `69bc27a` | `phase1_records.jsonl` | `PHASE1_BIRD_ABLATION.md` | 首次运行 —— **校准前**评估器 |
| **R2** | 2026-07-11 | `4954708` | `phase1_records_r2.jsonl` | `PHASE1_BIRD_ABLATION_R2.md` | 评估器假阴性修复(held-out,`EVALUATOR_CALIBRATION.md`) |
| **R3** | 2026-07-13 | `bcc11fb` | `phase1_records_r3.jsonl` | `PHASE1_BIRD_ABLATION_R3.md` | 新增 **L3 repeated-signature 检测器** |

两个臂(两轮机器完全相同,只差一个环境变量,`benchmark/phase1/driver.py:255`):

- **governor** —— 完整运行时:跑一次 → 评估器判合不合格 → 不合格就带反思重试 →
  策略决定停/继续。
- **naive** —— `REFORGE_GOVERNOR_BYPASS=1`:跑一次,`exit_code==0` 就接受,不评估,
  基本单发。这是基线。

## 2. 字段示意(每一行/列是什么意思)

所有 Δ 都是 **governor − naive**,按 seed 配对(同一个 seed 同时喂两臂),对 5 个
seed 聚合。gold = BIRD SQL 比对器(`KNOWN_LIMITATIONS` L6)。

| 字段 | 含义 | 怎么算的(`benchmark/phase1/report.py`) |
|---|---|---|
| **success_rate** | 做对(gold 正确)的 case 比例,不限第几次 —— **主结论指标** | `success_rate`,L47 |
| **first_try_rate** | 第一发就做对(`passed ∧ attempts==1`) | `first_try_rate`,L51 |
| **recovery_rate** | 在*没能*首发做对的 case 里,靠重试最终做对的比例 | `recovery_rate`,L55 |
| **attempts_per_case** | 每个 case 平均尝试次数(naive ≈ 1.0 = 单发) | `attempts_per_case`,L62 |
| **tokens_per_solved** | 每解决一道题花的 prompt+completion token —— 成本轴 | `tokens_per_solved`,L73 |
| **Δ mean** | governor 减 naive,5 个 seed 配对差的均值 | `paired_deltas` + `summarise` |
| **95% CI** | 该 Δ 的 Student-t 置信区间,`df = 5−1 = 4`,`t = 2.776` | `summarise`,`experience_multiseed.py:100` |
| **verdict** | CI **不越过 0** 才判 **significant**;否则"无显著效应" | `_verdict`,L137(预注册规则) |
| **FN rate**(§4) | 评估器假阴性 ÷ comparator 判对的尝试 —— 它多常拒绝一个*正确*答案 | `fn_rate_correct_attempts`,L159 |

阅读规则(预注册):**success_rate** 上的 null 才是 headline;成本/尝试次数上的显著
差异只说明"governor 更贵",不是"更好"。

## 3. 三轮并排 headline(Δ = governor − naive)

| 指标 | R1 Δ [95% CI] | R2 Δ [95% CI] | R3 Δ [95% CI] |
|---|---|---|---|
| **success_rate** | +0.0pp [−4.4, +4.4] · null | +0.0pp [−4.4, +4.4] · null | −1.0pp [−9.1, +7.1] · null |
| first_try_rate | **−36.0pp** [−38.8, −33.2] · 显著 | −3.0pp [−10.1, +4.1] · null | −5.0pp [−9.4, −0.6] · 显著* |
| recovery_rate | **+50.8pp** [+46.0, +55.5] · 显著 | +6.5pp [−5.0, +18.0] · null | +8.9pp [−2.7, +20.6] · null |
| attempts_per_case | +1.70 [+1.62, +1.78] · 显著 | +0.32 [+0.24, +0.40] · 显著 | +0.30 [+0.12, +0.48] · 显著 |
| tokens_per_solved | **+9,854** [+9.4k, +10.3k] · 显著 | +1,842 [+1.0k, +2.7k] · 显著 | +2,707 [+1.2k, +4.2k] · 显著 |

绝对值(governor / naive):success_rate R1 65/65、R2 61/61、R3 61/62 %。
tokens_per_solved R1 14,449/4,594、R2 6,457/4,615、R3 7,351/4,644。

`*` R3 的 first_try 是勉强显著(CI 上界 −0.6,≈1 道题);见 §5 —— 它不可能来自 L3
检测器(全程未触发),最合理的解释是跑间 seed 噪声。

## 4. 为什么 R1 不可用 —— 评估器假阴性不对称

这是把 R1 和 R2/R3 分开的那一行。FN = 评估器拒绝了一个被比对器确认正确的答案。

| 轮次 | governor FN | naive FN | 配对 Δ | 判决 |
|---|---|---|---|---|
| **R1** | **80.8%** | **52.3%** | +16.0pp [+11.0, +21.1] | **ASYMMETRIC(不对称)** |
| R2 | 0.0% | 0.0% | 0.0pp [0, 0] | 对称 |
| R3 | 0.0% | 0.0% | 0.0pp [0, 0] | 对称 |

R1 里校准前的评估器拒绝了 **governor 臂 80.8% 的正确尝试**(长度/契约格式 bug,已在
`EVALUATOR_CALIBRATION.md` 修复,held-out FN 42.7%→0%)。因为这个假阴性压力对两臂
**不均等**,R1 的各臂诊断被污染 —— 它的 headline 带 ASYMMETRIC 警告并触发了 L6
重修。R2/R3 的 FN 为零,所以它们的 null 是干净的。

## 5. 逐轮解读

**R1 —— 被污染的首跑。** success_rate 已经是 null(65 = 65),但 first_try 显示
governor 29% vs naive 65%、recovery 50.8% —— *看起来*像个很强的自愈系统。这是假象:
太严的评估器拒掉了正确的首答(80.8% FN),逼出重试,于是正确答案不再计入"首发"、
转而以"恢复"的形式重新出现。算术是闭合的:governor 29% 首发 + 71%×50.8% 恢复 = 65%
—— 正是 naive 单发就到的那个 65%。**governor 花了 3.1× 的力气,回到 naive 的起点。**

**R2 —— 干净的 null。** 评估器修好后(FN→0),governor 首发从 29% 跳到 58%、恢复从
50.8% 塌到 6.5%;准确率上每个 governor−naive 差都归 null,敏感度对称。**这才是真结果:
一旦评估器不再拒绝正确答案,治理相对单发没有任何成功率增益** —— 而且贵 1.4×
(6,457 vs 4,615 token)。

**R3 —— 检测器休眠,复现 R2。** 唯一的新增(L3 repeated-signature 检测器)在 BIRD 上
**测得休眠**:BIRD 的失败几乎全是安静的干净退出错答案(31 次重试 30 次由安静触发),
响亮失败近乎没有(1 次)且从不连续两次,所以检测器没有可作用的原料。按
`KNOWN_LIMITATIONS` L3,R3 的 governor "逐个决策都做出了 L3 之前的运行时会做的同样
选择",success_rate 的 null 复现。**从结果看,R3 ≈ R2。**(唯一的名义差别 first_try
−5pp,≈1 道题,不可能由惰性的检测器造成,且落在跑间噪声内 —— 是观察,不是结论。)

## 6. 结论

1. **三轮 success_rate 全是 null** —— 治理(带反思的重试)在这个语料上相对单发 naive
   没换来任何准确率。
2. **R1 表面的自愈是评估器假象**,42.7% 假阴性 bug 一修(R2)就消失。只有 R2/R3 可用。
3. **governor 一贯更贵** —— tokens_per_solved 的差每轮都显著(R2 +1.8k、R3 +2.7k),
   换来的准确率增益为零。这正是 `PASS_AT_T_ANALYSIS.md` 用 pass@t 形式化的算力低效。
4. **R3 对 BIRD 上的 null 没有新增确认力** —— 它的新检测器在这里休眠,所以 R3 行为上
   就是 R2。R3 真正的证据是反面空间:检测器在一个触发不了它的语料上**不误伤**。它的
   价值局限于响亮且持续的失败(Phase-0 式),不是 BIRD 式。

## 7. 另见

- 各轮完整报告:`PHASE1_BIRD_ABLATION{,_R2,_R3}.md`
- 评估器 FN 修复:`EVALUATOR_CALIBRATION.md` · FP 一面:`EVALUATOR_FP_MEASUREMENT.md`
- 检测器休眠与边界:`KNOWN_LIMITATIONS.md` L3
- 算力归一(pass@t)重读:`PASS_AT_T_ANALYSIS.md`
