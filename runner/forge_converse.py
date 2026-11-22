#!/usr/bin/env python3
"""Supply scoped memory, confirmed playback and optional cited context to a local client."""
import argparse
from pathlib import Path
import sys

from conversation_runtime.context import lookup, strings
from conversation_runtime.memory import ConversationMemory
from research_runner.core import Blocked, canonical, no_links, strict_loads
from research_runner.supervise import Supervisor


def read_json(stream):
    data = stream.read(16385)
    if len(data) > 16384:
        raise Blocked("JSON_INPUT_TOO_LARGE")
    result = strict_loads(data)
    if not isinstance(result, dict):
        raise Blocked("EXPECTED_JSON_OBJECT")
    return result


def load_profile(path):
    path = no_links(path)
    with path.open("rb") as stream:
        profile = read_json(stream)
    if set(profile) not in ({"project"}, {"project", "mission"}):
        raise Blocked("INVALID_PROFILE")
    if "mission" in profile:
        mission = profile["mission"]
        if not isinstance(mission, dict) or set(mission) != {"manifest", "source_root", "work", "sources"}:
            raise Blocked("INVALID_MISSION_PROFILE")
        for key in ("manifest", "source_root", "work"):
            if not isinstance(mission[key], str) or not mission[key].strip():
                raise Blocked("INVALID_MISSION_PATH")
            mission[key] = no_links(path.parent / mission[key])
        if not strings(mission["sources"], 32, 80) or len(set(mission["sources"])) != len(mission["sources"]):
            raise Blocked("INVALID_LOOKUP_SOURCES")
    return profile


# Only the host can set the user, project, database and permitted evidence.
# Inputs describe one operation and cannot select another user's store or directory.
FIELDS = {
    "remember": {"key", "value"}, "correct": {"key", "old_value", "new_value"},
    "forget": {"key"}, "clear_scope": set(), "start": {"turn_id"},
    "acknowledge": {"turn_id", "sequence", "text"}, "finish": {"turn_id", "interrupted"},
    "propose": {"proposal_id", "key", "value"}, "preview": {"proposal_id"},
    "decide": {"proposal_id", "accept"},
}


def execute(memory, request, profile, user, channel="host"):
    action = request.get("action")
    if not isinstance(action, str):
        raise Blocked("REQUEST_FIELDS")
    if channel == "speech" and action not in ("context", "propose"):
        raise Blocked("SPEECH_CHANNEL_ACTION")
    if (channel == "review") != (action in ("preview", "decide")):
        raise Blocked("REVIEW_CHANNEL_ACTION")
    if action == "context":
        if set(request) - {"action", "keys", "query", "anchors"}:
            raise Blocked("REQUEST_FIELDS")
        keys = strings(request.get("keys", []), 16, 128)
        evidence = None
        if "query" in request:
            mission = profile.get("mission")
            if mission is None:
                raise Blocked("LOOKUP_NOT_CONFIGURED")
            supervisor = Supervisor(mission["manifest"], mission["source_root"], mission["work"],
                                    Path(__file__).resolve().parents[1])
            evidence = lookup(supervisor, sources=mission["sources"], query=request["query"],
                              anchors=request.get("anchors", []), scope=[user, profile["project"]])
        elif "anchors" in request:
            raise Blocked("ANCHORS_REQUIRE_QUERY")
        snapshot = memory.snapshot(keys)
        return {"facts": snapshot["facts"], "last_turn": snapshot["latest_turn"], "evidence": evidence}
    if not isinstance(action, str) or action not in FIELDS or set(request) != FIELDS[action] | {"action"}:
        raise Blocked("REQUEST_FIELDS")
    if action == "remember":
        memory.remember(request["key"], request["value"])
    elif action == "propose":
        return {"proposal": memory.propose(request["proposal_id"], request["key"], request["value"])}
    elif action == "preview":
        return {"proposal": memory.proposal(request["proposal_id"])}
    elif action == "decide":
        return {"proposal": memory.decide(request["proposal_id"], request["accept"])}
    elif action == "correct":
        return {"updated": memory.correct(request["key"], request["old_value"], request["new_value"])}
    elif action == "forget":
        return {"removed": memory.forget(request["key"])}
    elif action == "clear_scope":
        memory.clear_scope()
    elif action == "start":
        memory.start_turn(request["turn_id"])
    elif action == "acknowledge":
        memory.acknowledge(request["turn_id"], request["sequence"], request["text"])
    else:
        memory.finish_turn(request["turn_id"], interrupted=request["interrupted"])
    return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="Local SQLite memory database")
    parser.add_argument("--user", required=True, help="User identity assigned by the trusted host")
    parser.add_argument("--profile", required=True, type=Path, help="Operator-owned project JSON")
    parser.add_argument("--channel", choices=("host", "speech", "review"), default="host",
                        help="Selected by the trusted host, outside the request")
    args = parser.parse_args()
    try:
        request = read_json(sys.stdin.buffer)
        profile = load_profile(args.profile)
        with ConversationMemory(args.db, args.user, profile["project"]) as memory:
            result = execute(memory, request, profile, args.user, args.channel)
        print(canonical({"ok": True, **result}).decode())
    except Exception as exc:
        reason = str(exc) if isinstance(exc, Blocked) else type(exc).__name__
        print(canonical({"ok": False, "reason": reason}).decode())
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
