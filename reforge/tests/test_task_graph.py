"""P20 — Task Graph Scheduling: Task / TaskGraph / TaskScheduler.

Test categories:
  1. Task model — construction, deps normalisation, priority default
  2. TaskGraph — add, ready(), validate() cycle detection
  3. execute_task — success, failure wrapping
  4. TaskScheduler.run() — single, chain, parallel, diamond, failure propagation,
                           priority ordering, cycle rejection, empty graph
  5. End-to-end — complex mixed DAG
"""

from __future__ import annotations

import threading
import time

import pytest

from reforge.runtime.tasks.graph import CycleError, TaskGraph
from reforge.runtime.tasks.models import Task, TaskResult, execute_task
from reforge.runtime.tasks.scheduler import TaskScheduler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(value: str = "ok"):
    return lambda: value


def _fail(msg: str = "boom"):
    def fn():
        raise RuntimeError(msg)
    return fn


def _task(task_id: str, fn=None, deps=(), priority: int = 0) -> Task:
    return Task(task_id=task_id, fn=fn or _ok(task_id), deps=frozenset(deps), priority=priority)


# ---------------------------------------------------------------------------
# 1. Task model
# ---------------------------------------------------------------------------


class TestTask:
    def test_task_stores_task_id(self) -> None:
        t = _task("t1")
        assert t.task_id == "t1"

    def test_default_priority_is_zero(self) -> None:
        assert _task("x").priority == 0

    def test_default_deps_is_empty_frozenset(self) -> None:
        assert _task("x").deps == frozenset()

    def test_deps_normalised_from_set(self) -> None:
        t = Task(task_id="x", fn=_ok(), deps={"a", "b"})
        assert isinstance(t.deps, frozenset)
        assert t.deps == frozenset({"a", "b"})

    def test_deps_normalised_from_list(self) -> None:
        t = Task(task_id="x", fn=_ok(), deps=["a"])
        assert isinstance(t.deps, frozenset)

    def test_task_result_completed(self) -> None:
        r = TaskResult(task_id="x", status="completed", output=42)
        assert r.status == "completed"
        assert r.output == 42

    def test_task_result_failed(self) -> None:
        r = TaskResult(task_id="x", status="failed", error="oops")
        assert r.status == "failed"
        assert "oops" in r.error

    def test_task_result_skipped(self) -> None:
        r = TaskResult(task_id="x", status="skipped")
        assert r.status == "skipped"


# ---------------------------------------------------------------------------
# 2. TaskGraph
# ---------------------------------------------------------------------------


class TestTaskGraph:
    def test_add_registers_task(self) -> None:
        g = TaskGraph()
        g.add(_task("a"))
        assert "a" in g.tasks

    def test_add_overwrites_duplicate(self) -> None:
        g = TaskGraph()
        g.add(_task("a"))
        g.add(Task(task_id="a", fn=_ok("new"), deps=frozenset()))
        assert g.tasks["a"].fn() == "new"

    def test_ready_empty_graph(self) -> None:
        assert TaskGraph().ready(set()) == []

    def test_ready_no_deps_returns_task(self) -> None:
        g = TaskGraph()
        g.add(_task("a"))
        assert len(g.ready(set())) == 1

    def test_ready_dep_not_completed_blocks_task(self) -> None:
        g = TaskGraph()
        g.add(_task("a"))
        g.add(_task("b", deps={"a"}))
        ready_ids = {t.task_id for t in g.ready(set())}
        assert "b" not in ready_ids
        assert "a" in ready_ids

    def test_ready_dep_completed_unblocks_task(self) -> None:
        g = TaskGraph()
        g.add(_task("a"))
        g.add(_task("b", deps={"a"}))
        ready_ids = {t.task_id for t in g.ready({"a"})}
        assert "b" in ready_ids

    def test_ready_excludes_already_completed(self) -> None:
        g = TaskGraph()
        g.add(_task("a"))
        ready_ids = {t.task_id for t in g.ready({"a"})}
        assert "a" not in ready_ids

    def test_ready_sorted_by_priority_desc(self) -> None:
        g = TaskGraph()
        g.add(_task("lo", priority=1))
        g.add(_task("hi", priority=10))
        g.add(_task("mid", priority=5))
        ready = g.ready(set())
        ids = [t.task_id for t in ready]
        assert ids == ["hi", "mid", "lo"]

    def test_validate_acyclic_graph_passes(self) -> None:
        g = TaskGraph()
        g.add(_task("a"))
        g.add(_task("b", deps={"a"}))
        g.add(_task("c", deps={"b"}))
        g.validate()  # no exception

    def test_validate_direct_cycle_raises(self) -> None:
        g = TaskGraph()
        g.add(_task("a", deps={"b"}))
        g.add(_task("b", deps={"a"}))
        with pytest.raises(CycleError):
            g.validate()

    def test_validate_self_loop_raises(self) -> None:
        g = TaskGraph()
        g.add(_task("a", deps={"a"}))
        with pytest.raises(CycleError):
            g.validate()

    def test_validate_indirect_cycle_raises(self) -> None:
        g = TaskGraph()
        g.add(_task("a", deps={"c"}))
        g.add(_task("b", deps={"a"}))
        g.add(_task("c", deps={"b"}))
        with pytest.raises(CycleError):
            g.validate()

    def test_cycle_error_contains_path(self) -> None:
        g = TaskGraph()
        g.add(_task("x", deps={"y"}))
        g.add(_task("y", deps={"x"}))
        with pytest.raises(CycleError) as exc_info:
            g.validate()
        assert exc_info.value.path

    def test_external_dep_not_in_graph_is_ignored(self) -> None:
        """A dep not registered in the graph counts as satisfied."""
        g = TaskGraph()
        g.add(_task("b", deps={"external"}))
        ready_ids = {t.task_id for t in g.ready(set())}
        assert "b" in ready_ids


