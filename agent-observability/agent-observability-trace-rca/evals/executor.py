"""Executor for the agent-observability-trace-rca Claude Code skill."""

import os
import sys
from typing import Any

_eval_lib_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if _eval_lib_path not in sys.path:
    sys.path.insert(0, _eval_lib_path)

from _eval_lib import DEFAULT_TIMEOUT_SECONDS, invoke_skill  # noqa: E402
from ddeval import BaseProjectExecutor, Config  # noqa: E402


class LlmObsTraceRcaExecutor(BaseProjectExecutor):
    def execute_single(
        self, input_data: dict[str, Any], config: Config | None
    ) -> dict[str, Any]:
        cfg = config.executor_config if config else {}
        timeout_seconds = cfg.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        mcp_env = cfg.get("mcp_env", "prod")

        if input_data.get("from") and input_data.get("to"):
            time_spec = f"from {input_data['from']} to {input_data['to']}"
        else:
            timeframe = input_data.get("timeframe", "now-24h")
            time_spec = f"over the last {timeframe}"

        ml_app = input_data.get("ml_app")
        eval_name = input_data.get("eval_name")
        mode = input_data.get("mode")
        failure_filter = input_data.get("failure_filter")

        # Append explicit mode/filter hints so the skill skips inference when needed.
        hints = []
        if mode:
            hints.append(f"mode={mode}")
        if failure_filter:
            hints.append(f"filter={failure_filter}")
        suffix = f" [{', '.join(hints)}]" if hints else ""

        if ml_app and eval_name:
            prompt = f"/agent-observability-trace-rca Analyze eval failures for {eval_name} on {ml_app} {time_spec}{suffix}"
        elif eval_name:
            prompt = f"/agent-observability-trace-rca Analyze eval failures for {eval_name} {time_spec}{suffix}"
        elif ml_app:
            prompt = f"/agent-observability-trace-rca What's wrong with {ml_app} {time_spec}{suffix}"
        else:
            raise ValueError("input_data must contain either 'ml_app' or 'eval_name'")

        report = invoke_skill(prompt, timeout_seconds=timeout_seconds, mcp_env=mcp_env)
        return {"report": report, "word_count": len(report.split()) if report else 0}
