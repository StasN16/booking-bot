import json

import pytest

from app.core import audit
from app.core.audit import (
    AuditEvent,
    MemorySink,
    group_traces,
    mask_phone,
    read_events,
    record,
    redact,
    set_sink,
    step,
    trace,
)


@pytest.fixture
def sink():
    memory = MemorySink()
    previous = set_sink(memory)
    yield memory
    set_sink(previous)


class TestRedaction:
    @pytest.mark.parametrize("key", [
        "token", "WHATSAPP_TOKEN", "api_key", "apiKey",
        "password", "secret", "authorization", "credential",
    ])
    def test_sensitive_keys_are_replaced(self, key):
        assert redact({key: "hunter2"})[key] == "***redacted***"

    def test_sensitive_keys_are_replaced_when_nested(self):
        out = redact({"config": {"WHATSAPP_TOKEN": "EAAG-real"}})
        assert "EAAG-real" not in json.dumps(out)

    def test_ordinary_values_survive(self):
        payload = {"treatment": "עיסוי שוודי", "price": 280, "active": True}
        assert redact(payload) == payload

    def test_long_text_is_summarized(self):
        out = redact({"reply": "x" * 5000})["reply"]
        assert len(out) < 400
        assert "5000 chars" in out

    def test_long_lists_are_capped(self):
        out = redact({"slots": list(range(100))})["slots"]
        assert len(out) <= audit.MAX_ITEMS + 1
        assert "more" in str(out[-1])

    def test_none_and_numbers_pass_through(self):
        assert redact({"a": None, "b": 1, "c": 1.5}) == {"a": None, "b": 1, "c": 1.5}


class TestMaskPhone:
    def test_keeps_only_the_last_four_digits(self):
        assert mask_phone("972543381998") == "***1998"

    def test_full_number_never_appears(self):
        assert "972543381998" not in mask_phone("972543381998")

    @pytest.mark.parametrize("value", ["", None, "12"])
    def test_short_or_missing_input(self, value):
        assert "***" in mask_phone(value) or mask_phone(value) == ""


class TestRecording:
    def test_events_are_recorded_in_order(self, sink):
        with trace("t"):
            record("first")
            record("second")
            record("third")
        assert [e.operation for e in sink.events] == ["first", "second", "third"]
        assert [e.seq for e in sink.events] == [1, 2, 3]

    def test_events_in_one_trace_share_an_id(self, sink):
        with trace("t"):
            record("a")
            record("b")
        assert len({e.trace_id for e in sink.events}) == 1

    def test_separate_traces_get_separate_ids(self, sink):
        with trace("one"):
            record("a")
        with trace("two"):
            record("b")
        assert len({e.trace_id for e in sink.events}) == 2

    def test_recording_outside_a_trace_is_ignored(self, sink):
        record("orphan")
        assert sink.events == []

    def test_inputs_and_outputs_are_kept(self, sink):
        with trace("t"):
            record("op", inputs={"a": 1}, outputs={"b": 2})
        assert sink.events[0].inputs == {"a": 1}
        assert sink.events[0].outputs == {"b": 2}


class TestStep:
    def test_success_is_recorded_as_ok(self, sink):
        with trace("t"):
            with step("work") as s:
                s.outputs = {"rows": 3}
        assert sink.events[0].status == "ok"
        assert sink.events[0].outputs == {"rows": 3}

    def test_duration_is_measured(self, sink):
        with trace("t"):
            with step("work"):
                sum(range(100000))
        assert sink.events[0].duration_ms >= 0

    def test_exception_is_recorded_and_re_raised(self, sink):
        with pytest.raises(ValueError):
            with trace("t"):
                with step("work"):
                    raise ValueError("boom")

        failed = [e for e in sink.events if e.operation == "work"]
        assert failed[0].status == "error"
        assert "boom" in failed[0].error

    def test_unhandled_exception_closes_the_trace(self, sink):
        with pytest.raises(RuntimeError):
            with trace("t"):
                raise RuntimeError("escaped")
        assert any(e.operation == "trace.unhandled_exception" for e in sink.events)


class TestAuditNeverBreaksTheRequest:
    def test_a_failing_sink_does_not_raise(self):
        class Broken:
            def write(self, event):
                raise IOError("disk full")

        previous = set_sink(Broken())
        try:
            with trace("t"):
                record("op")  # must not raise
        finally:
            set_sink(previous)

    def test_unserializable_values_do_not_raise(self, sink):
        class Weird:
            def __repr__(self):
                return "weird"

        with trace("t"):
            record("op", inputs={"thing": Weird()})
        assert sink.events[0].inputs["thing"] == "weird"


class TestPersistence:
    def test_events_round_trip_through_a_file(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        previous = set_sink(audit.JsonlSink(str(path)))
        try:
            with trace("t"):
                record("op", inputs={"treatment": "עיסוי שוודי"})
        finally:
            set_sink(previous)

        events = read_events(path)
        assert len(events) == 1
        assert events[0].inputs["treatment"] == "עיסוי שוודי"

    def test_unreadable_lines_are_skipped(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        good = AuditEvent("t1", 1, "op", "now", 1.0, "ok")
        path.write_text(good.to_json() + "\nnot json at all\n", encoding="utf-8")
        assert len(read_events(path)) == 1

    def test_missing_file_is_not_an_error(self, tmp_path):
        assert read_events(tmp_path / "nope.jsonl") == []


class TestGrouping:
    def test_events_are_grouped_and_ordered(self):
        events = [
            AuditEvent("a", 2, "second", "now", 1.0, "ok"),
            AuditEvent("b", 1, "other", "now", 1.0, "ok"),
            AuditEvent("a", 1, "first", "now", 1.0, "ok"),
        ]
        traces = group_traces(events)
        assert set(traces) == {"a", "b"}
        assert [e.operation for e in traces["a"]] == ["first", "second"]
