# EDA report: titanic.csv

## Overview

- Dataset path: `D:\Reforge\data\eda_samples\titanic.csv`
- Stages run: **8** (ok=8, recovered=0, failed=0)
- Total attempts (across all stages): **8**
- Wall time: **261.6 s**
- Generated at: 2026-06-14T15:51:37+00:00

## Per stage

| Stage | Status | Attempts | Eval | Duration (s) |
|---|---|---|---|---|
| `overview` | OK | 1 | 1.00 | 19.7 |
| `dtypes` | OK | 1 | 1.00 | 14.4 |
| `missing` | OK | 1 | 1.00 | 22.5 |
| `numeric_stats` | OK | 1 | 1.00 | 23.7 |
| `categorical_freq` | OK | 1 | 1.00 | 41.7 |
| `correlation` | OK | 1 | 1.00 | 35.3 |
| `outliers` | OK | 1 | 1.00 | 26.7 |
| `quality_warnings` | OK | 1 | 1.00 | 76.4 |

## Stage outputs

### Dataset overview (`overview`)

**Status:** OK  **Attempts:** 1  **Eval:** 1.00  **Duration:** 19.7s

```text
1309
14
- pclass
- survived
- name
- sex
- age
- sibsp
- parch
- ticket
- fare
- cabin
- embarked
- boat
- body
- home.dest
```

### Column dtypes (`dtypes`)

**Status:** OK  **Attempts:** 1  **Eval:** 1.00  **Duration:** 14.4s

```text
pclass: int64
survived: int64
name: object
sex: object
age: float64
sibsp: int64
parch: int64
ticket: object
fare: float64
cabin: object
embarked: object
boat: object
body: float64
home.dest: object
```

### Missing-value analysis (`missing`)

**Status:** OK  **Attempts:** 1  **Eval:** 1.00  **Duration:** 22.5s

```text
age: 263 (20.09%)
fare: 1 (0.08%)
cabin: 1014 (77.46%)
embarked: 2 (0.15%)
boat: 823 (62.87%)
body: 1188 (90.76%)
home.dest: 564 (43.09%)
```

### Numeric summary statistics (`numeric_stats`)

**Status:** OK  **Attempts:** 1  **Eval:** 1.00  **Duration:** 23.7s

```text
pclass  survived       age     sibsp     parch      fare     body
count  1309.000  1309.000  1046.000  1309.000  1309.000  1308.000  121.000
mean      2.295     0.382    29.881     0.499     0.385    33.295  160.810
std       0.838     0.486    14.413     1.042     0.866    51.759   97.697
min       1.000     0.000     0.167     0.000     0.000     0.000    1.000
25%       2.000     0.000    21.000     0.000     0.000     7.896   72.000
50%       3.000     0.000    28.000     0.000     0.000    14.454  155.000
75%       3.000     1.000    39.000     1.000     0.000    31.275  256.000
max       3.000     1.000    80.000     8.000     9.000   512.329  328.000
```

### Top categories per categorical column (`categorical_freq`)

**Status:** OK  **Attempts:** 1  **Eval:** 1.00  **Duration:** 41.7s

```text
## name
Connolly, Miss. Kate  2
Kelly, Mr. James  2
Abbing, Mr. Anthony  1
Abbott, Master. Eugene Joseph  1
Abbott, Mr. Rossmore Edward  1
## sex
male  843
female  466
## ticket
CA. 2343  11
1601  8
CA 2144  8
3101295  7
347077  7
## cabin
C23 C25 C27  6
B57 B59 B63 B66  5
G6  5
B96 B98  4
C22 C26  4
## embarked
S  914
C  270
Q  123
## boat
13  39
C  38
15  37
14  33
4  31
## home.dest
New York, NY  64
London  14
Montreal, PQ  10
Cornwall / Akron, OH  9
Paris, France  9
```

### Pairwise correlation (`correlation`)

**Status:** OK  **Attempts:** 1  **Eval:** 1.00  **Duration:** 35.3s

```text
pclass <-> fare: -0.559
pclass <-> age: -0.408
survived <-> fare: 0.244
survived <-> body: nan
sibsp <-> parch: 0.374
```

### Outlier detection (`outliers`)

**Status:** OK  **Attempts:** 1  **Eval:** 1.00  **Duration:** 26.7s

```text
age: 3 outliers
sibsp: 37 outliers
parch: 24 outliers
fare: 38 outliers
```

### Data quality warnings (`quality_warnings`)

**Status:** OK  **Attempts:** 1  **Eval:** 1.00  **Duration:** 76.4s

```text
- High-cardinality column: name
- High-cardinality column: ticket
- Heavy missingness: column 'cabin' (77.5% NaN)
- Heavy missingness: column 'boat' (62.9% NaN)
- Heavy missingness: column 'body' (90.8% NaN)
```

---

_Self-healing footprint: the runtime burned **0** extra attempt(s) beyond the 8 first-shots, recovering 0 stage(s) after failure._
