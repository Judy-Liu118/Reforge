# Reforge SQL benchmark (workers=4)

## Summary

- Cases: **15**
- Execution accuracy: **66.7%** (first-shot 46.7% + recovered 20.0%)
- Correct: 7  Recovered: 3  Wrong: 5  Error: 0
- Self-healing footprint: **15** extra attempt(s) on top of the 15 first-shots
- Wall time: **255.2 s**
- Generated at: 2026-06-15T05:08:12+00:00

## Per case

| Case | Difficulty | Status | Attempts | Eval | Duration (s) |
|---|---|---|---|---|---|
| `e01_count_students` | easy | RECOVERED | 4 | 0.50 | 85.3 |
| `e02_list_math_teachers` | easy | OK | 1 | 1.00 | 16.1 |
| `e03_avg_gpa` | easy | RECOVERED | 4 | 0.50 | 93.3 |
| `e04_count_courses_per_dept` | easy | OK | 1 | 1.00 | 34.8 |
| `e05_credits_total` | easy | RECOVERED | 4 | 0.50 | 132.9 |
| `m01_avg_score_per_course` | medium | OK | 1 | 1.00 | 19.9 |
| `m02_students_per_teacher` | medium | WRONG | 1 | 1.00 | 23.5 |
| `m03_top_gpa_per_grade` | medium | OK | 1 | 1.00 | 42.3 |
| `m04_courses_taught_by_carol` | medium | WRONG | 2 | 1.00 | 69.0 |
| `m05_students_failed_any` | medium | WRONG | 4 | 1.00 | 118.8 |
| `h01_strong_courses` | hard | OK | 1 | 1.00 | 18.8 |
| `h02_top_student_per_dept` | hard | OK | 1 | 1.00 | 39.0 |
| `h03_unenrolled_students` | hard | WRONG | 2 | 1.00 | 104.7 |
| `h04_teachers_hired_after_2014` | hard | WRONG | 2 | 1.00 | 59.5 |
| `h05_ordered_top_three` | hard | OK | 1 | 1.00 | 50.5 |

## Case details

### `e01_count_students` — RECOVERED

**Difficulty:** easy  **Attempts:** 4  **Eval:** 0.50  **Duration:** 85.3s

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

**Difficulty:** easy  **Attempts:** 1  **Eval:** 1.00  **Duration:** 16.1s

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

**Difficulty:** easy  **Attempts:** 4  **Eval:** 0.50  **Duration:** 93.3s

_runtime_outcome=FAILED_

**Predicted:**
```text
3.6
```

**Expected:**
```text
3.6
```

### `e04_count_courses_per_dept` — OK

**Difficulty:** easy  **Attempts:** 1  **Eval:** 1.00  **Duration:** 34.8s

_runtime_outcome=SUCCESS_

**Predicted:**
```text
History | 2
Math | 3
Science | 3
```

**Expected:**
```text
History | 2
Math | 3
Science | 3
```

### `e05_credits_total` — RECOVERED

**Difficulty:** easy  **Attempts:** 4  **Eval:** 0.50  **Duration:** 132.9s

_runtime_outcome=FAILED_

**Predicted:**
```text
28
```

**Expected:**
```text
28
```

### `m01_avg_score_per_course` — OK

**Difficulty:** medium  **Attempts:** 1  **Eval:** 1.00  **Duration:** 19.9s

_runtime_outcome=SUCCESS_

**Predicted:**
```text
Algebra I | 77.66666666666667
Algebra II | 83.66666666666667
Geometry | 82.5
World History | 61.0
US History | 71.33333333333333
Biology | 83.0
Chemistry | 77.33333333333333
Physics | 86.33333333333333
```

**Expected:**
```text
Algebra I | 77.66666666666667
Algebra II | 83.66666666666667
Geometry | 82.5
World History | 61.0
US History | 71.33333333333333
Biology | 83.0
Chemistry | 77.33333333333333
Physics | 86.33333333333333
```

### `m02_students_per_teacher` — WRONG

