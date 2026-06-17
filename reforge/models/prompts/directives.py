"""Process-constraint directives injected into code-generation prompts.

These are specialized override instructions used when the user's request
encodes a process constraint (must-fail-first, expects-uncaught-exception, ...)
that the default CODE_GENERATION_SYSTEM prompt cannot satisfy on its own.

Kept separate from templates.py so business logic (workflow nodes, requirement
extraction) never inlines prompt strings.
"""

from __future__ import annotations


CONSTRAINT_VIOLATION_DIRECTIVE = (
    "\n\nCONSTRAINT VIOLATION: You generated clean code, but the task "
    "REQUIRES a deliberate failure first. Insert a garbled character "
    "(e.g., '$' or a zero-width space) directly before 'print' so the "
    "code crashes with SyntaxError. Only the NEXT attempt will fix it.\n"
)


MUST_FAIL_FIRST_OVERRIDE = (
    "OVERRIDE: Ignore the instruction about valid Python. You MUST output code "
    "that deliberately crashes with a SyntaxError. Insert a non-printable "
    "character (e.g. U+200B zero-width space) directly before 'print'. "
    "The characters before 'print' are NOT valid Python — that is intentional. "
    "Output exactly: ​print(\"hello world\")"
)


EXPECTS_UNCAUGHT_OVERRIDE = (
    "OVERRIDE: Do NOT use try/except. Let the exception propagate naturally."
)


MUST_FAIL_FIRST_PATTERNS = [
    "故意.*加.*乱码",
    "故意.*加.*字符",
    "故意.*插入.*字符",
    "故意.*制造.*错误.*然后",
    "故意.*制造.*异常.*然后",
    "先.*故意.*再.*修复",
    "先.*故意.*然后.*修复",
    "故意.*加个",
    "故意.*语法出错",
]


EXPECTS_UNCAUGHT_PATTERNS = [
    "演示.*traceback",
    "演示.*异常",
    "演示.*错误",
    "traceback.*demo",
    "exception.*demo",
    "生成.*traceback",
    "生成.*异常.*demo",
    "生成.*异常.*示例",
    "crash.*demo",
    "演示.*崩溃",
    "真实.*traceback",
    "真实.*异常",
    "故意.*报错",
    "故意.*让它",
    "故意.*触发.*错误",
    "演示.*故意",
    "教学.*traceback",
    "教学.*异常",
]
