"""Construct hand-labeled fixtures; this file does not solve the optimization problem.

Expected minima and optimal sets below are authored explicitly from tiny cases.
The script serializes those labels and hashes the frozen inputs/rationale.
"""
from pathlib import Path
import hashlib
import json
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
CASES = []


def q(qid, cost, available=True):
    return {"id": qid, "label": "Synthetic evidence request " + qid, "cost": cost, "available": available}


def w(wid, decision, **answers):
    return {"id": wid, "decision": decision, "answers": answers}


def add(cid, title, worlds, questions, budget, status, cost, cardinality, plans, rationale,
        naive_cost, naive_count, naive_sufficient=True, pairs=None, total=None):
    """All expected values, including naive totals, are manually supplied."""
    supplied = {"schema": 1, "case_id": cid, "worlds": worlds, "questions": questions, "max_cost": budget}
    total = len(plans) if total is None else total
    expected = {
        "case_id": cid,
        "title": title,
        "status": status,
        "minimum_cost": cost,
        "minimum_question_count": cardinality,
        "optimal_question_sets": plans,
        "optimal_plan_count": total,
        "omitted_optimal_plan_count": total - len(plans),
        "indistinguishable_pairs": pairs or [],
        "actionable_within_budget": status == "PLAN_FOUND",
        "rationale": rationale,
        "ask_all_available_baseline": {
            "question_ids": sorted(row["id"] for row in questions if row["available"]),
            "declared_cost": naive_cost,
            "question_count": naive_count,
            "distinguishes_all_cross_decision_pairs": naive_sufficient,
            "within_budget": naive_cost <= budget,
        },
    }
    assert naive_cost == sum(row["cost"] for row in questions if row["available"]), cid
    assert naive_count == sum(row["available"] for row in questions), cid
    assert plans == sorted(plans), cid
    assert all(p == sorted(p) for p in plans), cid
    CASES.append((supplied, expected))


add("C01", "All declared histories have the same decision",
    [w("w1", "HOLD", noise="old"), w("w2", "HOLD", noise="new")],
    [q("noise", 9)], 0, "NO_QUESTION_NEEDED", 0, 0, [[]],
    "Both histories require HOLD. There are no cross-decision pairs. The sole minimum-cardinality optimum is the empty set, regardless of differing noise answers.",
    9, 1)

add("C02", "Single declared history and no questions",
    [w("w1", "REQUEST_RECEIPT")], [], 0, "NO_QUESTION_NEEDED", 0, 0, [[]],
    "One declared history creates no competing decision. No question can be necessary to distinguish decisions within this supplied world set.",
    0, 0)

add("C03", "One available separator",
    [w("w1", "HOLD", receipt="absent"), w("w2", "REVIEW", receipt="present")],
    [q("receipt", 3)], 3, "PLAN_FOUND", 3, 1, [["receipt"]],
    "The sole cross-decision pair has different known receipt answers. Its only available separator costs three units, exactly the budget.",
    3, 1)

add("C04", "Two questions are jointly necessary",
    [w("w1", "A", left="0", right="0"), w("w2", "B", left="1", right="0"), w("w3", "C", left="0", right="1")],
    [q("left", 2), q("right", 3)], 5, "PLAN_FOUND", 5, 2, [["left", "right"]],
    "left is required for w1/w2, and right is required for w1/w3. Each alone leaves a cross-decision pair indistinguishable. Together they also distinguish w2/w3, so the unique optimum costs five.",
    5, 2)

add("C05", "Cheaper duplicate separator dominates expensive one",
    [w("w1", "A", cheap="0", expensive="0"), w("w2", "B", cheap="1", expensive="1")],
    [q("cheap", 2), q("expensive", 5)], 7, "PLAN_FOUND", 2, 1, [["cheap"]],
    "Each question alone distinguishes the only pair. cheap costs two versus five. Adding expensive cannot improve coverage or the primary cost objective.",
    7, 2)

add("C06", "Two equal-cost alternative question sets",
    [w("w1", "A", qa="0", qb="old"), w("w2", "B", qa="1", qb="new")],
    [q("qa", 2), q("qb", 2)], 4, "PLAN_FOUND", 2, 1, [["qa"], ["qb"]],
    "Both individual questions distinguish the only pair and have the same cost and cardinality. Both one-question optima must be retained.",
    4, 2)

add("C07", "Histories with the same decision need not be separated",
    [w("w1", "A", flag="0", detail="0"), w("w2", "A", flag="0", detail="1"), w("w3", "B", flag="1", detail="0")],
    [q("flag", 2), q("detail", 0)], 2, "PLAN_FOUND", 2, 1, [["flag"]],
    "Only w1/w3 and w2/w3 need separation; flag separates both. detail is free but does not separate w1/w3. Adding it ties cost and loses the cardinality tie-break. Distinguishing w1 from w2 is unnecessary.",
    2, 2)

