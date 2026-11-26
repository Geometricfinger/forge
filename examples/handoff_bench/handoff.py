"""Deterministic export-to-model adaptation; no scan or history inference."""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time

if __package__:
    from . import burden as ledger
    from . import planner_core as core
else:
    import burden as ledger
    import planner_core as core

MAX_ARTIFACTS = 12
MAX_ARTIFACT_BYTES = 1_048_576
MAX_TOTAL_BYTES = 4_194_304
MAX_OUTPUT_BYTES = 240_000
CORE_SHA256 = "555a3fd37e87912d164550a8e915f4fd5052d1e0e2da32b7c4402da4e381d1d5"
CSV_FIELDS = {"Status": "status", "Case Name": "case_name", "Date of Birth": "date_of_birth",
              "Form Information": "form_information", "Last Modified date": "last_modified_date",
              "Scanned Date": "scanned_date", "Order Date": "order_date", "Lab Name": "lab_name"}
NATIVE_FIELDS = {"order_or_case", "scans", "screenshots", "printable_order_forms", "patient_information"}
KINDS = {"medit_csv", "case_information_pdf", "exocad_project_manifest", "native_package_manifest",
         "opaque_native", "scan_stl", "scan_ply", "reviewer_note"}
HASH = re.compile(r"[0-9a-f]{64}\Z")
EXPORT_ROUTES = ("export_reconciliation", "export_plus_planner")


class InvalidHandoff(ValueError):
    pass


def fail(code):
    raise InvalidHandoff(code)


def exact(value, required, optional=()):
    if type(value) is not dict or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        fail("HANDOFF_FIELDS")


def integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        fail("HANDOFF_INTEGER")


def text(value, *, nonempty=False):
    try:
        core.scalar_text(value, nonempty=nonempty)
    except core.InvalidInput as exc:
        fail(str(exc))


def identifier(value):
    try:
        core.identifier(value)
    except core.InvalidInput as exc:
        fail(str(exc))


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def decode(raw):
    try:
        return core.decode(raw)
    except core.InvalidInput as exc:
        fail(str(exc))


def no_symlink(path):
    absolute = path.absolute()
    if any(part.is_symlink() for part in (absolute, *absolute.parents)):
        fail("HANDOFF_SYMLINK")
    return absolute


def safe_artifact_path(root, relative):
    if type(relative) is not str or not relative or len(relative) > 200 or "\\" in relative or ":" in relative:
        fail("HANDOFF_ARTIFACT_PATH")
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts) or PurePosixPath(relative).is_absolute():
        fail("HANDOFF_ARTIFACT_PATH")
    path = no_symlink(root.joinpath(*parts))
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        fail("HANDOFF_ARTIFACT_PATH")
    return path


