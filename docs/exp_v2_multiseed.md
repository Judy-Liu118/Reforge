# Reforge Experience Memory Benchmark

## Overview

- Pairs           : **5**
- Seeds per pair  : **5**
- Total runs      : **100** (5 × 5 × 4 legs)

The headline KPIs below are *per-seed deltas* (each seed gives one warm-minus-cold number), then summarised across seeds. The 95% CI is two-tailed Student-t with df = n − 1. If the CI **excludes zero**, the effect is statistically distinguishable from null at α = 0.05; if it doesn't, the observation is consistent with noise.

## Headline KPIs (mean ± std, 95% CI)

| KPI | Mean | Std | 95% CI | CI excl. 0? | Verdict |
|---|---|---|---|---|---|
| Transfer success rate (Δ pass rate) | +0% | 0% | [+0%, +0%] | no | consistent with noise |
| First-try rate delta (Δ first-try) | +4% | 9% | [-7%, +15%] | no | consistent with noise |
| Attempts reduction (cold − warm) | +0.04 | 0.09 | [-0.07, +0.15] | no | consistent with noise |

## Per pair — multi-seed stats

Per-pair stats use the seed as the unit of observation. A pair with N=5 seeds and `Warm-A' first-try` mean=0.40, std=0.55 means warm hit first-try in 2 of 5 seeds — wide spread, noisy signal.

| Pair | Axis | Cold pass | Warm pass | Cold 1st-try | Warm 1st-try | Δ first-try (CI) |
|---|---|---|---|---|---|---|
| `P1` | KeyError + missing_key (pandas) | 100% ± 0% | 100% ± 0% | 0% ± 0% | 0% ± 0% | +0% [+0%, +0%] |
| `P2` | ModuleNotFoundError + missing_module | 100% ± 0% | 100% ± 0% | 60% ± 55% | 80% ± 45% | +20% [-36%, +76%] |
| `P3` | FileNotFoundError + missing_file | 100% ± 0% | 100% ± 0% | 100% ± 0% | 100% ± 0% | +0% [+0%, +0%] |
| `P4` | OperationalError + sqlite table | 100% ± 0% | 100% ± 0% | 100% ± 0% | 100% ± 0% | +0% [+0%, +0%] |
| `P5` | KeyError + missing_key (case mismatch) | 100% ± 0% | 100% ± 0% | 0% ± 0% | 0% ± 0% | +0% [+0%, +0%] |