"""Append recorded handoff comparisons to an existing report, without a new UI.

Rendering is data-only. No product, source artifact, URL or supplied code runs.
"""

from html import escape
import re


START = "<!-- FORGE_HANDOFF_BENCH_START -->"
END = "<!-- FORGE_HANDOFF_BENCH_END -->"
MAX_REPORT_BYTES = 10_000_000
ROUTES = (
    ("manual_model", "Manual model"),
    ("export_reconciliation", "Export reconciliation"),
    ("export_plus_planner", "Export plus planner"),
)
def _text(value):
    if value is None:
        return "UNKNOWN"
    text = str(value)
    try:
        text.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("Report evidence must contain Unicode scalar values.") from exc
    return escape(text, quote=True)


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _count(value):
    return str(value) if type(value) is int and value >= 0 else "UNKNOWN"


def _duration(value):
    return f"{value / 1_000_000_000:.6f} s" if type(value) is int and value >= 0 else "UNKNOWN"


def _planned(result):
    status = result.get("status")
    if status == "QUESTIONS_FOR_REVIEW":
        return _count(result.get("minimum_question_count"))
    if status == "NO_QUESTION_NEEDED":
        return "0"
    if status in ("INSUFFICIENT_EVIDENCE", "BUDGET_BLOCKED"):
        return "0 (no admissible recommendation)"
    return "UNKNOWN"


def _cost(result):
    value = _count(result.get("minimum_cost"))
    if result.get("status") == "BUDGET_BLOCKED":
        return value + " (diagnostic; over budget)"
    return value


def _quality(record):
    record = _mapping(record)
    status = record.get("status")
    if record.get("truth_available") is not True or status not in ("CORRECT", "INCORRECT"):
        return "UNKNOWN"
    return _text(status) + "<br><small>" + _text(record.get("basis")) + "</small>"


def _row(label, values):
    return "<tr><th scope=\"row\">" + _text(label) + "</th>" + "".join(
        "<td>" + value + "</td>" for value in values
    ) + "</tr>"


def _allocated_total(record):
    record = _mapping(record)
    value = record.get("wall_time_ns")
    if record.get("complete") is not True or type(value) is not int or value < 0:
        return "UNKNOWN (incomplete); known subtotal " + _duration(record.get("known_wall_time_ns"))
    kind = "measured allocation" if record.get("observed_complete") is True and record.get("measurement_class") == "measured" else "includes reported or synthetic durations"
    return _duration(value) + " (" + kind + ")"


