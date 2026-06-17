"""Tests for TaskDecomposer — heuristic detection and result parsing."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from reforge.runtime.orchestration.decomposition.decomposer import TaskDecomposer, _has_multistep_signals, _parse_response
from reforge.runtime.orchestration.decomposition.models import DecompositionResult


# --- Heuristic signal tests ---

def test_heuristic_detects_chinese_step_markers() -> None:
    assert _has_multistep_signals("第一步读取数据，第二步分析，第三步输出结果")


def test_heuristic_detects_numbered_list() -> None:
    assert _has_multistep_signals("1. 读取CSV\n2. 计算均值\n3. 保存结果")


def test_heuristic_detects_english_step_markers() -> None:
    assert _has_multistep_signals("Step 1: load data. Step 2: analyze.")


def test_heuristic_detects_first_then_finally() -> None:
    assert _has_multistep_signals("首先读取数据，然后清洗数据，最后输出分析报告")


def test_heuristic_no_signal_for_simple_task() -> None:
    assert not _has_multistep_signals("读取sales.csv并计算平均值")


def test_heuristic_no_signal_for_then_only() -> None:
    assert not _has_multistep_signals("analyze the csv and then print the result")


# --- DecompositionResult.single passthrough ---

def test_single_task_passthrough_when_no_signals() -> None:
    """No LLM call should happen for requests without multi-step signals."""
    llm = MagicMock()
    decomposer = TaskDecomposer(llm=llm)
    result = decomposer.decompose("read csv and calculate mean")
    assert not result.is_multistep
    assert len(result.subtasks) == 1
    assert result.subtasks[0].request == "read csv and calculate mean"
    llm.chat.assert_not_called()


def test_decomposer_calls_llm_when_signals_present() -> None:
    """LLM is called when multi-step signals are detected."""
    llm = MagicMock()
    llm.chat.return_value = """{
        "is_multistep": true,
        "subtasks": [
            {"index": 0, "request": "read CSV file", "description": "Load data"},
            {"index": 1, "request": "calculate revenue mean", "description": "Analyze"}
        ],
        "reasoning": "numbered list detected"
    }"""
    decomposer = TaskDecomposer(llm=llm)
    result = decomposer.decompose("1. read CSV\n2. calculate revenue mean")
    assert result.is_multistep
    assert len(result.subtasks) == 2
    assert result.subtasks[0].description == "Load data"


def test_decomposer_falls_back_on_llm_failure() -> None:
    """LLM exception falls back to single-task without crashing."""
    llm = MagicMock()
    llm.chat.side_effect = RuntimeError("network error")
    decomposer = TaskDecomposer(llm=llm)
    result = decomposer.decompose("Step 1: load data\nStep 2: export")
    assert not result.is_multistep
    assert len(result.subtasks) == 1


# --- JSON parsing tests ---

def test_parse_response_valid_multistep() -> None:
    raw = """{
        "is_multistep": true,
        "subtasks": [
            {"index": 0, "request": "task A", "description": "do A"},
            {"index": 1, "request": "task B", "description": "do B"}
        ],
        "reasoning": "two distinct goals"
    }"""
    result = _parse_response(raw, "original request")
    assert result.is_multistep
    assert len(result.subtasks) == 2
    assert result.subtasks[1].request == "task B"


def test_parse_response_strips_markdown_fences() -> None:
    raw = "```json\n{\"is_multistep\": false, \"subtasks\": [{\"index\": 0, \"request\": \"do X\", \"description\": \"\"}], \"reasoning\": \"\"}\n```"
    result = _parse_response(raw, "do X")
    assert not result.is_multistep


def test_parse_response_invalid_json_falls_back() -> None:
    result = _parse_response("this is not json at all", "original")
    assert not result.is_multistep
    assert result.subtasks[0].request == "original"


def test_parse_response_single_subtask_treated_as_single_task() -> None:
    raw = """{
        "is_multistep": true,
        "subtasks": [{"index": 0, "request": "only one task", "description": ""}],
        "reasoning": ""
    }"""
    result = _parse_response(raw, "only one task")
    assert not result.is_multistep