# ---------------------------------------------------------------------------
# 3. execute_task helper
# ---------------------------------------------------------------------------


class TestExecuteTask:
    def test_success_returns_completed_status(self) -> None:
        result = execute_task(_task("t", fn=_ok("output")))
        assert result.status == "completed"

    def test_success_captures_output(self) -> None:
        result = execute_task(_task("t", fn=lambda: 42))
        assert result.output == 42

    def test_failure_returns_failed_status(self) -> None:
        result = execute_task(_task("t", fn=_fail("boom")))
        assert result.status == "failed"

    def test_failure_captures_error_message(self) -> None:
        result = execute_task(_task("t", fn=_fail("specific error")))
        assert "specific error" in result.error

    def test_duration_ms_is_nonnegative(self) -> None:
        result = execute_task(_task("t"))
        assert result.duration_ms >= 0.0

    def test_task_id_preserved_in_result(self) -> None:
        result = execute_task(_task("my-task"))
        assert result.task_id == "my-task"


# ---------------------------------------------------------------------------
# 4. TaskScheduler
# ---------------------------------------------------------------------------


class TestTaskScheduler:
    def test_empty_graph_returns_empty_dict(self) -> None:
        results = TaskScheduler().run(TaskGraph())
        assert results == {}

    def test_single_task_completed(self) -> None:
        g = TaskGraph()
        g.add(_task("a"))
        results = TaskScheduler().run(g)
        assert results["a"].status == "completed"

    def test_single_task_output_preserved(self) -> None:
        g = TaskGraph()
        g.add(_task("a", fn=_ok("hello")))
        assert TaskScheduler().run(g)["a"].output == "hello"

    def test_all_tasks_present_in_results(self) -> None:
        g = TaskGraph()
        for tid in ["a", "b", "c"]:
            g.add(_task(tid))
        results = TaskScheduler().run(g)
        assert set(results) == {"a", "b", "c"}

    def test_chain_executes_in_dependency_order(self) -> None:
        order: list[str] = []
        lock = threading.Lock()

        def make_fn(name: str):
            def fn():
                with lock:
                    order.append(name)
                return name
            return fn

        g = TaskGraph()
        g.add(Task("a", fn=make_fn("a")))
        g.add(Task("b", fn=make_fn("b"), deps=frozenset({"a"})))
        g.add(Task("c", fn=make_fn("c"), deps=frozenset({"b"})))
        TaskScheduler(max_workers=1).run(g)
        assert order == ["a", "b", "c"]

    def test_parallel_tasks_all_complete(self) -> None:
        g = TaskGraph()
        for tid in ["x", "y", "z"]:
            g.add(_task(tid))
        results = TaskScheduler(max_workers=3).run(g)
        assert all(r.status == "completed" for r in results.values())

    def test_diamond_all_complete(self) -> None:
        #   a
        #  / \
        # b   c
        #  \ /
        #   d
        g = TaskGraph()
        g.add(_task("a"))
        g.add(_task("b", deps={"a"}))
        g.add(_task("c", deps={"a"}))
        g.add(_task("d", deps={"b", "c"}))
        results = TaskScheduler().run(g)
        assert all(r.status == "completed" for r in results.values())

    def test_d_not_run_until_b_and_c_done(self) -> None:
        done_at: dict[str, float] = {}

        def make_fn(name: str):
            def fn():
                done_at[name] = time.monotonic()
                return name
            return fn

        g = TaskGraph()
        g.add(Task("a", fn=make_fn("a")))
        g.add(Task("b", fn=make_fn("b"), deps=frozenset({"a"})))
        g.add(Task("c", fn=make_fn("c"), deps=frozenset({"a"})))
        g.add(Task("d", fn=make_fn("d"), deps=frozenset({"b", "c"})))
        TaskScheduler(max_workers=4).run(g)
        assert done_at["d"] >= done_at["b"]
        assert done_at["d"] >= done_at["c"]

    def test_failed_task_status_is_failed(self) -> None:
        g = TaskGraph()
        g.add(_task("a", fn=_fail()))
        results = TaskScheduler().run(g)
        assert results["a"].status == "failed"

    def test_dependent_of_failed_is_skipped(self) -> None:
        g = TaskGraph()
        g.add(_task("a", fn=_fail()))
        g.add(_task("b", deps={"a"}))
        results = TaskScheduler().run(g)
        assert results["b"].status == "skipped"

    def test_transitive_skip_propagates(self) -> None:
        g = TaskGraph()
        g.add(_task("a", fn=_fail()))
        g.add(_task("b", deps={"a"}))
        g.add(_task("c", deps={"b"}))
        results = TaskScheduler().run(g)
        assert results["c"].status == "skipped"

    def test_unrelated_task_not_skipped_on_failure(self) -> None:
        g = TaskGraph()
        g.add(_task("a", fn=_fail()))
        g.add(_task("independent"))
        results = TaskScheduler().run(g)
        assert results["independent"].status == "completed"

    def test_priority_high_runs_before_low(self) -> None:
        """With max_workers=1, high-priority task runs before low-priority."""
        order: list[str] = []
        lock = threading.Lock()

        def make_fn(name: str):
            def fn():
                with lock:
                    order.append(name)
                return name
            return fn

        g = TaskGraph()
        g.add(Task("lo", fn=make_fn("lo"), priority=1))
        g.add(Task("hi", fn=make_fn("hi"), priority=10))
        TaskScheduler(max_workers=1).run(g)
        assert order.index("hi") < order.index("lo")

    def test_cycle_raises_before_execution(self) -> None:
        g = TaskGraph()
        g.add(_task("a", deps={"b"}))
        g.add(_task("b", deps={"a"}))
        with pytest.raises(CycleError):
            TaskScheduler().run(g)

    def test_results_include_all_task_ids(self) -> None:
        g = TaskGraph()
        g.add(_task("a"))
        g.add(_task("b", fn=_fail(), deps={"a"}))
        g.add(_task("c", deps={"b"}))
        results = TaskScheduler().run(g)
        assert set(results.keys()) == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# 5. End-to-end: complex mixed DAG
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_complex_dag_all_results_present(self) -> None:
        """
        Topology:
          fetch_a  fetch_b
               |   |    |
             merge  transform
                 |  |
                report
        """
        g = TaskGraph()
        g.add(Task("fetch_a", fn=lambda: "data-a"))
        g.add(Task("fetch_b", fn=lambda: "data-b"))
        g.add(Task("merge", fn=lambda: "merged",
                   deps=frozenset({"fetch_a", "fetch_b"})))
        g.add(Task("transform", fn=lambda: "transformed",
                   deps=frozenset({"fetch_b"})))
        g.add(Task("report", fn=lambda: "report",
                   deps=frozenset({"merge", "transform"})))
        results = TaskScheduler(max_workers=4).run(g)
        assert set(results.keys()) == {"fetch_a", "fetch_b", "merge", "transform", "report"}
        assert all(r.status == "completed" for r in results.values())

    def test_partial_failure_skips_only_dependents(self) -> None:
        """fetch_b fails → merge+report skipped; fetch_a+transform unaffected."""
        g = TaskGraph()
        g.add(Task("fetch_a", fn=lambda: "data-a"))
        g.add(Task("fetch_b", fn=_fail("network error")))
        g.add(Task("merge", fn=lambda: "merged",
                   deps=frozenset({"fetch_a", "fetch_b"})))
        g.add(Task("transform", fn=lambda: "transformed",
                   deps=frozenset({"fetch_a"})))
        g.add(Task("report", fn=lambda: "report",
                   deps=frozenset({"merge", "transform"})))
        results = TaskScheduler(max_workers=4).run(g)
        assert results["fetch_a"].status == "completed"
        assert results["fetch_b"].status == "failed"
        assert results["merge"].status == "skipped"
        assert results["transform"].status == "completed"
        assert results["report"].status == "skipped"

    def test_output_of_completed_tasks_is_accessible(self) -> None:
        g = TaskGraph()
        g.add(Task("a", fn=lambda: {"key": "value"}))
        g.add(Task("b", fn=lambda: [1, 2, 3], deps=frozenset({"a"})))
        results = TaskScheduler().run(g)
        assert results["a"].output == {"key": "value"}
        assert results["b"].output == [1, 2, 3]
