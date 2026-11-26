import re
import unittest

from examples.handoff_bench.report import augment_report


class HandoffReportTests(unittest.TestCase):
    def cells(self, rendered, label):
        row = re.search(r'<tr><th scope="row">' + re.escape(label) + r'</th>(.*?)</tr>', rendered, re.S)
        self.assertIsNotNone(row, label)
        return re.findall(r'<td>(.*?)</td>', row.group(1), re.S)

    def sample(self):
        return {
            "schema": 1,
            "status": "PASS",
            "legacy": {"exact_cases": 24, "total_cases": 24, "fields_per_case": 8},
            "cases": [{
                "id": "H01",
                "plan": {},
                "burden": {},
                "checks": {},
                "execution": {"elapsed_seconds": 0.01, "usage": {}},
            }],
            "product_sha256": "a" * 64,
            "freeze_sha256": "b" * 64,
            "failures": [],
            "limitations": [],
        }

    def test_static_augmentation_preserves_existing_report(self):
        original = "<html><body><main><h1>Existing report</h1></main></body></html>"
        rendered = augment_report(original, self.sample())
        self.assertIn("<h1>Existing report</h1>", rendered)
        self.assertEqual(rendered.count('id="handoff-bench"'), 1)
        self.assertIn("Manual model", rendered)
        self.assertIn("Export reconciliation", rendered)
        self.assertIn("Export plus planner", rendered)
        self.assertNotIn("<script", rendered)
        self.assertNotIn("<style", rendered)
        self.assertLess(rendered.index('id="handoff-bench"'), rendered.index("</main>"))

    def test_repeated_augmentation_replaces_only_its_section(self):
        original = "<html><body>Retained text</body></html>"
        once = augment_report(original, self.sample())
        twice = augment_report(once, self.sample())
        self.assertEqual(once, twice)
        self.assertIn("Retained text", twice)

    def test_evidence_is_escaped(self):
        data = self.sample()
        data["cases"][0]["id"] = '<img src=x onerror="alert(1)">'
        data["limitations"] = ["<script>untrusted()</script>"]
        rendered = augment_report("<body>Existing</body>", data)
        self.assertNotIn("<img", rendered)
        self.assertNotIn("<script", rendered)
        self.assertIn("&lt;img", rendered)
        self.assertIn("&lt;script&gt;untrusted()", rendered)

    def test_absent_timing_and_scoring_remain_unknown(self):
        rendered = augment_report("<body></body>", self.sample())
        self.assertIn("UNKNOWN", rendered)
        self.assertIn("Incomplete", rendered)
        self.assertIn("Independently scored correctness", rendered)
        self.assertNotIn("0.000000 s total", rendered)

    def test_incomplete_total_cannot_be_presented_as_measured_savings(self):
        data = self.sample()
        data["cases"][0]["burden"] = {
            "totals": {"wall_time_ns": 1_000_000_000, "complete": False}
        }
        rendered = augment_report("<body></body>", data)
        self.assertIn("Incomplete", rendered)
        self.assertNotIn("1.000000 s total", rendered)
        self.assertNotIn("time saved", rendered.lower())

    def test_route_allocations_keep_incomplete_total_unknown(self):
        data = self.sample()
        data["cases"][0]["burden"] = {
            "routes": {"export_plus_planner": {
                "known_wall_time_ns": 2_000_000_000,
                "adapter_measured_wall_time_ns": 2_000_000_000,
                "wall_time_ns": 9_000_000_000,
                "complete": False,
            }}
        }
        rendered = augment_report("<body></body>", data)
        self.assertIn("known subtotal 2.000000 s", rendered)
        self.assertNotIn("9.000000 s", rendered)
        self.assertIn("accounting allocations", rendered)

    def test_quality_requires_separate_truth_available_score(self):
        data = self.sample()
        data["cases"][0]["plan"] = {"comparison": {
            "manual_model": {"status": "RESOLVED", "decision": "REVIEW", "candidate_ids": ["w1"]}
        }}
        data["cases"][0]["decision_quality"] = {
            "manual_model": {"status": "CORRECT", "truth_available": False},
            "export_reconciliation": {"status": "CORRECT", "truth_available": True,
                                      "basis": "frozen synthetic expectation"},
        }
        rendered = augment_report("<body></body>", data)
        self.assertEqual(rendered.count("CORRECT"), 1)
        self.assertIn("frozen synthetic expectation", rendered)
        quality = self.cells(rendered, "Independently scored correctness")
        self.assertEqual(quality[0], "UNKNOWN")
        self.assertTrue(quality[1].startswith("CORRECT"))

    def test_recommendations_and_actual_followup_stay_separate(self):
        data = self.sample()
        data["cases"][0]["plan"] = {
            "comparison": {"export_plus_planner": {
                "status": "UNRESOLVED", "decision": None, "unresolved_count": 1,
                "candidate_ids": ["w1", "w2"], "recorded_questions_asked": 0,
                "synthetic_questions_asked": 0,
            }},
            "planner_result": {"status": "QUESTIONS_FOR_REVIEW", "minimum_question_count": 2,
                               "minimum_cost": 3},
        }
        rendered = augment_report("<body></body>", data)
        self.assertIn("Planned follow-up questions", rendered)
        self.assertIn("Recorded questions actually asked", rendered)
        self.assertIn("Synthetic questions simulated", rendered)
        self.assertIn("UNRESOLVED", rendered)
        self.assertEqual(self.cells(rendered, "Planned follow-up questions")[2], "2")
        self.assertEqual(self.cells(rendered, "Recorded questions actually asked")[2], "0")
        self.assertEqual(self.cells(rendered, "Synthetic questions simulated")[2], "0")
        self.assertEqual(self.cells(rendered, "Resolved administrative decision")[2], "UNKNOWN")
        self.assertEqual(self.cells(rendered, "Unresolved cases")[2], "1")

    def test_reported_and_synthetic_times_not_labeled_measured_total(self):
        data = self.sample()
        data["cases"][0]["burden"] = {"totals": {
            "wall_time_ns": 3_000_000_000, "complete": True,
            "observed_complete": False, "measurement_class": "mixed_declared",
            "adapter_measured_wall_time_ns": 1_000_000_000,
            "reported_wall_time_ns": 500_000_000,
            "synthetic_wall_time_ns": 1_500_000_000,
        }}
        rendered = augment_report("<body></body>", data)
        self.assertIn("including declared or synthetic durations: 3.000000 s", rendered)
        self.assertNotIn("Measured pipeline total:", rendered)
        self.assertEqual(self.cells(rendered, "Measured adapter duration"), ["1.000000 s"])
        self.assertEqual(self.cells(rendered, "Reported duration"), ["0.500000 s"])
        self.assertEqual(self.cells(rendered, "Synthetic duration"), ["1.500000 s"])

    def test_unpaired_marker_refused(self):
        with self.assertRaises(ValueError):
            augment_report("<body><!-- FORGE_HANDOFF_BENCH_START --></body>", self.sample())

    def test_oversized_existing_report_refused(self):
        with self.assertRaises(ValueError):
            augment_report("x" * 10_000_001, self.sample())

    def test_non_scalar_evidence_refused(self):
        data = self.sample()
        data["cases"][0]["id"] = "bad\ud800"
        with self.assertRaises(ValueError):
            augment_report("<body></body>", data)


if __name__ == "__main__":
    unittest.main()

