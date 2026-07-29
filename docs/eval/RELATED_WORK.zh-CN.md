# 相关工作与结果定位

[English](RELATED_WORK.md) | 简体中文

BIRD 上 governor-vs-naive 的 null（run 1–3，见 README 评测节的表格）落在重试 /
self-repair 文献里一个已知的规律上。

- **Reflexion**（Shinn et al., arXiv:2303.11366）Table 3（GPT-4、HumanEval-Rust
  最难的 50 题）—— 有测试但无自反思的配置与 baseline 完全相同（0.60 vs 0.60），加上
  自反思才到 0.68。论文指出测试和编译能抓到错，但修复动作没有反映这些提示。*关联：* 失败信号本身
  不够，需要一层把信号翻译成修复方向；本项目测到的两端恰好缺这个中间层（BIRD 在
  exit 0 下没有信号；must_fail 的 traceback 自带解法）。

- **Is Self-Repair a Silver Bullet for Code Generation?**（Olausson et al.,
  ICLR 2024）—— 把修复成本计入后，self-repair 的增益常常微小、在子集间差异极大、
  有时不存在；GPT-3.5 在 APPS 上多数配置低于同预算的 i.i.d. 重采样。瓶颈被归因为
  模型对自己代码产出准确反馈的能力（GPT-4 把自己的反馈换成人类反馈后，overall 修复
  成功率 33.3% → 52.6%，1.58×）。
  *关联：* 本项目测到 null 且成本 1.6×，与该结论方向一致。

- **下一步方向（未测量，非结论）** —— 在 Olausson 等人里，self-repair 的*相对增益*
  随难度上升（GPT-3.5 在 APPS：competition 达约 1.34× baseline，introductory ≤baseline；
  §4.1 / Fig. 14），而单次修复*成功率*随难度**下降**（Table 2，附录：GPT-4 28.8% introductory
  → 8.6% competition；GPT-3.5 13.7% → 1.5%）—— 两个不同的量、方向相反。本项目语料是
  BIRD-simple，落在效应最小的一端；因此“更难任务 / 模型先验更弱的领域”是尚未测量的方向。
