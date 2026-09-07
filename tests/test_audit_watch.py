import logging

import pytest

from app.core.audit import AuditEvent, JsonlSink
from app.services import audit_watch


@pytest.fixture(autouse=True)
def fresh():
    audit_watch.reset()
    yield
    audit_watch.reset()


def write_log(path, events):
    sink = JsonlSink(str(path))
    for e in events:
        sink.write(e)
    return str(path)


def event(operation, seq, trace_id="t1", status="ok", outputs=None,
          error=None, duration_ms=10.0):
    return AuditEvent(trace_id, seq, operation, "2027-06-25T10:00:00",
                      duration_ms, status, {}, outputs or {}, error)


def healthy(trace_id):
    return [
        event("message.received", 1, trace_id),
        event("clinic.load", 2, trace_id, outputs={"treatments_empty": False}),
        event("whatsapp.send", 3, trace_id, outputs={"delivered": True}),
        event("message.handled", 4, trace_id, outputs={"delivered": True}),
    ]


def broken(trace_id):
    return [
        event("message.received", 1, trace_id),
        event("clinic.load", 2, trace_id, outputs={"treatments_empty": False}),
        event("whatsapp.send", 3, trace_id, outputs={"delivered": False},
              error="401 Unauthorized"),
        event("message.handled", 4, trace_id, outputs={"delivered": False}),
    ]


class TestChecking:
    def test_a_healthy_log_reports_nothing(self, tmp_path):
        path = write_log(tmp_path / "a.jsonl", healthy("t1"))
        assert audit_watch.check_recent(path)["error"] == 0

    def test_a_broken_conversation_is_reported(self, tmp_path):
        path = write_log(tmp_path / "a.jsonl", broken("t1"))
        assert audit_watch.check_recent(path)["error"] > 0

    def test_findings_reach_the_log(self, tmp_path, caplog):
        path = write_log(tmp_path / "a.jsonl", broken("t1"))
        with caplog.at_level(logging.ERROR):
            audit_watch.check_recent(path)
        assert "AUDIT" in caplog.text
        assert "t1" in caplog.text

    def test_the_message_says_how_to_investigate(self, tmp_path, caplog):
        path = write_log(tmp_path / "a.jsonl", broken("t1"))
        with caplog.at_level(logging.ERROR):
            audit_watch.check_recent(path)
        assert "analyze_audit.py" in caplog.text


class TestReportedOnce:
    def test_the_same_trace_is_not_reported_twice(self, tmp_path):
        path = write_log(tmp_path / "a.jsonl", broken("t1"))
        first = audit_watch.check_recent(path)
        second = audit_watch.check_recent(path)
        assert first["error"] > 0
        assert second["error"] == 0

    def test_a_new_trace_is_still_reported(self, tmp_path):
        path = tmp_path / "a.jsonl"
        write_log(path, broken("t1"))
        audit_watch.check_recent(str(path))

        write_log(path, broken("t2"))  # appended
        assert audit_watch.check_recent(str(path))["error"] > 0

    def test_memory_is_bounded(self, tmp_path):
        for i in range(audit_watch.MAX_REMEMBERED + 100):
            audit_watch.already_reported(f"trace{i}")
        assert len(audit_watch._reported) <= audit_watch.MAX_REMEMBERED


class TestPriming:
    def test_existing_traces_are_marked_as_seen(self, tmp_path, monkeypatch):
        path = write_log(tmp_path / "a.jsonl", broken("t1"))
        monkeypatch.setattr(audit_watch.settings, "AUDIT_LOG_PATH", path)

        assert audit_watch.prime() == 1
        # A restart must not replay findings that were dealt with already.
        assert audit_watch.check_recent(path)["error"] == 0


class TestResilience:
    def test_a_missing_log_is_not_an_error(self, tmp_path):
        counts = audit_watch.check_recent(str(tmp_path / "nope.jsonl"))
        assert counts["error"] == 0

    def test_a_corrupt_log_does_not_raise(self, tmp_path):
        path = tmp_path / "a.jsonl"
        path.write_text("not json\n{}\n", encoding="utf-8")
        audit_watch.check_recent(str(path))  # must not raise

    def test_info_findings_are_quiet_by_default(self, tmp_path, caplog):
        """Informational notes belong in a report, not in the server window."""
        events = healthy("t1") + [
            event("session.load", 5, "t1", outputs={"state": "choosing_date"}),
            event("ai.decision", 6, "t1", outputs={"intention": "book",
                                                   "extracted": {"treatment": "x"}}),
            event("session.save", 7, "t1"),
        ]
        path = write_log(tmp_path / "a.jsonl", events)
        counts = audit_watch.check_recent(path)
        assert counts["info"] == 0
