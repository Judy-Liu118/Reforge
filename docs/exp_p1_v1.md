# Reforge Experience Memory Benchmark

## Overview

- Pairs run                 : **1**
- Cold-A' pass rate         : **100%**
- Warm-A' pass rate         : **100%**
- **Transfer success rate** : **+0%** (warm − cold; positive = memory helped)
- Avg attempts (cold A')    : **2.00**
- Avg attempts (warm A')    : **2.00**
- **Attempts reduction**    : **+0.00**
- Warm-A' first-try success : **0%**
- Warm-A' recall hit rate   : **100%**

## Cold vs Warm

```
Cold-A' : ██████████████████████████████ 100%
Warm-A' : ██████████████████████████████ 100%
```

## Per pair

| Pair | Fingerprint axis | Cold-A | Cold-A' | Warm-A | Warm-A' | Transfer | Att. Δ | Recall |
|---|---|---|---|---|---|---|---|---|
| `P1` | KeyError + missing_key (pandas) | PASS (a=2) | PASS (a=2) | PASS (a=3) | PASS (a=2) | — | +0 | 2 |

## Per run trace

| Pair | Leg | Case | Outcome | Attempts | Score | Recalls | Duration (s) |
|---|---|---|---|---|---|---|---|
| `P1` | cold.A | `P1_seed_orders_profit` | RECOVERED | 2 | 1.00 | 2 | 45.68 |
| `P1` | cold.A' | `P1_transfer_customers_margin` | RECOVERED | 2 | 1.00 | 2 | 53.74 |
| `P1` | warm.A | `P1_seed_orders_profit` | RECOVERED | 3 | 1.00 | 3 | 50.11 |
| `P1` | warm.A' | `P1_transfer_customers_margin` | RECOVERED | 2 | 1.00 | 2 | 36.96 |