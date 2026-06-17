# Reforge HPO benchmark report

## Summary

- Cases: **1** (with a score: **1**)
- Total trials: 5 (successful: 5, success rate: 100.0%)
- Wall time: **206.9 s**
- Generated at: 2026-06-15T05:23:39+00:00

## Per case

| Case | Task | Best score | Best trial | Trials | Stopped | Duration (s) |
|---|---|---|---|---|---|---|
| `iris` | classification | **0.9667** | 2 | 5 (5 ok) | plateau | 206.9 |

## Trial details

### `iris` (classification)

**Best score:** 0.9667  **Best trial:** 2  **Stopped:** plateau  **Duration:** 206.9s

| # | Status | CV score | Attempts | Duration (s) | Pipeline |
|---|---|---|---|---|---|
| 1 | OK | 0.9600 | 1 | 22.2 | StandardScaler + LogisticRegression(multinomial) |
| 2 | OK | 0.9667 | 1 | 41.1 | StandardScaler + SVC(RBF) |
| 3 | OK | 0.9667 | 1 | 38.2 | StandardScaler + RandomForestClassifier(n_estimators=100) |
| 4 | OK | 0.9600 | 1 | 44.1 | MinMaxScaler + KNeighborsClassifier(n_neighbors=5) |
| 5 | OK | 0.9667 | 1 | 60.3 | StandardScaler + GradientBoostingClassifier |