**Difficulty:** medium  **Attempts:** 1  **Eval:** 1.00  **Duration:** 23.5s

_runtime_outcome=SUCCESS_

**Predicted:**
```text
1 | Alice Chen | 4
2 | Bob Khan | 4
3 | Carol Davis | 3
4 | Dan Park | 3
5 | Eve Liu | 5
```

**Expected:**
```text
Alice Chen | 4
Bob Khan | 4
Carol Davis | 3
Dan Park | 3
Eve Liu | 5
```

### `m03_top_gpa_per_grade` — OK

**Difficulty:** medium  **Attempts:** 1  **Eval:** 1.00  **Duration:** 42.3s

_runtime_outcome=SUCCESS_

**Predicted:**
```text
10 | 3.95
11 | 3.7
12 | 3.9
```

**Expected:**
```text
10 | 3.95
11 | 3.7
12 | 3.9
```

### `m04_courses_taught_by_carol` — WRONG

**Difficulty:** medium  **Attempts:** 2  **Eval:** 1.00  **Duration:** 69.0s

_runtime_outcome=RECOVERED_

**Predicted:**
```text
Connecting to database...
Executing query:

SELECT c.title
FROM courses c
JOIN teachers t ON c.teacher_id = t.teacher_id
WHERE t.name = 'Carol Davis'

Number of results: 1
Algebra II
```

**Expected:**
```text
Algebra II
```

### `m05_students_failed_any` — WRONG

**Difficulty:** medium  **Attempts:** 4  **Eval:** 1.00  **Duration:** 118.8s

_runtime_outcome=RECOVERED_

**Predicted:**
```text
Connecting to database: D:/Reforge/data/sql_bench/toy.db
Executing query:

    SELECT DISTINCT s.name
    FROM students s
    JOIN enrollments e ON s.student_id = e.student_id
    WHERE e.score < 60

Query returned 2 row(s).
Results:
Olive
Quinn
```

**Expected:**
```text
Olive
Quinn
```

### `h01_strong_courses` — OK

**Difficulty:** hard  **Attempts:** 1  **Eval:** 1.00  **Duration:** 18.8s

_runtime_outcome=SUCCESS_

**Predicted:**
```text
Algebra II
Geometry
Biology
Physics
```

**Expected:**
```text
Algebra II
Geometry
Biology
Physics
```

### `h02_top_student_per_dept` — OK

**Difficulty:** hard  **Attempts:** 1  **Eval:** 1.00  **Duration:** 39.0s

_runtime_outcome=SUCCESS_

**Predicted:**
```text
History | 79.0
Math | 96.0
Science | 90.0
```

**Expected:**
```text
History | 79.0
Math | 96.0
Science | 90.0
```

### `h03_unenrolled_students` — WRONG

**Difficulty:** hard  **Attempts:** 2  **Eval:** 1.00  **Duration:** 104.7s

_runtime_outcome=RECOVERED_

**Predicted:**
```text
Number of students without enrollments: 0
```

### `h04_teachers_hired_after_2014` — WRONG

**Difficulty:** hard  **Attempts:** 2  **Eval:** 1.00  **Duration:** 59.5s

_runtime_outcome=RECOVERED_

**Predicted:**
```text
Executing query:

SELECT t.name
FROM teachers t
JOIN courses c ON t.teacher_id = c.teacher_id
WHERE t.hire_year >= 2015
GROUP BY t.teacher_id
HAVING COUNT(DISTINCT c.course_id) >= 2


Number of teachers found: 2

Alice Chen
Bob Khan
```

**Expected:**
```text
Alice Chen
Bob Khan
```

### `h05_ordered_top_three` — OK

**Difficulty:** hard  **Attempts:** 1  **Eval:** 1.00  **Duration:** 50.5s

_runtime_outcome=SUCCESS_

**Predicted:**
```text
Noah | 3.95
Riley | 3.9
Liam | 3.85
```

**Expected:**
```text
Noah | 3.95
Riley | 3.9
Liam | 3.85
```
