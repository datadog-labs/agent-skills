#!/usr/bin/env python3
"""Copy this to `.auto_experiment/loop_helper.py` in iteration 1, beside `stats.py`.

Deterministic CLI helper for the auto-experiment orchestrator. Moves error-prone arithmetic,
stratified splitting, 5-gram memorization scanning, mechanism-audit / keep-discard decisions,
nearest-rank quartiles, Datadog normalization-safe tag building, narrative rendering, and the
final verification gate out of prose and into tested stdlib Python.

Subcommands:
  split               Stratified deterministic corpus split + corpus-size gate
  calibrate           Resolvable min_delta, runs, and per-guardrail floors
  check-memorization  5-gram overlap scan over git diff added lines vs val rows
  distribution        Nearest-rank five-number summary + zero/perfect counts
  audit               ID-keyed per-case flip diff + denominator guard on eval_results
  decide              3-valued mechanism audit + keep/discard matrix + stats.py labels
  record              Atomically append iteration row to config.json, advance best, compute ETA
  stop-check          Deterministic budget / plateau / stall-triage / no_change state machine
  build-tags          Datadog normalization-safe tag array for auto_experiment_score
  submit-payload      Complete ready-to-send MCP / pup payload for auto_experiment_score
  case-payloads       Batched ready-to-send payloads for auto_experiment_case_score
  render-narrative    Deterministic status table for .auto_experiment/narrative.md
  verify-final        Final verification gate before marking experiment completed
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
import math
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
REFERENCE_FIELDS = ("expected_output", "reference", "gold", "label")
WORD_RE = re.compile(r"[a-z0-9_]+")


def _load_stats():
    stats_path = HERE / "stats.py"
    if not stats_path.exists():
        raise SystemExit(f"stats.py not found alongside loop_helper.py at {stats_path}")
    spec = importlib.util.spec_from_file_location("auto_exp_stats", stats_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _nearest_rank(sorted_vals: list[float], fraction: float) -> float:
    rank = max(1, min(len(sorted_vals), math.ceil(fraction * len(sorted_vals))))
    return round(float(sorted_vals[rank - 1]), 4)


def compute_distribution(per_case_means: dict, basis: str) -> dict:
    vals = sorted(float(v) for v in per_case_means.values())
    if not vals:
        raise SystemExit("cannot compute score_distribution over empty per_case_means")
    return {
        "basis": basis,
        "values": [round(v, 6) for v in vals],
        "n": len(vals),
        "zero": sum(1 for v in vals if v == 0.0),
        "perfect": sum(1 for v in vals if v == 1.0),
        "min": round(vals[0], 4),
        "q1": _nearest_rank(vals, 0.25),
        "median": _nearest_rank(vals, 0.50),
        "q3": _nearest_rank(vals, 0.75),
        "max": round(vals[-1], 4),
    }


def _pick_stratify_key(rows: list[dict], explicit: str | None) -> tuple[str, list[str]]:
    if explicit:
        return explicit, [str(r.get(explicit, r.get("metadata", {}).get(explicit, "_missing"))) for r in rows]
    span_names = [r.get("span_name") or r.get("metadata", {}).get("span_name") for r in rows]
    if any(span_names):
        return "span_name", [str(s or "_missing") for s in span_names]
    exp_vals = [r.get("expected_output") for r in rows]
    non_empty = [str(v) for v in exp_vals if v not in (None, "", [], {})]
    if non_empty and len(set(non_empty)) <= 10:
        return "expected_output", [str(v if v not in (None, "", [], {}) else "_missing") for v in exp_vals]
    lengths = [len(r.get("input") if isinstance(r.get("input"), str) else json.dumps(r.get("input"))) for r in rows]
    s_len = sorted(lengths)
    t1 = s_len[len(s_len) // 3]
    t2 = s_len[(2 * len(s_len)) // 3]
    buckets = ["short" if ln <= t1 else ("medium" if ln <= t2 else "long") for ln in lengths]
    return "input_length_tercile", buckets


def cmd_split(args: argparse.Namespace) -> None:
    rows = _read_jsonl(Path(args.input))
    n = len(rows)
    if n < 10:
        raise SystemExit(
            f"corpus size gate STOP: only {n} scoreable cases (< 10). Widen the source before splitting."
        )
    if n < 30 and not args.force_split:
        stamped = [{**r, "split": "all"} for r in rows]
        if args.all_out:
            _write_jsonl(Path(args.all_out), stamped)
        print(json.dumps({
            "split_mode": "all_rows",
            "corpus_count": n,
            "val_case_count": n,
            "test_case_count": 0,
            "stratify_key": None,
            "split_strata": None,
            "split_seed": args.seed,
            "small_corpus_warning": True,
        }))
        return

    strat_key, strata_labels = _pick_stratify_key(rows, args.stratify_by)
    grouped: dict[str, list[dict]] = {}
    for row, label in zip(rows, strata_labels):
        grouped.setdefault(label, []).append(row)

    val_rows: list[dict] = []
    test_rows: list[dict] = []
    strata_counts: dict[str, dict[str, int]] = {}

    for label in sorted(grouped):
        group = grouped[label]
        ranked = sorted(
            group,
            key=lambda r: hashlib.sha256(f"{args.seed}:{r.get('id')}".encode()).hexdigest(),
        )
        n_test = max(1, round(len(ranked) * (1.0 - args.ratio))) if len(ranked) >= 2 else 0
        n_val = len(ranked) - n_test
        g_val = [{**r, "split": "val"} for r in ranked[:n_val]]
        g_test = [{**r, "split": "test"} for r in ranked[n_val:]]
        val_rows.extend(g_val)
        test_rows.extend(g_test)
        strata_counts[label] = {"val": len(g_val), "test": len(g_test)}

    val_ids = {r.get("id") for r in val_rows}
    test_ids = {r.get("id") for r in test_rows}
    overlap = val_ids & test_ids
    if overlap:
        raise SystemExit(f"split integrity error: overlapping ids {sorted(overlap)[:5]}")
    if len(val_rows) + len(test_rows) != n:
        raise SystemExit(f"split sum mismatch: {len(val_rows)} + {len(test_rows)} != {n}")

    _write_jsonl(Path(args.val_out), val_rows)
    _write_jsonl(Path(args.test_out), test_rows)
    print(json.dumps({
        "split_mode": "val_test",
        "corpus_count": n,
        "val_case_count": len(val_rows),
        "test_case_count": len(test_rows),
        "stratify_key": strat_key,
        "split_strata": strata_counts,
        "split_seed": args.seed,
        "small_corpus_warning": n < 50,
    }))


def cmd_calibrate(args: argparse.Namespace) -> None:
    sigma = max(float(args.val_stdev), float(args.test_stdev))
    max_runs = max(3, int(args.max_runs))
    flip = max(0.0, float(args.judge_flip_rate))
    min_delta = round(max(0.02, flip, sigma * math.sqrt(8.0 / max_runs)), 4)
    if sigma == 0.0:
        runs = 3
    else:
        raw_runs = math.ceil(8.0 * ((sigma / min_delta) ** 2))
        runs = max(3, min(max_runs, raw_runs))

    guardrails_in = json.loads(args.guardrails_json) if args.guardrails_json else {}
    guardrail_floors = {}
    for metric, info in guardrails_in.items():
        base = abs(float(info["baseline"]))
        sd = abs(float(info.get("stdev", 0.0)))
        guardrail_floors[metric] = round(max(0.02 * base, sd * math.sqrt(8.0 / max_runs)), 6)

    if min_delta > 0.02 and sigma > 0:
        double_runs = max_runs * 2
        tighter = round(max(0.02, flip, sigma * math.sqrt(8.0 / double_runs)), 4)
        note = (
            f"this metric's noise (sigma={sigma:.4f}, judge_flip_rate={flip:.4f}) means the run "
            f"can only confirm effects >= {min_delta:.4f} at max_runs={max_runs}; raising max_runs "
            f"to {double_runs} would tighten that to {tighter:.4f}"
        )
    else:
        note = f"baseline noise (sigma={sigma:.4f}) resolves the 0.0200 floor at runs={runs}"

    print(json.dumps({
        "sigma": round(sigma, 6),
        "judge_flip_rate": flip,
        "min_delta": min_delta,
        "runs": runs,
        "max_runs": max_runs,
        "guardrail_floors": guardrail_floors,
        "resolution_note": note,
    }))


def _ngrams(tokens: list[str], n: int = 5) -> set[tuple[str, ...]]:
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)} if len(tokens) >= n else set()


def cmd_check_memorization(args: argparse.Namespace) -> None:
    diff_text = sys.stdin.read() if args.diff_file == "-" else Path(args.diff_file).read_text()
    val_rows = _read_jsonl(Path(args.val_cache))
    corpus_ngrams: set[tuple[str, ...]] = set()
    for row in val_rows:
        for field in ("input",) + REFERENCE_FIELDS:
            val = row.get(field)
            if val in (None, "", [], {}):
                continue
            text = val if isinstance(val, str) else json.dumps(val)
            corpus_ngrams.update(_ngrams(WORD_RE.findall(text.lower()), 5))

    hits: list[dict] = []
    for raw_line in diff_text.splitlines():
        if not raw_line.startswith("+") or raw_line.startswith("+++"):
            continue
        added = raw_line[1:].strip()
        tokens = WORD_RE.findall(added.lower())
        if len(tokens) < 5:
            continue
        matched = _ngrams(tokens, 5) & corpus_ngrams
        if matched:
            hits.append({"line": added[:200], "matched_5gram": " ".join(sorted(matched)[0])})

    print(json.dumps({
        "checked": True,
        "hits": hits,
        "verdict": "clean" if not hits else "needs_review",
    }))


def cmd_distribution(args: argparse.Namespace) -> None:
    per_case = json.loads(args.per_case_json)
    print(json.dumps(compute_distribution(per_case, args.basis)))


def _paired_full_disagreement(best_pcm: dict, cand_pcm: dict) -> tuple[float, float, float]:
    ids = sorted(set(best_pcm) & set(cand_pcm))
    paired_delta = (
        (sum(cand_pcm[i] for i in ids) - sum(best_pcm[i] for i in ids)) / len(ids) if ids else 0.0
    )
    full_delta = (
        (sum(cand_pcm.values()) / len(cand_pcm) if cand_pcm else 0.0)
        - (sum(best_pcm.values()) / len(best_pcm) if best_pcm else 0.0)
    )
    return round(paired_delta, 6), round(full_delta, 6), round(abs(paired_delta - full_delta), 6)


def cmd_decide(args: argparse.Namespace) -> None:
    stats = _load_stats()
    best_val = json.loads(Path(args.best_val).read_text())
    cand_val = json.loads(Path(args.cand_val).read_text())
    best_test = json.loads(Path(args.best_test).read_text())
    cand_test = json.loads(Path(args.cand_test).read_text())
    min_delta = float(args.min_delta)
    higher = args.direction == "higher"

    def dir_sign(delta: float) -> float:
        return delta if higher else -delta

    val_delta = cand_val["mean"] - best_val["mean"]
    test_delta = cand_test["mean"] - best_test["mean"]
    t_val = stats.t_two_sample(cand_val["mean"], cand_val["stdev"], best_val["mean"], best_val["stdev"], cand_val["runs"], best_val["runs"])
    t_test = stats.t_two_sample(cand_test["mean"], cand_test["stdev"], best_test["mean"], best_test["stdev"], cand_test["runs"], best_test["runs"])
    boot_test = stats.paired_bootstrap_delta(best_test["per_case_means"], cand_test["per_case_means"])
    paired_test = stats.paired_case_test(best_test["per_case_means"], cand_test["per_case_means"])

    test_sig = (
        (abs(test_delta) >= min_delta and (t_test is None or t_test["t_stat"] >= 2.0))
    )

    # 3-valued mechanism audit
    audit_failed_reasons = []
    void_error = False
    for name, b, c in (("val", best_val, cand_val), ("test", best_test, cand_test)):
        if b["excluded"] != c["excluded"]:
            audit_failed_reasons.append(f"{name} excluded changed ({b['excluded']} -> {c['excluded']})")
        case_count = max(1, b["scored"] + b.get("errored_cases", 0))
        if c["errored"] > b["errored"] + max(1, int(0.05 * case_count)):
            void_error = True
        _, _, diff_gap = _paired_full_disagreement(b["per_case_means"], c["per_case_means"])
        if diff_gap > min_delta:
            audit_failed_reasons.append(f"{name} paired-vs-full delta gap {diff_gap:.4f} > min_delta {min_delta:.4f}")

    guardrails = json.loads(args.guardrails_json) if args.guardrails_json else []
    guardrail_regressed = any(g.get("status") == "regressed" for g in guardrails)
    memorized = args.memorization_verdict == "memorized"

    # Precedence: audit_failed > guardrail_regression > memorized > regression > overfit > unreplicated > significant > within_noise
    val_up = dir_sign(val_delta) > 0
    test_up = dir_sign(test_delta) > 0
    test_down_sig = dir_sign(test_delta) < 0 and test_sig

    if void_error:
        decision, is_best, basis = "no_change", False, "void_error_profile"
    elif audit_failed_reasons:
        decision, is_best, basis = "discarded", False, "audit_failed"
    elif guardrail_regressed:
        decision, is_best, basis = "discarded", False, "guardrail_regression"
    elif memorized:
        decision, is_best, basis = "discarded", False, "memorized"
    elif test_down_sig:
        decision, is_best, basis = "discarded", False, "regression"
    elif val_up and not test_up:
        decision, is_best, basis = "discarded", False, "overfit"
    elif test_up and not val_up:
        decision, is_best, basis = "discarded", False, "unreplicated"
    elif val_up and test_up:
        decision, is_best, basis = "kept", True, ("significant" if test_sig else "within_noise")
    else:
        decision, is_best, basis = "discarded", False, "within_noise"

    suspicion_bar = max(3.0 * min_delta, 0.15)
    suspicious_reason = None
    if dir_sign(test_delta) > suspicion_bar:
        suspicious_reason = f"test delta {test_delta:+.4f} exceeds 3*min_delta/0.15 bar ({suspicion_bar:.4f}) — hand-check val flips"
    elif args.target_ceiling is not None and dir_sign(test_delta) > float(args.target_ceiling):
        suspicious_reason = f"test delta {test_delta:+.4f} exceeds targeted bucket ceiling ({float(args.target_ceiling):.4f}) — hand-check val flips"

    out = {
        "headline_split": "test",
        "split_mode": "val_test",
        "grader_version": cand_test.get("grader_version", 1),
        "runs": cand_test["runs"],
        "min_delta": min_delta,
        "val": {
            "dataset_id": cand_val.get("dataset_id"),
            "before": best_val["mean"],
            "after": cand_val["mean"],
            "stdev": cand_val["stdev"],
            "delta": round(val_delta, 6),
            "se_diff": t_val["se_diff"] if t_val else 0.0,
            "t_stat": t_val["t_stat"] if t_val else None,
            "scored": cand_val["scored"],
            "excluded": cand_val["excluded"],
            "errored": cand_val["errored"],
            "truncated": cand_val["truncated"],
        },
        "test": {
            "dataset_id": cand_test.get("dataset_id"),
            "before": best_test["mean"],
            "after": cand_test["mean"],
            "stdev": cand_test["stdev"],
            "delta": round(test_delta, 6),
            "se_diff": t_test["se_diff"] if t_test else 0.0,
            "t_stat": t_test["t_stat"] if t_test else None,
            "bootstrap": boot_test,
            "paired_case": paired_test,
            "scored": cand_test["scored"],
            "excluded": cand_test["excluded"],
            "errored": cand_test["errored"],
            "truncated": cand_test["truncated"],
        },
        "overfit_gap": round(val_delta - test_delta, 6),
        "guardrails": guardrails,
        "usage": cand_test.get("usage", {}),
        "score_distribution": compute_distribution(cand_test["per_case_means"], "case_means_test"),
        "decision": decision,
        "significant": bool(test_sig),
        "is_best": is_best,
        "basis": basis,
        "audit_failed_reasons": audit_failed_reasons,
        "suspicious": suspicious_reason,
        "suspicion_checked": False,
        "n_moved": (boot_test or {}).get("n_moved", 0),
    }
    print(json.dumps(out, indent=2))


def _build_tags_list(
    res: dict,
    iteration: int,
    sha: str,
    decision: str,
    time_start_ms: int,
    time_end_ms: int,
    goal_target: str = "score",
    goal_direction: str = "higher",
    split_mode: str = "val_test",
    extra_tags: list[str] | None = None,
) -> list[str]:
    tags = [
        f"iteration:{iteration}",
        f"git.commit.sha:{sha.lower()}",
        f"decision:{decision.lower()}",
        f"basis:{str(res.get('basis') or decision).lower()}",
        f"goal_target:{goal_target.lower()}",
        f"goal_direction:{goal_direction.lower()}",
        f"grader_version:{int(res.get('grader_version', 1))}",
        f"time_start_ms:{int(time_start_ms)}",
        f"time_end_ms:{int(time_end_ms)}",
    ]
    if extra_tags:
        tags.extend(t.lower() for t in extra_tags)
    if decision == "no_change":
        return tags

    split = res.get("headline_split", "test")
    sm = res.get("split_mode", split_mode)
    tags.extend([f"split:{split}", f"split_mode:{sm}"])
    if sm == "val_test" and "val" in res and "test" in res:
        tags.append(f"val_score:{res['val']['after']:.4f}")
        tags.append(f"test_score:{res['test']['after']:.4f}")
    elif sm == "three_way" and "val" in res and "select" in res:
        tags.append(f"val_score:{res['val']['after']:.4f}")
        tags.append(f"select_score:{res['select']['after']:.4f}")

    if iteration > 0:
        tblock = res[split] if split in res else res.get("test") or res.get("all") or {}
        delta = float(tblock.get("delta", 0.0))
        sign = "pos" if delta > 0 else ("neg" if delta < 0 else "zero")
        t_stat = "null" if tblock.get("t_stat") is None else f"{tblock['t_stat']:.4f}"
        tags.extend([
            f"delta_vs_best:{abs(delta):.4f}",
            f"delta_sign:{sign}",
            f"t_stat:{t_stat}",
            f"significant:{str(bool(res.get('significant'))).lower()}",
            f"n_moved:{int(res.get('n_moved', 0))}",
        ])
        if sm in ("val_test", "three_way"):
            gap = float(res.get("overfit_gap", 0.0))
            gsign = "pos" if gap > 0 else ("neg" if gap < 0 else "zero")
            tags.extend([f"overfit_gap:{abs(gap):.4f}", f"overfit_gap_sign:{gsign}"])

    for g in res.get("guardrails") or []:
        m = str(g["metric"]).lower()
        tags.append(f"hold_{m}_ratio:{float(g['ratio']):.4f}")
        tags.append(f"hold_{m}_status:{str(g['status']).lower()}")

    tags.append(f"suspicious:{str(bool(res.get('suspicious'))).lower()}")

    dist = res.get("score_distribution")
    if dist:
        tags.extend([
            f"dist_basis:{dist['basis']}",
            f"dist_n:{int(dist['n'])}",
            f"dist_zero:{int(dist['zero'])}",
            f"dist_perfect:{int(dist['perfect'])}",
            f"dist_min:{float(dist['min']):.4f}",
            f"dist_q1:{float(dist['q1']):.4f}",
            f"dist_median:{float(dist['median']):.4f}",
            f"dist_q3:{float(dist['q3']):.4f}",
            f"dist_max:{float(dist['max']):.4f}",
        ])

    usage = res.get("usage") or {}
    if isinstance(usage.get("latency_ms_mean"), (int, float)):
        tags.append(f"latency_ms_mean:{int(round(usage['latency_ms_mean']))}")
    if isinstance(usage.get("latency_ms_p95"), (int, float)):
        tags.append(f"latency_ms_p95:{int(round(usage['latency_ms_p95']))}")
    if isinstance(usage.get("input_tokens_mean"), (int, float)) and isinstance(usage.get("output_tokens_mean"), (int, float)):
        tags.append(f"tokens_per_case:{int(round(usage['input_tokens_mean'] + usage['output_tokens_mean']))}")
    if isinstance(usage.get("cost_usd_mean"), (int, float)):
        tags.append(f"cost_usd_per_case:{usage['cost_usd_mean']:.6f}")
    return tags


def cmd_build_tags(args: argparse.Namespace) -> None:
    res = json.loads(Path(args.result).read_text())
    tags = _build_tags_list(
        res, int(args.iteration), args.sha, args.decision,
        int(args.time_start_ms), int(args.time_end_ms),
        args.goal_target, args.goal_direction, args.split_mode,
    )
    print(json.dumps(tags))


def _load_per_case_means(path: Path) -> dict[str, float]:
    raw = path.read_text().strip()
    if not raw:
        return {}
    if raw.startswith("{") and "\n" not in raw:
        obj = json.loads(raw)
        if "per_case_means" in obj:
            return {str(k): float(v) for k, v in obj["per_case_means"].items()}
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    grouped: dict[str, list[float]] = {}
    for row in rows:
        grouped.setdefault(str(row["id"]), []).append(float(row["score"]))
    return {k: round(sum(v) / len(v), 6) for k, v in grouped.items()}


def cmd_audit(args: argparse.Namespace) -> None:
    best = _load_per_case_means(Path(args.best))
    cand = _load_per_case_means(Path(args.candidate))
    shared = sorted(set(best) & set(cand))
    missing_in_cand = sorted(set(best) - set(cand))
    missing_in_best = sorted(set(cand) - set(best))
    flipped_up, flipped_down = [], []
    unchanged = 0
    for cid in shared:
        d = round(cand[cid] - best[cid], 6)
        if d > 0:
            flipped_up.append({"id": cid, "before": best[cid], "after": cand[cid], "delta": d})
        elif d < 0:
            flipped_down.append({"id": cid, "before": best[cid], "after": cand[cid], "delta": d})
        else:
            unchanged += 1
    excluded_match = None
    if args.best_excluded is not None and args.cand_excluded is not None:
        excluded_match = int(args.best_excluded) == int(args.cand_excluded)
    denominator_ok = (not missing_in_cand) and (not missing_in_best) and (excluded_match is not False)
    print(json.dumps({
        "best_scored": len(best),
        "cand_scored": len(cand),
        "shared": len(shared),
        "excluded_match": excluded_match,
        "denominator_ok": denominator_ok,
        "flipped_up": flipped_up,
        "flipped_down": flipped_down,
        "unchanged": unchanged,
        "missing_in_cand": missing_in_cand,
        "missing_in_best": missing_in_best,
    }, indent=2))


def _iso_to_seconds(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def cmd_record(args: argparse.Namespace) -> None:
    cfg_path = Path(args.config)
    cfg = json.loads(cfg_path.read_text())
    row = json.loads(Path(args.row_file).read_text()) if args.row_file else json.loads(args.row)
    rows = cfg.setdefault("iteration_results", [])
    rows.append(row)

    it = int(row.get("iteration", 0))
    split = row.get("headline_split") or ("all" if cfg.get("split_mode") == "all_rows" else "test")
    score_block = row.get(split) or row.get("test") or row.get("all") or {}
    headline_score = score_block.get("after") if isinstance(score_block, dict) else row.get("after_score")

    if row.get("decision") == "baseline" and it == 0:
        cfg["best_iteration"] = 0
        cfg["best_sha"] = row.get("sha")
        cfg["best_score"] = headline_score
    elif row.get("decision") == "kept":
        cfg["best_iteration"] = it
        cfg["best_sha"] = row.get("sha")
        cfg["best_score"] = headline_score

    # Compute estimated_duration_time (seconds remaining) from real elapsed wall-clock timestamps
    durations = []
    for r in rows:
        if r.get("time_start") and r.get("time_end"):
            try:
                durations.append(max(0.0, _iso_to_seconds(r["time_end"]) - _iso_to_seconds(r["time_start"])))
            except ValueError:
                pass
    max_iters = int(cfg.get("max_iterations", 8))
    imp_done = sum(1 for r in rows if int(r.get("iteration", 0)) > 0 and r.get("basis") != "stall_triage")
    iters_left = max(0, max_iters - imp_done)
    eta_s = round((sum(durations) / len(durations)) * iters_left) if durations else None

    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
    print(json.dumps({
        "best_iteration": cfg.get("best_iteration"),
        "best_sha": cfg.get("best_sha"),
        "best_score": cfg.get("best_score"),
        "iterations_recorded": len(rows),
        "improvement_iterations_done": imp_done,
        "iterations_left": iters_left,
        "estimated_duration_time": eta_s,
    }))


def cmd_stop_check(args: argparse.Namespace) -> None:
    cfg = json.loads(Path(args.config).read_text())
    rows = cfg.get("iteration_results") or []
    max_iters = int(cfg.get("max_iterations", 8))

    imp_rows = [r for r in rows if int(r.get("iteration", 0)) > 0]
    imp_done = sum(1 for r in imp_rows if r.get("basis") != "stall_triage")
    iters_left = max(0, max_iters - imp_done)
    stall_triage_ran = any(r.get("basis") == "stall_triage" for r in imp_rows)

    discard_streak = 0
    no_change_streak = 0
    for r in imp_rows:
        dec = r.get("decision")
        basis = r.get("basis")
        if basis == "stall_triage":
            # Reset streak when stall triage dispatched artifact_gap to keep searching
            if r.get("stall_dispatch") == "artifact_gap":
                discard_streak = 0
            continue
        if dec == "kept":
            discard_streak = 0
            no_change_streak = 0
        elif dec == "discarded":
            discard_streak += 1
            no_change_streak = 0
        elif dec == "no_change":
            no_change_streak += 1

    if imp_done >= max_iters:
        out = {"action": "stop", "stop_reason": "reached max_iterations"}
    elif no_change_streak >= 5:
        out = {"action": "stop", "stop_reason": "no_change streak (5 consecutive)"}
    elif discard_streak >= 3:
        if (not stall_triage_ran) and iters_left >= 2:
            out = {"action": "stall_triage", "stop_reason": None}
        else:
            out = {"action": "stop", "stop_reason": "plateau (3 consecutive discards)"}
    else:
        out = {"action": "continue", "stop_reason": None}

    out.update({
        "improvement_iterations_done": imp_done,
        "iterations_left": iters_left,
        "plateau_discard_streak": discard_streak,
        "no_change_streak": no_change_streak,
        "stall_triage_ran": stall_triage_ran,
    })
    print(json.dumps(out))


def cmd_submit_payload(args: argparse.Namespace) -> None:
    if not args.experiment_id:
        raise SystemExit("experiment_id is required")
    res = json.loads(Path(args.result).read_text()) if args.result else {"basis": args.basis or args.decision}
    it = int(args.iteration)
    now_ms = int(args.timestamp_ms) if args.timestamp_ms is not None else int(time.time() * 1000)

    if args.decision == "no_change":
        if args.best_score is None:
            raise SystemExit("no_change requires --best-score (carried-forward marker, never 0)")
        score_value = float(args.best_score)
    else:
        split = res.get("headline_split", "test")
        tblock = res[split] if split in res else res.get("test") or res.get("all") or {}
        score_value = float(tblock["after"])

    extra = [t for t in (args.extra_tag or []) if t]
    if args.promoted:
        res = {**res, "basis": "promoted"}
        extra.append("promoted:higher_power_confirmation")
    if args.regraded:
        res = {**res, "basis": "regraded"}

    tags = _build_tags_list(
        res, it, args.sha, args.decision,
        int(args.time_start_ms), int(args.time_end_ms),
        args.goal_target, args.goal_direction, args.split_mode,
        extra_tags=extra,
    )
    reasoning = args.reasoning or res.get("reasoning") or ""
    if not reasoning:
        raise SystemExit("reasoning is required for LLM-Obs metric submission")

    metric = {
        "label": "auto_experiment_score",
        "metric_type": "score",
        "score_value": score_value,
        "reasoning": reasoning,
        "timestamp_ms": now_ms,
        "tags": tags,
    }
    print(json.dumps({"experiment_id": args.experiment_id, "metrics": [metric]}, indent=2))


def cmd_case_payloads(args: argparse.Namespace) -> None:
    rows = _read_jsonl(Path(args.results_jsonl))
    if not rows:
        raise SystemExit(f"no scored rows in {args.results_jsonl}")
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(str(r["id"]), []).append(r)

    now_ms = int(args.timestamp_ms) if args.timestamp_ms is not None else int(time.time() * 1000)
    metrics = []
    for cid in sorted(grouped):
        case_rows = grouped[cid]
        score_mean = round(sum(float(r["score"]) for r in case_rows) / len(case_rows), 6)
        last = case_rows[-1]
        tags = [
            f"iteration:{int(args.iteration)}",
            f"split:{args.split.lower()}",
            f"case_id:{cid.lower()}",
            f"git.commit.sha:{args.sha.lower()}",
            f"grader_version:{int(args.grader_version)}",
        ]
        if last.get("trace_id"):
            tags.append(f"trace_id:{str(last['trace_id']).lower()}")
        if last.get("span_id"):
            tags.append(f"case_span_id:{str(last['span_id']).lower()}")
        metrics.append({
            "label": "auto_experiment_case_score",
            "metric_type": "score",
            "score_value": score_mean,
            "reasoning": str(last.get("justification") or "")[:1000],
            "timestamp_ms": now_ms,
            "tags": tags,
        })

    batch_size = max(1, int(args.batch_size))
    batches = [
        {"experiment_id": args.experiment_id, "metrics": metrics[i : i + batch_size]}
        for i in range(0, len(metrics), batch_size)
    ]
    over_cap = args.total_planned_events is not None and int(args.total_planned_events) > 2000
    print(json.dumps({
        "total_cases": len(metrics),
        "batch_count": len(batches),
        "over_2000_cap": over_cap,
        "batches": batches,
    }, indent=2))


def cmd_render_narrative(args: argparse.Namespace) -> None:
    cfg = json.loads(Path(args.config).read_text())
    rows = cfg.get("iteration_results") or []
    all_rows = cfg.get("split_mode") == "all_rows"

    if all_rows:
        lines = [
            "| iter | change (one line) | in-sample | Δ | n_moved | basis | $/case | p50 s | scored/excl/err | sha |",
            "|------|-------------------|-----------|---|---------|-------|--------|-------|-----------------|-----|",
        ]
    else:
        lines = [
            "| iter | change (one line) | test | val | Δtest | n_moved | basis | $/case | p50 s | scored/excl/err | sha |",
            "|------|-------------------|------|-----|-------|---------|-------|--------|-------|-----------------|-----|",
        ]

    base_cost = None
    base_p95 = None
    for row in rows:
        it = row.get("iteration", 0)
        change = str(row.get("change") or ("baseline" if it == 0 else "")).replace("|", "/")[:60]
        basis = row.get("basis") or row.get("decision") or "—"
        sha = str(row.get("sha") or "")[:7] or "—"
        usage = row.get("usage") or {}
        cost = usage.get("cost_usd_mean")
        p50 = row.get("latency_p50")
        p95 = usage.get("latency_ms_p95")
        if it == 0:
            base_cost = cost
            base_p95 = p95
        warn = ""
        if it > 0 and row.get("decision") == "kept":
            if isinstance(cost, (int, float)) and isinstance(base_cost, (int, float)) and base_cost > 0 and cost > 1.2 * base_cost:
                warn += " [cost>+20%]"
            if isinstance(p95, (int, float)) and isinstance(base_p95, (int, float)) and base_p95 > 0 and p95 > 1.2 * base_p95:
                warn += " [p95>+20%]"
        cost_s = f"{cost:.4f}" if isinstance(cost, (int, float)) else "—"
        p50_s = f"{p50:.2f}" if isinstance(p50, (int, float)) else "—"
        t = row.get("test") or row.get("all") or {}
        v = row.get("val") or {}
        counts = f"{t.get('scored', '—')}/{t.get('excluded', '—')}/{t.get('errored', '—')}"
        delta = t.get("delta")
        delta_s = "—" if it == 0 or delta is None else (f"**{delta:+.4f}**" if row.get("significant") else f"{delta:+.4f}")
        n_moved = "—" if it == 0 else str(row.get("n_moved", "—"))
        if all_rows:
            score_s = f"{t['after']:.4f}" if isinstance(t.get("after"), (int, float)) else "—"
            lines.append(f"| {it} | {change}{warn} | {score_s} | {delta_s} | {n_moved} | {basis} | {cost_s} | {p50_s} | {counts} | {sha} |")
        else:
            ts = f"{t['after']:.4f}" if isinstance(t.get("after"), (int, float)) else "—"
            vs = f"{v['after']:.4f}" if isinstance(v.get("after"), (int, float)) else "—"
            lines.append(f"| {it} | {change}{warn} | {ts} | {vs} | {delta_s} | {n_moved} | {basis} | {cost_s} | {p50_s} | {counts} | {sha} |")

    content = "\n".join(lines) + "\n\n" + (args.summary or "_Summary pending._") + "\n"
    Path(args.out).write_text(content)
    print(f"wrote {args.out}")


def cmd_verify_final(args: argparse.Namespace) -> None:
    cfg = json.loads(Path(args.config).read_text())
    fr = cfg.get("final_result") or {}
    errors: list[str] = []

    for key in ("merge_recommendation", "selection_rounds", "noise_calibration", "reasoning"):
        if fr.get(key) in (None, "", {}):
            errors.append(f"final_result.{key} is missing")

    if cfg.get("split_mode") != "all_rows":
        test = fr.get("test") or {}
        val = fr.get("val") or {}
        if not test:
            errors.append("final_result.test is missing")
        else:
            if test.get("dataset_id") and val.get("dataset_id") and test["dataset_id"] == val["dataset_id"]:
                errors.append("final_result.test.dataset_id equals final_result.val.dataset_id")
            if cfg.get("test_dataset_id") and test.get("dataset_id") != cfg["test_dataset_id"]:
                errors.append(f"final_result.test.dataset_id ({test.get('dataset_id')}) != config.test_dataset_id ({cfg['test_dataset_id']})")
            if isinstance(cfg.get("test_case_count"), int) and test.get("case_count") != cfg["test_case_count"]:
                errors.append(f"final_result.test.case_count ({test.get('case_count')}) != config.test_case_count ({cfg['test_case_count']})")
            for sub in ("baseline_score", "best_score", "delta", "n_moved", "pooled_runs_per_side"):
                if test.get(sub) is None:
                    errors.append(f"final_result.test.{sub} is missing")

    if errors:
        print(json.dumps({"ok": False, "errors": errors}, indent=2), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({"ok": True, "errors": []}))


def main() -> None:
    p = argparse.ArgumentParser(description="Deterministic helper for auto-experiment")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("split")
    sp.add_argument("--input", required=True)
    sp.add_argument("--val-out", required=True)
    sp.add_argument("--test-out", required=True)
    sp.add_argument("--all-out")
    sp.add_argument("--seed", type=int, default=0)
    sp.add_argument("--stratify-by")
    sp.add_argument("--ratio", type=float, default=0.7)
    sp.add_argument("--force-split", action="store_true")
    sp.set_defaults(func=cmd_split)

    cal = sub.add_parser("calibrate")
    cal.add_argument("--val-stdev", type=float, required=True)
    cal.add_argument("--test-stdev", type=float, required=True)
    cal.add_argument("--max-runs", type=int, required=True)
    cal.add_argument("--judge-flip-rate", type=float, default=0.0)
    cal.add_argument("--guardrails-json")
    cal.set_defaults(func=cmd_calibrate)

    mem = sub.add_parser("check-memorization")
    mem.add_argument("--diff-file", required=True)
    mem.add_argument("--val-cache", required=True)
    mem.set_defaults(func=cmd_check_memorization)

    dist = sub.add_parser("distribution")
    dist.add_argument("--per-case-json", required=True)
    dist.add_argument("--basis", default="case_means_test")
    dist.set_defaults(func=cmd_distribution)

    dec = sub.add_parser("decide")
    dec.add_argument("--best-val", required=True)
    dec.add_argument("--cand-val", required=True)
    dec.add_argument("--best-test", required=True)
    dec.add_argument("--cand-test", required=True)
    dec.add_argument("--min-delta", type=float, required=True)
    dec.add_argument("--direction", choices=("higher", "lower"), default="higher")
    dec.add_argument("--target-ceiling", type=float)
    dec.add_argument("--guardrails-json")
    dec.add_argument("--memorization-verdict", choices=("clean", "explained", "memorized"), default="clean")
    dec.set_defaults(func=cmd_decide)

    aud = sub.add_parser("audit")
    aud.add_argument("--best", required=True)
    aud.add_argument("--candidate", required=True)
    aud.add_argument("--best-excluded", type=int)
    aud.add_argument("--cand-excluded", type=int)
    aud.set_defaults(func=cmd_audit)

    rec = sub.add_parser("record")
    rec.add_argument("--config", required=True)
    rec_src = rec.add_mutually_exclusive_group(required=True)
    rec_src.add_argument("--row")
    rec_src.add_argument("--row-file")
    rec.set_defaults(func=cmd_record)

    sc = sub.add_parser("stop-check")
    sc.add_argument("--config", required=True)
    sc.set_defaults(func=cmd_stop_check)

    bt = sub.add_parser("build-tags")
    bt.add_argument("--result", required=True)
    bt.add_argument("--iteration", type=int, required=True)
    bt.add_argument("--sha", required=True)
    bt.add_argument("--decision", required=True)
    bt.add_argument("--time-start-ms", type=int, required=True)
    bt.add_argument("--time-end-ms", type=int, required=True)
    bt.add_argument("--goal-target", default="score")
    bt.add_argument("--goal-direction", default="higher")
    bt.add_argument("--split-mode", default="val_test")
    bt.set_defaults(func=cmd_build_tags)

    spay = sub.add_parser("submit-payload")
    spay.add_argument("--experiment-id", required=True)
    spay.add_argument("--result")
    spay.add_argument("--iteration", type=int, required=True)
    spay.add_argument("--sha", required=True)
    spay.add_argument("--decision", required=True)
    spay.add_argument("--basis")
    spay.add_argument("--best-score", type=float)
    spay.add_argument("--reasoning")
    spay.add_argument("--timestamp-ms", type=int)
    spay.add_argument("--time-start-ms", type=int, required=True)
    spay.add_argument("--time-end-ms", type=int, required=True)
    spay.add_argument("--goal-target", default="score")
    spay.add_argument("--goal-direction", default="higher")
    spay.add_argument("--split-mode", default="val_test")
    spay.add_argument("--promoted", action="store_true")
    spay.add_argument("--regraded", action="store_true")
    spay.add_argument("--extra-tag", action="append")
    spay.set_defaults(func=cmd_submit_payload)

    cpay = sub.add_parser("case-payloads")
    cpay.add_argument("--experiment-id", required=True)
    cpay.add_argument("--results-jsonl", required=True)
    cpay.add_argument("--iteration", type=int, required=True)
    cpay.add_argument("--split", required=True)
    cpay.add_argument("--sha", required=True)
    cpay.add_argument("--grader-version", type=int, default=1)
    cpay.add_argument("--timestamp-ms", type=int)
    cpay.add_argument("--batch-size", type=int, default=50)
    cpay.add_argument("--total-planned-events", type=int)
    cpay.set_defaults(func=cmd_case_payloads)

    rn = sub.add_parser("render-narrative")
    rn.add_argument("--config", required=True)
    rn.add_argument("--out", required=True)
    rn.add_argument("--summary")
    rn.set_defaults(func=cmd_render_narrative)

    vf = sub.add_parser("verify-final")
    vf.add_argument("--config", required=True)
    vf.set_defaults(func=cmd_verify_final)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
