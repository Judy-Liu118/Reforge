# Reforge Experience Memory Benchmark

## Overview

- Pairs run                 : **5**
- Cold-A' pass rate         : **100%**
- Warm-A' pass rate         : **100%**
- **Transfer success rate** : **+0%** (warm − cold; positive = memory helped)
- Avg attempts (cold A')    : **1.60**
- Avg attempts (warm A')    : **1.60**
- **Attempts reduction**    : **+0.00**
- Warm-A' first-try success : **40%**
- Warm-A' recall hit rate   : **100%**

## Cold vs Warm

```
Cold-A' : ██████████████████████████████ 100%
Warm-A' : ██████████████████████████████ 100%
```

## Per pair

| Pair | Fingerprint axis | Cold-A | Cold-A' | Warm-A | Warm-A' | Transfer | Att. Δ | Recall |
|---|---|---|---|---|---|---|---|---|
| `P1` | KeyError + missing_key (pandas) | PASS (a=2) | PASS (a=2) | PASS (a=2) | PASS (a=2) | — | +0 | 2 |
| `P2` | ModuleNotFoundError + missing_module | PASS (a=1) | PASS (a=1) | PASS (a=1) | PASS (a=1) | — | +0 | 1 |
| `P3` | FileNotFoundError + missing_file | PASS (a=2) | PASS (a=2) | PASS (a=2) | PASS (a=2) | — | +0 | 2 |
| `P4` | OperationalError + sqlite table | PASS (a=1) | PASS (a=1) | PASS (a=1) | PASS (a=1) | — | +0 | 1 |
| `P5` | KeyError + missing_key (case mismatch) | PASS (a=2) | PASS (a=2) | FAIL (a=2) | PASS (a=2) | — | +0 | 2 |

## Per run trace

| Pair | Leg | Case | Outcome | Attempts | Score | Recalls | Duration (s) |
|---|---|---|---|---|---|---|---|
| `P1` | cold.A | `P1_seed_orders_profit` | RECOVERED | 2 | 1.00 | 2 | 39.18 |
| `P1` | cold.A' | `P1_transfer_customers_margin` | RECOVERED | 2 | 1.00 | 2 | 46.86 |
| `P1` | warm.A | `P1_seed_orders_profit` | RECOVERED | 2 | 1.00 | 2 | 42.21 |
| `P1` | warm.A' | `P1_transfer_customers_margin` | RECOVERED | 2 | 1.00 | 2 | 44.64 |
| `P2` | cold.A | `P2_seed_import_pd` | SUCCESS | 1 | 1.00 | 1 | 37.85 |
| `P2` | cold.A' | `P2_transfer_import_np` | SUCCESS | 1 | 1.00 | 1 | 37.94 |
| `P2` | warm.A | `P2_seed_import_pd` | SUCCESS | 1 | 1.00 | 1 | 40.93 |
| `P2` | warm.A' | `P2_transfer_import_np` | SUCCESS | 1 | 1.00 | 1 | 19.90 |
| `P3` | cold.A | `P3_seed_sales_2024` | RECOVERED | 2 | 1.00 | 2 | 36.76 |
| `P3` | cold.A' | `P3_transfer_orders_2024` | RECOVERED | 2 | 1.00 | 2 | 37.61 |
| `P3` | warm.A | `P3_seed_sales_2024` | RECOVERED | 2 | 1.00 | 2 | 39.56 |
| `P3` | warm.A' | `P3_transfer_orders_2024` | RECOVERED | 2 | 1.00 | 2 | 46.55 |
| `P4` | cold.A | `P4_seed_sql_sales` | SUCCESS | 1 | 1.00 | 1 | 8.17 |
| `P4` | cold.A' | `P4_transfer_sql_users` | SUCCESS | 1 | 1.00 | 1 | 9.19 |
| `P4` | warm.A | `P4_seed_sql_sales` | SUCCESS | 1 | 1.00 | 1 | 22.32 |
| `P4` | warm.A' | `P4_transfer_sql_users` | SUCCESS | 1 | 1.00 | 1 | 10.48 |
| `P5` | cold.A | `P5_seed_case_revenue` | RECOVERED | 2 | 1.00 | 2 | 38.68 |
| `P5` | cold.A' | `P5_transfer_case_amount` | RECOVERED | 2 | 1.00 | 2 | 30.31 |
| `P5` | warm.A | `P5_seed_case_revenue` | RECOVERED | 2 | 1.00 | 2 | 48.07 |
| `P5` | warm.A' | `P5_transfer_case_amount` | RECOVERED | 2 | 1.00 | 2 | 51.83 |