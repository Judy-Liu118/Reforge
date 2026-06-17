"""P21 — Worker Orchestration: WorkerSpec / WorkerState / WorkerPool / WorkerOrchestrator.

Test categories:
  1. WorkerSpec model — construction, defaults, validation
  2. WorkerState model — initial values, field semantics
  3. WorkerPool — register, stop, routing by type, capacity, least-loaded,
                  per-worker state tracking, shutdown
  4. WorkerOrchestrator.run() — single, chain, parallel, diamond,
                                failure propagation, typed routing,
                                no-matching-worker finalization, cycle rejection
  5. End-to-end — mixed types, multi-worker concurrent execution
"""

from __future__ import annotations

import threading
import time

import pytest

from reforge.runtime.tasks.graph import CycleError, TaskGraph
from reforge.runtime.tasks.models import Task, TaskResult
from reforge.runtime.workers.models import WorkerSpec, WorkerState
from reforge.runtime.workers.orchestrator import WorkerOrchestrator
from reforge.runtime.workers.pool import WorkerPool, WorkerUnavailableError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(value: str = "ok"):
    return lambda: value


def _fail(msg: str = "boom"):
    def fn():
        raise RuntimeError(msg)
    return fn


def _task(task_id: str, fn=None, deps=(), priority: int = 0, worker_type: str = "") -> Task:
    return Task(
        task_id=task_id,
        fn=fn or _ok(task_id),
        deps=frozenset(deps),
        priority=priority,
        worker_type=worker_type,
    )


def _pool(*specs: WorkerSpec, max_threads: int = 8) -> WorkerPool:
    p = WorkerPool(max_threads=max_threads)
    for s in specs:
        p.register(s)
    return p


# ---------------------------------------------------------------------------
# 1. WorkerSpec
# ---------------------------------------------------------------------------


class TestWorkerSpec:
    def test_stores_worker_id(self) -> None:
        assert WorkerSpec("w1").worker_id == "w1"

    def test_default_worker_type_is_generic(self) -> None:
        assert WorkerSpec("w1").worker_type == "generic"

    def test_default_capacity_is_1(self) -> None:
        assert WorkerSpec("w1").capacity == 1

    def test_custom_worker_type(self) -> None:
        assert WorkerSpec("w1", worker_type="verifier").worker_type == "verifier"

    def test_custom_capacity(self) -> None:
        assert WorkerSpec("w1", capacity=4).capacity == 4

    def test_capacity_zero_raises(self) -> None:
        with pytest.raises(ValueError):
            WorkerSpec("w1", capacity=0)

    def test_capacity_negative_raises(self) -> None:
        with pytest.raises(ValueError):
            WorkerSpec("w1", capacity=-1)


# ---------------------------------------------------------------------------
# 2. WorkerState
# ---------------------------------------------------------------------------


class TestWorkerState:
    def test_initial_active_is_zero(self) -> None:
        assert WorkerState("w1", "generic").active == 0

    def test_initial_completed_is_zero(self) -> None:
        assert WorkerState("w1", "generic").completed == 0

    def test_initial_failed_is_zero(self) -> None:
        assert WorkerState("w1", "generic").failed == 0

    def test_stopped_defaults_false(self) -> None:
        assert WorkerState("w1", "generic").stopped is False

    def test_stores_worker_id_and_type(self) -> None:
        s = WorkerState("w99", "planner")
        assert s.worker_id == "w99"
        assert s.worker_type == "planner"


# ---------------------------------------------------------------------------
# 3. WorkerPool
# ---------------------------------------------------------------------------


