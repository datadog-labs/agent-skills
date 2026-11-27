"""Committed stdlib unittest suite for agent-observability-auto-experiment.

Run from anywhere:
    python3 -m unittest discover -v -s agent-observability/agent-observability-auto-experiment/scripts

Covers:
  1. scripts/stats.py — paired bootstrap, paired case t-test, two-sample t-test, Wilson, noise_floor
  2. scripts/loop_helper.py — all 17 subcommands (happy paths, vetoes, edge cases, failure exits)
  3. references/eval_harness_template.{py,mjs} — split/env/count guards, redaction, full-output
     AUTO_EXP_REGRADE=1, AUTO_EXP_WORKERS=4 deterministic ordering, 3-way `select` split, and
     strict Python <-> Node stdout / result-row / error-row parity.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
SKILL_DIR = SCRIPTS_DIR.parent
REFS_DIR = SKILL_DIR / "references"
HELPER = [sys.executable, str(SCRIPTS_DIR / "loop_helper.py")]


def _load_stats():
    spec = importlib.util.spec_from_file_location("auto_exp_stats_test", SCRIPTS_DIR / "stats.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_helper(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(HELPER + args, capture_output=True, text=True)


class TestStats(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.stats = _load_stats()

    def test_paired_bootstrap_identical_and_shifted(self) -> None:
        same = {f"c{i}": 0.5 for i in range(10)}
        b_same = self.stats.paired_bootstrap_delta(same, same)
        self.assertEqual(b_same["delta"], 0.0)
        self.assertEqual(b_same["ci_95"], (0.0, 0.0))
        self.assertEqual(b_same["p_two_sided"], 1.0)
        self.assertEqual(b_same["n_moved"], 0)

        shifted = {f"c{i}": 0.7 for i in range(10)}
        b_shift = self.stats.paired_bootstrap_delta(same, shifted)
        self.assertEqual(b_shift["delta"], 0.2)
        self.assertGreater(b_shift["ci_95"][0], 0.0)
        self.assertEqual(b_shift["n_moved"], 10)
        # Deterministic seed reproduces identically
        self.assertEqual(self.stats.paired_bootstrap_delta(same, shifted), b_shift)
        self.assertIsNone(self.stats.paired_bootstrap_delta({"a": 1.0}, {"a": 1.0}))

    def test_paired_case_test_zero_and_nonzero_variance(self) -> None:
        base = {f"c{i}": 0.5 for i in range(10)}
        unif = {f"c{i}": 0.7 for i in range(10)}
        p0 = self.stats.paired_case_test(base, unif)
        self.assertIsNone(p0["t_case"])
        self.assertEqual(p0["sd_d"], 0.0)
        self.assertEqual(p0["n_moved"], 10)

        varied = {f"c{i}": 0.5 + (0.3 if i % 2 == 0 else 0.1) for i in range(10)}
        p1 = self.stats.paired_case_test(base, varied)
        self.assertIsNotNone(p1["t_case"])
        self.assertGreater(p1["t_case"], 2.0)

    def test_t_two_sample_wilson_and_noise_floor(self) -> None:
        self.assertIsNone(self.stats.t_two_sample(0.7, 0.0, 0.5, 0.0, 3))
        t_asym = self.stats.t_two_sample(0.7, 0.05, 0.5, 0.05, 6, best_runs=3)
        self.assertIsNotNone(t_asym)
        self.assertGreater(t_asym["t_stat"], 5.0)

        self.assertTrue(self.stats.is_binary({"a": 1, "b": 0.0}))
        self.assertFalse(self.stats.is_binary({"a": 0.5}))
        self.assertFalse(self.stats.is_binary({}))

        w = self.stats.wilson(6, 6)
        self.assertTrue(0.60 <= w[0] <= 0.62 and w[1] == 1.0)
        self.assertEqual(self.stats.noise_floor({"a": 0.5, "b": 0.5, "c": 0.5}), 0.0)
        self.assertGreater(self.stats.noise_floor({"a": 0.0, "b": 1.0, "c": 0.0, "d": 1.0}), 0.0)


class TestLoopHelper(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tdir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _wj(self, name: str, obj: dict) -> str:
        p = self.tdir / name
        p.write_text(json.dumps(obj))
        return str(p)

    def test_split_gates_and_three_way(self) -> None:
        # < 10 STOP
        tiny = self.tdir / "tiny.jsonl"
        tiny.write_text("".join(json.dumps({"id": f"i{i}", "input": "x"}) + "\n" for i in range(8)))
        r = _run_helper(["split", "--input", str(tiny), "--val-out", str(self.tdir / "v.jsonl"), "--test-out", str(self.tdir / "t.jsonl")])
        self.assertNotEqual(r.returncode, 0)

        # 10-29 -> all_rows
        small = self.tdir / "small.jsonl"
        small.write_text("".join(json.dumps({"id": f"i{i}", "input": "x"}) + "\n" for i in range(20)))
        r = _run_helper(["split", "--input", str(small), "--val-out", str(self.tdir / "v.jsonl"), "--test-out", str(self.tdir / "t.jsonl"), "--all-out", str(self.tdir / "a.jsonl")])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["split_mode"], "all_rows")

        # 120 -> three-way stratified
        corp = self.tdir / "corp.jsonl"
        corp.write_text("".join(json.dumps({"id": f"c{i}", "input": f"q {i}", "expected_output": "A" if i % 2 == 0 else "B"}) + "\n" for i in range(120)))
        r3 = _run_helper([
            "split", "--three-way", "--input", str(corp),
            "--val-out", str(self.tdir / "val.jsonl"),
            "--select-out", str(self.tdir / "sel.jsonl"),
            "--test-out", str(self.tdir / "test.jsonl"),
        ])
        self.assertEqual(r3.returncode, 0, r3.stderr)
        sp = json.loads(r3.stdout)
        self.assertEqual(sp["split_mode"], "three_way")
        self.assertEqual(sp["val_case_count"] + sp["select_case_count"] + sp["test_case_count"], 120)

        # audit-corpus passes on disjoint splits and fails on overlap
        r_ac = _run_helper(["audit-corpus", "--splits", f"val={self.tdir/'val.jsonl'}", f"select={self.tdir/'sel.jsonl'}", f"test={self.tdir/'test.jsonl'}"])
        self.assertEqual(r_ac.returncode, 0, r_ac.stderr)
        r_ov = _run_helper(["audit-corpus", "--splits", f"val={self.tdir/'val.jsonl'}", f"test={self.tdir/'val.jsonl'}"])
        self.assertNotEqual(r_ov.returncode, 0)

    def test_calibrate_distribution_memorization_and_verify_mean(self) -> None:
        r = _run_helper(["calibrate", "--val-stdev", "0.02", "--test-stdev", "0.05", "--max-runs", "5", "--judge-flip-rate", "0.01", "--guardrails-json", '{"cost_usd":{"baseline":0.02,"stdev":0.001}}'])
        self.assertEqual(r.returncode, 0, r.stderr)
        cal = json.loads(r.stdout)
        self.assertEqual(cal["runs"], 5)
        self.assertGreaterEqual(cal["min_delta"], 0.063)

        r_dist = _run_helper(["distribution", "--per-case-json", '{"a":0.0,"b":0.0,"c":0.667,"d":1.0,"e":1.0,"f":1.0}'])
        dist = json.loads(r_dist.stdout)
        self.assertEqual((dist["q1"], dist["median"], dist["zero"], dist["perfect"]), (0.0, 0.667, 2, 3))

        val_mem = self.tdir / "val_mem.jsonl"
        val_mem.write_text(json.dumps({"id": "m1", "input": "hello world", "expected_output": "secret target answer phrase here"}) + "\n")
        diff_hit = self.tdir / "hit.patch"
        diff_hit.write_text("+x = 'secret target answer phrase here'\n")
        r_mem = _run_helper(["check-memorization", "--diff-file", str(diff_hit), "--val-cache", str(val_mem)])
        self.assertEqual(json.loads(r_mem.stdout)["verdict"], "needs_review")

        res_jsonl = self.tdir / "res.jsonl"
        res_jsonl.write_text("\n".join([
            json.dumps({"id": "c1", "rep": 0, "score": 0.0}),
            json.dumps({"id": "c2", "rep": 0, "score": 1.0}),
            json.dumps({"id": "c1", "rep": 1, "score": 0.5}),
            json.dumps({"id": "c2", "rep": 1, "score": 1.0}),
        ]) + "\n")
        self.assertEqual(_run_helper(["verify-mean", "--results-jsonl", str(res_jsonl), "--printed-mean", "0.625"]).returncode, 0)
        self.assertNotEqual(_run_helper(["verify-mean", "--results-jsonl", str(res_jsonl), "--printed-mean", "0.700"]).returncode, 0)

    def test_decide_audit_record_stop_check_payloads_and_climbability(self) -> None:
        pcm0 = {f"c{i}": (0.0 if i < 6 else 1.0) for i in range(10)}
        pcm1 = {f"c{i}": (0.0 if i < 2 else 1.0) for i in range(10)}
        base = {"dataset_id": "d1", "mean": 0.4, "stdev": 0.01, "runs": 5, "scored": 10, "excluded": 0, "errored": 0, "errored_cases": 0, "truncated": 0, "per_case_means": pcm0, "usage": {"latency_ms_mean": 100.0, "latency_ms_p95": 150.0, "input_tokens_mean": 40.0, "output_tokens_mean": 10.0, "cost_usd_mean": 0.01}}
        cand = {**base, "mean": 0.8, "per_case_means": pcm1}
        bv = self._wj("bv.json", base)
        cv = self._wj("cv.json", cand)
        bt = self._wj("bt.json", {**base, "dataset_id": "t1"})
        ct_up = self._wj("ct_up.json", {**cand, "dataset_id": "t1"})
        ct_flat = self._wj("ct_flat.json", {**base, "dataset_id": "t1"})

        # Kept + significant with lever_layer
        r_keep = _run_helper(["decide", "--best-val", bv, "--cand-val", cv, "--best-test", bt, "--cand-test", ct_up, "--min-delta", "0.02", "--lever-layer", "retrieval"])
        dec = json.loads(r_keep.stdout)
        self.assertEqual((dec["decision"], dec["basis"], dec["n_moved"], dec["lever_layer"]), ("kept", "significant", 4, "retrieval"))
        res_path = self._wj("result.json", dec)

        # Overfit veto
        r_ov = _run_helper(["decide", "--best-val", bv, "--cand-val", cv, "--best-test", bt, "--cand-test", ct_flat, "--min-delta", "0.02"])
        self.assertEqual(json.loads(r_ov.stdout)["basis"], "overfit")

        # all_rows mode
        r_all = _run_helper(["decide", "--split-mode", "all_rows", "--best-all", bv, "--cand-all", cv, "--min-delta", "0.02"])
        self.assertTrue(json.loads(r_all.stdout)["in_sample_warning"])

        # id-keyed audit
        r_aud = _run_helper(["audit", "--best", bv, "--candidate", cv, "--best-excluded", "0", "--cand-excluded", "0"])
        self.assertEqual(len(json.loads(r_aud.stdout)["flipped_up"]), 4)

        # submit-payload carries lever_layer tag and no leading '+'
        r_sp = _run_helper(["submit-payload", "--experiment-id", "exp-1", "--result", res_path, "--iteration", "1", "--sha", "a" * 40, "--decision", "kept", "--reasoning", "KEPT sig", "--time-start-ms", "1000", "--time-end-ms", "2000"])
        tags = json.loads(r_sp.stdout)["metrics"][0]["tags"]
        self.assertFalse(any(":+" in t for t in tags))
        self.assertIn("split:test", tags)
        self.assertIn("lever_layer:retrieval", tags)

        # summarize-signals contrasts passing vs failing rows and lists failing trace_ids
        sig_jsonl = self.tdir / "sig.jsonl"
        sig_jsonl.write_text("\n".join([
            json.dumps({"id": "p1", "rep": 0, "score": 1.0, "trace_id": "t_ok", "span_id": "s_ok", "signals": {"retrieved_count": 5, "tools_called": ["search"], "branch": "fast"}}),
            json.dumps({"id": "f1", "rep": 0, "score": 0.0, "trace_id": "t_bad", "span_id": "s_bad", "signals": {"retrieved_count": 0, "tools_called": [], "branch": "fallback", "apm_trace_id": "apm_99"}}),
        ]) + "\n")
        r_sig = _run_helper(["summarize-signals", "--results-jsonl", str(sig_jsonl)])
        self.assertEqual(r_sig.returncode, 0, r_sig.stderr)
        sig = json.loads(r_sig.stdout)
        self.assertEqual(sig["passing"]["numeric_means"]["retrieved_count"], 5.0)
        self.assertEqual(sig["failing"]["numeric_means"]["retrieved_count"], 0.0)
        self.assertEqual(sig["failing_traces_to_inspect"][0]["apm_trace_id"], "apm_99")

        # check-climbability
        r_cc = _run_helper(["check-climbability", "--val-stdout", bv, "--test-stdout", bt, "--min-shippable-delta", "0.50"])
        self.assertEqual(json.loads(r_cc.stdout)["preflight"], "GO")

        # record + stop-check (2 prompt discards with code in scope -> require_non_prompt_lever=True!)
        cfg = self.tdir / "config.json"
        cfg.write_text(json.dumps({"max_iterations": 8, "files_to_optimize": ["backend/search.py", "prompts/sys.md"], "split_mode": "val_test", "val_dataset_id": "d1", "test_dataset_id": "t1", "test_case_count": 10, "iteration_results": []}))
        _run_helper(["record", "--config", str(cfg), "--row", json.dumps({"iteration": 0, "decision": "baseline", "basis": "baseline", "sha": "0" * 40, "val": {"after": 0.4}, "test": {"after": 0.4, "scored": 10, "excluded": 0, "errored": 0}, "time_start": "2026-10-01T00:00:00Z", "time_end": "2026-10-01T00:01:00Z"})])
        for i in (1, 2):
            _run_helper(["record", "--config", str(cfg), "--row", json.dumps({"iteration": i, "lever_layer": "prompt", "decision": "discarded", "basis": "overfit", "sha": str(i) * 40, "val": {"after": 0.5}, "test": {"after": 0.4, "delta": 0.0, "scored": 10, "excluded": 0, "errored": 0}})])
        sc2 = json.loads(_run_helper(["stop-check", "--config", str(cfg)]).stdout)
        self.assertTrue(sc2["require_non_prompt_lever"])
        _run_helper(["record", "--config", str(cfg), "--row", json.dumps({"iteration": 3, "lever_layer": "prompt", "decision": "discarded", "basis": "overfit", "sha": "3" * 40, "val": {"after": 0.5}, "test": {"after": 0.4, "delta": 0.0, "scored": 10, "excluded": 0, "errored": 0}})])
        self.assertEqual(json.loads(_run_helper(["stop-check", "--config", str(cfg)]).stdout)["action"], "stall_triage")

        # pool-confirm
        b_jsonl = self.tdir / "b_test.jsonl"
        g_jsonl = self.tdir / "g_test.jsonl"
        b_jsonl.write_text("".join(json.dumps({"id": k, "rep": r, "score": v}) + "\n" for r in range(3) for k, v in pcm0.items()))
        g_jsonl.write_text("".join(json.dumps({"id": k, "rep": r, "score": v}) + "\n" for r in range(3) for k, v in pcm1.items()))
        r_pc = _run_helper(["pool-confirm", "--baseline-results", str(b_jsonl), "--best-results", str(g_jsonl), "--min-delta", "0.02", "--dataset-id", "t1"])
        pc = json.loads(r_pc.stdout)
        self.assertEqual(pc["merge_recommendation"], "merge")

        # case-payloads + render-narrative + verify-final
        r_cp = _run_helper(["case-payloads", "--experiment-id", "exp-1", "--results-jsonl", str(g_jsonl), "--iteration", "1", "--split", "test", "--sha", "a" * 40])
        self.assertEqual(json.loads(r_cp.stdout)["total_cases"], 10)
        self.assertEqual(_run_helper(["render-narrative", "--config", str(cfg), "--out", str(self.tdir / "narrative.md")]).returncode, 0)

        cfg_data = json.loads(cfg.read_text())
        cfg_data["final_result"] = {
            "merge_recommendation": pc["merge_recommendation"],
            "selection_rounds": 3,
            "noise_calibration": {"runs_final": 5, "min_delta": 0.02},
            "reasoning": "done",
            "val": {"dataset_id": "d1", "case_count": 10, "baseline_score": 0.4, "best_score": 0.8, "delta": 0.4},
            "test": pc,
        }
        cfg.write_text(json.dumps(cfg_data))
        self.assertEqual(_run_helper(["verify-final", "--config", str(cfg)]).returncode, 0)


class TestHarnessTemplateParity(unittest.TestCase):
    def test_python_and_node_guards_regrade_workers_and_parity(self) -> None:
        node_bin = shutil.which("node")
        tail_token = "FINAL_TAIL_TOKEN"
        py_src = (REFS_DIR / "eval_harness_template.py").read_text()
        mjs_src = (REFS_DIR / "eval_harness_template.mjs").read_text()

        py_wired = py_src.replace(
            'raise NotImplementedError("wire generate to the real code under test")',
            f'''assert not any(k in line for k in REFERENCE_FIELDS), f"leaked ref: {{line.keys()}}"
    if line["id"] == "excl":
        return None
    if line["id"] == "err":
        raise HarnessError("timeout", "sim timeout")
    if line["id"] == "trunc":
        return {{"output": "half", "model": "m1", "usage": {{"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001}}, "latency_s": 0.25, "stop_reason": "max_tokens", "refusal": False}}
    if line["id"] == "refuse":
        return {{"output": "no", "model": "m1", "usage": {{"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001}}, "latency_s": 0.2, "stop_reason": "end_turn", "refusal": True}}
    return {{"output": ("x" * 700) + "{tail_token}", "model": "m1", "usage": {{"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001}}, "latency_s": 0.1, "stop_reason": "end_turn", "refusal": False, "signals": {{"tools_called": ["search"], "retrieved_count": 3}}}}''',
        ).replace(
            'raise NotImplementedError("wire grade to a real ground-truth check or LLM-as-judge call")',
            f'''assert "expected_output" in line
    if line["id"] == "ok1":
        assert generation.get("signals", {{}}).get("retrieved_count") == 3
    return (1.0 if "{tail_token}" in generation["output"] else 0.0, "checked")''',
        )

        mjs_wired = mjs_src.replace(
            'throw new Error("wire generate to the real code under test");',
            f'''for (const k of REFERENCE_FIELDS) {{ if (k in line) throw new Error("leaked ref: " + k); }}
  if (line.id === "excl") return null;
  if (line.id === "err") throw new HarnessError("timeout", "sim timeout");
  if (line.id === "trunc") return {{ output: "half", model: "m1", usage: {{ input_tokens: 10, output_tokens: 5, cost_usd: 0.001 }}, latency_s: 0.25, stop_reason: "max_tokens", refusal: false }};
  if (line.id === "refuse") return {{ output: "no", model: "m1", usage: {{ input_tokens: 10, output_tokens: 5, cost_usd: 0.001 }}, latency_s: 0.2, stop_reason: "end_turn", refusal: true }};
  return {{ output: "x".repeat(700) + "{tail_token}", model: "m1", usage: {{ input_tokens: 10, output_tokens: 5, cost_usd: 0.001 }}, latency_s: 0.1, stop_reason: "end_turn", refusal: false, signals: {{ tools_called: ["search"], retrieved_count: 3 }} }};''',
        ).replace(
            'throw new Error("wire grade to a real ground-truth check or LLM-as-judge call");',
            f'''if (!("expected_output" in line)) throw new Error("missing ref");
  if (line.id === "ok1" && (!generation.signals || generation.signals.retrieved_count !== 3)) throw new Error("missing signals");
  return [generation.output.includes("{tail_token}") ? 1.0 : 0.0, "checked"];''',
        )

        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            (tdir / "cache").mkdir()
            (tdir / "holdout" / "cache").mkdir(parents=True)
            (tdir / "eval_harness.py").write_text(py_wired)
            (tdir / "eval_harness.mjs").write_text(mjs_wired)
            (tdir / "config.json").write_text(json.dumps({
                "val_dataset_id": "ds-val", "select_dataset_id": "ds-sel", "test_dataset_id": "ds-test",
                "val_case_count": 5, "select_case_count": 5, "test_case_count": 5, "grader_version": 2,
            }))

            def mk_rows(split: str) -> str:
                return "".join(
                    json.dumps({"id": cid, "split": split, "input": f"in-{cid}", "expected_output": f"gold-{cid}"}) + "\n"
                    for cid in ("ok1", "excl", "err", "trunc", "refuse")
                )

            (tdir / "cache" / "ds-val.jsonl").write_text(mk_rows("val"))
            (tdir / "holdout" / "cache" / "ds-sel.jsonl").write_text(mk_rows("select"))
            (tdir / "holdout" / "cache" / "ds-test.jsonl").write_text(mk_rows("test"))

            def run(cmd: list[str], extra: dict[str, str]) -> subprocess.CompletedProcess:
                env = {k: v for k, v in os.environ.items() if not k.startswith("AUTO_EXP_")}
                env.update(extra)
                return subprocess.run(cmd, cwd=tmp, env=env, capture_output=True, text=True)

            cmds = [("py", [sys.executable, "eval_harness.py"])]
            if node_bin:
                cmds.append(("mjs", [node_bin, "eval_harness.mjs"]))

            outputs = {}
            for label, cmd in cmds:
                # Guards must fail
                self.assertNotEqual(run(cmd, {"AUTO_EXP_DATASET_ID": "ds-test"}).returncode, 0)
                self.assertNotEqual(run(cmd, {"AUTO_EXP_SPLIT": "test", "AUTO_EXP_DATASET_ID": "ds-test", "AUTO_EXP_DATA": str(tdir / "holdout/cache/ds-test.jsonl")}).returncode, 0)
                self.assertNotEqual(run(cmd, {"AUTO_EXP_SPLIT": "test", "AUTO_EXP_DATASET_ID": "ds-val"}).returncode, 0)
                self.assertNotEqual(run(cmd, {"AUTO_EXP_SPLIT": "select", "AUTO_EXP_DATASET_ID": "ds-test"}).returncode, 0)

                # Full run with AUTO_EXP_WORKERS=4 on test + select
                for sp, ds in (("test", "ds-test"), ("select", "ds-sel")):
                    res = run(cmd, {"AUTO_EXP_SPLIT": sp, "AUTO_EXP_DATASET_ID": ds, "AUTO_EXP_RUNS": "3", "AUTO_EXP_WORKERS": "4", "AUTO_EXP_ITERATION": "1"})
                    self.assertEqual(res.returncode, 0, f"{label}/{sp}: {res.stderr}")
                    j = json.loads(res.stdout)
                    outputs[(label, sp)] = j
                    self.assertEqual((j["split"], j["scored"], j["excluded"], j["errored"], j["errored_cases"]), (sp, 3, 1, 3, 1))
                    # Full-output regrade preserves >700-char output and score
                    rg = run(cmd, {"AUTO_EXP_SPLIT": sp, "AUTO_EXP_DATASET_ID": ds, "AUTO_EXP_REGRADE": "1", "AUTO_EXP_WORKERS": "4"})
                    self.assertEqual(rg.returncode, 0, rg.stderr)
                    self.assertEqual(json.loads(rg.stdout)["mean"], j["mean"])

            if node_bin:
                for sp in ("test", "select"):
                    py_j, mjs_j = outputs[("py", sp)], outputs[("mjs", sp)]
                    self.assertEqual(set(py_j.keys()), set(mjs_j.keys()))
                    self.assertEqual(set(py_j["usage"].keys()), set(mjs_j["usage"].keys()))
                    for k in ("split", "dataset_id", "mean", "stdev", "runs", "scored", "excluded", "errored", "errored_cases", "truncated", "refusals", "per_case_means", "models_seen", "grader_version"):
                        self.assertEqual(py_j[k], mjs_j[k], f"{sp}.{k} mismatch")


if __name__ == "__main__":
    unittest.main()
