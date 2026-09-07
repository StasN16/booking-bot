"""
Checks that read the audit trail and report what looks wrong.

Each analyzer answers one question about a trace. They are deliberately
mechanical: given the events of a turn, does the story hold together? A
customer who got no reply, a booking attempted with no date, a step that
took eight seconds, a trace that stops halfway - all of these are visible
in the trail without knowing anything about the code that produced it.

Run over the log:

    python3 scripts/analyze_audit.py logs/audit.jsonl

The tests run the same analyzers over traces produced by the real code, so
a regression that breaks the flow shows up as a finding rather than as a
silently different log.
"""
import logging
from dataclasses import dataclass, field
from typing import Callable

from app.config import settings
from app.core.audit import AuditEvent, group_traces

logger = logging.getLogger(__name__)

# Severity, most serious first.
ERROR = "error"
WARNING = "warning"
INFO = "info"

SEVERITY_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}


@dataclass
class Finding:
    severity: str
    code: str
    message: str
    trace_id: str = ""
    operation: str = ""
    detail: dict = field(default_factory=dict)

    def __str__(self):
        where = f" [{self.trace_id}]" if self.trace_id else ""
        return f"{self.severity.upper():7} {self.code}{where}: {self.message}"


def op_names(events: list[AuditEvent]) -> list[str]:
    return [e.operation for e in events]


def find(events: list[AuditEvent], operation: str) -> AuditEvent | None:
    for event in events:
        if event.operation == operation:
            return event
    return None


# --- analyzers -------------------------------------------------------------
# Each takes one trace's events and returns the findings it can see.


def failed_steps(events: list[AuditEvent]) -> list[Finding]:
    """Any operation that recorded an error."""
    return [
        Finding(ERROR, "step_failed",
                f"{e.operation} failed: {e.error}",
                e.trace_id, e.operation, {"seq": e.seq})
        for e in events if e.status == "error"
    ]


def customer_got_no_reply(events: list[AuditEvent]) -> list[Finding]:
    """
    A turn that never reached the customer.

    The worst failure the system has: the message was accepted, work was
    done, and the person heard nothing back.
    """
    if not find(events, "message.received"):
        return []

    send = find(events, "whatsapp.send")
    if send is None:
        return [Finding(ERROR, "no_reply_sent",
                        "message handled but nothing was sent to the customer",
                        events[0].trace_id, detail={"operations": op_names(events)})]

    if send.outputs.get("delivered") is False:
        return [Finding(ERROR, "reply_not_delivered",
                        "reply was built but WhatsApp did not accept it",
                        send.trace_id, "whatsapp.send",
                        {"error": send.error})]
    return []


def incomplete_trace(events: list[AuditEvent]) -> list[Finding]:
    """A turn that started and never reached a conclusion."""
    if not find(events, "message.received"):
        return []
    if find(events, "message.handled") or find(events, "message.failed"):
        return []
    return [Finding(ERROR, "trace_incomplete",
                    "trace stops without success or failure being recorded",
                    events[0].trace_id,
                    detail={"last_operation": events[-1].operation})]


def booking_without_required_fields(events: list[AuditEvent]) -> list[Finding]:
    """A booking attempted while something it needs is missing."""
    attempt = find(events, "booking.attempt")
    if attempt is None:
        return []

    booking = attempt.inputs.get("booking") or {}
    missing = [f for f in ("treatment", "date", "time") if not booking.get(f)]
    if not missing:
        return []

    return [Finding(ERROR, "booking_missing_fields",
                    f"booking attempted without {', '.join(missing)}",
                    attempt.trace_id, "booking.attempt",
                    {"missing": missing, "had": sorted(booking.keys())})]


def availability_answered_nothing(events: list[AuditEvent]) -> list[Finding]:
    """
    The customer asked what was free and the reply carried no times.

    Legitimate when the day is genuinely full, so this is a warning: it
    points at where to look, it does not assert a fault.
    """
    lookup = find(events, "availability.lookup")
    if lookup is None or lookup.outputs.get("returned_text"):
        return []
    return [Finding(WARNING, "availability_empty",
                    "availability was asked for but produced no text",
                    lookup.trace_id, "availability.lookup",
                    {"booking": lookup.inputs.get("booking")})]


def clinic_data_empty(events: list[AuditEvent]) -> list[Finding]:
    """
    The bot ran against a clinic with no treatments.

    This is the shape of the business-id mismatch: everything succeeds and
    the customer is told there is nothing on offer.
    """
    clinic = find(events, "clinic.load")
    if clinic is None or not clinic.outputs.get("treatments_empty"):
        return []
    return [Finding(ERROR, "clinic_empty",
                    "no treatments visible; check BUSINESS_ID matches the data",
                    clinic.trace_id, "clinic.load")]


