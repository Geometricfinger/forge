"""Operator-invoked, external exact-zipapp transport experiment (Linux only).

This file is not part of Forge and is not a Forge execution command. Only reviewed
products on a trusted host are in scope. Resource supervision is not filesystem,
network, descendant-containment, chronology or execution authentication.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import selectors
import signal
import stat
import subprocess
import sys
import time

PROTOCOL = "external.zipapp.boundaries.v1"
PROFILE_BYTES = 1_000_000
PRODUCT_BYTES = 20_000_000
FIXTURE_BYTES = 1_000_000
IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
HEX = re.compile(r"[0-9a-f]{64}")
LIMIT_BOUNDS = {
    "wall_seconds": (0.05, 30),
    "cpu_seconds": (1, 20),
    "address_space_bytes": (64 * 1024**2, 1024 * 1024**2),
    "file_size_bytes": (1024, 4_000_000),
    "combined_stream_bytes": (128, 1_000_000),
    "total_work_file_bytes": (1024, 8_000_000),
    "max_work_files": (1, 128),
    "open_files": (16, 128),
}
LIMITATIONS = [
    "Reviewed programs on a trusted Linux host only; no hostile-code sandbox.",
    "No filesystem or network isolation; network activity is not measured.",
    "RLIMITs apply per process/file, not to an independently confined workload.",
    "Aggregate work-file bytes/counts are polled and can overshoot before termination.",
    "Original process-group cleanup does not contain descendants that escape that group.",
    "Hashes and local timing establish recorded byte consistency, not authenticated execution or chronology.",
    "Input preservation does not prove that the product consumed the declared input.",
    "JSON projection and export UTF-8 checks are narrow transport observations, not semantic, rendering or clinical validation.",
]


class Refused(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def distinct(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refused("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def finite(value):
    result = float(value)
    if not math.isfinite(result):
        raise Refused("NONFINITE_JSON")
    return result


def reject_constant(value):
    raise Refused("NONFINITE_JSON")


def strict_json(raw):
    return json.loads(raw.decode("utf-8"), object_pairs_hook=distinct,
                      parse_constant=reject_constant, parse_float=finite)


def scalar_strings(value):
    pending = [value]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeError:
                return False
        elif isinstance(value, dict):
            pending.extend(value.keys()); pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return True


def fields(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise Refused("FIELDS")


def relative(value):
    if (type(value) is not str or not value or len(value) > 240 or "\\" in value
            or any(ord(c) < 32 for c in value)):
        raise Refused("RELATIVE_PATH")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value or value == ".":
        raise Refused("RELATIVE_PATH")
    return path


def physical(path):
    path = Path(os.path.abspath(path))
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise Refused("SYMLINK_PATH")
    if any(p.exists() and not p.is_dir() for p in path.parents):
        raise Refused("NONDIRECTORY_ANCESTOR")
    return path


def read_regular(path, maximum):
    path = physical(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise Refused("FILE_BOUNDS_OR_TYPE")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(maximum + 1)
        if len(raw) > maximum:
            raise Refused("FILE_BOUNDS_OR_TYPE")
        return raw
    finally:
        os.close(fd)


def write_new(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)


def json_new(path, value):
    write_new(path, canonical(value) + b"\n")


def reference(path, root):
    raw = read_regular(path, PRODUCT_BYTES)
    return {"path": path.relative_to(root).as_posix(), "bytes": len(raw), "sha256": sha(raw)}


def artifact(row, root):
    fields(row, {"path", "bytes", "sha256"})
    path = root / relative(row["path"])
    if type(row["bytes"]) is not int or not 0 <= row["bytes"] <= FIXTURE_BYTES or not HEX.fullmatch(str(row["sha256"])):
        raise Refused("ARTIFACT_REFERENCE")
    raw = read_regular(path, FIXTURE_BYTES)
    if len(raw) != row["bytes"] or sha(raw) != row["sha256"]:
        raise Refused("FIXTURE_BINDING")
    return raw


def validate_profile(raw, root):
    profile = strict_json(raw)
    fields(profile, {"schema", "protocol", "name", "scope", "limits", "cases"})
    if type(profile["schema"]) is not int or profile["schema"] != 1 or profile["protocol"] != PROTOCOL:
        raise Refused("PROTOCOL")
    if not scalar_strings(profile) or len(canonical(profile)) > PROFILE_BYTES:
        raise Refused("PROFILE_BOUNDS")
    fields(profile["limits"], LIMIT_BOUNDS)
    for name, (low, high) in LIMIT_BOUNDS.items():
        value = profile["limits"][name]
        types = (int, float) if name == "wall_seconds" else (int,)
        if type(value) not in types or not math.isfinite(value) or not low <= value <= high:
            raise Refused("LIMIT_BOUNDS")
    if type(profile["cases"]) is not list or not 1 <= len(profile["cases"]) <= 128:
        raise Refused("CASE_BOUNDS")
    ids = set(); contents = {}; total = 0
    for case in profile["cases"]:
        fields(case, {"id", "arguments", "files", "links", "exports", "projections", "expected"})
        if type(case["id"]) is not str or not IDENT.fullmatch(case["id"]) or case["id"] in ids:
            raise Refused("CASE_ID")
        ids.add(case["id"])
        if (type(case["arguments"]) is not list or len(case["arguments"]) > 64
                or any(type(x) is not str or len(x) > 512 or any(ord(c) < 32 for c in x)
                       or Path(x.split("=", 1)[-1]).is_absolute() or ".." in Path(x).parts
                       for x in case["arguments"])):
            raise Refused("ARGUMENTS")
        if type(case["files"]) is not list or len(case["files"]) > 16:
            raise Refused("FILES")
        paths = set(); copies = {}
        for item in case["files"]:
            fields(item, {"path", "artifact"})
            relative(item["path"])
            if item["path"] in paths:
                raise Refused("PATH_COLLISION")
            paths.add(item["path"])
            data = artifact(item["artifact"], root)
            copies[item["path"]] = data
            total += len(data)
        if total > 8_000_000:
            raise Refused("FIXTURE_TOTAL_BOUNDS")
        if type(case["links"]) is not list or len(case["links"]) > 16:
            raise Refused("LINKS")
        for link in case["links"]:
            fields(link, {"path", "target", "kind"})
            relative(link["path"]); relative(link["target"])
            if link["kind"] not in ("symlink", "hardlink") or link["target"] not in copies or link["path"] in paths:
                raise Refused("LINK")
            paths.add(link["path"])
        if type(case["exports"]) is not list or len(case["exports"]) > 16 or len(set(case["exports"])) != len(case["exports"]):
            raise Refused("EXPORTS")
        for name in case["exports"]:
            relative(name)
        if type(case["projections"]) is not list or len(case["projections"]) > 128:
            raise Refused("PROJECTIONS")
        names = set()
        for projection in case["projections"]:
            if type(projection) is not dict or set(projection) not in ({"name", "path"}, {"name", "path", "measure"}):
                raise Refused("PROJECTION_FIELDS")
            if "measure" in projection and projection["measure"] != "length":
                raise Refused("PROJECTION_MEASURE")
            if (type(projection["name"]) is not str or not IDENT.fullmatch(projection["name"])
                    or projection["name"] in names or type(projection["path"]) is not list
                    or len(projection["path"]) > 16
                    or any(type(v) not in (str, int) or (type(v) is int and v < 0) for v in projection["path"])):
                raise Refused("PROJECTION")
            names.add(projection["name"])
        if len(canonical(case["expected"])) > 64_000:
            raise Refused("EXPECTATION_BOUNDS")
        contents[case["id"]] = copies
    return profile, contents


def file_state(path, maximum):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"kind": "ABSENT", "bytes": None, "sha256": None, "utf8": None}
    if stat.S_ISLNK(info.st_mode):
        return {"kind": "SYMLINK", "target": os.readlink(path), "bytes": None, "sha256": None, "utf8": None}
    if not stat.S_ISREG(info.st_mode):
        return {"kind": "OTHER", "bytes": None, "sha256": None, "utf8": None}
    if info.st_size > maximum:
        return {"kind": "OVER_BUDGET", "bytes": info.st_size, "sha256": None, "utf8": None}
    try:
        raw = read_regular(path, maximum)
    except (OSError, Refused):
        return {"kind": "UNREADABLE", "bytes": info.st_size, "sha256": None, "utf8": None}
    try:
        raw.decode("utf-8"); utf8 = True
    except UnicodeError:
        utf8 = False
    return {"kind": "REGULAR", "bytes": len(raw), "sha256": sha(raw), "utf8": utf8}


def work_inventory(root, limits):
    pending = [root]; files = 0; total = 0
    while pending:
        folder = pending.pop()
        with os.scandir(folder) as entries:
            for entry in entries:
                files += 1
                if files > limits["max_work_files"]:
                    return "WORK_FILE_COUNT_LIMIT", files, total
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(entry.path))
                elif stat.S_ISREG(info.st_mode):
                    total += info.st_size
                elif not stat.S_ISLNK(info.st_mode):
                    return "WORK_FILE_TYPE", files, total
                if total > limits["total_work_file_bytes"]:
                    return "WORK_FILE_BYTE_LIMIT", files, total
    return None, files, total


def kill_group(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
        return "SIGKILL_SENT"
    except ProcessLookupError:
        return "GROUP_NOT_FOUND"
    except OSError as exc:
        return "ERROR_" + type(exc).__name__


def launch(limits, product, arguments):
    """Fixed trusted launcher; set limits before replacing this process."""
    resource.setrlimit(resource.RLIMIT_AS, (limits["address_space_bytes"],) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (limits["cpu_seconds"],) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (limits["file_size_bytes"],) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (limits["open_files"],) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    command = [sys.executable, "-I", "-S", "-B", product, *arguments]
    os.execve(sys.executable, command, {"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})


def supervise(product, arguments, work, folder, limits, launcher):
    command = [sys.executable, "-I", "-S", "-B", str(launcher),
               "_launch", canonical(limits).decode(), str(product), *arguments]
    before = time.monotonic(); start = now()
    metadata = {"termination": None, "exit_code": None, "started_utc": start,
                "elapsed_seconds": None, "usage": None, "streams_truncated": False,
                "stream_bytes_seen_at_least": {"stdout": 0, "stderr": 0},
                "stream_bytes_retained": {"stdout": 0, "stderr": 0},
                "group_cleanup": None, "supervision_error": None, "limits": limits,
                "launcher_command": command,
                "product_command": [sys.executable, "-I", "-S", "-B", str(product), *arguments]}
    selector = selectors.DefaultSelector(); process = None; reaped = False; cleanup_deadline = None
    retained = 0; seen = 0; next_inventory = before
    streams = {name: (folder / (name + ".bin")).open("xb") for name in ("stdout", "stderr")}
    try:
        process = subprocess.Popen(command, cwd=work,
                                   env={"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True, bufsize=0)
        for name in ("stdout", "stderr"):
            pipe = getattr(process, name)
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        while True:
            for key, _ in selector.select(.005):
                chunk = os.read(key.fileobj.fileno(), 65_536)
                if not chunk:
                    selector.unregister(key.fileobj); key.fileobj.close(); continue
                name = key.data; seen += len(chunk)
                metadata["stream_bytes_seen_at_least"][name] += len(chunk)
                room = max(0, limits["combined_stream_bytes"] - retained)
                kept = chunk[:room]
                if kept:
                    streams[name].write(kept); retained += len(kept)
                    metadata["stream_bytes_retained"][name] += len(kept)
                if len(kept) != len(chunk):
                    metadata["streams_truncated"] = True
                if seen > limits["combined_stream_bytes"] and metadata["termination"] is None:
                    metadata["termination"] = "STREAM_BYTE_LIMIT"
            current = time.monotonic()
            if current >= next_inventory and metadata["termination"] is None:
                failure, count, size = work_inventory(work, limits)
                metadata["last_work_inventory"] = {"entries": count, "regular_bytes": size}
                metadata["termination"] = failure
                next_inventory = current + .02
            if current - before > limits["wall_seconds"] and metadata["termination"] is None:
                metadata["termination"] = "WALL_TIME_LIMIT"
            if not reaped:
                pid, status, usage = os.wait4(process.pid, os.WNOHANG)
                if pid:
                    reaped = True; process.returncode = os.waitstatus_to_exitcode(status)
                    metadata["exit_code"] = process.returncode
                    metadata["usage"] = {"cpu_user_seconds": usage.ru_utime,
                        "cpu_system_seconds": usage.ru_stime, "peak_rss_kib": usage.ru_maxrss,
                        "rss_platform_unit": "KiB on Linux", "scope": "wait4 exact launcher/product child"}
                    if metadata["termination"] is None:
                        metadata["termination"] = "EXITED" if process.returncode >= 0 else "SIGNALED"
            if metadata["termination"] is not None and cleanup_deadline is None:
                metadata["group_cleanup"] = kill_group(process.pid)
                cleanup_deadline = current + 2.0
            if reaped and not selector.get_map():
                break
            if cleanup_deadline is not None and current >= cleanup_deadline:
                metadata["termination"] = "CLEANUP_FAILED"
                break
    except Exception as exc:
        metadata["termination"] = "SUPERVISION_ERROR" if process else "SPAWN_ERROR"
        metadata["supervision_error"] = {"type": type(exc).__name__, "reason": str(exc)[:300]}
    finally:
        if process is not None:
            if metadata["group_cleanup"] is None:
                metadata["group_cleanup"] = kill_group(process.pid)
            if not reaped:
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    pid, status, usage = os.wait4(process.pid, os.WNOHANG)
                    if pid:
                        process.returncode = os.waitstatus_to_exitcode(status)
                        metadata["exit_code"] = process.returncode; reaped = True; break
                    time.sleep(.005)
                if not reaped:
                    metadata["termination"] = "CLEANUP_FAILED"
            for name in ("stdout", "stderr"):
                pipe = getattr(process, name)
                if pipe is not None:
                    pipe.close()
        selector.close()
        for stream in streams.values():
            stream.close()
    try:
        physical(work)
        failure, count, size = work_inventory(work, limits)
        metadata["final_work_inventory"] = {"entries": count, "regular_bytes": size, "limit": failure}
    except (OSError, Refused) as exc:
        failure = "WORK_INVENTORY_ERROR"
        metadata["final_work_inventory"] = {"entries": None, "regular_bytes": None, "limit": failure,
                                             "error_type": type(exc).__name__}
    if failure and metadata["termination"] == "EXITED":
        metadata["termination"] = failure
    metadata["elapsed_seconds"] = time.monotonic() - before
    metadata["finished_utc"] = now()
    return metadata


def project(value, path):
    for step in path:
        if type(value) is dict and type(step) is str and step in value:
            value = value[step]
        elif type(value) is list and type(step) is int and step < len(value):
            value = value[step]
        else:
            return {"present": False, "value": None}
    return {"present": True, "value": value}


def projection(value, row):
    result = project(value, row["path"])
    if row.get("measure") == "length":
        if result["present"] and type(result["value"]) in (list, dict, str):
            result["value"] = len(result["value"])
        else:
            result = {"present": False, "value": None}
    return result


def observe(case, raw, meta, before_files, after_files, product_preserved):
    parsed = None; utf8 = False; valid_json = False; scalars = None
    try:
        raw.decode("utf-8"); utf8 = True
        parsed = strict_json(raw); valid_json = True; scalars = scalar_strings(parsed)
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        pass
    projections = {row["name"]: projection(parsed, row) if valid_json else
                   {"present": False, "value": None} for row in case["projections"]}
    return {"termination": meta["termination"], "exit_code": meta["exit_code"],
            "stdout": {"utf8": utf8, "json": valid_json, "unicode_scalars": scalars,
                       "complete": not meta["streams_truncated"], "fields": projections},
            "stderr_empty": meta["stream_bytes_seen_at_least"]["stderr"] == 0,
            "inputs_preserved": all(before_files[name] == after_files[name] for name in before_files),
            "product_preserved": product_preserved,
            "exports": {name: {"kind": after_files[name]["kind"], "utf8": after_files[name]["utf8"]}
                        for name in case["exports"]}}


def run_profile(product, expected_product_sha256, profile_file, out, *, authorized=False):
    if not authorized:
        raise Refused("EXPLICIT_REVIEWED_PRODUCT_ACTION_REQUIRED")
    if not sys.platform.startswith("linux"):
        raise Refused("LINUX_RESOURCE_SUPERVISION_REQUIRED")
    product, profile_file, out = map(physical, (product, profile_file, out))
    if out.exists() or any(p == out or p.is_relative_to(out) for p in (product, profile_file, Path(__file__).resolve())):
        raise Refused("NEW_SEPARATE_OUTPUT_REQUIRED")
    product_raw = read_regular(product, PRODUCT_BYTES)
    if not HEX.fullmatch(str(expected_product_sha256)) or sha(product_raw) != expected_product_sha256:
        raise Refused("PRODUCT_BINDING")
    profile_raw = read_regular(profile_file, PROFILE_BYTES)
    profile, contents = validate_profile(profile_raw, profile_file.parent)
    if any((profile_file.parent / row["artifact"]["path"]).is_relative_to(out)
           for case in profile["cases"] for row in case["files"]):
        raise Refused("NEW_SEPARATE_OUTPUT_REQUIRED")
    harness_raw = read_regular(Path(__file__).resolve(), PROFILE_BYTES)
    started = now()
    out.mkdir(parents=True, mode=0o700)
    write_new(out / "profile.json", profile_raw)
    write_new(out / "harness.py", harness_raw)
    json_new(out / "start.json", {"schema": 1, "protocol": PROTOCOL, "started_utc": started,
             "product": {"bytes": len(product_raw), "sha256": sha(product_raw)},
             "profile_sha256": sha(profile_raw), "harness_sha256": sha(harness_raw),
             "operator_action": "EXPLICIT_REVIEWED_PRODUCT_EXECUTION",
             "runtime_execution_authenticated": False, "release_approved": False})
    results = []
    for case in profile["cases"]:
        folder = out / "cases" / case["id"]; work = folder / "work"
        work.mkdir(parents=True, mode=0o700)
        copy = folder / "product.pyz"; write_new(copy, product_raw)
        for name, raw in contents[case["id"]].items():
            write_new(work / name, raw)
        for link in case["links"]:
            path, target = work / link["path"], work / link["target"]
            path.parent.mkdir(parents=True, exist_ok=True)
            if link["kind"] == "symlink":
                path.symlink_to(os.path.relpath(target, path.parent))
            else:
                os.link(target, path)
        before_files = {name: file_state(work / name, FIXTURE_BYTES) for name in contents[case["id"]]}
        product_before = file_state(copy, PRODUCT_BYTES)
        json_new(folder / "start.json", {"case_id": case["id"], "started_utc": now(),
            "profile_sha256": sha(profile_raw), "product_before": product_before,
            "inputs_before": before_files, "arguments": case["arguments"], "limits": profile["limits"]})
        meta = supervise(copy, case["arguments"], work, folder, profile["limits"], out / "harness.py")
        after_files = {name: file_state(work / name, max(FIXTURE_BYTES, profile["limits"]["file_size_bytes"]))
                       for name in set(before_files) | set(case["exports"])}
        product_after = file_state(copy, PRODUCT_BYTES)
        raw = read_regular(folder / "stdout.bin", profile["limits"]["combined_stream_bytes"])
        observation = observe(case, raw, meta, before_files, after_files, product_before == product_after)
        meta.update({"schema": 1, "protocol": PROTOCOL, "case_id": case["id"],
            "product_before": product_before, "product_after": product_after,
            "inputs_before": before_files, "files_after": after_files,
            "stdout": reference(folder / "stdout.bin", out), "stderr": reference(folder / "stderr.bin", out),
            "profile_sha256": sha(profile_raw), "harness_sha256": sha(harness_raw)})
        json_new(folder / "execution.json", meta)
        json_new(folder / "observation.json", observation)
        results.append({"id": case["id"], "passed": canonical(observation) == canonical(case["expected"]),
                        "expected": case["expected"], "observed": observation,
                        "observation": reference(folder / "observation.json", out),
                        "execution": reference(folder / "execution.json", out)})
    original_after = file_state(product, PRODUCT_BYTES)
    profile_after = file_state(profile_file, PROFILE_BYTES)
    harness_after = file_state(Path(__file__).resolve(), PROFILE_BYTES)
    fixture_sources = {item["artifact"]["path"]: item["artifact"]
                       for case in profile["cases"] for item in case["files"]}
    fixture_after = {name: file_state(profile_file.parent / name, FIXTURE_BYTES) for name in fixture_sources}
    summary = {"schema": 1, "protocol": PROTOCOL, "status": "EXTERNAL_EXPERIMENT_RECORDED",
        "started_utc": started, "finished_utc": now(), "profile": reference(out / "profile.json", out),
        "harness": reference(out / "harness.py", out), "product_sha256": sha(product_raw),
        "product_bytes": len(product_raw), "original_product_preserved": original_after["sha256"] == sha(product_raw),
        "profile_source_preserved": profile_after["sha256"] == sha(profile_raw),
        "harness_source_preserved": harness_after["sha256"] == sha(harness_raw),
        "fixture_sources_preserved": all(fixture_after[name]["sha256"] == item["sha256"]
                                         and fixture_after[name]["bytes"] == item["bytes"]
                                         for name, item in fixture_sources.items()),
        "source_fixture_after": fixture_after,
        "results": results, "counts": {"passed": sum(row["passed"] for row in results), "total": len(results)},
        "runtime_execution_authenticated": False, "execution_input_usage_authenticated": False,
        "chronological_freeze_authenticated": False, "independent_evaluation": False,
        "release_approved": False, "network_isolation": "NOT_ENFORCED", "network_activity": "NOT_MEASURED",
        "limitations": LIMITATIONS}
    json_new(out / "run.json", summary)
    json_new(out / "assessment-case-rows.json", {"scope": "Rows only; bind a corresponding frozen Forge recipe/receipt separately.",
        "cases": [{"id": row["id"], "status": "OBSERVED", "artifact": row["observation"]} for row in results]})
    return summary


def main():
    if len(sys.argv) >= 4 and sys.argv[1] == "_launch":
        launch(strict_json(sys.argv[2].encode()), sys.argv[3], sys.argv[4:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", required=True)
    parser.add_argument("--expected-product-sha256", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--execute-reviewed-product", action="store_true", required=True)
    args = parser.parse_args()
    try:
        result = run_profile(args.product, args.expected_product_sha256, args.profile, args.out,
                             authorized=args.execute_reviewed_product)
    except (OSError, ValueError, RecursionError) as exc:
        print(canonical({"status": "REFUSED", "type": type(exc).__name__, "reason": str(exc)[:300]}).decode())
        return 2
    print(canonical({"status": result["status"], "counts": result["counts"],
                     "product_sha256": result["product_sha256"], "profile_sha256": result["profile"]["sha256"]}).decode())
    return 0 if result["counts"]["passed"] == result["counts"]["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
