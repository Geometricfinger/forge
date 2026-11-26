"""Separate measured, reported, synthetic and unknown handoff work."""
from __future__ import annotations

import copy
import re

ROUTES = ("manual_model", "export_reconciliation", "export_plus_planner")
STAGES = ("acquisition", "interpretation", "model_preparation", "follow_up")
HUMAN_STAGES = STAGES[:3]
ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
MAX_DURATION = 365 * 24 * 60 * 60 * 1_000_000_000


class InvalidBurden(ValueError):
    pass


def validate_events(events):
    if type(events) is not list or len(events) > 64:
        raise InvalidBurden("BURDEN_EVENT_LIMIT")
    ids = set()
    required = {"id", "stage", "actor", "measurement", "wall_time_ns", "step_count", "source"}
    for event in events:
        if type(event) is not dict or not required <= set(event) or set(event) - required - {"routes"}:
            raise InvalidBurden("BURDEN_EVENT_FIELDS")
        if type(event["id"]) is not str or not ID.fullmatch(event["id"]) or event["id"].startswith("runtime_"):
            raise InvalidBurden("BURDEN_EVENT_ID")
        if event["id"] in ids:
            raise InvalidBurden("BURDEN_DUPLICATE_EVENT")
        ids.add(event["id"])
        if event["stage"] not in STAGES or event["actor"] not in ("human", "adapter"):
            raise InvalidBurden("BURDEN_EVENT_KIND")
        if event["measurement"] not in ("observed", "reported", "synthetic", "unknown"):
            raise InvalidBurden("BURDEN_MEASUREMENT")
        ns = event["wall_time_ns"]
        if event["measurement"] == "unknown":
            if ns is not None:
                raise InvalidBurden("BURDEN_UNKNOWN_DURATION")
        elif type(ns) is not int or not 0 <= ns <= MAX_DURATION:
            raise InvalidBurden("BURDEN_DURATION")
        if type(event["step_count"]) is not int or not 1 <= event["step_count"] <= 100_000:
            raise InvalidBurden("BURDEN_STEP_COUNT")
        if type(event["source"]) is not str or not event["source"].strip() or len(event["source"]) > 500:
            raise InvalidBurden("BURDEN_SOURCE")
        if "routes" in event:
            routes = event["routes"]
            if (type(routes) is not list or not routes or len(routes) > len(ROUTES)
                    or any(type(route) is not str or route not in ROUTES for route in routes)
                    or len(set(routes)) != len(routes)):
                raise InvalidBurden("BURDEN_ROUTES")
    return events


def applies(event, route):
    return route in event.get("routes", ROUTES)


def runtime_event(name, stage, duration, source, routes=ROUTES):
    return {"id": "runtime_" + name, "stage": stage, "actor": "adapter",
            "measurement": "observed", "wall_time_ns": max(0, int(duration)),
            "step_count": 1, "source": source, "routes": list(routes)}


def aggregate(events):
    unknown = sum(event["wall_time_ns"] is None for event in events)
    known = sum(event["wall_time_ns"] or 0 for event in events)
    complete = not unknown
    observed_complete = complete and all(event["measurement"] == "observed" for event in events)
    def amount(kind, actor=None):
        return sum(event["wall_time_ns"] or 0 for event in events
                   if event["measurement"] == kind and (actor is None or event["actor"] == actor))
    return {"known_wall_time_ns": known, "wall_time_ns": known if complete else None,
            "complete": complete, "observed_complete": observed_complete,
            "step_count": sum(event["step_count"] for event in events),
            "unknown_event_count": unknown,
            "adapter_measured_wall_time_ns": amount("observed", "adapter"),
            "observed_human_wall_time_ns": amount("observed", "human"),
            "reported_wall_time_ns": amount("reported"),
            "synthetic_wall_time_ns": amount("synthetic"),
            "measurement_class": "incomplete" if not complete else "measured" if observed_complete else "mixed_declared"}


def make_ledger(input_events, runtime_events, comparison, planner_result):
    validate_events(input_events)
    events = copy.deepcopy(input_events) + copy.deepcopy(runtime_events)
    # A route cannot acquire complete human timing merely by omitting a stage.
    for stage in HUMAN_STAGES:
        missing = [route for route in ROUTES if not any(
            event["stage"] == stage and event["actor"] == "human" and applies(event, route)
            for event in input_events)]
        if missing:
            events.append({"id": "runtime_unknown_" + stage, "stage": stage,
                           "actor": "human", "measurement": "unknown", "wall_time_ns": None,
                           "step_count": 0, "source": "Necessary human work was not timed in the supplied record.",
                           "routes": missing})
    unresolved = {route: comparison[route]["unresolved_count"] for route in ROUTES}
    outcome = comparison["export_plus_planner"]
    planned = (planner_result["minimum_question_count"] or 0) if (
        planner_result and planner_result["status"] == "QUESTIONS_FOR_REVIEW") else 0
    phases = {stage: aggregate([event for event in events if event["stage"] == stage]) for stage in STAGES}
    phases["unresolved"] = {"cases_left_without_decision": unresolved, **aggregate([])}
    return {"schema": 1, "events": events, "phases": phases, "totals": aggregate(events),
            "routes": {route: aggregate([event for event in events if applies(event, route)]) for route in ROUTES},
            "follow_up": {"planned_question_count": planned,
                          "recorded_questions_asked": outcome["recorded_questions_asked"],
                          "synthetic_questions_asked": outcome["synthetic_questions_asked"]},
            "unresolved": unresolved,
            "allocation": "An event without routes is a shared declared charge; each route includes it once. Global totals sum executed/declared events once.",
            "scope": "Sequential work-duration sums. Unknown human work is not zero; synthetic and reported durations are not measured savings. Planner cost remains a separate objective.",
            "timing_authenticated": False}