def _comparison(case):
    plan = _mapping(case.get("plan"))
    comparison = _mapping(plan.get("comparison"))
    quality = _mapping(case.get("decision_quality"))
    outcomes = [_mapping(comparison.get(key)) for key, _ in ROUTES]
    manual = _mapping(plan.get("manual_planner_result"))
    exported = _mapping(plan.get("planner_result"))
    route_burden = _mapping(_mapping(case.get("burden")).get("routes"))
    rows = [
        _row("Recorded outcome", [_text(x.get("status")) for x in outcomes]),
        _row("Resolved administrative decision", [
            _text(x.get("decision")) if x.get("status") == "RESOLVED" else "UNKNOWN"
            for x in outcomes
        ]),
        _row("Independently scored correctness", [_quality(quality.get(key)) for key, _ in ROUTES]),
        _row("Supplied histories retained", [
            _count(len(x["candidate_ids"])) if isinstance(x.get("candidate_ids"), list) else "UNKNOWN"
            for x in outcomes
        ]),
        _row("Unresolved cases", [_count(x.get("unresolved_count")) for x in outcomes]),
        _row("Planned follow-up questions", [_planned(manual), "No planner recommendation", _planned(exported)]),
        _row("Recorded questions actually asked", [_count(x.get("recorded_questions_asked")) for x in outcomes]),
        _row("Synthetic questions simulated", [_count(x.get("synthetic_questions_asked")) for x in outcomes]),
        _row("Declared planner effort units", [_cost(manual), "Separate from burden", _cost(exported)]),
        _row("Allocated measured adapter duration", [
            _duration(_mapping(route_burden.get(key)).get("adapter_measured_wall_time_ns"))
            for key, _ in ROUTES
        ]),
        _row("Allocated whole-route wall burden", [
            _allocated_total(route_burden.get(key)) for key, _ in ROUTES
        ]),
    ]
    return (
        "<table><caption>Same handoff: " + _text(case.get("id")) + "</caption><thead><tr>"
        "<th scope=\"col\">Evidence</th>" +
        "".join("<th scope=\"col\">" + label + "</th>" for _, label in ROUTES) +
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _ledger(case):
    burden = _mapping(case.get("burden"))
    totals = _mapping(burden.get("totals"))
    value = totals.get("wall_time_ns")
    complete = totals.get("complete") is True and type(value) is int and value >= 0
    measured = complete and totals.get("observed_complete") is True and totals.get("measurement_class") == "measured"
    if measured:
        total = "Measured pipeline total: " + _duration(value)
    elif complete:
        total = "Recorded pipeline total, including declared or synthetic durations: " + _duration(value)
    else:
        total = "Incomplete timing: total wall burden UNKNOWN."
    parts = [
        "<h4>Burden ledger for the recorded pipeline</h4>",
        "<p>" + total + " This is one pipeline ledger; independent whole-route timings "
        "are not inferred from it. Per-route figures, where supplied, allocate shared work once per "
        "applicable alternative; they are accounting allocations, not independently timed route runs. "
        "No total-burden winner is inferred.</p>",
        "<table><caption>Timing classes remain separate</caption><thead><tr>"
        "<th scope=\"col\">Quantity</th><th scope=\"col\">Recorded value</th>"
        "</tr></thead><tbody>",
        _row("Measured adapter duration", [_duration(totals.get("adapter_measured_wall_time_ns"))]),
        _row("Observed human duration", [_duration(totals.get("observed_human_wall_time_ns"))]),
        _row("Reported duration", [_duration(totals.get("reported_wall_time_ns"))]),
        _row("Synthetic duration", [_duration(totals.get("synthetic_wall_time_ns"))]),
        _row("Known duration subtotal", [_duration(totals.get("known_wall_time_ns"))]),
        _row("Recorded step count", [_count(totals.get("step_count"))]),
        _row("Unknown-duration events", [_count(totals.get("unknown_event_count"))]),
        "</tbody></table>",
    ]
    events = burden.get("events", [])
    if not isinstance(events, list) or len(events) > 512:
        raise ValueError("Handoff report events exceed the declared rendering bounds.")
    parts += [
        "<table><caption>Acquisition, interpretation, model preparation and follow-up</caption>",
        "<thead><tr><th scope=\"col\">Stage</th><th scope=\"col\">Actor</th>"
        "<th scope=\"col\">Measurement</th><th scope=\"col\">Wall time</th>"
        "<th scope=\"col\">Steps</th><th scope=\"col\">Allocated routes</th>"
        "<th scope=\"col\">Evidence source</th></tr></thead><tbody>",
    ]
    for event in events:
        event = _mapping(event)
        parts.append(
            "<tr><td>" + _text(event.get("stage")) + "</td><td>" + _text(event.get("actor")) +
            "</td><td>" + _text(event.get("measurement")) + "</td><td>" +
            _duration(event.get("wall_time_ns")) + "</td><td>" + _count(event.get("step_count")) +
            "</td><td>" + (_text(", ".join(str(x) for x in event["routes"])) if isinstance(event.get("routes"), list) else "Shared declaration") +
            "</td><td>" + _text(event.get("source")) + "</td></tr>"
        )
    if not events:
        parts.append('<tr><td colspan="7">UNKNOWN: no burden events were supplied.</td></tr>')
    parts.append("</tbody></table>")
    return "".join(parts)


def _extraction(case):
    plan = _mapping(case.get("plan"))
    fields = _mapping(_mapping(plan.get("extraction")).get("fields"))
    if len(fields) > 128:
        raise ValueError("Handoff report fields exceed the declared rendering bounds.")
    rows = []
    for name, value in sorted(fields.items()):
        value = _mapping(value)
        rows.append(
            "<tr><td>" + _text(name) + "</td><td>" + _text(value.get("status")) +
            "</td><td>" + (_text(value.get("value")) if value.get("status") == "KNOWN" else "UNKNOWN") +
            "</td></tr>"
        )
    if not rows:
        return "<p>Extracted field observations: UNKNOWN.</p>"
    return (
        "<details><summary>Retained export observations</summary><table>"
        "<thead><tr><th scope=\"col\">Field</th><th scope=\"col\">Evidence state</th>"
        "<th scope=\"col\">Recorded value</th></tr></thead><tbody>" +
        "".join(rows) + "</tbody></table></details>"
    )


def _section(benchmark):
    cases = benchmark.get("cases", [])
    if not isinstance(cases, list) or len(cases) > 64:
        raise ValueError("Handoff report cases exceed the declared rendering bounds.")
    legacy = _mapping(benchmark.get("legacy"))
    parts = [
        START,
        '<section id="handoff-bench" aria-labelledby="handoff-bench-title">',
        '<h2 id="handoff-bench-title">Handoff route comparison</h2>',
        "<p>Recorded bench status: " + _text(benchmark.get("status")) + ". Legacy semantic cases: " +
        _count(legacy.get("exact_cases")) + "/" + _count(legacy.get("total_cases")) + ".</p>",
        "<p>The same supplied histories, decisions and questions feed the unchanged fixed-batch planner. "
        "An export can reduce the supplied history set. A recommended question batch does not resolve a "
        "decision until recorded answers support it. UNKNOWN correctness means no independent scored "
        "decision is available; it is not a pass.</p>",
        "<p>Correctness labels below come from separate frozen synthetic expectations. They do not "
        "establish clinical validity, real-world history completeness, vendor compatibility or market demand.</p>",
        "<h3>Parser coverage</h3><p>The bounded CSV path reads documented Medit case-list items. "
        "PDF, project and native-package manifests retain reviewed observations and artifact bindings. "
        "They do not establish decoding of proprietary 3oxz bytes. STL/PLY scan-only evidence cannot "
        "supply native administrative facts. Missing or undocumented fields remain UNKNOWN.</p>",
    ]
    if not cases:
        parts.append("<p>No handoff case results were supplied.</p>")
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Handoff report cases must be objects.")
        parts.extend([
            "<article><h3>Case " + _text(case.get("id")) + "</h3>",
            "<p>Declared source route: " + _text(_mapping(case.get("plan")).get("source")) + ".</p>",
            _comparison(case),
            _ledger(case),
            _extraction(case),
            "</article>",
        ])
    limits = benchmark.get("limitations", [])
    if not isinstance(limits, list) or len(limits) > 64:
        raise ValueError("Handoff report limitations exceed the declared rendering bounds.")
    if limits:
        parts.append("<h3>Recorded limitations</h3><ul>" + "".join("<li>" + _text(x) + "</li>" for x in limits) + "</ul>")
    parts.extend([
        "<p>Product SHA-256: <code>" + _text(benchmark.get("product_sha256")) + "</code><br>"
        "Evaluation freeze SHA-256: <code>" + _text(benchmark.get("freeze_sha256")) + "</code></p>",
        "</section>",
        END,
    ])
    return "\n".join(parts)


def augment_report(existing_html: str, benchmark: dict) -> str:
    """Insert or replace one static section while preserving surrounding HTML."""
    if not isinstance(existing_html, str) or not isinstance(benchmark, dict):
        raise ValueError("Handoff report expects HTML text and a benchmark object.")
    try:
        original_size = len(existing_html.encode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("Report text must contain Unicode scalar values.") from exc
    if original_size > MAX_REPORT_BYTES:
        raise ValueError("Handoff report exceeds the existing ten-million-byte bound.")
    section = _section(benchmark)
    starts, ends = existing_html.count(START), existing_html.count(END)
    if starts or ends:
        if starts != 1 or ends != 1 or existing_html.index(START) > existing_html.index(END):
            raise ValueError("Handoff report section markers are incomplete or ambiguous.")
        left, remainder = existing_html.split(START, 1)
        _, right = remainder.split(END, 1)
        result = left + section + right
    else:
        closing = re.search(r"</main\s*>", existing_html, re.IGNORECASE)
        if closing is None:
            closing = re.search(r"</body\s*>", existing_html, re.IGNORECASE)
        location = closing.start() if closing is not None else len(existing_html)
        result = existing_html[:location] + section + existing_html[location:]
    if len(result.encode("utf-8")) > MAX_REPORT_BYTES:
        raise ValueError("Handoff report exceeds the existing ten-million-byte bound.")
    return result