class TestWorkerPool:
    def test_register_stores_spec(self) -> None:
        p = WorkerPool()
        p.register(WorkerSpec("w1"))
        state = p.state("w1")
        assert state.worker_id == "w1"

    def test_stop_marks_worker_stopped(self) -> None:
        p = WorkerPool()
        p.register(WorkerSpec("w1"))
        p.stop("w1")
        assert p.state("w1").stopped is True

    def test_stop_unknown_worker_raises(self) -> None:
        p = WorkerPool()
        with pytest.raises(KeyError):
            p.stop("nonexistent")

    def test_state_unknown_worker_raises(self) -> None:
        p = WorkerPool()
        with pytest.raises(KeyError):
            p.state("ghost")

    def test_submit_returns_future(self) -> None:
        p = _pool(WorkerSpec("w1"))
        f = p.submit(_task("t"))
        result = f.result(timeout=5)
        assert isinstance(result, TaskResult)
        p.shutdown()

    def test_successful_task_result_status(self) -> None:
        p = _pool(WorkerSpec("w1"))
        result = p.submit(_task("t", fn=_ok("hi"))).result(timeout=5)
        assert result.status == "completed"
        p.shutdown()

    def test_successful_task_increments_completed(self) -> None:
        p = _pool(WorkerSpec("w1"))
        p.submit(_task("t")).result(timeout=5)
        assert p.state("w1").completed == 1
        p.shutdown()

    def test_failed_task_increments_failed(self) -> None:
        p = _pool(WorkerSpec("w1"))
        p.submit(_task("t", fn=_fail())).result(timeout=5)
        assert p.state("w1").failed == 1
        p.shutdown()

    def test_active_is_zero_after_completion(self) -> None:
        p = _pool(WorkerSpec("w1"))
        p.submit(_task("t")).result(timeout=5)
        assert p.state("w1").active == 0
        p.shutdown()

    def test_routing_by_worker_type(self) -> None:
        p = WorkerPool()
        p.register(WorkerSpec("v1", worker_type="verifier"))
        p.register(WorkerSpec("g1", worker_type="generic"))
        p.submit(_task("t", worker_type="verifier")).result(timeout=5)
        assert p.state("v1").completed == 1
        assert p.state("g1").completed == 0
        p.shutdown()

    def test_empty_worker_type_routes_to_any(self) -> None:
        p = _pool(WorkerSpec("w1", worker_type="verifier"))
        # task with no type requirement → any available worker
        result = p.submit(_task("t", worker_type="")).result(timeout=5)
        assert result.status == "completed"
        p.shutdown()

    def test_no_matching_type_raises_unavailable(self) -> None:
        p = _pool(WorkerSpec("w1", worker_type="planner"))
        with pytest.raises(WorkerUnavailableError):
            p.submit(_task("t", worker_type="verifier"))
        p.shutdown()

    def test_stopped_worker_not_routed_to(self) -> None:
        p = _pool(WorkerSpec("w1"))
        p.stop("w1")
        with pytest.raises(WorkerUnavailableError):
            p.submit(_task("t"))
        p.shutdown()

    def test_capacity_1_blocks_concurrent_task(self) -> None:
        p = _pool(WorkerSpec("w1", capacity=1))
        started = threading.Event()
        release = threading.Event()

        def blocking():
            started.set()
            release.wait()
            return "done"

        f = p.submit(_task("t1", fn=blocking))
        started.wait(timeout=5)
        with pytest.raises(WorkerUnavailableError):
            p.submit(_task("t2", fn=_ok()))
        release.set()
        f.result(timeout=5)
        p.shutdown()

    def test_capacity_2_allows_concurrent_tasks(self) -> None:
        p = _pool(WorkerSpec("w1", capacity=2))
        barrier = threading.Barrier(2)
        results = []
        lock = threading.Lock()

        def fn():
            barrier.wait(timeout=5)
            with lock:
                results.append(1)
            return 1

        f1 = p.submit(_task("t1", fn=fn))
        f2 = p.submit(_task("t2", fn=fn))
        f1.result(timeout=5)
        f2.result(timeout=5)
        assert len(results) == 2
        p.shutdown()

    def test_tasks_distributed_across_workers(self) -> None:
        """Simultaneous tasks route to separate workers when one is at capacity."""
        p = WorkerPool(max_threads=4)
        p.register(WorkerSpec("w1", capacity=1))
        p.register(WorkerSpec("w2", capacity=1))
        # Barrier between the two task threads only; both must run concurrently
        barrier = threading.Barrier(2)

        def barrier_fn():
            barrier.wait(timeout=5)
            return "x"

        f1 = p.submit(_task("t1", fn=barrier_fn))
        f2 = p.submit(_task("t2", fn=barrier_fn))  # w1 full → goes to w2
        f1.result(timeout=5)
        f2.result(timeout=5)
        assert p.state("w1").completed == 1
        assert p.state("w2").completed == 1
        p.shutdown()

    def test_all_states_returns_all_workers(self) -> None:
        p = WorkerPool()
        p.register(WorkerSpec("a"))
        p.register(WorkerSpec("b"))
        states = p.all_states()
        assert set(states) == {"a", "b"}
        p.shutdown()

    def test_all_states_is_snapshot(self) -> None:
        p = _pool(WorkerSpec("w1"))
        snap = p.all_states()
        p.submit(_task("t")).result(timeout=5)
        # Original snapshot unchanged
        assert snap["w1"].completed == 0
        p.shutdown()

    def test_register_overwrites_duplicate(self) -> None:
        p = WorkerPool()
        p.register(WorkerSpec("w1", capacity=1))
        p.register(WorkerSpec("w1", capacity=5))
        assert p.state("w1").worker_id == "w1"
        p.shutdown()

    def test_unavailable_error_message_contains_type(self) -> None:
        p = WorkerPool()
        try:
            p.submit(_task("t", worker_type="missing-type"))
        except WorkerUnavailableError as exc:
            assert "missing-type" in str(exc)
        p.shutdown()

    def test_multiple_tasks_sequentially(self) -> None:
        p = _pool(WorkerSpec("w1"))
        for i in range(5):
            p.submit(_task(f"t{i}")).result(timeout=5)
        assert p.state("w1").completed == 5
        p.shutdown()


