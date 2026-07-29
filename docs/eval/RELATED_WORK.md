# Related work & positioning

English | [简体中文](RELATED_WORK.zh-CN.md)

The BIRD governor-vs-naive null (runs 1–3, tables in the README eval section)
sits inside a known pattern in the retry / self-repair literature.

- **Reflexion** (Shinn et al., arXiv:2303.11366), Table 3 (GPT-4, 50 hardest
  HumanEval-Rust) — tests without self-reflection equal the baseline (0.60 vs
  0.60); adding self-reflection reaches 0.68. The paper notes tests and
  compilation catch the errors, but the repair action does not reflect those hints. *Relation:* the
  failure signal alone is not enough — a layer must translate it into a repair
  direction; the two ends measured here lack that middle (BIRD has no signal at
  exit 0; must_fail's traceback already carries its own fix).

- **Is Self-Repair a Silver Bullet for Code Generation?** (Olausson et al.,
  ICLR 2024) — with repair cost counted, self-repair gains are often small, vary
  widely across subsets, and are sometimes absent; GPT-3.5 on APPS is below
  same-budget i.i.d. resampling in most configurations. The bottleneck is
  attributed to the model's ability to produce accurate feedback on its own code
  (GPT-4 overall repair success 33.3% → 52.6%, 1.58×, when its own feedback is
  replaced by a human's). *Relation:* this project
  measured a null at 1.6× cost, directionally consistent.

- **Untested direction (not a conclusion)** — in Olausson et al. the *relative*
  self-repair gain grows with difficulty (GPT-3.5 on APPS: up to ~1.34× baseline
  at competition vs ≤baseline at introductory; §4.1 / Fig. 14), while the
  single-repair *success rate* falls with difficulty (Table 2, appendix: GPT-4
  28.8% introductory → 8.6% competition; GPT-3.5 13.7% → 1.5%) — different
  quantities, opposite directions. This project's corpus is BIRD-simple, at the low-effect
  end; harder tasks / domains where the model's prior is weaker are unmeasured.
