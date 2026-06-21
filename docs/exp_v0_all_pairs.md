# Reforge Experience Memory Benchmark

## Overview

- Pairs run                 : **5**
- Cold-A' pass rate         : **80%**
- Warm-A' pass rate         : **100%**
- **Transfer success rate** : **+20%** (warm − cold; positive = memory helped)
- Avg attempts (cold A')    : **1.80**
- Avg attempts (warm A')    : **1.80**
- **Attempts reduction**    : **+0.00**
- Warm-A' first-try success : **20%**
- Warm-A' recall hit rate   : **100%**

## Cold vs Warm

```
Cold-A' : ████████████████████████······ 80%
Warm-A' : ██████████████████████████████ 100%
```

## Per pair

| Pair | Fingerprint axis | Cold-A | Cold-A' | Warm-A | Warm-A' | Transfer | Att. Δ | Recall |
|---|---|---|---|---|---|---|---|---|
| `P1` | KeyError + missing_key (pandas) | PASS (a=2) | PASS (a=2) | PASS (a=2) | PASS (a=2) | — | +0 | 2 |
| `P2` | ModuleNotFoundError + missing_module | PASS (a=1) | FAIL (a=1) | PASS (a=1) | PASS (a=1) | PASS | +0 | 1 |
| `P3` | FileNotFoundError + missing_file | PASS (a=2) | PASS (a=2) | PASS (a=2) | PASS (a=2) | — | +0 | 2 |
| `P4` | OperationalError + sqlite table | PASS (a=2) | PASS (a=2) | PASS (a=2) | PASS (a=2) | — | +0 | 2 |
| `P5` | KeyError + missing_key (case mismatch) | PASS (a=2) | PASS (a=2) | PASS (a=2) | PASS (a=2) | — | +0 | 2 |

## Per run trace

| Pair | Leg | Case | Outcome | Attempts | Score | Recalls | Duration (s) |
|---|---|---|---|---|---|---|---|
| `P1` | cold.A | `P1_seed_orders_profit` | RECOVERED | 2 | 1.00 | 2 | 43.65 |
| `P1` | cold.A' | `P1_transfer_customers_margin` | RECOVERED | 2 | 1.00 | 2 | 33.52 |
| `P1` | warm.A | `P1_seed_orders_profit` | RECOVERED | 2 | 1.00 | 2 | 31.89 |
| `P1` | warm.A' | `P1_transfer_customers_margin` | RECOVERED | 2 | 1.00 | 2 | 50.50 |
| `P2` | cold.A | `P2_seed_import_pd` | SUCCESS | 1 | 1.00 | 1 | 29.57 |
| `P2` | cold.A' | `P2_transfer_import_np` | EXPECTED_FAILURE | 1 | 0.25 | 2 | 29.22 |
| `P2` | warm.A | `P2_seed_import_pd` | SUCCESS | 1 | 1.00 | 1 | 30.52 |
| `P2` | warm.A' | `P2_transfer_import_np` | SUCCESS | 1 | 1.00 | 1 | 31.14 |
| `P3` | cold.A | `P3_seed_sales_2024` | RECOVERED | 2 | 1.00 | 2 | 34.50 |
| `P3` | cold.A' | `P3_transfer_orders_2024` | RECOVERED | 2 | 1.00 | 2 | 32.48 |
| `P3` | warm.A | `P3_seed_sales_2024` | RECOVERED | 2 | 1.00 | 2 | 38.21 |
| `P3` | warm.A' | `P3_transfer_orders_2024` | RECOVERED | 2 | 1.00 | 2 | 43.40 |
| `P4` | cold.A | `P4_seed_sql_sales` | RECOVERED | 2 | 1.00 | 2 | 49.26 |
| `P4` | cold.A' | `P4_transfer_sql_users` | RECOVERED | 2 | 1.00 | 2 | 37.75 |
| `P4` | warm.A | `P4_seed_sql_sales` | RECOVERED | 2 | 1.00 | 2 | 41.72 |
| `P4` | warm.A' | `P4_transfer_sql_users` | RECOVERED | 2 | 1.00 | 2 | 31.83 |
| `P5` | cold.A | `P5_seed_case_revenue` | RECOVERED | 2 | 1.00 | 2 | 49.45 |
| `P5` | cold.A' | `P5_transfer_case_amount` | RECOVERED | 2 | 1.00 | 2 | 45.77 |
| `P5` | warm.A | `P5_seed_case_revenue` | RECOVERED | 2 | 1.00 | 2 | 48.55 |
| `P5` | warm.A' | `P5_transfer_case_amount` | RECOVERED | 2 | 1.00 | 2 | 62.84 |