# ---------------------------------------------------------------------------
# 4. WorkerOrchestrator
# ---------------------------------------------------------------------------


def _orchestrator(*specs: WorkerSpec) -> WorkerOrchestrator:
    pool = _pool(*specs)
    return WorkerOrchestrator(pool)


class TestWorkerOrchestrator:
    def test_empty_graph_returns_empty_dict(self) -> None:
        orch = _orchestrator(WorkerSpec("w1"))
        assert orch.run(TaskGraph()) == {}

    def test_single_task_completed(self) -> None:
        g = TaskGraph()
        g.add(_task("a"))
        orch = _orchestrator(WorkerSpec("w1"))
        results = orch.run(g)
        assert results["a"].status == "completed"

    def test_single_task_output_preserved(self) -> None:
        g = TaskGraph()
        g.add(_task("a", fn=_ok("hello")))
        orch = _orchestrator(WorkerSpec("w1"))
        assert orch.run(g)["a"].output == "hello"

    def test_all_tasks_in_results(self) -> None:
        g = TaskGraph()
        for tid in ["a", "b", "c"]:
            g.add(_task(tid))
        orch = _orchestrator(WorkerSpec("w1"))
        results = orch.run(g)
        assert set(results) == {"a", "b", "c"}

    def test_chain_executes_in_order(self) -> None:
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
        orch = WorkerOrchestrator(_pool(WorkerSpec("w1", capacity=1)))
        orch.run(g)
        assert order == ["a", "b", "c"]

    def test_parallel_tasks_all_complete(self) -> None:
        g = TaskGraph()
        for tid in ["x", "y", "z"]:
            g.add(_task(tid))
        orch = _orchestrator(WorkerSpec("w1", capacity=4))
        results = orch.run(g)
        assert all(r.status == "completed" for r in results.values())

    def test_diamond_all_complete(self) -> None:
        g = TaskGraph()
        g.add(_task("a"))
        g.add(_task("b", deps={"a"}))
        g.add(_task("c", deps={"a"}))
        g.add(_task("d", deps={"b", "c"}))
        orch = _orchestrator(WorkerSpec("w1", capacity=4))
        results = orch.run(g)
        assert all(r.status == "completed" for r in results.values())

    def test_failed_dep_skips_dependent(self) -> None:
        g = TaskGraph()
        g.add(_task("a", fn=_fail()))
        g.add(_task("b", deps={"a"}))
        orch = _orchestrator(WorkerSpec("w1"))
        results = orch.run(g)
        assert results["a"].status == "failed"
        assert results["b"].status == "skipped"

    def test_transitive_skip_propagates(self) -> None:
        g = TaskGraph()
        g.add(_task("a", fn=_fail()))
        g.add(_task("b", deps={"a"}))
        g.add(_task("c", deps={"b"}))
        orch = _orchestrator(WorkerSpec("w1"))
        results = orch.run(g)
        assert results["c"].status == "skipped"

    def test_unrelated_task_not_skipped(self) -> None:
        g = TaskGraph()
        g.add(_task("a", fn=_fail()))
        g.add(_task("independent"))
        orch = _orchestrator(WorkerSpec("w1"))
        results = orch.run(g)
        assert results["independent"].status == "completed"

    def test_typed_routing_uses_correct_worker(self) -> None:
        pool = WorkerPool(max_threads=4)
        pool.register(WorkerSpec("verifier-1", worker_type="verifier"))
        pool.register(WorkerSpec("generic-1", worker_type="generic"))

        g = TaskGraph()
        g.add(_task("v", worker_type="verifier"))
        g.add(_task("g", worker_type="generic"))

        WorkerOrchestrator(pool).run(g)

        assert pool.state("verifier-1").completed == 1
        assert pool.state("generic-1").completed == 1
        pool.shutdown()

    def test_no_matching_worker_marks_task_failed(self) -> None:
        pool = WorkerPool(max_threads=4)
        pool.register(WorkerSpec("w1", worker_type="planner"))

        g = TaskGraph()
        g.add(_task("orphan", worker_type="verifier"))  # no verifier registered
        results = WorkerOrchestrator(pool).run(g)
        assert results["orphan"].status == "failed"
        assert "verifier" in results["orphan"].error
        pool.shutdown()

    def test_no_matching_worker_skips_dependents(self) -> None:
        pool = WorkerPool(max_threads=4)
        pool.register(WorkerSpec("w1", worker_type="planner"))

        g = TaskGraph()
        g.add(_task("a", worker_type="verifier"))  # unroutable
        g.add(_task("b", deps={"a"}))               # should be skipped
        results = WorkerOrchestrator(pool).run(g)
        assert results["a"].status == "failed"
        assert results["b"].status == "skipped"
        pool.shutdown()

    def test_cycle_raises_before_execution(self) -> None:
        g = TaskGraph()
        g.add(_task("a", deps={"b"}))
        g.add(_task("b", deps={"a"}))
        orch = _orchestrator(WorkerSpec("w1"))
        with pytest.raises(CycleError):
            orch.run(g)

    def test_worker_states_updated_after_run(self) -> None:
        pool = _pool(WorkerSpec("w1", capacity=4))
        g = TaskGraph()
        for tid in ["a", "b", "c"]:
            g.add(_task(tid))
        WorkerOrchestrator(pool).run(g)
        assert pool.state("w1").completed == 3
        pool.shutdown()

    def test_mixed_typed_and_untyped_tasks(self) -> None:
        pool = WorkerPool(max_threads=4)
        pool.register(WorkerSpec("v1", worker_type="verifier"))
        pool.register(WorkerSpec("g1", worker_type="generic"))

        g = TaskGraph()
        g.add(_task("typed", worker_type="verifier"))
        g.add(_task("untyped"))  # worker_type="" → any worker
        g.add(_task("dep", deps={"typed", "untyped"}))

        results = WorkerOrchestrator(pool).run(g)
        assert all(r.status == "completed" for r in results.values())
        pool.shutdown()


