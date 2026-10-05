"""Offline regressions for product routing and run-scoped resolver traces."""

import copy
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[2]
RESOLVE = ROOT / "dd-orchestrator" / "scripts" / "resolve.py"
SPEC = importlib.util.spec_from_file_location("orchestrator_resolve", RESOLVE)
resolve = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resolve)


class ProductRoutingTests(unittest.TestCase):
    def test_every_detected_alias_round_trips_without_catalog_support(self):
        for alias, token in resolve.PRODUCT_TOKENS.items():
            with self.subTest(alias=alias, token=token):
                self.assertEqual(resolve.detect_products(alias), [token])
                self.assertEqual(resolve.normalize_product(token), token)
                self.assertEqual(resolve.normalize_product(f"  {token.upper()}  "), token)

    def test_named_product_without_setup_is_a_coverage_gap(self):
        products = resolve.detect_products("Set up Cloud Cost Management")
        self.assertEqual(products, ["cost"])
        result = resolve.Router({"nodes": []}).resolve(
            products, {"platform": "none", "cloud": "none"}
        )
        self.assertEqual(result["plan"], [])
        self.assertEqual(result["choices"], [])
        self.assertTrue(any("no setup skill yet" in reason for reason in result["dead_ends"]))
        self.assertFalse(any("unrecognized product" in reason for reason in result["dead_ends"]))

    def test_catalog_only_token_still_requires_catalog_support(self):
        catalog = resolve.load_catalog()
        account = copy.deepcopy(next(n for n in catalog["nodes"] if n["id"] == "dd-account-setup"))
        widget = {
            **copy.deepcopy(account),
            "key": "example-widget-enable",
            "id": "example-widget-enable",
            "kind": "product-enable",
            "product": "example-widget",
            "action": "enable",
            "requires": [{"category": "foundation"}],
        }
        router = resolve.Router({"nodes": [account, widget]})
        context = {"platform": "none", "cloud": "none"}
        result = router.resolve(["example-widget"], context)
        self.assertEqual([n["id"] for n in result["plan"]],
                         ["dd-account-setup", "example-widget-enable"])
        router.product_tokens = frozenset()
        result = router.resolve(["example-widget"], context)
        self.assertEqual(result["plan"], [])
        self.assertTrue(any("unrecognized product" in reason for reason in result["dead_ends"]))

    def test_unknown_product_is_not_treated_as_known(self):
        self.assertIsNone(resolve.normalize_product("unknown-example-product"))
        result = resolve.Router(resolve.load_catalog()).resolve(
            ["unknown-example-product"], {"platform": "kubernetes", "cloud": "aws"}
        )
        self.assertEqual(result["plan"], [])
        self.assertTrue(any("unrecognized product" in reason for reason in result["dead_ends"]))


class ResolveTraceTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="dd-orch-test-")
        self.addCleanup(scratch.cleanup)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("DD_")}
        self.env.update({"DD_ORCH_TELEMETRY_DISABLED": "1", "TMPDIR": scratch.name})

    def run_cli(self, *args, session_id=None):
        env = dict(self.env)
        if session_id is not None:
            env["DD_ORCH_SESSION_ID"] = session_id
        return subprocess.run(
            [sys.executable, str(RESOLVE), *args],
            capture_output=True, text=True, env=env, check=True, timeout=10,
        )

    def trace(self, *, products="apm", platform="kubernetes", session_id=None):
        return self.run_cli(
            "--trace", "--intent-mode", "explicit", "--products", products,
            "--platform", platform, "--cloud", "aws", session_id=session_id,
        )

    def session_id(self, output):
        ids = [line.removeprefix("SESSION_ID: ") for line in output.splitlines()
               if line.startswith("SESSION_ID: ")]
        self.assertEqual(len(ids), 1)
        return ids[0]

    def test_minted_session_reminder_matches_trace_and_stays_on_stderr(self):
        result = self.trace()
        session_id = self.session_id(result.stdout)
        self.assertEqual(str(uuid.UUID(session_id)), session_id)
        self.assertIn(f"export DD_ORCH_SESSION_ID={session_id}", result.stderr)
        self.assertNotIn("export DD_ORCH_SESSION_ID=", result.stdout)
        self.assertTrue(result.stdout.startswith("=== DD-ORCH TRACE v1 ===\n"))
        self.assertTrue(result.stdout.endswith("=== END DD-ORCH TRACE ===\n"))

    def test_pinned_session_is_reused_without_an_export_reminder(self):
        result = self.trace(session_id="example-pinned-session")
        self.assertEqual(self.session_id(result.stdout), "example-pinned-session")
        self.assertEqual(result.stderr, "")

    def test_empty_session_setting_mints_a_session(self):
        result = self.trace(session_id="")
        session_id = self.session_id(result.stdout)
        self.assertIn(f"export DD_ORCH_SESSION_ID={session_id}", result.stderr)

    def test_resolving_a_platform_choice_keeps_the_same_session(self):
        first = self.trace(platform="none")
        self.assertIn("STOP_REASON: awaiting_choice", first.stdout)
        session_id = self.session_id(first.stdout)
        second = self.trace(session_id=session_id)
        self.assertEqual(self.session_id(second.stdout), session_id)
        self.assertEqual(second.stderr, "")
        self.assertIn("STOP_REASON: none", second.stdout)
        self.assertIn("CHOICE_POINTS: (none)", second.stdout)

    def test_known_context_composes_the_account_first_plan(self):
        result = self.trace()
        self.assertIn("CONTEXT: platform=kubernetes cloud=aws", result.stdout)
        self.assertIn("STOP_REASON: none", result.stdout)
        plan = result.stdout.split("PLAN:\n", 1)[1].split("DEAD_ENDS:", 1)[0]
        self.assertEqual([line.split()[1] for line in plan.splitlines()], [
            "dd-account-setup", "apm-agent-install-kubernetes",
            "apm-enable-kubernetes", "apm-verify-ssi-kubernetes",
        ])

    def test_missing_platform_blocks_even_a_partial_plan(self):
        result = self.trace(products="apm,rum", platform="none")
        self.assertIn("STOP_REASON: awaiting_choice", result.stdout)
        self.assertIn("dd-instrument-rum", result.stdout)
        self.assertNotIn("PLAN: (empty)", result.stdout)
        self.assertIn("CHOICE_POINTS:\n  - platform:", result.stdout)

    def test_detected_unsupported_product_reports_a_coverage_gap(self):
        detected = self.run_cli("--detect-products", "Set up Cloud Cost Management")
        self.assertEqual(detected.stdout.strip(), "cost")
        result = self.trace(products=detected.stdout.strip())
        self.assertIn("STOP_REASON: no_enabled_capability", result.stdout)
        self.assertIn("PLAN: (empty)", result.stdout)
        self.assertIn("no setup skill yet", result.stdout)
        self.assertNotIn("unrecognized product", result.stdout)

    def test_plain_output_keeps_its_session_header(self):
        result = self.run_cli("--products", "apm", "--platform", "kubernetes")
        self.assertTrue(result.stdout.startswith("SESSION ID: "))
        self.assertEqual(result.stderr, "")
        self.assertNotIn("=== DD-ORCH TRACE v1 ===", result.stdout)


if __name__ == "__main__":
    unittest.main()