def raw_state(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def read_regular(path, maximum, code):
    """Refuse devices/FIFOs and symlinks without blocking on their first read."""
    path = no_symlink(Path(path))
    try:
        if not path.is_file():
            fail(code)
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
                fail(code)
            raw = stream.read(maximum + 1)
            if len(raw) > maximum or len(raw) != info.st_size:
                fail(code)
            return raw
    except OSError:
        fail(code)


def validate_case(case):
    exact(case, {"schema", "case_id", "source", "raw_artifacts", "reviewer_decisions",
                 "questions", "budget", "burden_events", "follow_up"})
    if type(case["schema"]) is not int or case["schema"] != 1:
        fail("HANDOFF_SCHEMA")
    identifier(case["case_id"])
    if case["source"] not in ("medit_export", "3shape_native", "manual"):
        fail("HANDOFF_SOURCE")
    if type(case["reviewer_decisions"]) is not list or not 1 <= len(case["reviewer_decisions"]) <= core.MAX_WORLDS:
        fail("HANDOFF_HISTORY_LIMIT")
    fields = set()
    worlds = []
    for history in case["reviewer_decisions"]:
        exact(history, {"id", "decision", "answers", "conditions"})
        if type(history["conditions"]) is not dict or len(history["conditions"]) > 32:
            fail("HANDOFF_CONDITIONS")
        for field, value in history["conditions"].items():
            identifier(field)
            text(value)
            fields.add(field)
        worlds.append({key: copy.deepcopy(history[key]) for key in ("id", "decision", "answers")})
    if len(fields) > 32:
        fail("HANDOFF_FIELD_LIMIT")
    if type(case["questions"]) is not list or len(case["questions"]) > core.MAX_QUESTIONS:
        fail("HANDOFF_QUESTION_LIMIT")
    questions = []
    for question in case["questions"]:
        exact(question, {"id", "label", "cost", "available", "source"})
        text(question["source"], nonempty=True)
        questions.append({key: copy.deepcopy(question[key]) for key in ("id", "label", "cost", "available")})
    model = {"schema": 1, "cases": [{"schema": 1, "case_id": case["case_id"], "worlds": worlds,
                                     "questions": questions, "max_cost": case["budget"]}]}
    try:
        core.validate(model)
        ledger.validate_events(case["burden_events"])
    except (core.InvalidInput, ledger.InvalidBurden) as exc:
        fail(str(exc))
    artifacts = case["raw_artifacts"]
    if type(artifacts) is not list or len(artifacts) > MAX_ARTIFACTS:
        fail("HANDOFF_ARTIFACT_LIMIT")
    ids, paths, total = set(), set(), 0
    for artifact in artifacts:
        exact(artifact, {"id", "path", "bytes", "sha256", "kind", "provenance", "selection"})
        identifier(artifact["id"])
        if artifact["id"] in ids or type(artifact["path"]) is not str or artifact["path"] in paths:
            fail("HANDOFF_DUPLICATE_ARTIFACT")
        ids.add(artifact["id"])
        paths.add(artifact["path"])
        integer(artifact["bytes"], 0, MAX_ARTIFACT_BYTES)
        total += artifact["bytes"]
        if type(artifact["sha256"]) is not str or not HASH.fullmatch(artifact["sha256"]):
            fail("HANDOFF_ARTIFACT_HASH")
        if type(artifact["kind"]) is not str or artifact["kind"] not in KINDS:
            fail("HANDOFF_ARTIFACT_KIND")
        if artifact["provenance"] not in ("synthetic", "reviewer_supplied"):
            fail("HANDOFF_ARTIFACT_PROVENANCE")
        if artifact["kind"] == "medit_csv":
            exact(artifact["selection"], {"case_name"})
            text(artifact["selection"]["case_name"], nonempty=True)
        elif artifact["selection"] != {}:
            fail("HANDOFF_ARTIFACT_SELECTION")
    if total > MAX_TOTAL_BYTES:
        fail("HANDOFF_ARTIFACT_TOTAL")
    if type(case["follow_up"]) is not list or len(case["follow_up"]) > core.MAX_QUESTIONS:
        fail("HANDOFF_FOLLOWUP_LIMIT")
    return model


def load_artifacts(case, root):
    raw = {}
    refs = {}
    for artifact in case["raw_artifacts"]:
        path = safe_artifact_path(root, artifact["path"])
        data = read_regular(path, MAX_ARTIFACT_BYTES, "HANDOFF_ARTIFACT_UNREADABLE")
        if raw_state(data) != {key: artifact[key] for key in ("bytes", "sha256")}:
            fail("HANDOFF_ARTIFACT_BINDING")
        raw[artifact["id"]] = data
        refs[artifact["id"]] = artifact
    return raw, refs


def unknown(reason="MISSING_OR_UNDOCUMENTED"):
    return {"status": "UNKNOWN", "value": None, "provenance": [], "reason": reason}


def finalize_fields(case, observations):
    needed = {field for history in case["reviewer_decisions"] for field in history["conditions"]}
    needed.update(CSV_FIELDS.values() if case["source"] == "medit_export" else NATIVE_FIELDS if case["source"] == "3shape_native" else [])
    fields = {}
    for field in sorted(needed | set(observations)):
        rows = observations.get(field, [])
        values = sorted({row["value"] for row in rows})
        if not values:
            fields[field] = unknown()
        elif len(values) == 1:
            fields[field] = {"status": "KNOWN", "value": values[0], "provenance": rows}
        else:
            fields[field] = {"status": "CONFLICT", "value": None, "values": values, "provenance": rows}
    return fields


def provenance(ref, locator, value):
    return {"artifact_id": ref["id"], "sha256": ref["sha256"], "bytes": ref["bytes"],
            "locator": locator, "value": value, "basis": ref["provenance"]}


def parse_medit_export(case, raw, refs=None):
    refs = refs or {ref["id"]: ref for ref in case["raw_artifacts"]}
    observations, ignored, notes = {}, [], []
    for ref in case["raw_artifacts"]:
        if ref["kind"] != "medit_csv":
            if ref["kind"] in ("case_information_pdf", "exocad_project_manifest"):
                notes.append({"artifact_id": ref["id"], "reason": "OPAQUE_LAYOUT_NOT_DOCUMENTED"})
            continue
        try:
            content = raw[ref["id"]].decode("utf-8-sig", "strict")
            rows = list(csv.reader(io.StringIO(content, newline=""), strict=True))
        except (UnicodeError, csv.Error):
            fail("HANDOFF_CSV_FORMAT")
        if not rows or len(rows) > 1001:
            fail("HANDOFF_CSV_ROWS")
        headers = rows[0]
        if not headers or len(headers) > 32 or len(set(headers)) != len(headers):
            fail("HANDOFF_CSV_HEADERS")
        for header in headers:
            text(header)
            if header not in CSV_FIELDS:
                ignored.append({"artifact_id": ref["id"], "column": header, "reason": "UNDOCUMENTED_FIELD"})
        if any(len(row) != len(headers) for row in rows[1:]):
            fail("HANDOFF_CSV_COLUMNS")
        if "Case Name" not in headers:
            notes.append({"artifact_id": ref["id"], "reason": "CASE_SELECTOR_UNAVAILABLE"})
            continue
        name_index = headers.index("Case Name")
        matched = 0
        for index, row in enumerate(rows[1:], 2):
            for value in row:
                text(value)
            if row[name_index] != ref["selection"]["case_name"]:
                continue
            matched += 1
            for label, value in zip(headers, row):
                if label in CSV_FIELDS and value.strip():
                    observations.setdefault(CSV_FIELDS[label], []).append(
                        provenance(ref, "row:" + str(index) + "/column:" + label, value))
        if not matched:
            notes.append({"artifact_id": ref["id"], "reason": "NO_MATCHING_CASE_ROW"})
    return {"fields": finalize_fields(case, observations), "ignored_fields": ignored, "notes": notes,
            "route_kind": "medit_documented_csv_profile", "native_package_declared": False,
            "native_package_decoded": False, "vendor_format_verified": False,
            "scope": "Documented item labels in a fixed synthetic CSV profile; installed-export wire compatibility unverified. PDF/project fields remain unknown."}


def checked_quote(row, raw, refs, *, require_note=True):
    aid = row["artifact_id"]
    if type(aid) is not str or aid not in refs:
        fail("HANDOFF_QUOTE_ARTIFACT")
    if require_note and refs[aid]["kind"] != "reviewer_note":
        fail("HANDOFF_QUOTE_SOURCE_KIND")
    integer(row["start"], 0, MAX_ARTIFACT_BYTES)
    integer(row["end"], 0, MAX_ARTIFACT_BYTES)
    text(row["quote"], nonempty=True)
    try:
        content = raw[aid].decode("utf-8", "strict")
    except UnicodeError:
        fail("HANDOFF_QUOTE_ENCODING")
    if row["end"] <= row["start"] or row["end"] > len(content) or content[row["start"]:row["end"]] != row["quote"]:
        fail("HANDOFF_QUOTE_BINDING")
    return provenance(refs[aid], "characters:" + str(row["start"]) + "-" + str(row["end"]), "present")


def parse_3shape_native(case, raw, refs=None):
    refs = refs or {ref["id"]: ref for ref in case["raw_artifacts"]}
    observations, ignored, notes = {}, [], []
    native_declared = False
    scan_only = bool(refs) and all(ref["kind"] in ("scan_stl", "scan_ply", "reviewer_note") for ref in refs.values())
    for ref in case["raw_artifacts"]:
        if ref["kind"] != "native_package_manifest":
            continue
        manifest = decode(raw[ref["id"]])
        exact(manifest, {"schema", "case_id", "manifest_kind", "declared_format", "provenance", "package_artifact", "observations"})
        if (type(manifest["schema"]) is not int or manifest["schema"] != 1
                or manifest["case_id"] != case["case_id"] or manifest["manifest_kind"] != "reviewed_native_case"
                or manifest["declared_format"] not in ("3oxz", "stl", "ply")
                or manifest["provenance"] not in ("synthetic", "reviewer_supplied")):
            fail("HANDOFF_NATIVE_MANIFEST")
        package = manifest["package_artifact"]
        if type(package) is not str or package not in refs:
            fail("HANDOFF_NATIVE_PACKAGE_BINDING")
        if type(manifest["observations"]) is not list or len(manifest["observations"]) > 32:
            fail("HANDOFF_NATIVE_OBSERVATION_LIMIT")
        is_scan = manifest["declared_format"] in ("stl", "ply") or refs[package]["kind"] in ("scan_stl", "scan_ply")
        if is_scan:
            scan_only = True
            notes.append({"artifact_id": ref["id"], "reason": "SCAN_ONLY_NOT_NATIVE"})
            continue
        if refs[package]["kind"] != "opaque_native":
            fail("HANDOFF_NATIVE_PACKAGE_KIND")
        native_declared = True
        for row in manifest["observations"]:
            exact(row, {"category", "artifact_id", "start", "end", "quote"})
            identifier(row["category"])
            proof = checked_quote(row, raw, refs)
            if row["category"] not in NATIVE_FIELDS:
                ignored.append({"artifact_id": ref["id"], "field": row["category"], "reason": "UNDOCUMENTED_FIELD"})
                continue
            # A byte-bound quotation alone does not assert presence. Require a
            # structured claim, or the original explicitly synthetic fixture
            # grammar. A negative or arbitrary sentence must not become present.
            synthetic_statement = "Synthetic reviewer observation: " + row["category"] + " content is present."
            synthetic_profile = (manifest["provenance"] == "synthetic"
                                 and refs[package]["provenance"] == "synthetic"
                                 and refs[row["artifact_id"]]["provenance"] == "synthetic"
                                 and row["quote"] == synthetic_statement)
            if synthetic_profile:
                present = True
                proof["observation_profile"] = "synthetic_presence_statement_v1"
            else:
                try:
                    claim = decode(row["quote"].encode("utf-8"))
                except InvalidHandoff:
                    fail("HANDOFF_NATIVE_OBSERVATION_FORMAT")
                exact(claim, {"category", "value"})
                if claim["category"] != row["category"] or claim["value"] not in ("present", "absent", "unknown"):
                    fail("HANDOFF_NATIVE_OBSERVATION_VALUE")
                present = claim["value"] == "present"
                proof["observation_profile"] = "reviewed_category_json_v1"
            if not present:
                notes.append({"artifact_id": ref["id"], "field": row["category"],
                              "reason": "CATEGORY_NOT_KNOWN_PRESENT"})
                continue
            proof["manifest_id"] = ref["id"]
            proof["manifest_sha256"] = ref["sha256"]
            proof["interpretation"] = "Reviewer category declaration; opaque native bytes not decoded."
            observations.setdefault(row["category"], []).append(proof)
    if scan_only and not native_declared:
        notes.append({"reason": "SCAN_ONLY_NOT_NATIVE"})
    return {"fields": finalize_fields(case, observations), "ignored_fields": ignored, "notes": notes,
            "route_kind": "reviewed_native_manifest" if native_declared else "scan_only" if scan_only else "native_unverified",
            "native_package_declared": native_declared, "native_package_decoded": False,
            "vendor_format_verified": False,
            "scope": "Hash-bound reviewer manifest of documented categories; no proprietary package decoder or administrative-field inference."}


def build_histories_from_export(histories, fields):
    conflicts = sorted(field for field, value in fields.items() if value["status"] == "CONFLICT")
    if conflicts:
        return {"histories": copy.deepcopy(histories), "eliminated": [], "conflicts": conflicts, "status": "EXPORT_EVIDENCE_CONFLICT"}
    retained, eliminated = [], []
    for history in histories:
        contradictions = [field for field, expected in history["conditions"].items()
                          if fields.get(field, {}).get("status") == "KNOWN" and fields[field]["value"] != expected]
        if contradictions:
            eliminated.append({"history_id": history["id"], "fields": sorted(contradictions)})
        else:
            retained.append(copy.deepcopy(history))
    return {"histories": retained, "eliminated": eliminated, "conflicts": [],
            "status": "CONSISTENT" if retained else "EXPORT_MODEL_CONFLICT"}


def outcome(histories, *, conflict=False, recorded=0, synthetic=0):
    decisions = {history["decision"] for history in histories}
    resolved = not conflict and len(decisions) == 1
    return {"status": "CONFLICT" if conflict else "RESOLVED" if resolved else "UNRESOLVED",
            "decision": next(iter(decisions)) if resolved else None,
            "candidate_ids": sorted(history["id"] for history in histories),
            "unresolved_count": 0 if resolved else 1,
            "recorded_questions_asked": recorded, "synthetic_questions_asked": synthetic}


def apply_follow_up(case, histories, raw, refs):
    questions = {question["id"]: question for question in case["questions"]}
    events = {event["id"]: event for event in case["burden_events"]}
    seen, seen_events = set(), set()
    retained = copy.deepcopy(histories)
    recorded = synthetic = 0
    for row in case["follow_up"]:
        exact(row, {"question_id", "answer", "artifact_id", "start", "end", "quote", "event_id"})
        qid = row["question_id"]
        if type(qid) is not str or qid not in questions or not questions[qid]["available"] or qid in seen:
            fail("HANDOFF_FOLLOWUP_QUESTION")
        seen.add(qid)
        text(row["answer"])
        checked_quote(row, raw, refs)
        # A response is an explicit data record, not an interpretation of prose.
        # Bind the declared answer and question to the exact quoted source bytes.
        response = decode(row["quote"].encode("utf-8"))
        exact(response, {"question_id", "answer"})
        if response != {"question_id": qid, "answer": row["answer"]}:
            fail("HANDOFF_FOLLOWUP_BINDING")
        event_id = row["event_id"]
        if type(event_id) is not str or event_id not in events or event_id in seen_events:
            fail("HANDOFF_FOLLOWUP_EVENT")
        seen_events.add(event_id)
        event = events[event_id]
        if event["stage"] != "follow_up" or event["measurement"] == "unknown" or not ledger.applies(event, "export_plus_planner"):
            fail("HANDOFF_FOLLOWUP_EVENT")
        is_synthetic = event["measurement"] == "synthetic" or refs[row["artifact_id"]]["provenance"] == "synthetic"
        synthetic += is_synthetic
        recorded += not is_synthetic
        retained = [history for history in retained if qid not in history["answers"] or history["answers"][qid] == row["answer"]]
    # A source-declared response is bound to the quoted artifact, not certified as
    # an actual exchange. Synthetic records never increment recorded human asks.
    return retained, recorded, synthetic


def evaluate_handoff(path):
    start = time.perf_counter_ns()
    path = no_symlink(Path(path))
    input_raw = read_regular(path, core.MAX_BYTES, "HANDOFF_INPUT_UNREADABLE")
    case = decode(input_raw)
    manual = validate_case(case)
    events = [ledger.runtime_event("validate", "model_preparation", time.perf_counter_ns() - start,
                                   "Validate the supplied finite model and handoff contract.")]
    start = time.perf_counter_ns()
    raw, refs = load_artifacts(case, path.parent)
    events.append(ledger.runtime_event("read_artifacts", "acquisition", time.perf_counter_ns() - start,
                                      "Read and hash already supplied local files; vendor export acquisition remains separate.", EXPORT_ROUTES))
    start = time.perf_counter_ns()
    if case["source"] == "medit_export":
        extraction = parse_medit_export(case, raw, refs)
    elif case["source"] == "3shape_native":
        extraction = parse_3shape_native(case, raw, refs)
    else:
        extraction = {"fields": finalize_fields(case, {}), "ignored_fields": [], "notes": [],
                      "route_kind": "manual_supplied_model", "native_package_declared": False,
                      "native_package_decoded": False, "vendor_format_verified": False,
                      "scope": "No extraction; original supplied histories retained."}
    events.append(ledger.runtime_event("extract", "interpretation", time.perf_counter_ns() - start,
                                      "Apply the declared CSV/manifest extraction profile.", EXPORT_ROUTES))
    start = time.perf_counter_ns()
    manual_result = core.solve(manual["cases"][0])
    events.append(ledger.runtime_event("manual_plan", "model_preparation", time.perf_counter_ns() - start,
                                      "Run the unchanged planner on the unfiltered supplied model.", ("manual_model",)))
    start = time.perf_counter_ns()
    mapped = build_histories_from_export(case["reviewer_decisions"], extraction["fields"])
    model = result = None
    if mapped["status"] == "CONSISTENT":
        model = copy.deepcopy(manual)
        model["cases"][0]["worlds"] = [{key: copy.deepcopy(history[key]) for key in ("id", "decision", "answers")}
                                       for history in mapped["histories"]]
        core.validate(model)
        result = core.solve(model["cases"][0])
    events.append(ledger.runtime_event("export_model", "model_preparation", time.perf_counter_ns() - start,
                                      "Filter only supplied histories and run the unchanged planner when consistent.", EXPORT_ROUTES))
    start = time.perf_counter_ns()
    conflict = mapped["status"] != "CONSISTENT"
    remaining, recorded, synthetic = apply_follow_up(case, mapped["histories"], raw, refs)
    comparison = {
        "manual_model": outcome(case["reviewer_decisions"]),
        "export_reconciliation": outcome(mapped["histories"], conflict=conflict),
        "export_plus_planner": outcome(remaining, conflict=conflict or not remaining, recorded=recorded, synthetic=synthetic),
    }
    if case["follow_up"]:
        events.append(ledger.runtime_event("apply_followup", "follow_up", time.perf_counter_ns() - start,
                                          "Reconcile supplied response records; no question is sent.", ("export_plus_planner",)))
    plan = {"schema": 1, "version": "handoff-bench-1", "case_id": case["case_id"], "source": case["source"],
            "input_sha256": hashlib.sha256(input_raw).hexdigest(), "planner_core_sha256": CORE_SHA256,
            "raw_artifacts": copy.deepcopy(case["raw_artifacts"]), "extraction": extraction,
            "model_status": mapped["status"], "eliminated_histories": mapped["eliminated"],
            "manual_model_input": manual, "model_input": model, "manual_planner_result": manual_result,
            "planner_result": result, "comparison": comparison,
            "question_sources": {q["id"]: q["source"] for q in case["questions"]},
            "planner_cost_unit": "declared synthetic effort units; separate from wall time",
            "questions_sent": 0, "model_complete_in_reality": False, "evidence_authenticated": False,
            "scope": "Reviewer-supplied histories and source declarations; no clinical decision, native-format certification, or market-advantage claim."}
    burden = ledger.make_ledger(case["burden_events"], events, comparison, result)
    burden["case_id"] = case["case_id"]
    output = {"plan": plan, "burden": burden}
    if len(canonical(output)) + 1 > MAX_OUTPUT_BYTES:
        fail("HANDOFF_OUTPUT_BOUNDS")
    # Detect ordinary local changes during this read/compute interval. This is a
    # consistency check, not isolation from a concurrently malicious filesystem.
    try:
        current_input = read_regular(path, core.MAX_BYTES, "HANDOFF_INPUT_CHANGED")
        if current_input != input_raw:
            fail("HANDOFF_INPUT_CHANGED")
        for aid, ref in refs.items():
            file = safe_artifact_path(path.parent, ref["path"])
            after = read_regular(file, MAX_ARTIFACT_BYTES, "HANDOFF_ARTIFACT_CHANGED")
            if after != raw[aid]:
                fail("HANDOFF_ARTIFACT_CHANGED")
    except OSError:
        fail("HANDOFF_ARTIFACT_CHANGED")
    return output


def publish(output, destination):
    """Validate size before creating a new result directory; never overwrite."""
    stdout = canonical(output) + b"\n"
    plan = canonical(output["plan"]) + b"\n"
    burden = canonical(output["burden"]) + b"\n"
    if max(len(stdout), len(plan), len(burden)) > MAX_OUTPUT_BYTES:
        fail("HANDOFF_OUTPUT_BOUNDS")
    destination = no_symlink(Path(destination))
    if destination.exists():
        fail("HANDOFF_OUTPUT_EXISTS")
    if not destination.parent.is_dir():
        fail("HANDOFF_OUTPUT_PARENT")
    receipt = {"schema": 1, "input_sha256": output["plan"]["input_sha256"],
               "planner_core_sha256": CORE_SHA256,
               "files": [{"path": "plan.json", **raw_state(plan)}, {"path": "burden.json", **raw_state(burden)}],
               "stdout": raw_state(stdout)}
    # Check and build every serialized payload before the first publication write.
    payloads = {"plan.json": plan, "burden.json": burden, "receipt.json": canonical(receipt) + b"\n"}
    try:
        destination.mkdir()
        for name, data in payloads.items():
            with (destination / name).open("xb") as stream:
                stream.write(data)
    except FileExistsError:
        fail("HANDOFF_OUTPUT_EXISTS")
    except OSError:
        # Do not remove or overwrite any pre-existing path on a publication error.
        fail("HANDOFF_OUTPUT_WRITE")
    return stdout
