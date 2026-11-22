"""Local context lookup in an operator-selected FORGE mission."""
import re

from research_runner.core import Blocked, digest


def strings(values, limit, item_limit):
    if not isinstance(values, list) or len(values) > limit:
        raise Blocked("INVALID_STRING_LIST")
    for value in values:
        if not isinstance(value, str) or not value.strip() or len(value) > item_limit:
            raise Blocked("INVALID_STRING_LIST")
    return values


def _completed(supervisor, request):
    response = supervisor.execute(request)
    if response["state"] != "COMPLETED":
        raise Blocked(response.get("reason", response["state"]))
    return response


def lookup(supervisor, *, sources, query, anchors, scope):
    """Return a cited text passage. The caller interprets it in the conversation."""
    if not isinstance(query, str) or not query.strip() or len(query) > 400:
        raise Blocked("INVALID_LOOKUP_QUERY")
    needles = []
    for anchor in strings(anchors, 6, 100):
        phrase = r"\s+".join(re.escape(term) for term in anchor.strip().split())
        needles.append(re.compile(r"(?<!\w)" + phrase + r"(?!\w)", re.IGNORECASE))
    # A retry reuses the supervisor's budgeted question, including when only
    # the excerpt filter changes. All source choices come from the host.
    request_id = "ctxq_" + digest([scope, sources, query])
    question = {"id": request_id, "query": query, "intent": "reference", "sources": sources}
    reply = _completed(supervisor, {"action": "ask", "request_id": request_id, "question": question})
    result = reply["result"]
    response = {"status": "NO_MATCH_IN_REVIEWED_PASSAGES", "passage": None,
                "claims_verified": False, "untrusted_content": True, "reviewed": 0,
                "parent_report_sha256": reply["parent_report_sha256"]}
    for hit in result["references"][:3]:
        if hit["source_id"] not in sources:
            raise Blocked("OUT_OF_SCOPE_REFERENCE")
        request = {"action": "inspect", "source_id": hit["source_id"], "evidence_id": hit["id"],
                   "max_chars": 1600}
        request["request_id"] = "ctxi_" + digest([scope, request])
        inspection = _completed(supervisor, request)
        if inspection["parent_report_sha256"] != response["parent_report_sha256"]:
            raise Blocked("MISSION_CHANGED_DURING_LOOKUP")
        part = inspection["result"]
        text = part["text"]
        # A character limit can split the final word and make a prefix look
        # like the requested whole word.
        searchable = re.sub(r"\S+$", "", text) if part["truncated"] else text
        response["reviewed"] += 1
        if not all(pattern.search(searchable) for pattern in needles):
            continue
        response["status"] = "PASSAGE_FOUND"
        response["passage"] = {key: part[key] for key in
                               ("id", "source_id", "source_sha256", "origin", "capture",
                                "path", "start_line", "end_line", "truncated", "text")}
        break
    return response
