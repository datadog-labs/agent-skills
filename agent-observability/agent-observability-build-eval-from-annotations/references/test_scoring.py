#!/usr/bin/env python3
"""Regression tests for scoring.py — run: python3 -m unittest test_scoring -v

Every test here is a bug that shipped once. The numbers this file guards are the ones the loop
makes keep/discard decisions on, so a silent regression here is a silent wrong decision.
Stdlib only, no fixtures on disk beyond a tmpdir.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import scoring


def write_jsonl(path: Path, records):
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


class LabelIdentity(unittest.TestCase):
    def test_multiselect_order_is_not_a_difference(self):
        self.assertEqual(scoring.key(["b", "a"]), scoring.key(["a", "b"]))

    def test_majority_groups_by_canonical_key(self):
        # two passes that answered the same multi-select in different orders are one answer,
        # not a 1-1 tie
        vote = scoring.majority([["a", "b"], ["b", "a"], ["c"]])
        self.assertEqual(scoring.key(vote), "a+b")


class FlipRate(unittest.TestCase):
    def test_reordered_multiselect_is_not_a_flip(self):
        with tempfile.TemporaryDirectory() as tmp:
            pred = Path(tmp) / "p.jsonl"
            write_jsonl(pred, [
                {"id": "r1", "run": 0, "label": ["a", "b"], "confidence": 90},
                {"id": "r1", "run": 1, "label": ["b", "a"], "confidence": 90},
                {"id": "r1", "run": 2, "label": ["a", "b"], "confidence": 90},
            ])
            preds = scoring.load_predictions(str(pred))
            self.assertFalse(preds["r1"]["flipped"], "list order must not count as instability")

    def test_a_real_disagreement_is_a_flip(self):
        with tempfile.TemporaryDirectory() as tmp:
            pred = Path(tmp) / "p.jsonl"
            write_jsonl(pred, [
                {"id": "r1", "run": 0, "label": True},
                {"id": "r1", "run": 1, "label": False},
                {"id": "r1", "run": 2, "label": True},
            ])
            self.assertTrue(scoring.load_predictions(str(pred))["r1"]["flipped"])


class MetricDirection(unittest.TestCase):
    def test_mae_constant_baseline_takes_the_best_constant_not_the_worst(self):
        # truths 1,1,1,10 -> constant 1 gives MAE 2.25, constant 10 gives 6.75.
        # The laziest judge scores 2.25; a max() here would advertise 6.75 as the bar to clear.
        pairs = [(1, 1), (1, 1), (1, 1), (10, 1)]
        self.assertEqual(scoring.constant_baseline(pairs, "mae"), 2.25)

    def test_accuracy_constant_baseline_still_takes_the_max(self):
        pairs = [(True, True)] * 9 + [(False, True)]
        self.assertEqual(scoring.constant_baseline(pairs, "accuracy"), 0.9)


class Metrics(unittest.TestCase):
    def test_spearman_is_implemented_and_handles_ties(self):
        self.assertAlmostEqual(scoring.score([(1, 1), (2, 2), (3, 3)], "spearman"), 1.0)
        self.assertAlmostEqual(scoring.score([(1, 3), (2, 2), (3, 1)], "spearman"), -1.0)
        self.assertEqual(scoring.score([(1, 5), (2, 5), (3, 5)], "spearman"), 0.0)

    def test_weighted_f1_weights_by_support(self):
        pairs = [("a", "a")] * 8 + [("b", "a")] * 2
        weighted = scoring.score(pairs, "weighted_f1")
        macro = scoring.score(pairs, "macro_f1")
        self.assertGreater(weighted, macro, "support weighting must favour the majority class")

    def test_fbeta_minority_beta_shifts_the_tradeoff(self):
        # judge over-predicts the minority class: high recall, poor precision
        pairs = [("f", "f"), ("f", "f"), ("t", "f"), ("t", "f"), ("t", "t"), ("t", "t")]
        recall_weighted = scoring.score(pairs, "fbeta_minority", beta=2.0)
        precision_weighted = scoring.score(pairs, "fbeta_minority", beta=0.5)
        self.assertGreater(recall_weighted, precision_weighted)

    def test_every_documented_metric_is_implemented(self):
        pairs = [(True, True), (False, False), (True, False)]
        for metric in scoring.METRICS:
            with self.subTest(metric=metric):
                scoring.score(pairs, metric)  # must not raise SystemExit


class UsablePasses(unittest.TestCase):
    def test_one_usable_pass_of_three_is_not_a_majority(self):
        with tempfile.TemporaryDirectory() as tmp:
            pred = Path(tmp) / "p.jsonl"
            write_jsonl(pred, [
                {"id": "r1", "run": 0, "label": True},
                {"id": "r1", "run": 1, "unparseable": True},
                {"id": "r1", "run": 2, "unparseable": True},
            ])
            preds = scoring.load_predictions(str(pred), min_usable_passes=2)
            self.assertFalse(preds["r1"]["usable"])
            self.assertTrue(preds["r1"]["insufficient_usable_passes"])

    def test_two_usable_passes_that_disagree_are_a_tie_not_a_vote(self):
        with tempfile.TemporaryDirectory() as tmp:
            pred = Path(tmp) / "p.jsonl"
            write_jsonl(pred, [
                {"id": "r1", "run": 0, "label": True},
                {"id": "r1", "run": 1, "label": False},
                {"id": "r1", "run": 2, "unparseable": True},
            ])
            preds = scoring.load_predictions(str(pred), min_usable_passes=2)
            self.assertTrue(preds["r1"]["tie"])
            self.assertFalse(preds["r1"]["usable"])


class JointVerdicts(unittest.TestCase):
    def test_baseline_is_loaded_with_the_same_label_field(self):
        # The bug: the baseline was loaded WITHOUT --label-field, so a joint verdict's whole
        # object was compared against one label of the candidate. Every row then looked gained.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_jsonl(tmp / "base.jsonl", [
                {"id": "r1", "run": r, "label": {"L1": True, "L2": "x"}} for r in range(3)
            ])
            base_typed = scoring.load_predictions(str(tmp / "base.jsonl"), "L1")
            base_untyped = scoring.load_predictions(str(tmp / "base.jsonl"))
            self.assertEqual(base_typed["r1"]["vote"], True)
            self.assertIsInstance(base_untyped["r1"]["vote"], dict)
            # comparing the two is exactly the corruption the fix removes
            self.assertNotEqual(scoring.key(base_typed["r1"]["vote"]),
                                scoring.key(base_untyped["r1"]["vote"]))


class Uncertainty(unittest.TestCase):
    def test_bootstrap_ci_is_deterministic_and_brackets_the_point_estimate(self):
        pairs = [(True, True)] * 9 + [(False, True)]
        first = scoring.bootstrap_ci(pairs, "accuracy", resamples=500)
        second = scoring.bootstrap_ci(pairs, "accuracy", resamples=500)
        self.assertEqual(first, second, "a fixed seed must make the CI reproducible")
        self.assertLessEqual(first[0], 0.9)
        self.assertGreaterEqual(first[1], 0.9)

    def test_bootstrap_ci_works_where_wilson_cannot(self):
        pairs = [("a", "a"), ("a", "b"), ("b", "b"), ("b", "a"), ("c", "c")]
        self.assertIsNotNone(scoring.bootstrap_ci(pairs, "macro_f1", resamples=200))

    def test_paired_bootstrap_reports_the_headline_delta(self):
        # candidate fixes two rows the baseline got wrong, on the same rows
        triples = [(True, True, True)] * 6 + [(False, False, True), (False, False, True)]
        out = scoring.paired_bootstrap_delta(triples, "accuracy", resamples=500)
        self.assertGreater(out["delta"], 0)
        self.assertIn("accuracy", out["tests"])

    def test_per_class_recall_ci_shows_how_wide_six_rows_is(self):
        pairs = [("rare", "rare")] * 6
        out = scoring.per_class_recall_ci(pairs)
        self.assertEqual(out["rare"]["recall"], 1.0)
        self.assertLess(out["rare"]["wilson_95"][0], 0.7,
                        "6/6 must not read as a settled 100%")


class Deployable(unittest.TestCase):
    def test_single_pass_mean_is_below_the_majority_vote_when_the_judge_flips(self):
        # one row is decided 2-1 by the vote; a single call gets it right two times in three.
        # The published evaluator makes one call, so the vote score overstates what ships.
        with tempfile.TemporaryDirectory() as tmp:
            pred = Path(tmp) / "p.jsonl"
            write_jsonl(pred, [
                {"id": "r1", "run": 0, "label": True},
                {"id": "r1", "run": 1, "label": True},
                {"id": "r1", "run": 2, "label": False},
            ])
            by_run, _, _, _ = scoring.load_passes(str(pred))
            preds = scoring.load_predictions(str(pred), min_usable_passes=2)
            truth = {"r1": True}
            vote_score = scoring.score([(True, preds["r1"]["vote"])], "accuracy")
            deployable = scoring.single_pass_scores(by_run, truth, "accuracy")
            self.assertEqual(vote_score, 1.0)
            self.assertAlmostEqual(deployable["mean"], 2 / 3, places=3)
            self.assertLess(deployable["mean"], vote_score)


if __name__ == "__main__":
    unittest.main()
