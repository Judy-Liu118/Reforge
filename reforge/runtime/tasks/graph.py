"""TaskGraph — DAG of Tasks with dependency validation and ready-set queries."""

from __future__ import annotations

from reforge.runtime.tasks.models import Task


class CycleError(ValueError):
    """Raised when TaskGraph.validate() detects a dependency cycle."""

    def __init__(self, path: list[str]) -> None:
        cycle = " → ".join(path)
        super().__init__(f"Dependency cycle detected: {cycle}")
        self.path = path


class TaskGraph:
    """Directed acyclic graph of Tasks.

    Usage::

        g = TaskGraph()
        g.add(Task("a", fn_a))
        g.add(Task("b", fn_b, deps=frozenset({"a"})))
        g.validate()                   # raises CycleError if cyclic
        g.ready(set())                 # → [Task("a", ...)]
        g.ready({"a"})                 # → [Task("b", ...)]
    """

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    # ------------------------------------------------------------------
    # Mutation

    def add(self, task: Task) -> None:
        """Register *task*.  Duplicate task_id silently overwrites."""
        self._tasks[task.task_id] = task

    # ------------------------------------------------------------------
    # Query

    @property
    def tasks(self) -> dict[str, Task]:
        return dict(self._tasks)

    def ready(self, completed: set[str]) -> list[Task]:
        """Return tasks whose deps are all in *completed*, sorted by priority desc.

        Tasks already in *completed* are excluded (they're done).
        Unknown dep names (tasks not registered in this graph) are treated as
        externally completed so the scheduler isn't blocked by missing nodes.
        """
        result: list[Task] = []
        for task in self._tasks.values():
            if task.task_id in completed:
                continue
            # All deps must be satisfied (either completed or not registered)
            if task.deps <= (completed | (task.deps - self._tasks.keys())):
                result.append(task)
        result.sort(key=lambda t: t.priority, reverse=True)
        return result

    # ------------------------------------------------------------------
    # Validation

    def validate(self) -> None:
        """Raise CycleError if any dependency cycle exists (DFS coloring)."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {tid: WHITE for tid in self._tasks}

        def dfs(tid: str, path: list[str]) -> None:
            color[tid] = GRAY
            path.append(tid)
            for dep in self._tasks[tid].deps:
                if dep not in self._tasks:
                    continue  # external dep — skip
                if color[dep] == GRAY:
                    raise CycleError(path + [dep])
                if color[dep] == WHITE:
                    dfs(dep, path)
            path.pop()
            color[tid] = BLACK

        for tid in list(self._tasks):
            if color[tid] == WHITE:
                dfs(tid, [])
