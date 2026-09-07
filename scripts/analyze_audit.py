"""
Read the audit trail and report what looks wrong.

    poetry run python scripts/analyze_audit.py
    poetry run python scripts/analyze_audit.py logs/audit.jsonl --trace a1b2c3
    poetry run python scripts/analyze_audit.py --show a1b2c3

Exits 1 when anything of ERROR severity was found, so it can gate a build.
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.config import settings
from app.core.audit import group_traces, read_events
from app.services.audit_analysis import ERROR, WARNING, INFO, report


def main():
    parser = argparse.ArgumentParser(description="Analyze the audit trail")
    parser.add_argument("path", nargs="?", default=settings.AUDIT_LOG_PATH)
    parser.add_argument("--trace", help="only analyze this trace id")
    parser.add_argument("--show", metavar="TRACE_ID",
                        help="print one trace step by step and stop")
    args = parser.parse_args()

    events = read_events(args.path)
    if not events:
        print(f"No audit events in {args.path}")
        print("Send the bot a message, then run this again.")
        return 0

    if args.show:
        return show_trace(events, args.show)

    if args.trace:
        events = [e for e in events if e.trace_id == args.trace]
        if not events:
            print(f"No trace {args.trace} in {args.path}")
            return 1

    result = report(events)

    print("=" * 74)
    print(f"AUDIT REPORT  -  {args.path}")
    print("=" * 74)
    print(f"traces: {result['traces']}    events: {result['events']}    "
          f"failed events: {result['failed_events']}")

    print("\nOperations")
    print("-" * 74)
    print(f"{'operation':<34}{'count':>7}{'errors':>8}{'avg ms':>10}")
    for name, stat in result["operations"].items():
        print(f"{name:<34}{stat['count']:>7}{stat['errors']:>8}{stat['avg_ms']:>10.1f}")

    counts = result["counts"]
    print(f"\nFindings: {counts[ERROR]} error, {counts[WARNING]} warning, "
          f"{counts[INFO]} info")
    print("-" * 74)

    if not result["findings"]:
        print("Nothing to report.")
    for finding in result["findings"]:
        print(finding)
        if finding.detail:
            print(f"         {finding.detail}")

    if counts[ERROR]:
        print(f"\nInspect one with:  python3 {sys.argv[0]} --show <trace_id>")

    return 1 if counts[ERROR] else 0


def show_trace(events, trace_id):
    """Print a single trace in order, so a turn can be read end to end."""
    traces = group_traces(events)
    matches = [t for t in traces if t.startswith(trace_id)]
    if not matches:
        print(f"No trace starting with {trace_id}")
        return 1

    for match in matches:
        trace_events = traces[match]
        print("=" * 74)
        print(f"TRACE {match}   -   {len(trace_events)} events")
        print("=" * 74)
        for event in trace_events:
            marker = "!!" if event.status == "error" else "  "
            print(f"\n{marker} {event.seq:>2}. {event.operation}  "
                  f"({event.duration_ms:.0f}ms)")
            if event.inputs:
                print(f"      in : {event.inputs}")
            if event.outputs:
                print(f"      out: {event.outputs}")
            if event.error:
                print(f"      ERR: {event.error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
