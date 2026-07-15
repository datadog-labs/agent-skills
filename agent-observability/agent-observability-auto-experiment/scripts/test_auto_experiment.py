"""Tests for the auto-experiment orchestrator's MACHINE logic.

These guard the loop's correctness core — the parts SKILL.md used to describe only in English and
Claude had to re-derive every run: the noise-band gate, the denominator guard, the stop
conditions, the exact LLM-Obs payload. If any of these regress, the loop silently keeps
within-noise wins, reports fabricated scores, or never stops.

Run: `python -m pytest scripts/test_auto_experiment.py`  (or `python scripts/test_auto_experiment.py`)
"""

import json

import auto_experiment as ax


# --- decide: the keep/discard gate -----------------------------------------------------------


def test_within_noise_gain_is_not_kept():
    # point estimate rose 0.01 but stdev is 0.05 -> within noise, NOT a real move
    r = ax.decide(before=0.70, after=0.71, before_stdev=0.05, after_stdev=0.05)
    assert r["improved"] is True
    assert r["cleared_band"] is False
    assert r["within_noise"] is True
    assert r["is_best_numeric"] is False


def test_real_gain_clears_band():
    r = ax.decide(before=0.70, after=0.85, before_stdev=0.02, after_stdev=0.02)
    assert r["cleared_band"] is True
    assert r["is_best_numeric"] is True


def test_min_delta_floor_applies_when_stdev_tiny():
    # deterministic metric: stdev ~0, but a 0.01 move is below the 0.02 floor -> not kept
    r = ax.decide(before=0.90, after=0.91, before_stdev=0.0, after_stdev=0.0, min_delta=0.02)
    assert r["noise_band"] == 0.02
    assert r["is_best_numeric"] is False
    r2 = ax.decide(before=0.90, after=0.93, before_stdev=0.0, after_stdev=0.0, min_delta=0.02)
    assert r2["is_best_numeric"] is True


def test_pooled_stdev_takes_larger_run():
    r = ax.decide(before=0.5, after=0.6, before_stdev=0.03, after_stdev=0.08)
    assert r["pooled_stdev"] == 0.08
    assert r["noise_band"] == 0.08  # 0.10 delta < 0.08? no, 0.10 > 0.08 -> cleared
    assert r["cleared_band"] is True


def test_regression_is_not_within_noise_when_it_clears_band():
    # a real drop: improved False, cleared_band True, within_noise False (regression, not plateau)
    r = ax.decide(before=0.80, after=0.60, before_stdev=0.02, after_stdev=0.02)
    assert r["improved"] is False
    assert r["cleared_band"] is True
    assert r["within_noise"] is False
    assert r["is_best_numeric"] is False


def test_minimize_direction():
    # goal is to MINIMIZE (e.g. latency / error count): a drop is an improvement
    r = ax.decide(before=0.80, after=0.60, before_stdev=0.02, after_stdev=0.02, direction="min")
    assert r["improved"] is True
    assert r["is_best_numeric"] is True
    r2 = ax.decide(before=0.60, after=0.80, before_stdev=0.02, after_stdev=0.02, direction="min")
    assert r2["improved"] is False
    assert r2["is_best_numeric"] is False


def test_bad_direction_raises():
    try:
        ax.decide(0.5, 0.6, 0.01, 0.01, direction="sideways")
    except ValueError:
        return
    raise AssertionError("expected ValueError for bad direction")


# --- audit: mechanism + denominator guard ----------------------------------------------------


def test_denominator_guard_flags_fewer_scored():
    # candidate scored fewer datapoints -> higher mean is an artifact, denominator NOT ok
    best = [0.0, 0.0, 1.0, 1.0]
    cand = [1.0, 1.0, 1.0]  # dropped a hard case out of the eval set
    r = ax.audit(best, cand)
    assert r["scored_match"] is False
    assert r["denominator_ok"] is False


def test_audit_reports_flips():
    best = [0.0, 1.0, 0.0]
    cand = [1.0, 1.0, 0.0]  # index 0 flipped up
    r = ax.audit(best, cand)
    assert r["denominator_ok"] is True
    assert [f["index"] for f in r["flipped_up"]] == [0]
    assert r["flipped_down"] == []
    assert r["unchanged"] == 2


def test_audit_excluded_mismatch_fails_denominator():
    best = [1.0, 0.0]
    cand = [1.0, 0.0]
    r = ax.audit(best, cand, best_excluded=3, candidate_excluded=5)
    assert r["scored_match"] is True
    assert r["excluded_match"] is False
    assert r["denominator_ok"] is False


# --- submit-payload: the exact LLM-Obs metric ------------------------------------------------


