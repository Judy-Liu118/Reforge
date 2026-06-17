"""Lightweight runtime case loader.

Cases are plain .txt files under tests/runtime_cases/.
Lines starting with '#' are metadata; the rest is the user request.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


CASES_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "runtime_cases"


@dataclass
class Case:
    name: str
    category: str
    expected_behavior: str
    request: str
    file_path: Path


def _parse_case(file_path: Path) -> Case:
    metadata: dict[str, str] = {}
    request_lines: list[str] = []

    text = file_path.read_text(encoding="utf-8")
    for line in text.strip().split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            # Parse "# key: value" metadata
            content = stripped.removeprefix("#").strip()
            if ":" in content:
                key, _, value = content.partition(":")
                metadata[key.strip()] = value.strip()
        elif stripped:
            request_lines.append(stripped)

    return Case(
        name=metadata.get("case_name", file_path.stem),
        category=metadata.get("category", "unknown"),
        expected_behavior=metadata.get("expected_behavior", ""),
        request="\n".join(request_lines),
        file_path=file_path,
    )


def find_case(name: str) -> Case | None:
    """Search for a case by name (without .txt extension)."""
    pattern = f"{name}.txt"
    for path in CASES_DIR.rglob("*.txt"):
        if path.name == pattern:
            return _parse_case(path)
    return None


def list_cases() -> list[Case]:
    """Return all available cases."""
    cases: list[Case] = []
    for path in sorted(CASES_DIR.rglob("*.txt")):
        cases.append(_parse_case(path))
    return cases
