#!/usr/bin/env python3
"""Copy this to `.auto_experiment/loop_helper.py` in iteration 1, beside `stats.py`.

Deterministic CLI helper for the auto-experiment orchestrator. Moves error-prone arithmetic,
stratified splitting, 5-gram memorization scanning, mechanism-audit / keep-discard decisions,
nearest-rank quartiles, Datadog normalization-safe tag building, narrative rendering, and the
final verification gate out of prose and into tested stdlib Python.

Subcommands:
  split               Stratified deterministic corpus split + corpus-size gate
  audit-corpus        Gate 1 Tier-1 corpus checks (dupes, schema, lengths, zero split overlap)
  summarize-signals   Compare execution signals on passing vs failing rows + list failing trace_ids
  verify-mean         Gate 1 + per-iteration raw-row mean recomputation check to 1e-9
  check-climbability  Gate 3 climbability, saturation class, and val/test population check
  calibrate           Resolvable min_delta, runs, and per-guardrail floors
  check-memorization  5-gram overlap scan over git diff added lines vs val rows
  distribution        Nearest-rank five-number summary + zero/perfect counts
  audit               ID-keyed per-case flip diff + denominator guard on eval_results
  decide              3-valued mechanism audit + keep/discard matrix (val_test|three_way|all_rows)
  pool-confirm        Pool baseline + best test/confirm jsonl files & compute merge_recommendation
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
    non_empty_spans = [str(s) for s in span_names if s not in (None, "")]
    if non_empty_spans and len(set(non_empty_spans)) <= max(2, min(20, len(rows) // 2)):
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


def _collapse_small_strata(grouped: dict[str, list[dict]], min_size: int) -> dict[str, list[dict]]:
    """Pool singleton/tiny strata so high-cardinality keys never yield empty holdout splits."""
    collapsed: dict[str, list[dict]] = {}
    small: list[dict] = []
    for label, group in grouped.items():
        if len(group) < min_size:
            small.extend(group)
        else:
            collapsed[label] = group
    if small:
        collapsed.setdefault("_other_small_strata", []).extend(small)
    return collapsed


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
    select_rows: list[dict] = []
    test_rows: list[dict] = []
    strata_counts: dict[str, dict[str, int]] = {}

    if args.three_way:
        if not args.select_out:
            raise SystemExit("--three-way requires --select-out")
        grouped = _collapse_small_strata(grouped, min_size=3)
        val_ratio = args.ratio if args.ratio != 0.7 else 0.5
        sel_ratio = float(args.select_ratio)
        if val_ratio <= 0 or sel_ratio <= 0 or (val_ratio + sel_ratio) >= 1.0:
            raise SystemExit("invalid three-way ratios: val_ratio and select_ratio must be > 0 and sum < 1")
        for label in sorted(grouped):
            ranked = sorted(
                grouped[label],
                key=lambda r: hashlib.sha256(f"{args.seed}:{r.get('id')}".encode()).hexdigest(),
            )
            m = len(ranked)
            if m >= 3:
                n_sel = max(1, round(m * sel_ratio))
                n_test = max(1, round(m * (1.0 - val_ratio - sel_ratio)))
                n_val = max(1, m - n_sel - n_test)
                # Adjust if rounding overflowed m
                while n_val + n_sel + n_test > m:
                    if n_sel > 1:
                        n_sel -= 1
                    else:
                        n_test -= 1
            elif m == 2:
                n_val, n_sel, n_test = 1, 1, 0
            else:
                n_val, n_sel, n_test = m, 0, 0
            g_val = [{**r, "split": "val"} for r in ranked[:n_val]]
            g_sel = [{**r, "split": "select"} for r in ranked[n_val : n_val + n_sel]]
            g_test = [{**r, "split": "test"} for r in ranked[n_val + n_sel :]]
            val_rows.extend(g_val)
            select_rows.extend(g_sel)
            test_rows.extend(g_test)
            strata_counts[label] = {"val": len(g_val), "select": len(g_sel), "test": len(g_test)}

        v_ids = {r.get("id") for r in val_rows}
        s_ids = {r.get("id") for r in select_rows}
        t_ids = {r.get("id") for r in test_rows}
        if (v_ids & s_ids) or (v_ids & t_ids) or (s_ids & t_ids):
            raise SystemExit("split integrity error: overlapping ids across three-way splits")
        if len(val_rows) + len(select_rows) + len(test_rows) != n:
            raise SystemExit("split sum mismatch in three-way split")
        if not val_rows or not select_rows or not test_rows:
            raise SystemExit("split integrity error: three-way split produced an empty partition")

        _write_jsonl(Path(args.val_out), val_rows)
        _write_jsonl(Path(args.select_out), select_rows)
        _write_jsonl(Path(args.test_out), test_rows)
        print(json.dumps({
            "split_mode": "three_way",
            "corpus_count": n,
            "val_case_count": len(val_rows),
            "select_case_count": len(select_rows),
            "test_case_count": len(test_rows),
            "stratify_key": strat_key,
            "split_strata": strata_counts,
            "split_seed": args.seed,
            "small_corpus_warning": n < 100,
        }))
        return

    grouped = _collapse_small_strata(grouped, min_size=2)
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
    if not val_rows or not test_rows:
        raise SystemExit("split integrity error: val/test split produced an empty partition")

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


def cmd_audit_corpus(args: argparse.Namespace) -> None:
    split_ids: dict[str, set[str]] = {}
    split_stats: dict[str, dict] = {}
    seen_inputs: dict[str, int] = {}
    errors: list[str] = []

    for spec in args.splits:
        if "=" not in spec:
            raise SystemExit(f"expected name=path for --splits, got {spec!r}")
        name, path_str = spec.split("=", 1)
        rows = _read_jsonl(Path(path_str))
        ids: set[str] = set()
        missing_id = 0
        missing_input = 0
        in_lens: list[int] = []
        ref_lens: list[int] = []
        for r in rows:
            cid = r.get("id")
            if cid in (None, ""):
                missing_id += 1
            else:
                ids.add(str(cid))
            inp = r.get("input")
            if inp in (None, ""):
                missing_input += 1
            else:
                text = inp if isinstance(inp, str) else json.dumps(inp, sort_keys=True)
                in_lens.append(len(text))
                seen_inputs[text] = seen_inputs.get(text, 0) + 1
            ref = r.get("expected_output")
            if ref not in (None, "", [], {}):
                ref_lens.append(len(ref if isinstance(ref, str) else json.dumps(ref)))
        if missing_id:
            errors.append(f"{name}: {missing_id} rows missing id")
        if missing_input:
            errors.append(f"{name}: {missing_input} rows missing input")
        in_sorted = sorted(in_lens)
        split_ids[name] = ids
        split_stats[name] = {
            "rows": len(rows),
            "unique_ids": len(ids),
            "missing_id": missing_id,
            "missing_input": missing_input,
            "input_len_min": in_sorted[0] if in_sorted else None,
            "input_len_median": in_sorted[len(in_sorted) // 2] if in_sorted else None,
            "input_len_max": in_sorted[-1] if in_sorted else None,
            "rows_with_reference": len(ref_lens),
        }

    overlap_pairs: dict[str, list[str]] = {}
    names = sorted(split_ids)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            ov = sorted(split_ids[a] & split_ids[b])
            if ov:
                overlap_pairs[f"{a}&{b}"] = ov[:20]
                errors.append(f"overlap between {a} and {b}: {len(ov)} shared ids")

    dup_inputs = sum(c - 1 for c in seen_inputs.values() if c > 1)
    total_rows = sum(s["rows"] for s in split_stats.values())
    out = {
        "ok": not errors,
        "total_rows": total_rows,
        "exact_duplicate_inputs": dup_inputs,
        "duplicate_rate": round(dup_inputs / total_rows, 4) if total_rows else 0.0,
        "overlap_ids": overlap_pairs,
        "splits": split_stats,
        "errors": errors,
    }
    print(json.dumps(out, indent=2))
    if errors:
        raise SystemExit(1)


def cmd_summarize_signals(args: argparse.Namespace) -> None:
    """Compare execution `signals` between passing and failing rows to surface code-layer root causes."""
    rows = _read_jsonl(Path(args.results_jsonl))
    if not rows:
        raise SystemExit(f"no rows in {args.results_jsonl}")
    threshold = float(args.pass_threshold)
    # Aggregate per case id (mean score across reps, merge signals from latest rep)
    by_id: dict[str, list[dict]] = {}
    for r in rows:
        by_id.setdefault(str(r["id"]), []).append(r)

    passing, failing = [], []
    failing_trace_ids: list[dict] = []
    cases_with_signals = 0
    for cid, case_rows in sorted(by_id.items()):
        mean_score = sum(float(r["score"]) for r in case_rows) / len(case_rows)
        last = case_rows[-1]
        sig = last.get("signals") if isinstance(last.get("signals"), dict) else {}
        if sig:
            cases_with_signals += 1
        entry = {"id": cid, "score_mean": mean_score, "signals": sig, "trace_id": last.get("trace_id"), "span_id": last.get("span_id")}
        if mean_score >= threshold:
            passing.append(entry)
        else:
            failing.append(entry)
            if last.get("trace_id") or sig.get("apm_trace_id"):
                failing_trace_ids.append({
                    "id": cid,
                    "score_mean": round(mean_score, 4),
                    "llmobs_trace_id": last.get("trace_id"),
                    "llmobs_span_id": last.get("span_id"),
                    "apm_trace_id": sig.get("apm_trace_id"),
                })

    def _agg_group(group: list[dict]) -> dict:
        if not group:
            return {"count": 0, "numeric_means": {}, "categorical_counts": {}, "list_item_counts": {}}
        num_buckets: dict[str, list[float]] = {}
        cat_buckets: dict[str, dict[str, int]] = {}
        list_buckets: dict[str, dict[str, int]] = {}
        for item in group:
            for k, v in item["signals"].items():
                if isinstance(v, bool):
                    cat_buckets.setdefault(k, {})[str(v).lower()] = cat_buckets.setdefault(k, {}).get(str(v).lower(), 0) + 1
                elif isinstance(v, (int, float)):
                    num_buckets.setdefault(k, []).append(float(v))
                elif isinstance(v, str):
                    cat_buckets.setdefault(k, {})[v] = cat_buckets.setdefault(k, {}).get(v, 0) + 1
                elif isinstance(v, list):
                    for el in v:
                        key = str(el)
                        list_buckets.setdefault(k, {})[key] = list_buckets.setdefault(k, {}).get(key, 0) + 1
        return {
            "count": len(group),
            "numeric_means": {k: round(sum(vs) / len(vs), 4) for k, vs in sorted(num_buckets.items())},
            "categorical_counts": {k: dict(sorted(d.items())) for k, d in sorted(cat_buckets.items())},
            "list_item_counts": {k: dict(sorted(d.items())) for k, d in sorted(list_buckets.items())},
        }

    print(json.dumps({
        "total_cases": len(by_id),
        "cases_with_signals": cases_with_signals,
        "pass_threshold": threshold,
        "passing": _agg_group(passing),
        "failing": _agg_group(failing),
        "failing_traces_to_inspect": failing_trace_ids[:30],
    }, indent=2))


def cmd_verify_mean(args: argparse.Namespace) -> None:
    rows = _read_jsonl(Path(args.results_jsonl))
    if not rows:
        raise SystemExit(f"no rows in {args.results_jsonl}")
    by_rep: dict[int, list[float]] = {}
    for row in rows:
        by_rep.setdefault(int(row.get("rep", 0)), []).append(float(row["score"]))
    rep_means = [sum(v) / len(v) for _, v in sorted(by_rep.items()) if v]
    recomputed = sum(rep_means) / len(rep_means)
    printed = float(args.printed_mean)
    diff = abs(recomputed - printed)
    ok = diff <= 1e-9
    print(json.dumps({
        "ok": ok,
        "recomputed_mean": recomputed,
        "printed_mean": printed,
        "abs_diff": diff,
        "reps": len(rep_means),
    }))
    if not ok:
        raise SystemExit(1)


def cmd_check_climbability(args: argparse.Namespace) -> None:
    stats = _load_stats()
    test_obj = json.loads(Path(args.test_stdout).read_text())
    pcm = test_obj["per_case_means"]
    vals = [float(v) for v in pcm.values()]
    if not vals:
        raise SystemExit("empty per_case_means in --test-stdout")
    test_mean = float(test_obj["mean"])
    nf_test = stats.noise_floor(pcm)
    higher = args.direction == "higher"
    headroom = round((1.0 - test_mean) if higher else test_mean, 4)
    min_ship = float(args.min_shippable_delta)

    v_min, v_max = min(vals), max(vals)
    if v_max == 0.0:
        sat = "always_zero"
    elif v_min == 1.0:
        sat = "perfect"
    elif test_mean >= 0.99 and v_min < 1.0:
        sat = "saturated"
    elif test_mean < 0.70:
        sat = "struggling"
    else:
        sat = "interesting"

    pop_check = None
    if args.val_stdout and args.split_mode != "all_rows":
        val_obj = json.loads(Path(args.val_stdout).read_text())
        val_mean = float(val_obj["mean"])
        gap = abs(val_mean - test_mean)
        t_pop = stats.t_two_sample(test_mean, float(test_obj["stdev"]), val_mean, float(val_obj["stdev"]), int(test_obj["runs"]), int(val_obj["runs"]))
        if t_pop is not None:
            threshold = round(2.0 * t_pop["se_diff"], 4)
        else:
            nf_val = stats.noise_floor(val_obj["per_case_means"]) or 0.0
            threshold = round(max(nf_val, nf_test or 0.0), 4)
        agree = gap <= threshold
        pop_check = {
            "agree": agree,
            "val_mean": round(val_mean, 6),
            "test_mean": round(test_mean, 6),
            "gap": round(gap, 6),
            "threshold": threshold,
            "needs_redraw": not agree,
        }

    stops = []
    if sat in ("always_zero", "perfect"):
        stops.append(f"saturation_class={sat}")
    if nf_test is not None and nf_test > min_ship:
        stops.append(f"noise_floor ({nf_test:.4f}) > min_shippable_delta ({min_ship:.4f})")
    if nf_test is not None and nf_test > headroom:
        stops.append(f"noise_floor ({nf_test:.4f}) > headroom ({headroom:.4f})")
    if pop_check is not None and not pop_check["agree"]:
        stops.append(f"val/test baselines disagree (gap={pop_check['gap']:.4f} > {pop_check['threshold']:.4f}) — eligible for 1 split redraw")

    if stops:
        preflight = "STOP: " + "; ".join(stops)
    elif sat == "saturated":
        preflight = "GO with caveat: metric is saturated (mean >= 0.99); consider switching goal.target to cost_usd or latency_s"
    else:
        preflight = "GO"

    print(json.dumps({
        "noise_floor_test": nf_test,
        "headroom": headroom,
        "min_shippable_delta": min_ship,
        "saturation_class": sat,
        "population_check": pop_check,
        "preflight": preflight,
    }, indent=2))


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


def _extract_target_stats(stdout_obj: dict, target: str, label: str) -> dict:
    """Extract {mean, stdev, per_case_means, runs} for the configured goal.target."""
    if target == "score":
        ms = (stdout_obj.get("metric_stats") or {}).get("score")
        if isinstance(ms, dict):
            return {
                "mean": float(ms["mean"]),
                "stdev": float(ms["stdev"]),
                "per_case_means": ms["per_case_means"],
                "runs": int(stdout_obj.get("runs", len(ms.get("run_means") or []))),
            }
        return {
            "mean": float(stdout_obj["mean"]),
            "stdev": float(stdout_obj["stdev"]),
            "per_case_means": stdout_obj["per_case_means"],
            "runs": int(stdout_obj["runs"]),
        }
    ms = (stdout_obj.get("metric_stats") or {}).get(target)
    if not isinstance(ms, dict) or not ms.get("per_case_means"):
        raise SystemExit(
            f"{label}: goal target {target!r} was not measured (missing metric_stats.{target}); "
            "measure it in generate() / harness instead of falling back to quality score"
        )
    return {
        "mean": float(ms["mean"]),
        "stdev": float(ms["stdev"]),
        "per_case_means": ms["per_case_means"],
        "runs": len(ms.get("run_means") or []) or int(stdout_obj["runs"]),
    }


def cmd_decide(args: argparse.Namespace) -> None:
    stats = _load_stats()
    min_delta = float(args.min_delta)
    higher = args.direction == "higher"
    split_mode = args.split_mode
    target = args.target

    def dir_sign(delta: float) -> float:
        return delta if higher else -delta

    guardrails = json.loads(args.guardrails_json) if args.guardrails_json else []
    guardrail_regressed = any(g.get("status") == "regressed" for g in guardrails)
    memorized = args.memorization_verdict == "memorized"

    if split_mode == "all_rows":
        if not args.best_all or not args.cand_all:
            raise SystemExit("--split-mode all_rows requires --best-all and --cand-all")
        best_a = json.loads(Path(args.best_all).read_text())
        cand_a = json.loads(Path(args.cand_all).read_text())
        ba_t = _extract_target_stats(best_a, target, "best_all")
        ca_t = _extract_target_stats(cand_a, target, "cand_all")
        delta = ca_t["mean"] - ba_t["mean"]
        t_res = stats.t_two_sample(ca_t["mean"], ca_t["stdev"], ba_t["mean"], ba_t["stdev"], ca_t["runs"], ba_t["runs"])
        boot = stats.paired_bootstrap_delta(ba_t["per_case_means"], ca_t["per_case_means"])
        paired = stats.paired_case_test(ba_t["per_case_means"], ca_t["per_case_means"])
        sig = abs(delta) >= min_delta and (t_res is None or t_res["t_stat"] >= 2.0)

        audit_failed_reasons = []
        void_error = False
        if best_a["excluded"] != cand_a["excluded"]:
            audit_failed_reasons.append(f"all excluded changed ({best_a['excluded']} -> {cand_a['excluded']})")
        case_count = max(1, best_a["scored"] + best_a.get("errored_cases", 0))
        if cand_a["errored"] > best_a["errored"] + max(1, int(0.05 * case_count)):
            void_error = True
        _, _, diff_gap = _paired_full_disagreement(ba_t["per_case_means"], ca_t["per_case_means"])
        if diff_gap > min_delta:
            audit_failed_reasons.append(f"all paired-vs-full delta gap {diff_gap:.4f} > min_delta {min_delta:.4f}")

        up = dir_sign(delta) > 0
        down_sig = dir_sign(delta) < 0 and sig
        if void_error:
            decision, is_best, basis = "no_change", False, "void_error_profile"
        elif audit_failed_reasons:
            decision, is_best, basis = "discarded", False, "audit_failed"
        elif guardrail_regressed:
            decision, is_best, basis = "discarded", False, "guardrail_regression"
        elif memorized:
            decision, is_best, basis = "discarded", False, "memorized"
        elif down_sig:
            decision, is_best, basis = "discarded", False, "regression"
        elif up:
            decision, is_best, basis = "kept", True, ("significant" if sig else "within_noise")
        else:
            decision, is_best, basis = "discarded", False, "within_noise"

        suspicion_bar = max(3.0 * min_delta, 0.15) if target == "score" else max(3.0 * min_delta, 0.15 * abs(ba_t["mean"]))
        suspicious_reason = None
        if dir_sign(delta) > suspicion_bar:
            suspicious_reason = f"in-sample {target} delta {delta:+.4f} exceeds suspicion bar ({suspicion_bar:.4f})"
        elif args.target_ceiling is not None and dir_sign(delta) > float(args.target_ceiling):
            suspicious_reason = f"in-sample {target} delta {delta:+.4f} exceeds targeted bucket ceiling ({float(args.target_ceiling):.4f})"

        print(json.dumps({
            "headline_split": "all",
            "split_mode": "all_rows",
            "target": target,
            "in_sample_warning": True,
            "grader_version": cand_a.get("grader_version", 1),
            "runs": ca_t["runs"],
            "min_delta": min_delta,
            "all": {
                "dataset_id": cand_a.get("dataset_id"),
                "before": ba_t["mean"],
                "after": ca_t["mean"],
                "stdev": ca_t["stdev"],
                "delta": round(delta, 6),
                "se_diff": t_res["se_diff"] if t_res else 0.0,
                "t_stat": t_res["t_stat"] if t_res else None,
                "bootstrap": boot,
                "paired_case": paired,
                "scored": cand_a["scored"],
                "excluded": cand_a["excluded"],
                "errored": cand_a["errored"],
                "truncated": cand_a["truncated"],
            },
            "overfit_gap": None,
            "guardrails": guardrails,
            "usage": cand_a.get("usage", {}),
            "score_distribution": compute_distribution(ca_t["per_case_means"], "case_means_all"),
            "decision": decision,
            "significant": bool(sig),
            "is_best": is_best,
            "basis": basis,
            "audit_failed_reasons": audit_failed_reasons,
            "suspicious": suspicious_reason,
            "suspicion_checked": False,
            "lever_layer": args.lever_layer,
            "n_moved": (boot or {}).get("n_moved", 0),
        }, indent=2))
        return

    headline_key = "select" if split_mode == "three_way" else "test"
    best_h_path = args.best_select if split_mode == "three_way" else args.best_test
    cand_h_path = args.cand_select if split_mode == "three_way" else args.cand_test
    if not (args.best_val and args.cand_val and best_h_path and cand_h_path):
        raise SystemExit(f"--split-mode {split_mode} requires --best-val, --cand-val, --best-{headline_key}, and --cand-{headline_key}")

    best_val = json.loads(Path(args.best_val).read_text())
    cand_val = json.loads(Path(args.cand_val).read_text())
    best_test = json.loads(Path(best_h_path).read_text())
    cand_test = json.loads(Path(cand_h_path).read_text())

    bv_t = _extract_target_stats(best_val, target, "best_val")
    cv_t = _extract_target_stats(cand_val, target, "cand_val")
    bt_t = _extract_target_stats(best_test, target, f"best_{headline_key}")
    ct_t = _extract_target_stats(cand_test, target, f"cand_{headline_key}")

    val_delta = cv_t["mean"] - bv_t["mean"]
    test_delta = ct_t["mean"] - bt_t["mean"]
    t_val = stats.t_two_sample(cv_t["mean"], cv_t["stdev"], bv_t["mean"], bv_t["stdev"], cv_t["runs"], bv_t["runs"])
    t_test = stats.t_two_sample(ct_t["mean"], ct_t["stdev"], bt_t["mean"], bt_t["stdev"], ct_t["runs"], bt_t["runs"])
    boot_test = stats.paired_bootstrap_delta(bt_t["per_case_means"], ct_t["per_case_means"])
    paired_test = stats.paired_case_test(bt_t["per_case_means"], ct_t["per_case_means"])

    test_sig = abs(test_delta) >= min_delta and (t_test is None or t_test["t_stat"] >= 2.0)

    # 3-valued mechanism audit
    audit_failed_reasons = []
    void_error = False
    for name, b, c, bt_m, ct_m in (
        ("val", best_val, cand_val, bv_t, cv_t),
        (headline_key, best_test, cand_test, bt_t, ct_t),
    ):
        if b["excluded"] != c["excluded"]:
            audit_failed_reasons.append(f"{name} excluded changed ({b['excluded']} -> {c['excluded']})")
        case_count = max(1, b["scored"] + b.get("errored_cases", 0))
        if c["errored"] > b["errored"] + max(1, int(0.05 * case_count)):
            void_error = True
        _, _, diff_gap = _paired_full_disagreement(bt_m["per_case_means"], ct_m["per_case_means"])
        if diff_gap > min_delta:
            audit_failed_reasons.append(f"{name} paired-vs-full delta gap {diff_gap:.4f} > min_delta {min_delta:.4f}")

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

    suspicion_bar = max(3.0 * min_delta, 0.15) if target == "score" else max(3.0 * min_delta, 0.15 * abs(bt_t["mean"]))
    suspicious_reason = None
    if dir_sign(test_delta) > suspicion_bar:
        suspicious_reason = f"{headline_key} {target} delta {test_delta:+.4f} exceeds suspicion bar ({suspicion_bar:.4f}) — hand-check val flips"
    elif args.target_ceiling is not None and dir_sign(test_delta) > float(args.target_ceiling):
        suspicious_reason = f"{headline_key} {target} delta {test_delta:+.4f} exceeds targeted bucket ceiling ({float(args.target_ceiling):.4f}) — hand-check val flips"

    out = {
        "headline_split": headline_key,
        "split_mode": split_mode,
        "target": target,
        "grader_version": cand_test.get("grader_version", 1),
        "runs": ct_t["runs"],
        "min_delta": min_delta,
        "val": {
            "dataset_id": cand_val.get("dataset_id"),
            "before": bv_t["mean"],
            "after": cv_t["mean"],
            "stdev": cv_t["stdev"],
            "delta": round(val_delta, 6),
            "se_diff": t_val["se_diff"] if t_val else 0.0,
            "t_stat": t_val["t_stat"] if t_val else None,
            "scored": cand_val["scored"],
            "excluded": cand_val["excluded"],
            "errored": cand_val["errored"],
            "truncated": cand_val["truncated"],
        },
        headline_key: {
            "dataset_id": cand_test.get("dataset_id"),
            "before": bt_t["mean"],
            "after": ct_t["mean"],
            "stdev": ct_t["stdev"],
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
        "overfit_gap": round(dir_sign(val_delta) - dir_sign(test_delta), 6),
        "guardrails": guardrails,
        "usage": cand_test.get("usage", {}),
        "score_distribution": compute_distribution(ct_t["per_case_means"], f"case_means_{headline_key}"),
        "decision": decision,
        "significant": bool(test_sig),
        "is_best": is_best,
        "basis": basis,
        "audit_failed_reasons": audit_failed_reasons,
        "suspicious": suspicious_reason,
        "suspicion_checked": False,
        "lever_layer": args.lever_layer,
        "n_moved": (boot_test or {}).get("n_moved", 0),
    }
    print(json.dumps(out, indent=2))


def _row_metric_value(row: dict, target: str, prices: dict | None = None) -> float | None:
    if target == "score":
        v = row.get("score")
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    if target == "latency_s":
        v = row.get("latency_s")
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    if target == "cost_usd":
        usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
        if isinstance(usage.get("cost_usd"), (int, float)):
            return float(usage["cost_usd"])
        if prices and row.get("model") in prices:
            rate = prices[row["model"]]
            in_tok = usage.get("input_tokens")
            out_tok = usage.get("output_tokens")
            if isinstance(in_tok, (int, float)) and isinstance(out_tok, (int, float)):
                return (
                    float(in_tok) * float(rate.get("input_per_token", 0.0))
                    + float(out_tok) * float(rate.get("output_per_token", 0.0))
                )
        return None
    raise SystemExit(f"unsupported target metric: {target!r}")


def _pool_jsonl_files(paths: list[str], target: str = "score", prices: dict | None = None) -> tuple[list[float], dict[str, float]]:
    run_means: list[float] = []
    per_case: dict[str, list[float]] = {}
    for p in paths:
        rows = _read_jsonl(Path(p))
        by_rep: dict[int, list[float]] = {}
        for row in rows:
            val = _row_metric_value(row, target, prices)
            if val is None:
                continue
            rep = int(row.get("rep", 0))
            by_rep.setdefault(rep, []).append(val)
            per_case.setdefault(str(row["id"]), []).append(val)
        for rep in sorted(by_rep):
            vals = by_rep[rep]
            if vals:
                run_means.append(sum(vals) / len(vals))
    if not run_means:
        raise SystemExit(f"no rows with measurable target {target!r} across pooled files: {paths}")
    pcm = {k: round(sum(v) / len(v), 6) for k, v in per_case.items()}
    return run_means, pcm


def _pstdev(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def cmd_pool_confirm(args: argparse.Namespace) -> None:
    stats = _load_stats()
    target = args.target
    prices = json.loads(args.prices_json) if args.prices_json else None
    base_runs, base_pcm = _pool_jsonl_files(args.baseline_results, target=target, prices=prices)
    best_runs, best_pcm = _pool_jsonl_files(args.best_results, target=target, prices=prices)

    base_mean = sum(base_runs) / len(base_runs)
    best_mean = sum(best_runs) / len(best_runs)
    base_sd = _pstdev(base_runs)
    best_sd = _pstdev(best_runs)
    delta = best_mean - base_mean
    higher = args.direction == "higher"
    dir_delta = delta if higher else -delta
    min_delta = float(args.min_delta)

    t_res = stats.t_two_sample(best_mean, best_sd, base_mean, base_sd, len(best_runs), len(base_runs))
    boot = stats.paired_bootstrap_delta(base_pcm, best_pcm)
    paired = stats.paired_case_test(base_pcm, best_pcm)
    n_moved = (boot or {}).get("n_moved", 0)

    wilson_base = (
        stats.wilson(sum(1 for v in base_pcm.values() if v == 1.0), len(base_pcm))
        if target == "score" and stats.is_binary(base_pcm) else None
    )
    wilson_best = (
        stats.wilson(sum(1 for v in best_pcm.values() if v == 1.0), len(best_pcm))
        if target == "score" and stats.is_binary(best_pcm) else None
    )

    # Optional quality guardrail check when optimizing cost_usd / latency_s
    held_score_check = None
    held_score_regressed = False
    if target != "score" and args.hold_score_min_delta is not None:
        sb_runs, _ = _pool_jsonl_files(args.baseline_results, target="score")
        sg_runs, _ = _pool_jsonl_files(args.best_results, target="score")
        sb_m, sg_m = sum(sb_runs) / len(sb_runs), sum(sg_runs) / len(sg_runs)
        sb_sd, sg_sd = _pstdev(sb_runs), _pstdev(sg_runs)
        s_delta = sg_m - sb_m
        s_t = stats.t_two_sample(sg_m, sg_sd, sb_m, sb_sd, len(sg_runs), len(sb_runs))
        s_floor = float(args.hold_score_min_delta)
        held_score_regressed = s_delta < 0 and abs(s_delta) >= s_floor and (s_t is None or s_t["t_stat"] >= 2.0)
        held_score_check = {
            "baseline_score": round(sb_m, 6),
            "best_score": round(sg_m, 6),
            "delta": round(s_delta, 6),
            "t_stat": s_t["t_stat"] if s_t else None,
            "regressed": held_score_regressed,
        }

    paired_delta, full_case_delta, paired_vs_full_gap = _paired_full_disagreement(base_pcm, best_pcm)
    denominator_artifact = paired_vs_full_gap > min_delta
    dir_paired_delta = paired_delta if higher else -paired_delta

    ci_clears = False
    if boot is not None:
        lo, hi = boot["ci_95"]
        ci_clears = (lo > 0) if higher else (hi < 0)
    t_case = (paired or {}).get("t_case")
    case_sig = (
        (not denominator_artifact)
        and ci_clears
        and (t_case is None or t_case >= 2.0)
        and n_moved > 2
        and abs(paired_delta) >= min_delta
    )

    if dir_delta <= 0 or dir_paired_delta <= 0 or held_score_regressed or denominator_artifact:
        rec = "do_not_merge_regression"
    elif not case_sig:
        rec = "do_not_merge_within_noise"
    else:
        rec = "merge"

    print(json.dumps({
        "target": target,
        "dataset_id": args.dataset_id,
        "case_count": len(set(base_pcm) & set(best_pcm)),
        "baseline_case_count": len(base_pcm),
        "best_case_count": len(best_pcm),
        "baseline_score": round(base_mean, 6),
        "best_score": round(best_mean, 6),
        "delta": round(delta, 6),
        "paired_delta": paired_delta,
        "paired_vs_full_gap": paired_vs_full_gap,
        "denominator_artifact": denominator_artifact,
        "baseline_stdev": round(base_sd, 6),
        "best_stdev": round(best_sd, 6),
        "baseline_runs": len(base_runs),
        "best_runs": len(best_runs),
        "pooled_runs_per_side": min(len(base_runs), len(best_runs)),
        "t_stat": t_res["t_stat"] if t_res else None,
        "se_diff": t_res["se_diff"] if t_res else 0.0,
        "bootstrap": boot,
        "paired_case": paired,
        "wilson_baseline": wilson_base,
        "wilson_best": wilson_best,
        "held_score_guardrail": held_score_check,
        "n_moved": n_moved,
        "merge_recommendation": rec,
    }, indent=2))


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
    if res.get("lever_layer"):
        tags.append(f"lever_layer:{str(res['lever_layer']).lower()}")

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
    sm = cfg.get("split_mode")
    default_split = "all" if sm == "all_rows" else ("select" if sm == "three_way" else "test")
    split = row.get("headline_split") or default_split
    score_block = row.get(split) or row.get("select") or row.get("test") or row.get("all") or {}
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
    prompt_discard_streak = 0
    for r in imp_rows:
        dec = r.get("decision")
        basis = r.get("basis")
        if basis == "stall_triage":
            # Reset streak when stall triage dispatched artifact_gap to keep searching
            if r.get("stall_dispatch") == "artifact_gap":
                discard_streak = 0
                prompt_discard_streak = 0
            continue
        if dec == "kept":
            discard_streak = 0
            no_change_streak = 0
            prompt_discard_streak = 0
        elif dec == "discarded":
            discard_streak += 1
            no_change_streak = 0
            if r.get("lever_layer") == "prompt":
                prompt_discard_streak += 1
            else:
                prompt_discard_streak = 0
        elif dec == "no_change":
            no_change_streak += 1

    scope = cfg.get("files_to_optimize") or []
    has_non_prompt_code = any(
        not str(f).lower().endswith((".md", ".txt", ".j2", ".jinja", ".jinja2"))
        for f in scope
    )

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
        "prompt_discard_streak": prompt_discard_streak,
        "require_non_prompt_lever": prompt_discard_streak >= 2 and has_non_prompt_code,
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
    split_mode = cfg.get("split_mode")
    all_rows = split_mode == "all_rows"
    three_way = split_mode == "three_way"
    primary_col = "select" if three_way else "test"

    if all_rows:
        lines = [
            "| iter | layer | change (one line) | in-sample | Δ | n_moved | basis | $/case | p50 s | scored/excl/err | sha |",
            "|------|-------|-------------------|-----------|---|---------|-------|--------|-------|-----------------|-----|",
        ]
    else:
        lines = [
            f"| iter | layer | change (one line) | {primary_col} | val | Δ{primary_col} | n_moved | basis | $/case | p50 s | scored/excl/err | sha |",
            "|------|-------|-------------------|--------|-----|--------|---------|-------|--------|-------|-----------------|-----|",
        ]

    base_cost = None
    base_p95 = None
    for row in rows:
        it = row.get("iteration", 0)
        layer = str(row.get("lever_layer") or "—")
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
        hs = row.get("headline_split")
        t = (row.get(hs) if hs else None) or row.get("select") or row.get("test") or row.get("all") or {}
        v = row.get("val") or {}
        counts = f"{t.get('scored', '—')}/{t.get('excluded', '—')}/{t.get('errored', '—')}"
        delta = t.get("delta")
        delta_s = "—" if it == 0 or delta is None else (f"**{delta:+.4f}**" if row.get("significant") else f"{delta:+.4f}")
        n_moved = "—" if it == 0 else str(row.get("n_moved", "—"))
        if all_rows:
            score_s = f"{t['after']:.4f}" if isinstance(t.get("after"), (int, float)) else "—"
            lines.append(f"| {it} | {layer} | {change}{warn} | {score_s} | {delta_s} | {n_moved} | {basis} | {cost_s} | {p50_s} | {counts} | {sha} |")
        else:
            ts = f"{t['after']:.4f}" if isinstance(t.get("after"), (int, float)) else "—"
            vs = f"{v['after']:.4f}" if isinstance(v.get("after"), (int, float)) else "—"
            lines.append(f"| {it} | {layer} | {change}{warn} | {ts} | {vs} | {delta_s} | {n_moved} | {basis} | {cost_s} | {p50_s} | {counts} | {sha} |")

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
        if not val:
            errors.append("final_result.val is missing")
        else:
            if cfg.get("val_dataset_id") and val.get("dataset_id") != cfg["val_dataset_id"]:
                errors.append(f"final_result.val.dataset_id ({val.get('dataset_id')}) != config.val_dataset_id ({cfg['val_dataset_id']})")
            if isinstance(cfg.get("val_case_count"), int) and val.get("case_count") != cfg["val_case_count"]:
                errors.append(f"final_result.val.case_count ({val.get('case_count')}) != config.val_case_count ({cfg['val_case_count']})")
            for sub in ("baseline_score", "best_score", "delta"):
                if val.get(sub) is None:
                    errors.append(f"final_result.val.{sub} is missing")
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
    sp.add_argument("--select-out")
    sp.add_argument("--test-out", required=True)
    sp.add_argument("--all-out")
    sp.add_argument("--seed", type=int, default=0)
    sp.add_argument("--stratify-by")
    sp.add_argument("--ratio", type=float, default=0.7)
    sp.add_argument("--select-ratio", type=float, default=0.25)
    sp.add_argument("--three-way", action="store_true")
    sp.add_argument("--force-split", action="store_true")
    sp.set_defaults(func=cmd_split)

    ac = sub.add_parser("audit-corpus")
    ac.add_argument("--splits", nargs="+", required=True, help="name=path pairs, e.g. val=v.jsonl test=t.jsonl")
    ac.set_defaults(func=cmd_audit_corpus)

    ss = sub.add_parser("summarize-signals")
    ss.add_argument("--results-jsonl", required=True)
    ss.add_argument("--pass-threshold", type=float, default=1.0)
    ss.set_defaults(func=cmd_summarize_signals)

    vm = sub.add_parser("verify-mean")
    vm.add_argument("--results-jsonl", required=True)
    vm.add_argument("--printed-mean", type=float, required=True)
    vm.set_defaults(func=cmd_verify_mean)

    cc = sub.add_parser("check-climbability")
    cc.add_argument("--test-stdout", required=True)
    cc.add_argument("--val-stdout")
    cc.add_argument("--min-shippable-delta", type=float, required=True)
    cc.add_argument("--direction", choices=("higher", "lower"), default="higher")
    cc.add_argument("--split-mode", choices=("val_test", "three_way", "all_rows"), default="val_test")
    cc.set_defaults(func=cmd_check_climbability)

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
    dec.add_argument("--split-mode", choices=("val_test", "three_way", "all_rows"), default="val_test")
    dec.add_argument("--best-val")
    dec.add_argument("--cand-val")
    dec.add_argument("--best-select")
    dec.add_argument("--cand-select")
    dec.add_argument("--best-test")
    dec.add_argument("--cand-test")
    dec.add_argument("--best-all")
    dec.add_argument("--cand-all")
    dec.add_argument("--min-delta", type=float, required=True)
    dec.add_argument("--target", choices=("score", "cost_usd", "latency_s"), default="score")
    dec.add_argument("--direction", choices=("higher", "lower"), default="higher")
    dec.add_argument("--target-ceiling", type=float)
    dec.add_argument("--guardrails-json")
    dec.add_argument("--memorization-verdict", choices=("clean", "explained", "memorized"), default="clean")
    dec.add_argument(
        "--lever-layer",
        choices=("retrieval", "tool_io", "preprocessing", "control_flow", "postprocessing", "model_params", "prompt"),
    )
    dec.set_defaults(func=cmd_decide)

    pc = sub.add_parser("pool-confirm")
    pc.add_argument("--baseline-results", nargs="+", required=True)
    pc.add_argument("--best-results", nargs="+", required=True)
    pc.add_argument("--min-delta", type=float, required=True)
    pc.add_argument("--target", choices=("score", "cost_usd", "latency_s"), default="score")
    pc.add_argument("--direction", choices=("higher", "lower"), default="higher")
    pc.add_argument("--prices-json")
    pc.add_argument("--hold-score-min-delta", type=float)
    pc.add_argument("--dataset-id")
    pc.set_defaults(func=cmd_pool_confirm)

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