def test_payload_shape():
    sha = "33ec6e0959bd46b0ea9c337cf6a28a763d3eeb0a"  # full 40-char hash, never abbreviated
    p = ax.submit_payload(iteration=5, sha=sha, score=0.72,
                          experiment_id="exp_123", now_ms=1752430000000,
                          decision="kept", reasoning="rewrote retrieval query; kept")
    assert p["experiment_id"] == "exp_123"
    assert len(p["metrics"]) == 1  # exactly one metric per iteration
    m = p["metrics"][0]
    assert m["label"] == "auto_experiment_score"
    assert m["metric_type"] == "score"
    assert m["score_value"] == 0.72
    assert m["timestamp_ms"] == 1752430000000
    assert m["tags"] == [f"iteration:5", f"git.commit.sha:{sha}", "decision:kept"]
    assert m["reasoning"] == "rewrote retrieval query; kept"
    assert "span_id" not in m and "boolean_value" not in m


def test_payload_baseline_and_optional_fields():
    # iteration 0 baseline carries decision:baseline; reasoning omitted => no reasoning key
    p = ax.submit_payload(0, "abc", 0.5, experiment_id="exp", now_ms=1, decision="baseline")
    m = p["metrics"][0]
    assert m["tags"] == ["iteration:0", "git.commit.sha:abc", "decision:baseline"]
    assert "reasoning" not in m
    # no decision/reasoning => neither appears (back-compat)
    m2 = ax.submit_payload(1, "abc", 0.5, experiment_id="exp", now_ms=1)["metrics"][0]
    assert m2["tags"] == ["iteration:1", "git.commit.sha:abc"]


def test_payload_skips_when_no_experiment_id():
    p = ax.submit_payload(1, "abc", 0.5, experiment_id="", now_ms=1)
    assert "skip" in p
    assert "metrics" not in p


def test_payload_refuses_none_score():
    try:
        ax.submit_payload(1, "abc", None, experiment_id="exp", now_ms=1)
    except ValueError:
        return
    raise AssertionError("expected ValueError when score is None (no_change must not report)")


# --- record: state machine -------------------------------------------------------------------


def test_record_kept_advances_best():
    cfg = {"max_iterations": 5, "iteration_results": []}
    row = {"iteration": 1, "decision": "kept", "sha": "aaa", "after_score": 0.8}
    cfg = ax.record_row(cfg, row)
    assert cfg["best_sha"] == "aaa"
    assert cfg["best_score"] == 0.8
    assert cfg["best_iteration"] == 1
    assert len(cfg["iteration_results"]) == 1


def test_record_discarded_does_not_advance_best():
    cfg = {"max_iterations": 5, "best_sha": "aaa", "best_score": 0.8,
           "iteration_results": [{"iteration": 1, "decision": "kept", "sha": "aaa", "after_score": 0.8}]}
    row = {"iteration": 2, "decision": "discarded", "sha": "bbb", "after_score": 0.81, "within_noise": True}
    cfg = ax.record_row(cfg, row)
    assert cfg["best_sha"] == "aaa"  # unchanged
    assert cfg["best_score"] == 0.8
    assert len(cfg["iteration_results"]) == 2


# --- stop-check: budget / plateau / no_change ------------------------------------------------


def _rows(*decisions_within):
    """Build iteration_results from (decision, within_noise) pairs."""
    return [{"iteration": i + 1, "decision": d, "within_noise": w}
            for i, (d, w) in enumerate(decisions_within)]


def test_stop_at_max_iterations():
    cfg = {"max_iterations": 2, "iteration_results": _rows(("kept", False), ("discarded", True))}
    assert ax.stop_check(cfg) == {"stop": True, "stop_reason": "reached max_iterations"}


def test_stop_on_plateau():
    cfg = {"max_iterations": 10,
           "iteration_results": _rows(("kept", False), ("discarded", True),
                                      ("discarded", True), ("discarded", True))}
    r = ax.stop_check(cfg)
    assert r["stop"] is True
    assert r["stop_reason"] == "plateau (deltas within noise)"


def test_regression_streak_is_not_a_plateau():
    # three discards but they CLEARED the band (real regressions, within_noise False) -> keep going
    cfg = {"max_iterations": 10,
           "iteration_results": _rows(("discarded", False), ("discarded", False), ("discarded", False))}
    assert ax.stop_check(cfg)["stop"] is False


def test_stop_on_no_change_streak():
    cfg = {"max_iterations": 10, "iteration_results": _rows(*[("no_change", False)] * 5)}
    r = ax.stop_check(cfg)
    assert r["stop"] is True
    assert r["stop_reason"] == "no_change streak (nothing computable)"


def test_no_stop_when_progressing():
    cfg = {"max_iterations": 10, "iteration_results": _rows(("kept", False), ("discarded", True))}
    assert ax.stop_check(cfg)["stop"] is False


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
