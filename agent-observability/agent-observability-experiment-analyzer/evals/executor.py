"""Executor for the agent-observability-experiment-analyzer Claude Code skill."""

import os
import sys
from typing import Any

_eval_lib_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if _eval_lib_path not in sys.path:
    sys.path.insert(0, _eval_lib_path)

from _eval_lib import DEFAULT_TIMEOUT_SECONDS, invoke_skill  # noqa: E402
from ddeval import BaseProjectExecutor, Config  # noqa: E402


class ExperimentAnalyzerExecutor(BaseProjectExecutor):
    def execute_single(
        self, input_data: dict[str, Any], config: Config | None
    ) -> dict[str, Any]:
        cfg = config.executor_config if config else {}
        timeout_seconds = cfg.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        mcp_env = cfg.get("mcp_env", "prod")

        baseline = input_data.get("baseline")
        candidate = input_data.get("candidate")
        experiment_id = input_data.get("experiment_id")

        if not baseline and not candidate and not experiment_id:
            raise ValueError(
                "input_data must contain 'baseline', 'candidate', or 'experiment_id'"
            )

        parts = ["/agent-observability-experiment-analyzer"]
        if baseline:
            parts.append(baseline)
        if candidate:
            parts.append(candidate)
        if not baseline and not candidate and experiment_id:
            parts.append(experiment_id)
        if input_data.get("question"):
            parts.append(input_data["question"])
        parts.append("--output agent")
        prompt = " ".join(parts)

        # prompt is passed to the Claude Agent SDK, not to a shell — not a command injection risk
        report = invoke_skill(prompt, timeout_seconds=timeout_seconds, mcp_env=mcp_env)
        return {"report": report, "word_count": len(report.split()) if report else 0}
