"""Contract tests for P-R.1 — verify workflow.py is split into nodes/* modules.

These tests guard against regressions where node logic is re-inlined into
workflow.py or grows beyond the 100-line/node budget.
"""

from __future__ import annotations

from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_GRAPH_DIR = _PROJECT_ROOT / "reforge" / "runtime" / "orchestration" / "graph"
_NODES_DIR = _GRAPH_DIR / "nodes"


def _line_count(p: Path) -> int:
    return sum(1 for _ in p.read_text(encoding="utf-8").splitlines())


class TestWorkflowSlim:
    def test_workflow_module_is_builder_only(self) -> None:
        """workflow.py should hold only build_graph and imports, not node bodies.

        The invariant guarded here is "no business logic in workflow.py", not a
        literal line count — the complementary
        test_workflow_does_not_define_node_functions enforces the actual
        no-node-body rule.
        """
        workflow_lines = _line_count(_GRAPH_DIR / "workflow.py")
        assert workflow_lines < 110, f"workflow.py grew to {workflow_lines} lines"

    def test_workflow_does_not_define_node_functions(self) -> None:
        """No `_xxx_node` private function should be *defined* in workflow.py.

        Check for `def <name>` (function definitions), not bare name references,
        so injected wrapper imports like `wrap_reflection_node` don't false-positive.
        """
        source = (_GRAPH_DIR / "workflow.py").read_text(encoding="utf-8")
        for forbidden in (
            "_planner_node",
            "_code_generation_node",
            "_reflection_node",
            "_evaluation_node",
            "_retry_decision_node",
            "_final_response_node",
            "_capability_node",
            "_query_memory_context",
            "_extract_requirements",
        ):
            assert f"def {forbidden}" not in source, (
                f"{forbidden} should live in nodes/, not workflow.py"
            )


class TestNodesModuleStructure:
    EXPECTED_NODES = (
        "planner",
        "codegen",
        "execution",
        "reflection",
        "evaluation",
        "retry_decision",
        "final_response",
        "capability",
    )

    def test_each_node_file_exists(self) -> None:
        for name in self.EXPECTED_NODES:
            assert (_NODES_DIR / f"{name}.py").is_file(), (
                f"nodes/{name}.py is missing"
            )

    def test_each_node_file_under_size_budget(self) -> None:
        for name in self.EXPECTED_NODES:
            lines = _line_count(_NODES_DIR / f"{name}.py")
            assert lines <= 110, (
                f"nodes/{name}.py grew to {lines} lines (budget 110)"
            )

    def test_nodes_package_reexports_public_api(self) -> None:
        from reforge.runtime.orchestration.graph import nodes

        for symbol in (
            "planner_node",
            "code_generation_node",
            "execution_node",
            "reflection_node",
            "evaluation_node",
            "retry_decision_node",
            "final_response_node",
            "capability_node",
            "route_after_capability",
            "should_retry",
        ):
            assert hasattr(nodes, symbol), f"nodes.{symbol} missing"


class TestBuildGraphStillCompiles:
    def test_build_graph_no_args(self) -> None:
        from reforge.runtime.orchestration.graph.workflow import build_graph

        graph = build_graph()
        assert graph is not None

    def test_build_graph_accepts_substrate(self) -> None:
        from reforge.memory.substrate import CompositeMemorySubstrate
        from reforge.runtime.orchestration.graph.workflow import build_graph

        graph = build_graph(memory_substrate=CompositeMemorySubstrate())
        assert graph is not None
