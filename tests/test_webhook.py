import pytest

from app.api.v1 import webhook
from app.api.v1.webhook import already_processed, extract_messages


@pytest.fixture(autouse=True)
def clear_seen_ids():
    webhook._seen_message_ids.clear()
    yield
    webhook._seen_message_ids.clear()


def payload(*messages):
    return {
        "entry": [{
            "changes": [{
                "value": {"messages": list(messages)}
            }]
        }]
    }


def text_message(id_="wamid.1", from_="972500000000", body="שלום"):
    return {"id": id_, "from": from_, "type": "text", "text": {"body": body}}


class TestDeduplication:
    def test_first_delivery_is_processed(self):
        assert already_processed("wamid.1") is False

    def test_repeat_delivery_is_skipped(self):
        """Meta redelivers until it gets a 2xx - the same message must not book twice."""
        already_processed("wamid.1")
        assert already_processed("wamid.1") is True

    def test_different_messages_are_independent(self):
        already_processed("wamid.1")
        assert already_processed("wamid.2") is False

    def test_missing_id_is_never_treated_as_duplicate(self):
        assert already_processed(None) is False
        assert already_processed("") is False

    def test_memory_is_bounded(self):
        for i in range(webhook.MAX_REMEMBERED_MESSAGES + 50):
            already_processed(f"wamid.{i}")
        assert len(webhook._seen_message_ids) <= webhook.MAX_REMEMBERED_MESSAGES


class TestExtractMessages:
    def test_extracts_a_text_message(self):
        found = extract_messages(payload(text_message(body="היי")))
        assert found == [{"id": "wamid.1", "from": "972500000000", "text": "היי"}]

    def test_extracts_several_messages(self):
        found = extract_messages(payload(
            text_message(id_="wamid.1", body="one"),
            text_message(id_="wamid.2", body="two"),
        ))
        assert [m["text"] for m in found] == ["one", "two"]

    def test_ignores_non_text_messages(self):
        image = {"id": "wamid.9", "from": "972500000000", "type": "image", "image": {}}
        assert extract_messages(payload(image)) == []

    def test_ignores_message_with_empty_body(self):
        assert extract_messages(payload(text_message(body=""))) == []

    @pytest.mark.parametrize("body", [
        {},
        {"entry": []},
        {"entry": [{}]},
        {"entry": [{"changes": []}]},
        {"entry": [{"changes": [{}]}]},
        {"entry": [{"changes": [{"value": {}}]}]},
    ])
    def test_malformed_payloads_do_not_raise(self, body):
        """The old code did body['entry'][0] and raised IndexError on status pings."""
        assert extract_messages(body) == []

    def test_status_update_payload_yields_nothing(self):
        """Delivery receipts arrive on the same webhook and must be ignored."""
        body = {"entry": [{"changes": [{"value": {
            "statuses": [{"id": "wamid.1", "status": "delivered"}]
        }}]}]}
        assert extract_messages(body) == []
