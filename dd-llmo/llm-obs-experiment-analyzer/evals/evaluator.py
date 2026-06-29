"""LLM judge evaluator for the llm-obs-experiment-analyzer skill."""

import threading
from pathlib import Path
from typing import Any

from ddtrace.llmobs._evaluators import BaseEvaluator, EvaluatorContext, EvaluatorResult
from ddtrace.llmobs._evaluators.llm_judge import LLMJudge
from ddeval import BaseProjectEvaluator, Evaluator
from ddeval.evaluators._ai_gateway import DEFAULT_MODEL, create_ai_gateway_client


# Metrics scored 0–10 by the judge, normalized to 0.0–1.0
_STRUCTURED_OUTPUT = {
    "type": "object",
    "properties": {
        "answer_accuracy": {"type": "number"},
        "key_points_coverage": {"type": "number"},
        "evidence_quality": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["answer_accuracy", "key_points_coverage", "evidence_quality", "reasoning"],
    "additionalProperties": False,
}


class _JudgeCache:
    """Thread-safe cache: one LLM call per unique row, shared across all metric evaluators."""

    def __init__(self):
        self._cache: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._cache.get(key)

    def set(self, key: str, scores: dict[str, Any]) -> None:
        with self._lock:
            self._cache[key] = scores


class _SkillJudge(BaseEvaluator):
    def __init__(self, metric_name: str, cache: _JudgeCache):
        super().__init__(name=metric_name)
        self.metric_name = metric_name
        self._cache = cache
        rubric_path = Path(__file__).parent / "prompts" / "judge_rubric.txt"
        self._judge = LLMJudge(
            user_prompt=rubric_path.read_text(),
            client=create_ai_gateway_client(),
            structured_output=_STRUCTURED_OUTPUT,
            model=DEFAULT_MODEL,
        )

    def evaluate(self, context: EvaluatorContext) -> EvaluatorResult:
        if not context.output_data or not context.expected_output:
            return EvaluatorResult(value=None)

        # Fields that uniquely identify a row — prevents redundant LLM calls across metrics
        cache_key = "|".join(
            str(context.input_data.get(f, "")) for f in ["baseline", "candidate", "question"]
        )

        scores = self._cache.get(cache_key)
        if scores is None:
            result = self._judge.evaluate(context)
            scores = result.value
            self._cache.set(cache_key, scores)

        raw = scores.get(self.metric_name)
        return EvaluatorResult(
            value=float(raw) / 10.0 if raw is not None else None,
            reasoning=scores.get("reasoning"),
        )


class ExperimentAnalyzerEvaluator(BaseProjectEvaluator):
    def get_evaluators(self) -> list[Evaluator]:
        cache = _JudgeCache()
        return [
            _SkillJudge("answer_accuracy", cache),
            _SkillJudge("key_points_coverage", cache),
            _SkillJudge("evidence_quality", cache),
        ]

    def get_summary_evaluators(self) -> list:
        return []
