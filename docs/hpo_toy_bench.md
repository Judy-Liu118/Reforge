# Reforge HPO benchmark (4 toy datasets, workers=4)

## Summary

- Cases: **4** (with a score: **4**)
- Total trials: 17 (successful: 17, success rate: 100.0%)
- Wall time: **255.2 s**
- Generated at: 2026-06-15T05:28:29+00:00

## Per case

| Case | Task | Best score | Best trial | Trials | Stopped | Duration (s) |
|---|---|---|---|---|---|---|
| `iris` | classification | **0.9667** | 1 | 4 (4 ok) | plateau | 176.1 |
| `wine` | classification | **0.9833** | 2 | 5 (5 ok) | plateau | 255.2 |
| `breast_cancer` | classification | **0.9807** | 1 | 4 (4 ok) | plateau | 230.0 |
| `diabetes` | regression | **0.4822** | 1 | 4 (4 ok) | plateau | 190.3 |

## Trial details

### `iris` (classification)

**Best score:** 0.9667  **Best trial:** 1  **Stopped:** plateau  **Duration:** 176.1s

| # | Status | CV score | Attempts | Duration (s) | Pipeline |
|---|---|---|---|---|---|
| 1 | OK | 0.9667 | 1 | 35.1 | StandardScaler + SVC |
| 2 | OK | 0.9133 | 1 | 61.0 | StandardScaler + PCA(n_components=2) + LogisticRegression(max_iter=2000) |
| 3 | OK | 0.9600 | 1 | 47.9 | StandardScaler + KNN(n_neighbors=5) |
| 4 | OK | 0.9667 | 1 | 31.0 | StandardScaler + RandomForestClassifier(n_estimators=100) |

### `wine` (classification)

**Best score:** 0.9833  **Best trial:** 2  **Stopped:** plateau  **Duration:** 255.2s

| # | Status | CV score | Attempts | Duration (s) | Pipeline |
|---|---|---|---|---|---|
| 1 | OK | 0.9832 | 1 | 26.7 | StandardScaler + LogisticRegression(multinomial) |
| 2 | OK | 0.9833 | 1 | 40.1 | StandardScaler + SVC(rbf, gamma='scale') |
| 3 | OK | 0.9778 | 1 | 77.6 | StandardScaler + RandomForestClassifier(n_estimators=100) |
| 4 | OK | 0.9386 | 1 | 39.0 | StandardScaler + GradientBoostingClassifier |
| 5 | OK | 0.9494 | 1 | 70.6 | StandardScaler+KNeighborsClassifier(n_neighbors=5) |

### `breast_cancer` (classification)

**Best score:** 0.9807  **Best trial:** 1  **Stopped:** plateau  **Duration:** 230.0s

| # | Status | CV score | Attempts | Duration (s) | Pipeline |
|---|---|---|---|---|---|
| 1 | OK | 0.9807 | 2 | 84.0 | StandardScaler + LogisticRegression(max_iter=5000) |
| 2 | OK | 0.9561 | 1 | 48.6 | RandomForestClassifier(n_estimators=100, random_state=42) |
| 3 | OK | 0.9736 | 1 | 41.7 | StandardScaler+SVC(rbf,C=1,gamma=scale) |
| 4 | OK | 0.9737 | 1 | 54.7 | StandardScaler+MLPClassifier(hidden=(100,),max_iter=2000) |

### `diabetes` (regression)

**Best score:** 0.4822  **Best trial:** 1  **Stopped:** plateau  **Duration:** 190.3s

| # | Status | CV score | Attempts | Duration (s) | Pipeline |
|---|---|---|---|---|---|
| 1 | OK | 0.4822 | 1 | 44.6 | StandardScaler + Ridge(alpha=1.0, random_state=42) |
| 2 | OK | 0.4211 | 1 | 69.5 | RandomForestRegressor(n_estimators=200, random_state=42) |
| 3 | OK | 0.3211 | 1 | 45.0 | StandardScaler + GradientBoostingRegressor(n_estimators=500, learning_rate=0.05, max_depth=3, random_state=42) |
| 4 | OK | 0.1466 | 1 | 30.1 | StandardScaler + SVR(RBF kernel, C=1.0, gamma='scale') |
