"""Build a small SQLite DB + a hand-picked Text-to-SQL benchmark.

Produces:
  data/sql_bench/toy.db
  data/sql_bench/toy_cases.json

The schema is a tiny school registry — small enough for a reviewer to
keep in their head, but rich enough that 15 questions can span EASY
(direct SELECT) -> MEDIUM (single JOIN + GROUP BY) -> HARD (subquery /
HAVING / multi-condition).

Re-run any time: the script is idempotent.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "sql_bench"
DB_PATH = OUT_DIR / "toy.db"
CASES_PATH = OUT_DIR / "toy_cases.json"


SCHEMA = """
DROP TABLE IF EXISTS students;
DROP TABLE IF EXISTS courses;
DROP TABLE IF EXISTS enrollments;
DROP TABLE IF EXISTS teachers;

CREATE TABLE teachers (
    teacher_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    department TEXT NOT NULL,
    hire_year INTEGER
);

CREATE TABLE courses (
    course_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    credits INTEGER NOT NULL,
    teacher_id INTEGER REFERENCES teachers(teacher_id)
);

CREATE TABLE students (
    student_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    grade_level INTEGER NOT NULL,
    gpa REAL
);

CREATE TABLE enrollments (
    student_id INTEGER REFERENCES students(student_id),
    course_id INTEGER REFERENCES courses(course_id),
    score INTEGER,
    PRIMARY KEY (student_id, course_id)
);
"""


TEACHERS = [
    (1, "Alice Chen",  "Math",        2015),
    (2, "Bob Khan",    "History",     2018),
    (3, "Carol Davis", "Math",        2010),
    (4, "Dan Park",    "Science",     2020),
    (5, "Eve Liu",     "Science",     2012),
]

COURSES = [
    (101, "Algebra I",       3, 1),
    (102, "Algebra II",      3, 3),
    (103, "Geometry",        4, 1),
    (104, "World History",   3, 2),
    (105, "US History",      3, 2),
    (106, "Biology",         4, 4),
    (107, "Chemistry",       4, 5),
    (108, "Physics",         4, 5),
]

STUDENTS = [
    (201, "Liam",  10, 3.85),
    (202, "Maya",  11, 3.50),
    (203, "Noah",  10, 3.95),
    (204, "Olive", 12, 3.20),
    (205, "Peter", 11, 3.70),
    (206, "Quinn", 10, 3.10),
    (207, "Riley", 12, 3.90),
    (208, "Sara",  11, 3.60),
]

ENROLLMENTS = [
    # (student_id, course_id, score)
    (201, 101, 88), (201, 103, 92), (201, 106, 81),
    (202, 102, 75), (202, 105, 70), (202, 107, 85),
    (203, 101, 95), (203, 106, 90), (203, 108, 89),
    (204, 104, 60), (204, 105, 65), (204, 107, 55),
    (205, 102, 80), (205, 106, 78), (205, 108, 82),
    (206, 101, 50), (206, 104, 62),
    (207, 102, 96), (207, 107, 92), (207, 108, 88),
    (208, 103, 73), (208, 105, 79),
]


SCHEMA_DDL_FOR_PROMPT = """\
CREATE TABLE teachers (
    teacher_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    department TEXT NOT NULL,
    hire_year INTEGER
);
CREATE TABLE courses (
    course_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    credits INTEGER NOT NULL,
    teacher_id INTEGER REFERENCES teachers(teacher_id)
);
CREATE TABLE students (
    student_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    grade_level INTEGER NOT NULL,
    gpa REAL
);
CREATE TABLE enrollments (
    student_id INTEGER REFERENCES students(student_id),
    course_id INTEGER REFERENCES courses(course_id),
    score INTEGER,
    PRIMARY KEY (student_id, course_id)
);"""


# The 15 questions span 3 difficulty buckets, intentionally cover error-
# prone SQL patterns (typo-prone column names, joins requiring direction,
# HAVING vs WHERE, NULL filtering, ordering).
CASES_RAW = [
    # ---- EASY: direct SELECT / single aggregation ------------------------
    {
        "case_id": "e01_count_students",
        "difficulty": "easy",
        "question": "How many students are in the database?",
        "gold_sql": "SELECT COUNT(*) FROM students",
    },
    {
        "case_id": "e02_list_math_teachers",
        "difficulty": "easy",
        "question": "List the names of every teacher in the Math department.",
        "gold_sql": "SELECT name FROM teachers WHERE department = 'Math'",
    },
    {
        "case_id": "e03_avg_gpa",
        "difficulty": "easy",
        "question": "What is the average GPA across all students, rounded to 2 decimals?",
        "gold_sql": "SELECT ROUND(AVG(gpa), 2) FROM students",
    },
    {
        "case_id": "e04_count_courses_per_dept",
        "difficulty": "easy",
        "question": "How many courses does each department offer? Return department and course count.",
        "gold_sql": (
            "SELECT t.department, COUNT(c.course_id) "
            "FROM teachers t JOIN courses c ON t.teacher_id = c.teacher_id "
            "GROUP BY t.department"
        ),
    },
    {
        "case_id": "e05_credits_total",
        "difficulty": "easy",
        "question": "What is the total number of credits across every course?",
        "gold_sql": "SELECT SUM(credits) FROM courses",
    },

    # ---- MEDIUM: JOIN + GROUP BY + filter --------------------------------
    {
        "case_id": "m01_avg_score_per_course",
        "difficulty": "medium",
        "question": "For each course, return its title and the average score of all enrolled students.",
        "gold_sql": (
            "SELECT c.title, AVG(e.score) "
            "FROM courses c JOIN enrollments e ON c.course_id = e.course_id "
            "GROUP BY c.course_id"
        ),
    },
    {
        "case_id": "m02_students_per_teacher",
        "difficulty": "medium",
        "question": "For each teacher, count how many distinct students are enrolled in any of their courses.",
        "gold_sql": (
            "SELECT t.name, COUNT(DISTINCT e.student_id) "
            "FROM teachers t "
            "JOIN courses c ON t.teacher_id = c.teacher_id "
            "JOIN enrollments e ON c.course_id = e.course_id "
            "GROUP BY t.teacher_id"
        ),
    },
    {
        "case_id": "m03_top_gpa_per_grade",
        "difficulty": "medium",
        "question": (
            "What is the highest GPA in each grade level? Return grade_level and that max GPA."
        ),
        "gold_sql": (
            "SELECT grade_level, MAX(gpa) FROM students GROUP BY grade_level"
        ),
    },
    {
        "case_id": "m04_courses_taught_by_carol",
        "difficulty": "medium",
        "question": "Which course titles are taught by Carol Davis?",
        "gold_sql": (
            "SELECT c.title FROM courses c "
            "JOIN teachers t ON c.teacher_id = t.teacher_id "
            "WHERE t.name = 'Carol Davis'"
        ),
    },
    {
        "case_id": "m05_students_failed_any",
        "difficulty": "medium",
        "question": "List the names of every student who scored below 60 in at least one course.",
        "gold_sql": (
            "SELECT DISTINCT s.name FROM students s "
            "JOIN enrollments e ON s.student_id = e.student_id "
            "WHERE e.score < 60"
        ),
    },

    # ---- HARD: HAVING, subquery, multi-step ------------------------------
    {
        "case_id": "h01_strong_courses",
        "difficulty": "hard",
        "question": (
            "Which course titles have an average score of 80 or higher across "
            "all enrollments?"
        ),
        "gold_sql": (
            "SELECT c.title FROM courses c "
            "JOIN enrollments e ON c.course_id = e.course_id "
            "GROUP BY c.course_id HAVING AVG(e.score) >= 80"
        ),
    },
    {
        "case_id": "h02_top_student_per_dept",
        "difficulty": "hard",
        "question": (
            "For each department, return the department name and the highest "
            "average score any of its students achieved across that "
            "department's courses."
        ),
        "gold_sql": (
            "SELECT t.department, MAX(per_student.avg_score) FROM ("
            " SELECT t2.department AS dept, s.student_id, AVG(e.score) AS avg_score "
            " FROM teachers t2 JOIN courses c ON t2.teacher_id = c.teacher_id "
            " JOIN enrollments e ON c.course_id = e.course_id "
            " JOIN students s ON s.student_id = e.student_id "
            " GROUP BY t2.department, s.student_id"
            ") per_student "
            "JOIN teachers t ON t.department = per_student.dept "
            "GROUP BY t.department"
        ),
    },
    {
        "case_id": "h03_unenrolled_students",
        "difficulty": "hard",
        "question": "List the names of any students who have no enrollments at all.",
        "gold_sql": (
            "SELECT s.name FROM students s "
            "LEFT JOIN enrollments e ON s.student_id = e.student_id "
            "WHERE e.student_id IS NULL"
        ),
    },
    {
        "case_id": "h04_teachers_hired_after_2014",
        "difficulty": "hard",
        "question": (
            "Which teacher names were hired in 2015 or later AND teach at least "
            "two distinct courses?"
        ),
        "gold_sql": (
            "SELECT t.name FROM teachers t "
            "JOIN courses c ON t.teacher_id = c.teacher_id "
            "WHERE t.hire_year >= 2015 "
            "GROUP BY t.teacher_id HAVING COUNT(DISTINCT c.course_id) >= 2"
        ),
    },
    {
        "case_id": "h05_ordered_top_three",
        "difficulty": "hard",
        "question": (
            "List the names of the top 3 students by GPA, highest first. "
            "Use the exact column order: name, gpa."
        ),
        "gold_sql": "SELECT name, gpa FROM students ORDER BY gpa DESC LIMIT 3",
        "expects_ordering": True,
    },
]


# ---------------------------------------------------------------------------


def _build_db() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO teachers VALUES (?, ?, ?, ?)", TEACHERS)
        conn.executemany("INSERT INTO courses VALUES (?, ?, ?, ?)", COURSES)
        conn.executemany("INSERT INTO students VALUES (?, ?, ?, ?)", STUDENTS)
        conn.executemany("INSERT INTO enrollments VALUES (?, ?, ?)", ENROLLMENTS)
        conn.commit()
    finally:
        conn.close()


def _write_cases() -> None:
    items = []
    db_rel = "./toy.db"
    for raw in CASES_RAW:
        items.append({
            "case_id": raw["case_id"],
            "db_path": db_rel,
            "schema_ddl": SCHEMA_DDL_FOR_PROMPT,
            "question": raw["question"],
            "gold_sql": raw["gold_sql"],
            "evidence": raw.get("evidence", ""),
            "difficulty": raw["difficulty"],
            "expects_ordering": raw.get("expects_ordering", False),
        })
    CASES_PATH.write_text(
        json.dumps(items, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _sanity_check_gold() -> None:
    """Run every gold SQL against the freshly built DB to catch typos in advance."""
    conn = sqlite3.connect(DB_PATH)
    try:
        for raw in CASES_RAW:
            try:
                conn.execute(raw["gold_sql"]).fetchall()
            except Exception as exc:
                raise RuntimeError(f"gold SQL for {raw['case_id']} failed: {exc}") from exc
    finally:
        conn.close()


def main() -> int:
    _build_db()
    _sanity_check_gold()
    _write_cases()
    print(f"  -> SQLite DB    : {DB_PATH}")
    print(f"  -> Cases JSON   : {CASES_PATH}   ({len(CASES_RAW)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
