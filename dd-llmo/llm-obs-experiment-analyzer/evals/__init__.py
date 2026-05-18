from .executor import ExperimentAnalyzerExecutor
from .evaluator import ExperimentAnalyzerEvaluator

PROJECT_CONFIG = {
    "name": "llmo-bits-ai-eng-skill-evals",
    "executor": ExperimentAnalyzerExecutor,
    "evaluator": ExperimentAnalyzerEvaluator,
    "description": "Evaluates the llm-obs-experiment-analyzer Claude Code skill",
}

__all__ = ["ExperimentAnalyzerExecutor", "ExperimentAnalyzerEvaluator"]