add("C08", "Only distinguishing question is unavailable",
    [w("w1", "A", unavailable="0", noise="same"), w("w2", "B", unavailable="1", noise="same")],
    [q("unavailable", 1, False), q("noise", 0)], 10, "INSUFFICIENT_EVIDENCE", None, None, [],
    "The available noise question has identical answers. The differing unavailable answer is not an admissible question, so w1/w2 cannot be guaranteed separated.",
    0, 1, False, [["w1", "w2"]])

add("C09", "Identical known histories with different decisions",
    [w("w1", "A", record="same"), w("w2", "B", record="same")],
    [q("record", 4)], 10, "INSUFFICIENT_EVIDENCE", None, None, [],
    "The sole available known answer is identical in both histories even though decisions differ. More budget cannot create a separating question.",
    4, 1, False, [["w1", "w2"]])

add("C10", "Missing answer cannot guarantee a distinction",
    [w("w1", "A", receipt="present"), w("w2", "B")],
    [q("receipt", 1)], 10, "INSUFFICIENT_EVIDENCE", None, None, [],
    "w2 has no receipt key. Missing is an unknown answer, not a different known value. The pair has no guaranteed available separator.",
    1, 1, False, [["w1", "w2"]])

add("C11", "Complete alternative evidence rescues an unknown question",
    [w("w1", "A", incomplete="0", evidence="old"), w("w2", "B", evidence="new")],
    [q("incomplete", 1), q("evidence", 4)], 5, "PLAN_FOUND", 4, 1, [["evidence"]],
    "incomplete cannot guarantee separation because w2's answer is missing. evidence has different known answers in both worlds and is the only effective separator, costing four.",
    5, 2)

add("C12", "Zero-cost separating question still must be asked",
    [w("w1", "A", free="0", paid="0"), w("w2", "B", free="1", paid="1")],
    [q("free", 0), q("paid", 2)], 0, "PLAN_FOUND", 0, 1, [["free"]],
    "free alone distinguishes the pair at zero declared cost. Zero cost does not mean no question is needed: the minimum cardinality is one.",
    2, 2)

add("C13", "Free irrelevant question loses cardinality tie-break",
    [w("w1", "A", noise="same", separator="0"), w("w2", "B", noise="same", separator="1")],
    [q("noise", 0), q("separator", 2)], 2, "PLAN_FOUND", 2, 1, [["separator"]],
    "separator is necessary. Adding the free noise question does not raise cost, but raises cardinality from one to two, so it must not appear in an optimal set.",
    2, 2)

add("C14", "Minimum feasible cost equals the budget",
    [w("w1", "A", qa="0", qb="0"), w("w2", "B", qa="1", qb="0"), w("w3", "C", qa="0", qb="1")],
    [q("qa", 2), q("qb", 4)], 6, "PLAN_FOUND", 6, 2, [["qa", "qb"]],
    "qa is uniquely needed for w1/w2, qb for w1/w3. Their total six equals the budget and is permitted.",
    6, 2)

add("C15", "Minimum feasible cost exceeds the budget by one",
    [w("w1", "A", qa="0", qb="0"), w("w2", "B", qa="1", qb="0"), w("w3", "C", qa="0", qb="1")],
    [q("qa", 2), q("qb", 4)], 5, "BUDGET_BLOCKED", 6, 2, [["qa", "qb"]],
    "The same mandatory pair of questions as C14 costs six, while the budget is five. Separation is possible in the declared universe but not within budget. The oracle retains the mathematical optimum; it is not a permitted recommendation.",
    6, 2)

add("C16", "Irrelevant and missing-answer questions do not help",
    [w("w1", "A", zero="same", noise="same", separator="0"), w("w2", "B", zero="same", noise="same", separator="1")],
    [q("zero", 0), q("noise", 1), q("unanswered", 50), q("separator", 3)], 3,
    "PLAN_FOUND", 3, 1, [["separator"]],
    "separator is the only effective question. zero and noise have equal answers; unanswered is missing in both worlds. The optimum is three versus fifty-four units for asking every available question.",
    54, 4)

add("C17", "Three pairwise alternative two-question optima",
    [w("w1", "A", qa="1", qb="0", qc="0"), w("w2", "B", qa="0", qb="1", qc="0"), w("w3", "C", qa="0", qb="0", qc="1")],
    [q("qa", 1), q("qb", 1), q("qc", 1)], 2, "PLAN_FOUND", 2, 2,
    [["qa", "qb"], ["qa", "qc"], ["qb", "qc"]],
    "Every single question leaves the other two worlds together. Any pair gives three distinct answer vectors. All three two-question sets have cost two and cardinality two.",
    3, 3)

add("C18", "Report only the genuinely indistinguishable pairs",
    [w("w1", "A", qx="0"), w("w2", "B", qx="1"), w("w3", "C")],
    [q("qx", 1)], 10, "INSUFFICIENT_EVIDENCE", None, None, [],
    "qx separates w1/w2. It cannot guarantee either pair involving w3 because w3's answer is missing. The unresolved pair list is exactly w1/w3 and w2/w3.",
    1, 1, False, [["w1", "w3"], ["w2", "w3"]])

