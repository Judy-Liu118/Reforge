# EDA report: wine_quality.csv

## Overview

- Dataset path: `D:\Reforge\data\eda_samples\wine_quality.csv`
- Stages run: **8** (ok=7, recovered=1, failed=0)
- Total attempts (across all stages): **9**
- Wall time: **246.8 s**
- Generated at: 2026-06-14T15:51:25+00:00

## Per stage

| Stage | Status | Attempts | Eval | Duration (s) |
|---|---|---|---|---|
| `overview` | OK | 1 | 1.00 | 30.4 |
| `dtypes` | OK | 1 | 1.00 | 24.5 |
| `missing` | RECOVERED | 2 | 1.00 | 50.5 |
| `numeric_stats` | OK | 1 | 1.00 | 19.0 |
| `categorical_freq` | OK | 1 | 1.00 | 34.0 |
| `correlation` | OK | 1 | 1.00 | 23.5 |
| `outliers` | OK | 1 | 1.00 | 29.8 |
| `quality_warnings` | OK | 1 | 1.00 | 34.1 |

## Stage outputs

### Dataset overview (`overview`)

**Status:** OK  **Attempts:** 1  **Eval:** 1.00  **Duration:** 30.4s

```text
1599
12
- fixed_acidity
- volatile_acidity
- citric_acid
- residual_sugar
- chlorides
- free_sulfur_dioxide
- total_sulfur_dioxide
- density
- pH
- sulphates
- alcohol
- quality
```

### Column dtypes (`dtypes`)

**Status:** OK  **Attempts:** 1  **Eval:** 1.00  **Duration:** 24.5s

```text
fixed_acidity: float64
volatile_acidity: float64
citric_acid: float64
residual_sugar: float64
chlorides: float64
free_sulfur_dioxide: float64
total_sulfur_dioxide: float64
density: float64
pH: float64
sulphates: float64
alcohol: float64
quality: int64
```

### Missing-value analysis (`missing`)

**Status:** RECOVERED  **Attempts:** 2  **Eval:** 1.00  **Duration:** 50.5s

```text
Loading dataset from D:/Reforge/data/eda_samples/wine_quality.csv
Data shape: (1599, 12)
Columns: ['fixed_acidity', 'volatile_acidity', 'citric_acid', 'residual_sugar', 'chlorides', 'free_sulfur_dioxide', 'total_sulfur_dioxide', 'density', 'pH', 'sulphates', 'alcohol', 'quality']
Missing value counts (all columns):
  fixed_acidity: 0 (0.0%)
  volatile_acidity: 0 (0.0%)
  citric_acid: 0 (0.0%)
  residual_sugar: 0 (0.0%)
  chlorides: 0 (0.0%)
  free_sulfur_dioxide: 0 (0.0%)
  total_sulfur_dioxide: 0 (0.0%)
  density: 0 (0.0%)
  pH: 0 (0.0%)
  sulphates: 0 (0.0%)
  alcohol: 0 (0.0%)
  quality: 0 (0.0%)

--- Final result ---
No missing values.
```

### Numeric summary statistics (`numeric_stats`)

**Status:** OK  **Attempts:** 1  **Eval:** 1.00  **Duration:** 19.0s

```text
fixed_acidity  volatile_acidity  citric_acid  residual_sugar  chlorides  free_sulfur_dioxide  total_sulfur_dioxide   density        pH  sulphates   alcohol   quality
count       1599.000          1599.000     1599.000        1599.000   1599.000             1599.000              1599.000  1599.000  1599.000   1599.000  1599.000  1599.000
mean           8.320             0.528        0.271           2.539      0.087               15.875                46.468     0.997     3.311      0.658    10.423     5.636
std            1.741             0.179        0.195           1.410      0.047               10.460                32.895     0.002     0.154      0.170     1.066     0.808
min            4.600             0.120        0.000           0.900      0.012                1.000                 6.000     0.990     2.740      0.330     8.400     3.000
25%            7.100             0.390        0.090           1.900      0.070                7.000                22.000     0.996     3.210      0.550     9.500     5.000
50%            7.900             0.520        0.260           2.200      0.079               14.000                38.000     0.997     3.310      0.620    10.200     6.000
75%            9.200             0.640        0.420           2.600      0.090               21.000                62.000     0.998     3.400      0.730    11.100     6.000
max           15.900             1.580        1.000          15.500      0.611               72.000               289.000     1.004     4.010      2.000    14.900     8.000
```

### Top categories per categorical column (`categorical_freq`)

**Status:** OK  **Attempts:** 1  **Eval:** 1.00  **Duration:** 34.0s

```text
No categorical columns.
```

### Pairwise correlation (`correlation`)

**Status:** OK  **Attempts:** 1  **Eval:** 1.00  **Duration:** 23.5s

```text
fixed_acidity <-> pH: -0.683
fixed_acidity <-> citric_acid: 0.672
fixed_acidity <-> density: 0.668
free_sulfur_dioxide <-> total_sulfur_dioxide: 0.668
volatile_acidity <-> citric_acid: -0.552
```

### Outlier detection (`outliers`)

**Status:** OK  **Attempts:** 1  **Eval:** 1.00  **Duration:** 29.8s

```text
fixed_acidity: 12 outliers
volatile_acidity: 10 outliers
citric_acid: 1 outliers
residual_sugar: 30 outliers
chlorides: 31 outliers
free_sulfur_dioxide: 22 outliers
total_sulfur_dioxide: 15 outliers
density: 18 outliers
pH: 8 outliers
sulphates: 27 outliers
alcohol: 8 outliers
quality: 10 outliers
```

### Data quality warnings (`quality_warnings`)

**Status:** OK  **Attempts:** 1  **Eval:** 1.00  **Duration:** 34.1s

```text
No quality warnings.
```

---

_Self-healing footprint: the runtime burned **1** extra attempt(s) beyond the 8 first-shots, recovering 1 stage(s) after failure._
