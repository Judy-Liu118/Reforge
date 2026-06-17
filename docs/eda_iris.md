# EDA report: iris.csv

## Overview

- Dataset path: `D:\Reforge\data\eda_samples\iris.csv`
- Stages run: **8** (ok=7, recovered=1, failed=0)
- Total attempts (across all stages): **9**
- Wall time: **279.9 s**
- Generated at: 2026-06-14T15:46:43+00:00

## Per stage

| Stage | Status | Attempts | Eval | Duration (s) |
|---|---|---|---|---|
| `overview` | OK | 1 | 1.00 | 28.0 |
| `dtypes` | OK | 1 | 1.00 | 20.4 |
| `missing` | RECOVERED | 2 | 1.00 | 41.5 |
| `numeric_stats` | OK | 1 | 1.00 | 12.9 |
| `categorical_freq` | OK | 1 | 1.00 | 49.3 |
| `correlation` | OK | 1 | 1.00 | 30.2 |
| `outliers` | OK | 1 | 1.00 | 39.2 |
| `quality_warnings` | OK | 1 | 1.00 | 57.3 |

## Stage outputs

### Dataset overview (`overview`)

**Status:** OK  **Attempts:** 1  **Eval:** 1.00  **Duration:** 28.0s

```text
row_count: 150
column_count: 5
- sepal length (cm)
- sepal width (cm)
- petal length (cm)
- petal width (cm)
- species
```

### Column dtypes (`dtypes`)

**Status:** OK  **Attempts:** 1  **Eval:** 1.00  **Duration:** 20.4s

```text
sepal length (cm): float64
sepal width (cm): float64
petal length (cm): float64
petal width (cm): float64
species: object
```

### Missing-value analysis (`missing`)

**Status:** RECOVERED  **Attempts:** 2  **Eval:** 1.00  **Duration:** 41.5s

```text
Data shape: (150, 5)
Columns: ['sepal length (cm)', 'sepal width (cm)', 'petal length (cm)', 'petal width (cm)', 'species']
No missing values.
```

### Numeric summary statistics (`numeric_stats`)

**Status:** OK  **Attempts:** 1  **Eval:** 1.00  **Duration:** 12.9s

```text
sepal length (cm)  sepal width (cm)  petal length (cm)  petal width (cm)
count            150.000           150.000            150.000           150.000
mean               5.843             3.057              3.758             1.199
std                0.828             0.436              1.765             0.762
min                4.300             2.000              1.000             0.100
25%                5.100             2.800              1.600             0.300
50%                5.800             3.000              4.350             1.300
75%                6.400             3.300              5.100             1.800
max                7.900             4.400              6.900             2.500
```

### Top categories per categorical column (`categorical_freq`)

**Status:** OK  **Attempts:** 1  **Eval:** 1.00  **Duration:** 49.3s

```text
## species
species
setosa        50
versicolor    50
virginica     50
Name: count, dtype: int64
```

### Pairwise correlation (`correlation`)

**Status:** OK  **Attempts:** 1  **Eval:** 1.00  **Duration:** 30.2s

```text
petal length (cm) <-> petal width (cm): 0.963
petal length (cm) <-> sepal length (cm): 0.872
petal width (cm) <-> sepal length (cm): 0.818
petal length (cm) <-> sepal width (cm): -0.428
petal width (cm) <-> sepal width (cm): -0.366
```

### Outlier detection (`outliers`)

**Status:** OK  **Attempts:** 1  **Eval:** 1.00  **Duration:** 39.2s

```text
sepal width (cm): 1 outliers
```

### Data quality warnings (`quality_warnings`)

**Status:** OK  **Attempts:** 1  **Eval:** 1.00  **Duration:** 57.3s

```text
No quality warnings.
```

---

_Self-healing footprint: the runtime burned **1** extra attempt(s) beyond the 8 first-shots, recovering 1 stage(s) after failure._