def slow_steps(events: list[AuditEvent], threshold_ms: int = None) -> list[Finding]:
    """Operations slow enough that a customer would notice."""
    threshold = threshold_ms or settings.AUDIT_SLOW_MS
    return [
        Finding(WARNING, "slow_step",
                f"{e.operation} took {e.duration_ms:.0f}ms",
                e.trace_id, e.operation, {"duration_ms": e.duration_ms})
        for e in events if e.duration_ms > threshold
    ]


def fallback_reply_used(events: list[AuditEvent]) -> list[Finding]:
    """The generic error text was sent because nothing better existed."""
    if find(events, "reply.fallback_used") is None:
        return []
    return [Finding(WARNING, "fallback_reply",
                    "customer received the generic error message",
                    events[0].trace_id, "reply.fallback_used")]


def ai_extracted_nothing_repeatedly(events: list[AuditEvent]) -> list[Finding]:
    """
    The model booked an intention but pulled no details out of the message.

    Not wrong on its own, but a booking intention with nothing extracted is
    how a conversation goes in circles.
    """
    decision = find(events, "ai.decision")
    if decision is None:
        return []
    intention = decision.outputs.get("intention")
    extracted = decision.outputs.get("extracted") or {}
    if intention in ("book", "check_availability") and not extracted:
        return [Finding(INFO, "nothing_extracted",
                        f"intention '{intention}' with no details extracted",
                        decision.trace_id, "ai.decision")]
    return []


def state_did_not_advance(events: list[AuditEvent]) -> list[Finding]:
    """
    A booking conversation that ended where it started.

    Repeated across traces for one customer, this is the signature of a
    conversation that cannot progress.
    """
    load = find(events, "session.load")
    save = find(events, "session.save")
    if not load or not save:
        return []

    before, after = load.outputs.get("state"), save.inputs.get("state")
    decision = find(events, "ai.decision")
    intention = decision.outputs.get("intention") if decision else None

    if before == after and intention in ("book", "check_availability"):
        return [Finding(INFO, "state_stalled",
                        f"state stayed at '{after}' during a booking attempt",
                        load.trace_id, detail={"intention": intention})]
    return []


def sensitive_data_written(events: list[AuditEvent]) -> list[Finding]:
    """
    The audit trail itself leaking something it should not.

    Phone numbers must be masked and credentials never written. This checks
    the safety property rather than the application.
    """
    findings = []
    for event in events:
        blob = f"{event.inputs}{event.outputs}"
        # A masked number keeps four digits; a full one has many more.
        for token in ("Bearer ", "sk-", "EAAG"):
            if token in blob:
                findings.append(Finding(
                    ERROR, "sensitive_data_in_audit",
                    f"{event.operation} wrote something credential-shaped",
                    event.trace_id, event.operation, {"marker": token}))
    return findings


ANALYZERS: list[Callable[[list[AuditEvent]], list[Finding]]] = [
    failed_steps,
    customer_got_no_reply,
    incomplete_trace,
    booking_without_required_fields,
    availability_answered_nothing,
    clinic_data_empty,
    slow_steps,
    fallback_reply_used,
    ai_extracted_nothing_repeatedly,
    state_did_not_advance,
    sensitive_data_written,
]


def analyze_trace(events: list[AuditEvent]) -> list[Finding]:
    """Run every analyzer over one trace."""
    findings = []
    for analyzer in ANALYZERS:
        try:
            findings.extend(analyzer(events))
        except Exception as e:
            logger.warning(f"Analyzer {analyzer.__name__} failed: {e}")
            findings.append(Finding(
                WARNING, "analyzer_failed",
                f"{analyzer.__name__} could not run: {e}",
                events[0].trace_id if events else ""))
    return findings


def analyze(events: list[AuditEvent]) -> list[Finding]:
    """Run every analyzer over every trace, most serious first."""
    findings = []
    for trace_events in group_traces(events).values():
        findings.extend(analyze_trace(trace_events))
    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.code))
    return findings


def report(events: list[AuditEvent]) -> dict:
    """A summary of the trail plus everything the analyzers found."""
    traces = group_traces(events)
    findings = analyze(events)

    operations: dict[str, dict] = {}
    for event in events:
        stat = operations.setdefault(
            event.operation, {"count": 0, "errors": 0, "total_ms": 0.0})
        stat["count"] += 1
        stat["errors"] += 1 if event.status == "error" else 0
        stat["total_ms"] += event.duration_ms

    for stat in operations.values():
        stat["avg_ms"] = round(stat["total_ms"] / stat["count"], 1)
        stat["total_ms"] = round(stat["total_ms"], 1)

    return {
        "events": len(events),
        "traces": len(traces),
        "failed_events": sum(1 for e in events if e.status == "error"),
        "operations": dict(sorted(operations.items())),
        "findings": findings,
        "counts": {
            severity: sum(1 for f in findings if f.severity == severity)
            for severity in (ERROR, WARNING, INFO)
        },
    }