# ---------------------------------------------------------------------------
# 5. End-to-end
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_multi_type_complex_dag(self) -> None:
        """
        fetch (generic) → parse (verifier) → merge (generic) → report (generic)
                          validate (verifier) ↗
        """
        pool = WorkerPool(max_threads=8)
        pool.register(WorkerSpec("g1", worker_type="generic", capacity=3))
        pool.register(WorkerSpec("v1", worker_type="verifier", capacity=2))

        g = TaskGraph()
        g.add(Task("fetch", fn=lambda: "raw", worker_type="generic"))
        g.add(Task("parse", fn=lambda: "parsed", deps=frozenset({"fetch"}), worker_type="verifier"))
        g.add(Task("validate", fn=lambda: "valid", deps=frozenset({"fetch"}), worker_type="verifier"))
        g.add(Task("merge", fn=lambda: "merged", deps=frozenset({"parse", "validate"}), worker_type="generic"))
        g.add(Task("report", fn=lambda: "report", deps=frozenset({"merge"}), worker_type="generic"))

        results = WorkerOrchestrator(pool).run(g)
        assert set(results) == {"fetch", "parse", "validate", "merge", "report"}
        assert all(r.status == "completed" for r in results.values())
        assert pool.state("g1").completed == 3  # fetch, merge, report
        assert pool.state("v1").completed == 2  # parse, validate
        pool.shutdown()

    def test_partial_failure_typed_workers(self) -> None:
        pool = WorkerPool(max_threads=4)
        pool.register(WorkerSpec("g1", worker_type="generic"))
        pool.register(WorkerSpec("v1", worker_type="verifier"))

        g = TaskGraph()
        g.add(Task("fetch", fn=_fail("net error"), worker_type="generic"))
        g.add(Task("verify", fn=lambda: "ok", worker_type="verifier"))
        g.add(Task("process", fn=lambda: "done", deps=frozenset({"fetch"}), worker_type="generic"))

        results = WorkerOrchestrator(pool).run(g)
        assert results["fetch"].status == "failed"
        assert results["verify"].status == "completed"
        assert results["process"].status == "skipped"
        pool.shutdown()

    def test_concurrent_workers_finish_faster_than_serial(self) -> None:
        """4 tasks each sleeping 0.05 s: 2 workers finish in ~0.1 s, serial in ~0.2 s."""
        delay = 0.05

        def slow():
            time.sleep(delay)
            return "done"

        pool = WorkerPool(max_threads=4)
        pool.register(WorkerSpec("w1", capacity=2))
        pool.register(WorkerSpec("w2", capacity=2))

        g = TaskGraph()
        for i in range(4):
            g.add(Task(f"t{i}", fn=slow))

        start = time.monotonic()
        results = WorkerOrchestrator(pool).run(g)
        elapsed = time.monotonic() - start

        assert all(r.status == "completed" for r in results.values())
        assert elapsed < delay * 3.5  # 4 serial would be ~4×delay
        pool.shutdown()
