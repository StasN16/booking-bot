import pytest

from app.core.audit import AuditEvent
from app.services.audit_analysis import (
    ERROR,
    INFO,
    WARNING,
    analyze,
    analyze_trace,
    report,
)


def event(operation, seq=1, status="ok", inputs=None, outputs=None,
          error=None, duration_ms=10.0, trace_id="t1"):
    return AuditEvent(
        trace_id=trace_id, seq=seq, operation=operation,
        started_at="2027-06-25T10:00:00", duration_ms=duration_ms,
        status=status, inputs=inputs or {}, outputs=outputs or {}, error=error,
    )


def healthy_trace():
    """What a successful turn looks like, for contrast."""
    return [
        event("message.received", 1, inputs={"phone": "***1998", "text": "היי"}),
        event("session.load", 2, outputs={"state": "idle", "history_messages": 0,
                                          "booking_fields": [], "language": "he"}),
        event("clinic.load", 3, outputs={"treatments_chars": 200,
                                         "treatments_empty": False}),
        event("ai.request", 4, outputs={"finish_reason": "stop"}),
        event("ai.decision", 5, outputs={"intention": "greeting",
                                         "next_state": "idle", "extracted": {}}),
        event("session.save", 6, inputs={"state": "idle"}),
        event("whatsapp.send", 7, outputs={"delivered": True}),
        event("message.handled", 8, outputs={"delivered": True}),
    ]


def codes(findings):
    return {f.code for f in findings}


class TestHealthyTrace:
    def test_a_good_turn_produces_no_findings(self):
        assert analyze_trace(healthy_trace()) == []


class TestFailures:
    def test_a_failed_step_is_reported(self):
        events = healthy_trace()
        events[3] = event("ai.request", 4, status="error", error="Timeout")
        findings = analyze_trace(events)
        assert "step_failed" in codes(findings)
        assert any(f.severity == ERROR for f in findings)

    def test_customer_receiving_nothing_is_an_error(self):
        events = [e for e in healthy_trace()
                  if e.operation not in ("whatsapp.send", "message.handled")]
        findings = analyze_trace(events)
        assert "no_reply_sent" in codes(findings)

    def test_undelivered_reply_is_an_error(self):
        events = healthy_trace()
        events[6] = event("whatsapp.send", 7, outputs={"delivered": False},
                          error="401 Unauthorized")
        findings = analyze_trace(events)
        assert "reply_not_delivered" in codes(findings)

    def test_a_trace_that_stops_halfway_is_reported(self):
        events = healthy_trace()[:4]
        assert "trace_incomplete" in codes(analyze_trace(events))

    def test_a_trace_ending_in_recorded_failure_is_complete(self):
        events = healthy_trace()[:4] + [
            event("message.failed", 5, status="error", error="boom"),
        ]
        assert "trace_incomplete" not in codes(analyze_trace(events))


class TestBookingChecks:
    def test_booking_without_a_date_is_reported(self):
        events = healthy_trace() + [
            event("booking.attempt", 9,
                  inputs={"booking": {"treatment": "עיסוי", "time": "14:00"}}),
        ]
        findings = analyze_trace(events)
        assert "booking_missing_fields" in codes(findings)
        assert "date" in str(findings)

    def test_a_complete_booking_passes(self):
        events = healthy_trace() + [
            event("booking.attempt", 9, inputs={"booking": {
                "treatment": "עיסוי", "date": "2027-06-25", "time": "14:00"}}),
        ]
        assert "booking_missing_fields" not in codes(analyze_trace(events))


class TestDataChecks:
    def test_an_empty_clinic_is_reported(self):
        """The business-id mismatch: everything succeeds, nothing is offered."""
        events = healthy_trace()
        events[2] = event("clinic.load", 3, outputs={"treatments_empty": True})
        findings = analyze_trace(events)
        assert "clinic_empty" in codes(findings)
        assert any("BUSINESS_ID" in f.message for f in findings)

    def test_availability_with_no_times_is_a_warning(self):
        events = healthy_trace() + [
            event("availability.lookup", 9, outputs={"returned_text": False},
                  inputs={"booking": {"treatment": "עיסוי"}}),
        ]
        findings = analyze_trace(events)
        assert "availability_empty" in codes(findings)
        assert all(f.severity != ERROR for f in findings)


