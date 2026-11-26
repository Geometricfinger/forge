"""One reviewed product entry point for the legacy planner and handoff bench."""
from __future__ import annotations

import argparse
import json
import sys

import planner_core


def main():
    # Preserve the original command's validation, output and objective verbatim.
    if "--mode" not in sys.argv[1:] and not any(arg.startswith("--mode=") for arg in sys.argv[1:]):
        return planner_core.main()
    parser = argparse.ArgumentParser(description="Reviewed finite-model planner and export handoff bench.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--mode", choices=("planner", "handoff"), required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    if args.mode == "planner":
        if args.out is not None:
            parser.error("--out is available only in handoff mode")
        sys.argv = [sys.argv[0], "--input", args.input]
        return planner_core.main()
    import handoff
    try:
        destination = args.out or "handoff-results"
        # Refuse an existing output before doing computation or reading artifacts.
        from pathlib import Path
        output = handoff.no_symlink(Path(destination))
        if output.exists():
            raise handoff.InvalidHandoff("HANDOFF_OUTPUT_EXISTS")
        result = handoff.evaluate_handoff(args.input)
        raw = handoff.publish(result, destination)
        sys.stdout.buffer.write(raw)
        return 0
    except handoff.InvalidHandoff as exc:
        print(json.dumps({"status": "INVALID_HANDOFF", "error_code": str(exc)}, sort_keys=True))
        return 2
    except OSError:
        print(json.dumps({"status": "INVALID_HANDOFF", "error_code": "HANDOFF_IO"}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
