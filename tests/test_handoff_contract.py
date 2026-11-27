"""Independent, test-first synthetic handoff contract and boundary controls.

Expected labels are hand supplied. These tests do not generate an optimizer
oracle or describe measurements of practitioner work.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "handoff_bench"
FIXTURES = BENCH / "inputs" / "handoff"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class HandoffContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(BENCH))
        sys.path.insert(0, str(BENCH / "vendor"))
        cls.handoff = importlib.import_module("handoff")
        cls.labels = read_json(FIXTURES / "expected.json")

    def assert_subset(self, actual, expected, path="result"):
        if isinstance(expected, dict):
            self.assertIsInstance(actual, dict, path)
            for key, value in expected.items():
                self.assertIn(key, actual, path + "." + key)
                self.assert_subset(actual[key], value, path + "." + key)
        else:
            self.assertEqual(actual, expected, path)

    def fixture(self, index):
        return FIXTURES / ("handoff-%03d.json" % index)

    def copied_fixture(self, index, directory):
        target = Path(directory) / "input"
        shutil.copytree(FIXTURES, target)
        path = target / ("handoff-%03d.json" % index)
        return path, read_json(path)

    def change_artifact(self, path, case, artifact_id, data):
        artifact = next(x for x in case["raw_artifacts"] if x["id"] == artifact_id)
        (path.parent / artifact["path"]).write_bytes(data)
        artifact["bytes"] = len(data)
        artifact["sha256"] = hashlib.sha256(data).hexdigest()
        write_json(path, case)

    def evaluate_case(self, index):
        return self.handoff.evaluate_handoff(self.fixture(index))

    def test_independent_fixture_freeze(self):
        freeze = read_json(FIXTURES / "freeze.json")
        self.assertEqual(freeze["case_count"], 5)
        for entry in freeze["files"]:
            with self.subTest(path=entry["path"]):
                data = (FIXTURES / entry["path"]).read_bytes()
                self.assertEqual(len(data), entry["bytes"])
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])

    def test_five_frozen_semantic_expectations(self):
        for row in self.labels["cases"]:
            with self.subTest(case=row["case_id"]):
                actual = self.handoff.evaluate_handoff(FIXTURES / row["fixture"])
                self.assert_subset(actual, row["expected"])

    def test_models_only_copy_supplied_histories_and_questions(self):
        for index in range(1, 6):
            with self.subTest(case=index):
                source = read_json(self.fixture(index))
                plan = self.evaluate_case(index)["plan"]
                supplied = {w["id"]: {k: w[k] for k in ("id", "decision", "answers")}
                            for w in source["reviewer_decisions"]}
                questions = [{k: q[k] for k in ("id", "label", "cost", "available")}
                             for q in source["questions"]]
                for key in ("model_input", "manual_model_input"):
                    batch = plan[key]
                    if batch is None:
                        continue
                    self.assertEqual(set(batch), {"schema", "cases"})
                    self.assertEqual(len(batch["cases"]), 1)
                    case = batch["cases"][0]
                    self.assertEqual(set(case), {"schema", "case_id", "worlds", "questions", "max_cost"})
                    self.assertEqual(case["case_id"], source["case_id"])
                    self.assertEqual(case["max_cost"], source["budget"])
                    self.assertEqual(case["questions"], questions)
                    for world in case["worlds"]:
                        self.assertEqual(world, supplied[world["id"]])

    def test_native_manifest_does_not_claim_vendor_decoding(self):
        extraction = self.evaluate_case(3)["plan"]["extraction"]
        self.assertEqual(extraction["route_kind"], "reviewed_native_manifest")
        self.assertTrue(extraction["native_package_declared"])
        self.assertFalse(extraction["native_package_decoded"])
        self.assertFalse(extraction["vendor_format_verified"])

    def test_stl_only_manifest_stays_scan_only(self):
        extraction = self.evaluate_case(4)["plan"]["extraction"]
        self.assertEqual(extraction["route_kind"], "scan_only")
        self.assertFalse(extraction["native_package_declared"])
        self.assertFalse(extraction["native_package_decoded"])
        self.assertFalse(extraction["vendor_format_verified"])

    def replace_native_observation(self, path, case, quote):
        manifest_ref = next(x for x in case["raw_artifacts"] if x["id"] == "native_manifest")
        manifest = read_json(path.parent / manifest_ref["path"])
        self.change_artifact(path, case, "review_note", (quote + "\n").encode())
        manifest["observations"][0].update(start=0, end=len(quote), quote=quote)
        self.change_artifact(path, case, "native_manifest",
                             (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode())

    def test_native_contradictory_quote_cannot_resolve_presence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, case = self.copied_fixture(3, tmp)
            self.replace_native_observation(path, case,
                "Synthetic reviewer observation: order_or_case content is absent.")
            with self.assertRaises(self.handoff.InvalidHandoff):
                self.handoff.evaluate_handoff(path)

    def test_native_structured_presence_record_is_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, case = self.copied_fixture(3, tmp)
            quote = json.dumps({"category": "order_or_case", "value": "present"},
                               sort_keys=True, separators=(",", ":"))
            self.replace_native_observation(path, case, quote)
            plan = self.handoff.evaluate_handoff(path)["plan"]
            self.assert_subset(plan["extraction"]["fields"]["order_or_case"],
                               {"status": "KNOWN", "value": "present"})
            self.assertEqual(plan["comparison"]["export_reconciliation"]["status"], "RESOLVED")
            self.assertEqual(plan["comparison"]["export_reconciliation"]["candidate_ids"], ["positive"])

    def test_plans_do_not_become_observed_followups(self):
        for index in (2, 4):
            plan = self.evaluate_case(index)["plan"]
            self.assertEqual(plan["planner_result"]["status"], "QUESTIONS_FOR_REVIEW")
            for name in ("export_reconciliation", "export_plus_planner"):
                outcome = plan["comparison"][name]
                self.assertEqual(outcome["status"], "UNRESOLVED")
                self.assertEqual(outcome["unresolved_count"], 1)
                self.assertEqual(outcome["recorded_questions_asked"], 0)
                self.assertEqual(outcome["synthetic_questions_asked"], 0)

    def test_ledger_preserves_unknown_human_work_and_reconciles(self):
        for index in range(1, 6):
            with self.subTest(case=index):
                source = read_json(self.fixture(index))
                burden = self.evaluate_case(index)["burden"]
                self.assertEqual(burden["case_id"], source["case_id"])
                events = burden["events"]
                by_id = {e["id"]: e for e in events}
                self.assertEqual(len(by_id), len(events))
                for event in source["burden_events"]:
                    self.assertEqual(by_id[event["id"]], event)
                for event in events:
                    self.assertIn(event["stage"], {"acquisition", "interpretation", "model_preparation", "follow_up"})
                    self.assertIn(event["actor"], {"human", "adapter"})
                    self.assertIn(event["measurement"], {"observed", "reported", "synthetic", "unknown"})
                    self.assertIs(type(event["step_count"]), int)
                    self.assertGreaterEqual(event["step_count"], 0)
                    if event["wall_time_ns"] is not None:
                        self.assertIs(type(event["wall_time_ns"]), int)
                        self.assertGreaterEqual(event["wall_time_ns"], 0)
                totals = burden["totals"]
                self.assertEqual(totals["known_wall_time_ns"], sum(e["wall_time_ns"] for e in events if e["wall_time_ns"] is not None))
                self.assertEqual(totals["step_count"], sum(e["step_count"] for e in events))
                self.assertEqual(totals["unknown_event_count"], sum(e["wall_time_ns"] is None for e in events))
                self.assertFalse(totals["complete"])
                self.assertIsNone(totals["wall_time_ns"])
                self.assertFalse(totals["observed_complete"])
                self.assertEqual(totals["measurement_class"], "incomplete")
                self.assertEqual(totals["adapter_measured_wall_time_ns"], sum(e["wall_time_ns"] for e in events if e["actor"] == "adapter" and e["measurement"] == "observed"))
                self.assertEqual(totals["observed_human_wall_time_ns"], 0)
                self.assertEqual(totals["reported_wall_time_ns"], 0)
                self.assertEqual(totals["synthetic_wall_time_ns"], 0)

    def test_synthetic_burden_is_separate_from_optimizer_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, case = self.copied_fixture(2, tmp)
            for event, duration in zip(case["burden_events"], (2_000_000_000, 3_000_000_000, 7_000_000_000)):
                event["measurement"] = "synthetic"
                event["wall_time_ns"] = duration
            write_json(path, case)
            actual = self.handoff.evaluate_handoff(path)
            self.assertEqual(actual["plan"]["planner_result"], self.evaluate_case(2)["plan"]["planner_result"])
            totals = actual["burden"]["totals"]
            self.assertEqual(totals["synthetic_wall_time_ns"], 12_000_000_000)
            self.assertTrue(totals["complete"])
            self.assertFalse(totals["observed_complete"])
            self.assertEqual(totals["measurement_class"], "mixed_declared")
            self.assertEqual(totals["wall_time_ns"], totals["known_wall_time_ns"])

    def test_route_allocations_count_each_event_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, case = self.copied_fixture(2, tmp)
            case["burden_events"].append({"id": "export_task", "stage": "interpretation",
                "actor": "human", "measurement": "synthetic", "wall_time_ns": 9_000_000_000,
                "step_count": 2, "source": "Synthetic route accounting control.",
                "routes": ["export_reconciliation", "export_plus_planner"]})
            write_json(path, case)
            burden = self.handoff.evaluate_handoff(path)["burden"]
            self.assertEqual(burden["totals"]["synthetic_wall_time_ns"], 9_000_000_000)
            self.assertEqual(burden["routes"]["manual_model"]["synthetic_wall_time_ns"], 0)
            for route in ("export_reconciliation", "export_plus_planner"):
                totals = burden["routes"][route]
                self.assertEqual(totals["synthetic_wall_time_ns"], 9_000_000_000)
                self.assertFalse(totals["complete"])
                self.assertIsNone(totals["wall_time_ns"])

    def add_synthetic_followup(self, path, case):
        answer = {"question_id": "receipt", "answer": "Pending"}
        quote = json.dumps(answer, sort_keys=True, separators=(",", ":"))
        raw = (quote + "\n").encode()
        relative = "artifacts/recorded-answer.json"
        (path.parent / relative).write_bytes(raw)
        case["raw_artifacts"].append({"id": "answer_record", "path": relative,
            "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "kind": "reviewer_note", "provenance": "synthetic", "selection": {}})
        case["burden_events"].append({"id": "answer_event", "stage": "follow_up", "actor": "human",
            "measurement": "synthetic", "wall_time_ns": 2_000_000_000, "step_count": 1,
            "source": "Synthetic supplied answer record; no real external contact.",
            "routes": ["export_plus_planner"]})
        case["follow_up"] = [{"question_id": "receipt", "answer": "Pending",
            "artifact_id": "answer_record", "start": 0, "end": len(quote),
            "quote": quote, "event_id": "answer_event"}]
        write_json(path, case)

    def test_supplied_synthetic_followup_is_counted_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, case = self.copied_fixture(2, tmp)
            self.add_synthetic_followup(path, case)
            plan = self.handoff.evaluate_handoff(path)["plan"]
            before = plan["comparison"]["export_reconciliation"]
            self.assertEqual(before["status"], "UNRESOLVED")
            self.assertEqual(before["recorded_questions_asked"], 0)
            self.assertEqual(before["synthetic_questions_asked"], 0)
            after = plan["comparison"]["export_plus_planner"]
            self.assertEqual(after["status"], "RESOLVED")
            self.assertEqual(after["decision"], "REQUEST_REVIEW")
            self.assertEqual(after["candidate_ids"], ["negative"])
            self.assertEqual(after["recorded_questions_asked"], 0)
            self.assertEqual(after["synthetic_questions_asked"], 1)

    def test_followup_answer_must_match_quoted_structured_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, case = self.copied_fixture(2, tmp)
            self.add_synthetic_followup(path, case)
            case["follow_up"][0]["answer"] = "Completed"
            write_json(path, case)
            with self.assertRaises(self.handoff.InvalidHandoff):
                self.handoff.evaluate_handoff(path)

    def test_missing_human_burden_stays_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, case = self.copied_fixture(1, tmp)
            case["burden_events"] = []
            write_json(path, case)
            burden = self.handoff.evaluate_handoff(path)["burden"]
            unknown = [e for e in burden["events"] if e["actor"] == "human" and e["measurement"] == "unknown"]
            self.assertEqual({e["stage"] for e in unknown}, {"acquisition", "interpretation", "model_preparation"})
            self.assertTrue(all(e["wall_time_ns"] is None and e["step_count"] == 0 for e in unknown))
            self.assertFalse(burden["totals"]["complete"])

    def test_empty_csv_cell_stays_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, case = self.copied_fixture(1, tmp)
            self.change_artifact(path, case, "case_csv", b"Case Name,Status\nSYNTHETIC-H01,\n")
            plan = self.handoff.evaluate_handoff(path)["plan"]
            self.assert_subset(plan["extraction"]["fields"]["status"], {"status": "UNKNOWN", "value": None})
            self.assertEqual(len(plan["model_input"]["cases"][0]["worlds"]), 2)

    def test_literal_unknown_is_known_and_zero_survivors_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, case = self.copied_fixture(1, tmp)
            self.change_artifact(path, case, "case_csv", b"Case Name,Status\nSYNTHETIC-H01,UNKNOWN\n")
            plan = self.handoff.evaluate_handoff(path)["plan"]
            self.assert_subset(plan["extraction"]["fields"]["status"], {"status": "KNOWN", "value": "UNKNOWN"})
            self.assertIsNone(plan["model_input"])
            self.assertIsNone(plan["planner_result"])
            outcome = plan["comparison"]["export_reconciliation"]
            self.assertEqual(outcome["status"], "CONFLICT")
            self.assertEqual(outcome["candidate_ids"], [])
            self.assertEqual(outcome["unresolved_count"], 1)

    def test_no_condition_for_known_field_does_not_rule_out_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, case = self.copied_fixture(1, tmp)
            case["reviewer_decisions"][1]["conditions"] = {}
            write_json(path, case)
            plan = self.handoff.evaluate_handoff(path)["plan"]
            self.assertEqual(len(plan["model_input"]["cases"][0]["worlds"]), 2)
            self.assertEqual(plan["comparison"]["export_reconciliation"]["status"], "UNRESOLVED")

    def test_conflicting_rows_do_not_have_an_order_winner(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, case = self.copied_fixture(5, tmp)
            self.change_artifact(path, case, "case_csv", b"Case Name,Status\nSYNTHETIC-H05,Pending\nSYNTHETIC-H05,Completed\n")
            plan = self.handoff.evaluate_handoff(path)["plan"]
            self.assertEqual(plan["extraction"]["fields"]["status"]["status"], "CONFLICT")
            self.assertIsNone(plan["planner_result"])
            self.assertEqual(plan["comparison"]["export_reconciliation"]["status"], "CONFLICT")

    def test_changed_artifact_hash_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, case = self.copied_fixture(1, tmp)
            artifact = case["raw_artifacts"][0]
            (path.parent / artifact["path"]).write_bytes(b"changed")
            with self.assertRaises(self.handoff.InvalidHandoff):
                self.handoff.evaluate_handoff(path)

    def test_changed_artifact_size_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, case = self.copied_fixture(1, tmp)
            case["raw_artifacts"][0]["bytes"] += 1
            write_json(path, case)
            with self.assertRaises(self.handoff.InvalidHandoff):
                self.handoff.evaluate_handoff(path)

    def test_unsafe_artifact_path_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, case = self.copied_fixture(1, tmp)
            case["raw_artifacts"][0]["path"] = "../outside.csv"
            write_json(path, case)
            with self.assertRaises(self.handoff.InvalidHandoff):
                self.handoff.evaluate_handoff(path)

    def test_artifact_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, case = self.copied_fixture(1, tmp)
            artifact = case["raw_artifacts"][0]
            original = path.parent / artifact["path"]
            target = original.with_name("target.csv")
            original.rename(target)
            original.symlink_to(target.name)
            with self.assertRaises(self.handoff.InvalidHandoff):
                self.handoff.evaluate_handoff(path)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX input-file boundary")
    def test_nonregular_handoff_input_is_refused_before_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "handoff.fifo"
            os.mkfifo(path)
            # An actual FIFO open could hang. Interception makes the pre-open
            # requirement observable without waiting on external writers.
            with mock.patch.object(Path, "open", side_effect=AssertionError("nonregular input was opened")):
                with self.assertRaises(self.handoff.InvalidHandoff):
                    self.handoff.evaluate_handoff(path)

    def test_duplicate_csv_header_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, case = self.copied_fixture(1, tmp)
            self.change_artifact(path, case, "case_csv", b"Case Name,Status,Status\nSYNTHETIC-H01,Completed,Pending\n")
            with self.assertRaises(self.handoff.InvalidHandoff):
                self.handoff.evaluate_handoff(path)

    def test_unknown_top_level_field_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, case = self.copied_fixture(1, tmp)
            case["invent_history"] = True
            write_json(path, case)
            with self.assertRaises(self.handoff.InvalidHandoff):
                self.handoff.evaluate_handoff(path)

    def test_duplicate_question_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, case = self.copied_fixture(1, tmp)
            case["questions"].append(copy.deepcopy(case["questions"][0]))
            write_json(path, case)
            with self.assertRaises(self.handoff.InvalidHandoff):
                self.handoff.evaluate_handoff(path)

    def test_boolean_burden_duration_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, case = self.copied_fixture(1, tmp)
            case["burden_events"][0].update(measurement="reported", wall_time_ns=True)
            write_json(path, case)
            with self.assertRaises(self.handoff.InvalidHandoff):
                self.handoff.evaluate_handoff(path)

    def test_manual_route_uses_the_same_unmodified_declared_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, case = self.copied_fixture(1, tmp)
            case["source"] = "manual"
            case["raw_artifacts"] = []
            write_json(path, case)
            plan = self.handoff.evaluate_handoff(path)["plan"]
            self.assertEqual(plan["model_input"], plan["manual_model_input"])
            self.assertEqual(plan["planner_result"], plan["manual_planner_result"])
            self.assertEqual(plan["comparison"]["export_reconciliation"]["status"], "UNRESOLVED")

    def test_export_only_burden_excludes_planner_runtime(self):
        # Only solver calls advance this clock. The test therefore attributes a
        # known cost to unchanged solver execution without depending on machine
        # speed or interpreting unmeasured human work as zero.
        original_solve = self.handoff.core.solve
        solver_ns = 7_000_000
        for index in range(1, 6):
            with self.subTest(case=index):
                now = [0]

                def timed_solve(case):
                    result = original_solve(case)
                    now[0] += solver_ns
                    return result

                with mock.patch.object(self.handoff.time, "perf_counter_ns", side_effect=lambda: now[0]):
                    with mock.patch.object(self.handoff.core, "solve", side_effect=timed_solve) as solve:
                        burden = self.evaluate_case(index)["burden"]
                events = {event["id"]: event for event in burden["events"]}
                routes = burden["routes"]
                self.assertEqual(routes["export_reconciliation"]["adapter_measured_wall_time_ns"], 0)
                self.assertEqual(events["runtime_export_model"]["wall_time_ns"], 0)
                self.assertEqual(set(events["runtime_export_model"]["routes"]),
                                 {"export_reconciliation", "export_plus_planner"})
                self.assertEqual(routes["manual_model"]["adapter_measured_wall_time_ns"], solver_ns)
                if index == 5:
                    self.assertEqual(solve.call_count, 1)
                    self.assertNotIn("runtime_export_plan", events)
                    self.assertEqual(routes["export_plus_planner"]["adapter_measured_wall_time_ns"], 0)
                else:
                    self.assertEqual(solve.call_count, 2)
                    self.assertEqual(events["runtime_export_plan"]["wall_time_ns"], solver_ns)
                    self.assertEqual(events["runtime_export_plan"]["routes"], ["export_plus_planner"])
                    self.assertEqual(routes["export_plus_planner"]["adapter_measured_wall_time_ns"], solver_ns)
                self.assertEqual(burden["totals"]["adapter_measured_wall_time_ns"], solver_ns * solve.call_count)


if __name__ == "__main__":
    unittest.main()