class TestPerformance:
    def test_a_slow_step_is_flagged(self):
        events = healthy_trace()
        events[3] = event("ai.request", 4, duration_ms=9000)
        findings = analyze_trace(events)
        assert "slow_step" in codes(findings)

    def test_a_fast_step_is_not(self):
        assert "slow_step" not in codes(analyze_trace(healthy_trace()))


class TestQualitySignals:
    def test_fallback_reply_is_flagged(self):
        events = healthy_trace() + [event("reply.fallback_used", 9)]
        assert "fallback_reply" in codes(analyze_trace(events))

    def test_booking_intention_with_nothing_extracted(self):
        events = healthy_trace()
        events[4] = event("ai.decision", 5, outputs={
            "intention": "book", "next_state": "choosing_treatment",
            "extracted": {}})
        assert "nothing_extracted" in codes(analyze_trace(events))

    def test_a_stalled_booking_conversation(self):
        events = healthy_trace()
        events[1] = event("session.load", 2, outputs={"state": "choosing_date"})
        events[4] = event("ai.decision", 5, outputs={"intention": "book",
                                                     "extracted": {"treatment": "x"}})
        events[5] = event("session.save", 6, inputs={"state": "choosing_date"})
        assert "state_stalled" in codes(analyze_trace(events))


class TestAuditSafety:
    """The trail must not become the leak."""

    @pytest.mark.parametrize("leaked", [
        "Bearer abc123", "sk-proj-secret", "EAAGtoken",
    ])
    def test_credentials_in_the_trail_are_reported(self, leaked):
        events = healthy_trace() + [
            event("something", 9, inputs={"header": leaked}),
        ]
        findings = analyze_trace(events)
        assert "sensitive_data_in_audit" in codes(findings)

    def test_a_clean_trail_is_not_flagged(self):
        assert "sensitive_data_in_audit" not in codes(analyze_trace(healthy_trace()))


class TestAnalyzerRobustness:
    def test_an_empty_trace_does_not_raise(self):
        assert analyze_trace([]) == []

    def test_unknown_operations_are_ignored(self):
        assert analyze_trace([event("something.unheard.of", 1)]) == []

    def test_missing_fields_do_not_raise(self):
        """Analyzers meet older events lacking newer fields."""
        events = [
            event("message.received", 1),
            event("availability.lookup", 2),   # no outputs at all
            event("booking.attempt", 3),       # no inputs at all
            event("whatsapp.send", 4),
            event("message.handled", 5),
        ]
        findings = analyze_trace(events)
        assert "analyzer_failed" not in codes(findings)


class TestReport:
    def test_report_counts_traces_and_operations(self):
        events = healthy_trace()
        result = report(events)
        assert result["traces"] == 1
        assert result["events"] == len(events)
        assert "ai.request" in result["operations"]

    def test_report_counts_errors(self):
        events = healthy_trace()
        events[3] = event("ai.request", 4, status="error", error="Timeout")
        result = report(events)
        assert result["failed_events"] == 1
        assert result["counts"][ERROR] >= 1

    def test_findings_are_ordered_by_severity(self):
        events = healthy_trace()
        events[3] = event("ai.request", 4, status="error",
                          error="Timeout", duration_ms=9000)
        findings = report(events)["findings"]
        severities = [f.severity for f in findings]
        assert severities == sorted(
            severities, key=lambda s: {ERROR: 0, WARNING: 1, INFO: 2}[s])

    def test_several_traces_are_analyzed_separately(self):
        first = healthy_trace()
        second = [event(e.operation, e.seq, e.status, e.inputs, e.outputs,
                        e.error, e.duration_ms, trace_id="t2")
                  for e in healthy_trace()]
        assert report(first + second)["traces"] == 2

    def test_average_duration_is_computed(self):
        events = [event("op", 1, duration_ms=100), event("op", 2, duration_ms=200)]
        assert report(events)["operations"]["op"]["avg_ms"] == 150.0