add("C19", "Renamed and reordered copy of the synergistic case",
    [w("M4", "C", alpha="0", zeta="1"), w("N9", "A", alpha="0", zeta="0"), w("A1", "B", alpha="1", zeta="0")],
    [q("zeta", 3), q("alpha", 2)], 5, "PLAN_FOUND", 5, 2, [["alpha", "zeta"]],
    "This is C04 with renamed world and question identifiers and reordered arrays. alpha is left and zeta is right. Both are required; cost and cardinality remain five and two. Names/order do not change feasibility.",
    5, 2)

add("C20", "Cardinality breaks a primary-cost tie",
    [w("w1", "A", qa="0", qb="0", whole="A"), w("w2", "B", qa="1", qb="0", whole="B"), w("w3", "C", qa="0", qb="1", whole="C")],
    [q("qa", 1), q("qb", 1), q("whole", 2)], 2, "PLAN_FOUND", 2, 1, [["whole"]],
    "qa plus qb separates all decisions at total cost two. whole also separates all at cost two but asks only one question. The stated cardinality tie-break selects whole alone.",
    4, 3)

add("C21", "More than eight equally optimal plans",
    [w("w1", "A", **{f"q{i:02}": "0" for i in range(10)}), w("w2", "B", **{f"q{i:02}": "1" for i in range(10)})],
    [q(f"q{i:02}", 1) for i in range(10)], 1, "PLAN_FOUND", 1, 1,
    [[f"q{i:02}"] for i in range(8)],
    "All ten questions individually distinguish the only pair and cost one. There are exactly ten optimal singleton sets. Lexicographic retention keeps q00 through q07 and reports two omitted optima.",
    10, 10, total=10)

add("C22", "Two zero-cost questions are jointly necessary",
    [w("w1", "A", qa="0", qb="0"), w("w2", "B", qa="1", qb="0"), w("w3", "C", qa="0", qb="1")],
    [q("qa", 0), q("qb", 0)], 0, "PLAN_FOUND", 0, 2, [["qa", "qb"]],
    "Each question uniquely separates one pair from w1. Both must be asked even though the total declared cost is zero. An empty or singleton plan is insufficient.",
    0, 2)

add("C23", "Empty and literal unknown strings are known answers",
    [w("w1", "RÉVISER", observed=""), w("w2", "保持", observed="unknown")],
    [q("observed", 1)], 1, "PLAN_FOUND", 1, 1, [["observed"]],
    "Both worlds contain an observed key with a string answer. Empty string and the literal word unknown are different known values under the frozen schema; neither is a missing key. Unicode decision labels do not change that rule.",
    1, 1)

add("C24", "Lower cost wins over fewer questions",
    [w("w1", "A", qa="0", qb="0", whole="A"), w("w2", "B", qa="1", qb="0", whole="B"), w("w3", "C", qa="0", qb="1", whole="C")],
    [q("qa", 2), q("qb", 2), q("whole", 7)], 7, "PLAN_FOUND", 4, 2, [["qa", "qb"]],
    "The two-question plan costs four and separates all pairs. whole alone costs seven. Cost is the primary objective, so fewer questions cannot displace the cheaper two-question optimum.",
    11, 3)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


(ROOT / "inputs").mkdir(exist_ok=True)
for supplied, expected in CASES:
    (ROOT / "inputs" / f"{supplied['case_id']}.json").write_bytes(encoded(supplied))
(ROOT / "input-batch.json").write_bytes(encoded({"schema": 1, "cases": [a for a, _ in CASES]}))
(ROOT / "expected.json").write_bytes(encoded({
    "schema": 1,
    "baseline_kind": "independent_agent_manual_judgment",
    "not_a_practitioner_study": True,
    "generation": "Optima and rationales hand-authored; no optimization solver or product implementation used.",
    "cases": [b for _, b in CASES],
}))
manifest = []
for file in sorted(ROOT.rglob("*")):
    if file.is_file() and file.name not in {"freeze.json"} and "__pycache__" not in file.parts:
        raw = file.read_bytes()
        manifest.append({"path": file.relative_to(ROOT).as_posix(), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
freeze = {
    "schema": 1,
    "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
    "case_count": len(CASES),
    "labels_visible_to_implementation_agent_before_first_run": False,
    "implementation_access_by_baseline_author": False,
    "optimization_solver_used_for_labels": False,
    "files": manifest,
}
(ROOT / "freeze.json").write_bytes(encoded(freeze))
print(json.dumps({"cases": len(CASES), "files": len(manifest), "freeze_sha256": hashlib.sha256((ROOT / "freeze.json").read_bytes()).hexdigest(), "frozen_at_utc": freeze["frozen_at_utc"]}, indent=2))
