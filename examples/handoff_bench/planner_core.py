"""Local evidence-question planner for explicitly supplied finite histories.

This adapter is original experiment code. Whole source modules for `powerset`
and `bucketize` are preserved by FORGE's reviewed creation recipe. The model
does not discover histories, authenticate evidence, or choose clinical actions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from more_itertools.recipes import powerset
from boltons.iterutils import bucketize


MAX_BYTES = 262_144
MAX_WORLDS = 24
MAX_QUESTIONS = 16
MAX_CASES = 64
MAX_SUBSETS_PER_BATCH = 262_144
MAX_PLANS = 8
ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
VERSION = "evidence-recovery-0.2"


class InvalidInput(Exception):
    pass


def fail(code):
    raise InvalidInput(code)


def fields(value, required):
    if type(value) is not dict or set(value) != set(required):
        fail("INVALID_FIELDS")


def integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        fail("INVALID_INTEGER")


def identifier(value):
    if type(value) is not str or not ID.fullmatch(value):
        fail("INVALID_ID")


def scalar_text(value, *, nonempty=False):
    if type(value) is not str or len(value) > 500 or (nonempty and not value.strip()):
        fail("INVALID_TEXT")
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        fail("INVALID_UNICODE")


def pairs_object(rows):
    result = {}
    for key, value in rows:
        if key in result:
            fail("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def reject_constant(_):
    fail("NONFINITE_JSON")


def decode(raw):
    if not raw or len(raw) > MAX_BYTES:
        fail("INPUT_BYTES_LIMIT")
    try:
        value = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs_object,
                           parse_constant=reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        fail("INVALID_JSON")
    # Validate all keys and values, including strings that would otherwise never
    # be printed. No unpaired surrogate may enter a saved UTF-8 result.
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > 12:
            fail("INPUT_DEPTH_LIMIT")
        if type(item) is str:
            scalar_text(item)
        elif type(item) is dict:
            stack.extend((x, depth + 1) for pair in item.items() for x in pair)
        elif type(item) is list:
            stack.extend((x, depth + 1) for x in item)
    return value


def validate_case(case):
    fields(case, {"schema", "case_id", "worlds", "questions", "max_cost"})
    if type(case["schema"]) is not int or case["schema"] != 1:
        fail("INVALID_SCHEMA")
    identifier(case["case_id"])
    integer(case["max_cost"], 0, 16_000_000)
    questions = case["questions"]
    worlds = case["worlds"]
    if type(questions) is not list or not 0 <= len(questions) <= MAX_QUESTIONS:
        fail("QUESTION_LIMIT")
    if type(worlds) is not list or not 1 <= len(worlds) <= MAX_WORLDS:
        fail("WORLD_LIMIT")
    qids = set()
    for q in questions:
        fields(q, {"id", "label", "cost", "available"})
        identifier(q["id"])
        scalar_text(q["label"], nonempty=True)
        integer(q["cost"], 0, 1_000_000)
        if type(q["available"]) is not bool:
            fail("INVALID_AVAILABILITY")
        if q["id"] in qids:
            fail("DUPLICATE_QUESTION")
        qids.add(q["id"])
    wids = set()
    for w in worlds:
        fields(w, {"id", "decision", "answers"})
        identifier(w["id"])
        scalar_text(w["decision"], nonempty=True)
        if w["id"] in wids:
            fail("DUPLICATE_WORLD")
        wids.add(w["id"])
        if type(w["answers"]) is not dict or set(w["answers"]) - qids:
            fail("UNKNOWN_QUESTION")
        for answer in w["answers"].values():
            scalar_text(answer)
    return 1 << sum(q["available"] for q in questions)


def validate(value):
    fields(value, {"schema", "cases"})
    if type(value["schema"]) is not int or value["schema"] != 1:
        fail("INVALID_SCHEMA")
    cases = value["cases"]
    if type(cases) is not list or not 1 <= len(cases) <= MAX_CASES:
        fail("CASE_LIMIT")
    subset_budget = 0
    ids = set()
    for case in cases:
        subset_budget += validate_case(case)
        if case["case_id"] in ids:
            fail("DUPLICATE_CASE")
        ids.add(case["case_id"])
    if subset_budget > MAX_SUBSETS_PER_BATCH:
        fail("SUBSET_BUDGET")
    return value


def solve(case):
    worlds = sorted(case["worlds"], key=lambda x: x["id"])
    questions = sorted((q for q in case["questions"] if q["available"]), key=lambda x: x["id"])
    groups = bucketize(worlds, key=lambda w: w["decision"])
    # The reviewed grouping component supplies the decision blocks used here.
    # Only pairs across distinct blocks need evidence that separates them.
    blocks = list(groups.values())
    cross_pairs = [(a, b) for i, block in enumerate(blocks) for other in blocks[i + 1:]
                   for a in block for b in other]
    pairs = sorted((tuple(sorted(pair, key=lambda w: w["id"])) for pair in cross_pairs),
                   key=lambda pair: (pair[0]["id"], pair[1]["id"]))
    masks = {}
    coverage = {}
    for q in questions:
        key = q["id"]
        covered = [i for i, (a, b) in enumerate(pairs)
                   if key in a["answers"] and key in b["answers"]
                   and a["answers"][key] != b["answers"][key]]
        masks[key] = sum(1 << i for i in covered)
        coverage[key] = covered
    target = (1 << len(pairs)) - 1
    union = 0
    for mask in masks.values():
        union |= mask
    unresolved = [[a["id"], b["id"]] for i, (a, b) in enumerate(pairs) if not union & (1 << i)]
    base = {
        "case_id": case["case_id"],
        "status": None,
        "minimum_cost": None,
        "minimum_question_count": None,
        "optimal_question_sets": [],
        "optimal_plan_count": 0,
        "omitted_optimal_plan_count": 0,
        "indistinguishable_pairs": unresolved,
        "plans_to_review": [],
        "decision_groups": {decision: [w["id"] for w in members]
                            for decision, members in sorted(groups.items())},
        "cross_decision_pairs": [[a["id"], b["id"]] for a, b in pairs],
        "question_pair_coverage": coverage,
        "ask_all_baseline": {
            "questions": [q["id"] for q in questions],
            "cost": sum(q["cost"] for q in questions),
            "question_count": len(questions),
            "distinguishes_decisions": union == target,
            "within_budget": sum(q["cost"] for q in questions) <= case["max_cost"],
        },
        "subsets_examined": 0,
        "search_complete_for_declared_model": True,
        "model_complete_in_reality": False,
        "evidence_authenticated": False,
        "questions_sent": 0,
    }
    if unresolved:
        base["status"] = "INSUFFICIENT_EVIDENCE"
        return base
    # `powerset` is the unchanged reviewed implementation from more-itertools.
    # Cost then cardinality gives a reproducible finite-model optimum. No belief
    # probabilities, favorable missing answers or hidden candidate histories.
    optimum = None
    retained = []
    count = 0
    for subset in powerset(questions):
        base["subsets_examined"] += 1
        score = (sum(q["cost"] for q in subset), len(subset))
        if optimum is not None and score > optimum:
            continue
        mask = 0
        for q in subset:
            mask |= masks[q["id"]]
        if mask != target:
            continue
        ids = [q["id"] for q in subset]
        if optimum is None or score < optimum:
            optimum, retained, count = score, [ids], 1
        else:
            count += 1
            retained.append(ids)
            retained.sort()
            del retained[MAX_PLANS:]
    assert optimum is not None  # The union of all available questions covers.
    base.update({"minimum_cost": optimum[0], "minimum_question_count": optimum[1],
                 "optimal_question_sets": sorted(retained), "optimal_plan_count": count,
                 "omitted_optimal_plan_count": count - len(retained)})
    if not pairs:
        base["status"] = "NO_QUESTION_NEEDED"
    elif optimum[0] > case["max_cost"]:
        base["status"] = "BUDGET_BLOCKED"
    else:
        base["status"] = "QUESTIONS_FOR_REVIEW"
    if base["status"] != "BUDGET_BLOCKED":
        base["plans_to_review"] = base["optimal_question_sets"]
    return base


def main():
    parser = argparse.ArgumentParser(description="Find minimal distinguishing questions in a reviewed finite model.")
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    try:
        with Path(args.input).open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        value = validate(decode(raw))
        output = {"schema": 1, "status": "EVALUATED_DECLARED_MODELS", "version": VERSION,
                  "input_sha256": hashlib.sha256(raw).hexdigest(),
                  "cases": [solve(case) for case in value["cases"]],
                  "limits": {"worlds_per_case": MAX_WORLDS, "questions_per_case": MAX_QUESTIONS,
                             "subsets_per_batch": MAX_SUBSETS_PER_BATCH, "retained_optimal_sets": MAX_PLANS},
                  "scope": "Synthetic or reviewer-declared histories and effort units; no real-world completeness, clinical decision or human time-saving claim.",
                  "execution_authority": "Local reviewed prototype only; no contacts, source fetching, or production writes."}
        print(json.dumps(output, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    except InvalidInput as exc:
        print(json.dumps({"status": "INVALID_INPUT", "error_code": str(exc)}, sort_keys=True))
        return 2
    except OSError:
        print(json.dumps({"status": "INVALID_INPUT", "error_code": "INPUT_UNREADABLE"}, sort_keys=True))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
