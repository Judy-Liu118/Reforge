"""Regression tests for the self-heal trigger threshold + judge strictness.

The script-side threshold (`if score < 0.85`) and the judge-side rubric
work together: the judge anchors to numeric deductions so it gives
honest 0.3-0.7 scores on imperfect output, and the threshold catches
anything below 0.85 to trigger another codegen attempt.

0.85 is the empirical sweet spot — 0.75 is too lenient (lets visibly bad
output pass), 0.92 was unreachable for the qwen-vl-max codegen and caused
over-correction failures (model inlines giant SVG paths trying to fix
icons, blows the token budget, emits truncated HTML).
"""

from __future__ import annotations

from reforge.models.prompts.templates import (
    CODE_GENERATION_SYSTEM,
    VISION_CODEGEN_SYSTEM,
)


class TestSelfHealThreshold:
    def test_codegen_prompt_uses_0_85_threshold(self) -> None:
        """The example raise pattern must use 0.85 — not 0.75 (too lenient)
        and not 0.92 (causes over-correction divergence)."""
        assert "score < 0.85" in CODE_GENERATION_SYSTEM
        assert "score < 0.75" not in CODE_GENERATION_SYSTEM
        assert "score < 0.92" not in CODE_GENERATION_SYSTEM

    def test_vision_codegen_prompt_uses_0_85_threshold(self) -> None:
        assert "score < 0.85" in VISION_CODEGEN_SYSTEM
        assert "score < 0.75" not in VISION_CODEGEN_SYSTEM
        assert "score < 0.92" not in VISION_CODEGEN_SYSTEM


class TestJudgeRubricStrictness:
    """The rubric used by compare_images must include the concrete worked
    example. Without it the model anchors to "be helpful" and gives 0.85
    to outputs that miss icons, get text wrong, and use wrong proportions.
    """

    def test_rubric_has_worked_example_with_numeric_deductions(self) -> None:
        from reforge.runtime.skills.builtin.image_compare import _build_question

        prompt = _build_question("text and layout")
        # The worked example must be present so the model sees actual
        # numbers next to actual failure modes.
        assert "WORKED EXAMPLE" in prompt
        # Each major deduction category must be explicitly priced.
        assert "-0.40" in prompt  # text substitution
        assert "-0.20" in prompt  # missing region
        assert "-0.15" in prompt  # wrong proportion / color

    def test_rubric_is_explicit_about_what_0_8_means(self) -> None:
        """A score above 0.9 should mean "pixel-close". Without this the
        judge default-behaves as if 0.8 is "good enough"."""
        from reforge.runtime.skills.builtin.image_compare import _build_question

        prompt = _build_question("text and layout")
        assert "0.8" in prompt
        # Anchor language — explicit about not being lenient.
        assert "NOT a good reproduction" in prompt or "be honest" in prompt
