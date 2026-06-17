"""P24 — Parallel Execution Runtime.

Test categories:
  1. RuntimeTask — construction, deps normalisation, defaults
  2. RuntimeOutput — fields, event_log default
  3. RuntimeResult — status variants, field access
  4. ParallelRuntime.run() — empty, single, parallel, chain, diamond,
                             failure isolation, worker routing, priority,
                             runner_factory isolation, exception handling,
                             skipped deps, cycle detection
  5. End-to-end — realistic multi-task pipelines with mixed deps/failures
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from reforge.runtime.parallel.executor import ParallelRuntime
from reforge.runtime.parallel.models import RuntimeOutput, RuntimeResult, RuntimeTask
from reforge.runtime.domain.state.models import OutcomeState, RuntimeState
from reforge.runtime.tasks.graph import CycleError
from reforge.runtime.workers.models import WorkerSpec
from reforge.runtime.workers.pool import WorkerPool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state(answer: str = "ok") -> RuntimeState:
    return RuntimeState(outcome_state=OutcomeState(final_answer=answer))


def _mock_runner(answer: str = "ok", session_id: str = "sid") -> MagicMock:
    runner = MagicMock()
    runner.session_id = session_id
    runner.event_log = None
    runner.run.return_value = _state(answer)
    return runner


def _factory(answer: str = "ok", session_id: str = "sid"):
    """Return a factory that always creates the same mock runner."""
    return lambda: _mock_runner(answer, session_id)


def _fail_factory(msg: str = "boom"):
    """Return a factory whose runner.run() raises RuntimeError."""
    def factory():
        runner = MagicMock()
        runner.session_id = "fail-sid"
        runner.event_log = None
        runner.run.side_effect = RuntimeError(msg)
        return runner
    return factory


def _pool(*specs: WorkerSpec, max_threads: int = 8) -> WorkerPool:
    p = WorkerPool(max_threads=max_threads)
    for s in specs:
        p.register(s)
    return p


def _rt(
    task_id: str,
    answer: str = "ok",
    deps: tuple = (),
    priority: int = 0,
    worker_type: str = "",
    session_id: str | None = None,
) -> RuntimeTask:
    sid = session_id or task_id
    return RuntimeTask(
        task_id=task_id,
        user_request=f"request for {task_id}",
        runner_factory=_factory(answer, sid),
        deps=frozenset(deps),
        priority=priority,
        worker_type=worker_type,
    )


# ---------------------------------------------------------------------------
# 1. RuntimeTask
# ---------------------------------------------------------------------------


class TestRuntimeTask:
    def test_stores_task_id(self) -> None:
        rt = _rt("t1")
        assert rt.task_id == "t1"

    def test_stores_user_request(self) -> None:
        rt = RuntimeTask("t", "hello world", _factory())
        assert rt.user_request == "hello world"

    def test_default_deps_frozenset(self) -> None:
        assert _rt("t").deps == frozenset()

    def test_deps_normalised_from_set(self) -> None:
        rt = RuntimeTask("t", "req", _factory(), deps={"a", "b"})
        assert isinstance(rt.deps, frozenset)
        assert rt.deps == frozenset({"a", "b"})

    def test_deps_normalised_from_list(self) -> None:
        rt = RuntimeTask("t", "req", _factory(), deps=["a"])
        assert isinstance(rt.deps, frozenset)

    def test_default_priority_zero(self) -> None:
        assert _rt("t").priority == 0

    def test_default_worker_type_empty(self) -> None:
        assert _rt("t").worker_type == ""


# ---------------------------------------------------------------------------
# 2. RuntimeOutput
# ---------------------------------------------------------------------------


class TestRuntimeOutput:
    def test_stores_state(self) -> None:
        st = _state("42")
        ro = RuntimeOutput(state=st, session_id="s1")
        assert ro.state is st

    def test_stores_session_id(self) -> None:
        ro = RuntimeOutput(state=_state(), session_id="my-sid")
        assert ro.session_id == "my-sid"

    def test_event_log_defaults_none(self) -> None:
        assert RuntimeOutput(state=_state(), session_id="s").event_log is None


# ---------------------------------------------------------------------------
# 3. RuntimeResult
# ---------------------------------------------------------------------------


class TestRuntimeResult:
    def test_completed_status(self) -> None:
        r = RuntimeResult("t", "req", "completed", final_answer="done")
        assert r.status == "completed"

    def test_failed_status(self) -> None:
        r = RuntimeResult("t", "req", "failed", error="oops")
        assert r.status == "failed"
        assert "oops" in r.error

    def test_skipped_status(self) -> None:
        r = RuntimeResult("t", "req", "skipped")
        assert r.status == "skipped"

    def test_final_answer_stored(self) -> None:
        assert RuntimeResult("t", "r", "completed", final_answer="42").final_answer == "42"

    def test_session_id_stored(self) -> None:
        assert RuntimeResult("t", "r", "completed", session_id="xyz").session_id == "xyz"

    def test_output_field(self) -> None:
        ro = RuntimeOutput(state=_state(), session_id="s")
        r = RuntimeResult("t", "r", "completed", output=ro)
        assert r.output is ro


# ---------------------------------------------------------------------------
# 4. ParallelRuntime
# ---------------------------------------------------------------------------


def _pr(*specs: WorkerSpec) -> ParallelRuntime:
    return ParallelRuntime(_pool(*specs))


class TestParallelRuntime:
    def test_empty_list_returns_empty(self) -> None:
        pr = _pr(WorkerSpec("w1"))
        assert pr.run([]) == {}

    def test_single_task_completed(self) -> None:
        pr = _pr(WorkerSpec("w1"))
        results = pr.run([_rt("t1", answer="hello")])
        assert results["t1"].status == "completed"

    def test_single_task_final_answer(self) -> None:
        pr = _pr(WorkerSpec("w1"))
        results = pr.run([_rt("t1", answer="42")])
        assert results["t1"].final_answer == "42"

    def test_single_task_session_id(self) -> None:
        pr = _pr(WorkerSpec("w1"))
        results = pr.run([_rt("t1", session_id="my-sid")])
        assert results["t1"].session_id == "my-sid"

    def test_all_task_ids_in_results(self) -> None:
        pr = _pr(WorkerSpec("w1", capacity=4))
        tasks = [_rt(f"t{i}") for i in range(4)]
        results = pr.run(tasks)
        assert set(results) == {"t0", "t1", "t2", "t3"}

    def test_parallel_tasks_all_complete(self) -> None:
        pr = _pr(WorkerSpec("w1", capacity=4))
        tasks = [_rt(f"t{i}") for i in range(4)]
        results = pr.run(tasks)
        assert all(r.status == "completed" for r in results.values())

    def test_failed_runner_marks_task_failed(self) -> None:
        pool = _pool(WorkerSpec("w1"))
        tasks = [RuntimeTask("t1", "req", _fail_factory("net error"))]
        results = ParallelRuntime(pool).run(tasks)
        assert results["t1"].status == "failed"

    def test_failed_runner_error_captured(self) -> None:
        pool = _pool(WorkerSpec("w1"))
        tasks = [RuntimeTask("t1", "req", _fail_factory("specific error"))]
        results = ParallelRuntime(pool).run(tasks)
        assert "specific error" in results["t1"].error

    def test_failed_task_does_not_affect_independent(self) -> None:
        pool = _pool(WorkerSpec("w1", capacity=2))
        tasks = [
            RuntimeTask("fail", "req", _fail_factory()),
            _rt("independent"),
        ]
        results = ParallelRuntime(pool).run(tasks)
        assert results["independent"].status == "completed"

    def test_dep_failed_skips_dependent(self) -> None:
        pool = _pool(WorkerSpec("w1"))
        tasks = [
            RuntimeTask("a", "req", _fail_factory()),
            _rt("b", deps=("a",)),
        ]
        results = ParallelRuntime(pool).run(tasks)
        assert results["b"].status == "skipped"

    def test_transitive_skip(self) -> None:
        pool = _pool(WorkerSpec("w1"))
        tasks = [
            RuntimeTask("a", "req", _fail_factory()),
            _rt("b", deps=("a",)),
            _rt("c", deps=("b",)),
        ]
        results = ParallelRuntime(pool).run(tasks)
        assert results["c"].status == "skipped"

    def test_chain_ordering_respected(self) -> None:
        order: list[str] = []
        lock = threading.Lock()

        def make_factory(name: str):
            def factory():
                runner = MagicMock()
                runner.session_id = name
                runner.event_log = None
                def run(req):
                    with lock:
                        order.append(name)
                    return _state(name)
                runner.run.side_effect = run
                return runner
            return factory

        pool = _pool(WorkerSpec("w1", capacity=1))
        tasks = [
            RuntimeTask("a", "req", make_factory("a")),
            RuntimeTask("b", "req", make_factory("b"), deps=frozenset({"a"})),
            RuntimeTask("c", "req", make_factory("c"), deps=frozenset({"b"})),
        ]
        ParallelRuntime(pool).run(tasks)
        assert order == ["a", "b", "c"]

    def test_diamond_all_complete(self) -> None:
        pool = _pool(WorkerSpec("w1", capacity=4))
        tasks = [
            _rt("a"),
            _rt("b", deps=("a",)),
            _rt("c", deps=("a",)),
            _rt("d", deps=("b", "c")),
        ]
        results = ParallelRuntime(pool).run(tasks)
        assert all(r.status == "completed" for r in results.values())

    def test_runner_factory_called_per_task(self) -> None:
        """Each task must create an isolated runner — factory called once per task."""
        call_count = [0]

        def counting_factory():
            call_count[0] += 1
            return _mock_runner(f"answer-{call_count[0]}", f"sid-{call_count[0]}")

        pool = _pool(WorkerSpec("w1", capacity=2))
        tasks = [
            RuntimeTask("t1", "req", counting_factory),
            RuntimeTask("t2", "req", counting_factory),
        ]
        ParallelRuntime(pool).run(tasks)
        assert call_count[0] == 2

    def test_different_session_ids_per_task(self) -> None:
        pool = _pool(WorkerSpec("w1", capacity=2))
        tasks = [
            _rt("t1", session_id="sid-a"),
            _rt("t2", session_id="sid-b"),
        ]
        results = ParallelRuntime(pool).run(tasks)
        assert results["t1"].session_id == "sid-a"
        assert results["t2"].session_id == "sid-b"

    def test_output_contains_full_runtime_output(self) -> None:
        pool = _pool(WorkerSpec("w1"))
        results = ParallelRuntime(pool).run([_rt("t1", answer="full")])
        assert results["t1"].output is not None
        assert isinstance(results["t1"].output, RuntimeOutput)

    def test_output_none_on_failure(self) -> None:
        pool = _pool(WorkerSpec("w1"))
        tasks = [RuntimeTask("t1", "req", _fail_factory())]
        results = ParallelRuntime(pool).run(tasks)
        assert results["t1"].output is None

    def test_worker_type_routing(self) -> None:
        pool = WorkerPool(max_threads=4)
        pool.register(WorkerSpec("researcher", worker_type="research"))
        pool.register(WorkerSpec("executor", worker_type="execute"))

        tasks = [
            _rt("r1", worker_type="research"),
            _rt("e1", worker_type="execute"),
        ]
        results = ParallelRuntime(pool).run(tasks)
        assert results["r1"].status == "completed"
        assert results["e1"].status == "completed"
        assert pool.state("researcher").completed == 1
        assert pool.state("executor").completed == 1
        pool.shutdown()

    def test_priority_high_before_low_single_worker(self) -> None:
        order: list[str] = []
        lock = threading.Lock()

        def make_factory(name: str):
            def factory():
                runner = MagicMock()
                runner.session_id = name
                runner.event_log = None
                def run(req):
                    with lock:
                        order.append(name)
                    return _state(name)
                runner.run.side_effect = run
                return runner
            return factory

        pool = _pool(WorkerSpec("w1", capacity=1))
        tasks = [
            RuntimeTask("lo", "req", make_factory("lo"), priority=1),
            RuntimeTask("hi", "req", make_factory("hi"), priority=10),
        ]
        ParallelRuntime(pool).run(tasks)
        assert order.index("hi") < order.index("lo")

    def test_cycle_raises_before_execution(self) -> None:
        pool = _pool(WorkerSpec("w1"))
        tasks = [
            _rt("a", deps=("b",)),
            _rt("b", deps=("a",)),
        ]
        with pytest.raises(CycleError):
            ParallelRuntime(pool).run(tasks)

    def test_concurrent_tasks_faster_than_serial(self) -> None:
        """4 tasks each sleeping 0.05s finish in ~0.05s with 4-slot worker."""
        delay = 0.05

        def slow_factory():
            def factory():
                runner = MagicMock()
                runner.session_id = "s"
                runner.event_log = None
                def run(req):
                    time.sleep(delay)
                    return _state("done")
                runner.run.side_effect = run
                return runner
            return factory

        pool = _pool(WorkerSpec("w1", capacity=4))
        tasks = [RuntimeTask(f"t{i}", "req", slow_factory()) for i in range(4)]

        start = time.monotonic()
        results = ParallelRuntime(pool).run(tasks)
        elapsed = time.monotonic() - start

        assert all(r.status == "completed" for r in results.values())
        assert elapsed < delay * 3.0  # serial would be ~4×delay
        pool.shutdown()


# ---------------------------------------------------------------------------
# 5. End-to-end
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_pipeline_chain_with_deps(self) -> None:
        """fetch → process → report — each must wait for the previous."""
        completed_in_order: list[str] = []
        lock = threading.Lock()

        def stage_factory(name: str):
            def factory():
                runner = MagicMock()
                runner.session_id = name
                runner.event_log = None
                def run(req):
                    with lock:
                        completed_in_order.append(name)
                    return _state(name)
                runner.run.side_effect = run
                return runner
            return factory

        pool = _pool(WorkerSpec("w1", capacity=1))
        tasks = [
            RuntimeTask("fetch", "fetch data", stage_factory("fetch")),
            RuntimeTask("process", "process", stage_factory("process"), deps=frozenset({"fetch"})),
            RuntimeTask("report", "report", stage_factory("report"), deps=frozenset({"process"})),
        ]
        results = ParallelRuntime(pool).run(tasks)
        assert all(r.status == "completed" for r in results.values())
        assert completed_in_order == ["fetch", "process", "report"]

    def test_partial_failure_skips_only_dependents(self) -> None:
        """fetch_b fails → merge+report skipped; fetch_a+transform unaffected."""
        pool = _pool(WorkerSpec("w1", capacity=4))
        tasks = [
            _rt("fetch_a"),
            RuntimeTask("fetch_b", "req", _fail_factory("network error")),
            _rt("merge", deps=("fetch_a", "fetch_b")),
            _rt("transform", deps=("fetch_a",)),
            _rt("report", deps=("merge", "transform")),
        ]
        results = ParallelRuntime(pool).run(tasks)

        assert results["fetch_a"].status == "completed"
        assert results["fetch_b"].status == "failed"
        assert results["merge"].status == "skipped"
        assert results["transform"].status == "completed"
        assert results["report"].status == "skipped"

    def test_multi_type_workers_route_correctly(self) -> None:
        """Research tasks → researcher workers; execution tasks → exec workers."""
        pool = WorkerPool(max_threads=8)
        pool.register(WorkerSpec("r1", worker_type="research", capacity=2))
        pool.register(WorkerSpec("e1", worker_type="execute", capacity=2))

        tasks = [
            _rt("plan", worker_type="research"),
            _rt("verify1", deps=("plan",), worker_type="research"),
            _rt("verify2", deps=("plan",), worker_type="research"),
            _rt("run", deps=("verify1", "verify2"), worker_type="execute"),
        ]
        results = ParallelRuntime(pool).run(tasks)

        assert all(r.status == "completed" for r in results.values())
        assert pool.state("r1").completed == 3   # plan + verify1 + verify2
        assert pool.state("e1").completed == 1   # run
        pool.shutdown()

    def test_all_results_present_even_with_failures(self) -> None:
        pool = _pool(WorkerSpec("w1", capacity=4))
        tasks = [
            _rt("a"),
            RuntimeTask("b", "req", _fail_factory()),
            _rt("c", deps=("b",)),
            _rt("d"),
        ]
        results = ParallelRuntime(pool).run(tasks)
        assert set(results) == {"a", "b", "c", "d"}

    def test_final_answers_accessible(self) -> None:
        pool = _pool(WorkerSpec("w1", capacity=2))
        tasks = [
            _rt("q1", answer="Paris"),
            _rt("q2", answer="42"),
        ]
        results = ParallelRuntime(pool).run(tasks)
        assert results["q1"].final_answer == "Paris"
        assert results["q2"].final_answer == "42"
