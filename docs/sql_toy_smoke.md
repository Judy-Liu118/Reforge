# Reforge SQL benchmark report

## Summary

- Cases: **3**
- Execution accuracy: **100.0%** (first-shot 33.3% + recovered 66.7%)
- Correct: 1  Recovered: 2  Wrong: 0  Error: 0
- Self-healing footprint: **6** extra attempt(s) on top of the 3 first-shots
- Wall time: **227.9 s**
- Generated at: 2026-06-14T16:29:22+00:00

## Per case

| Case | Difficulty | Status | Attempts | Eval | Duration (s) |
|---|---|---|---|---|---|
| `e01_count_students` | easy | RECOVERED | 4 | 0.50 | 124.6 |
| `e02_list_math_teachers` | easy | OK | 1 | 1.00 | 18.5 |
| `e03_avg_gpa` | easy | RECOVERED | 4 | 0.50 | 83.7 |

## Case details

### `e01_count_students` — RECOVERED

**Difficulty:** easy  **Attempts:** 4  **Eval:** 0.50  **Duration:** 124.6s

_runtime_outcome=FAILED_

**Predicted:**
```text
8
```

**Expected:**
```text
8
```

### `e02_list_math_teachers` — OK

**Difficulty:** easy  **Attempts:** 1  **Eval:** 1.00  **Duration:** 18.5s

_runtime_outcome=SUCCESS_

**Predicted:**
```text
Alice Chen
Carol Davis
```

**Expected:**
```text
Alice Chen
Carol Davis
```

### `e03_avg_gpa` — RECOVERED

**Difficulty:** easy  **Attempts:** 4  **Eval:** 0.50  **Duration:** 83.7s

_runtime_outcome=FAILED_

**Predicted:**
```text
3.6
```

**Expected:**
```text
3.6
```
