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
        assert found == [{"id": "wamid.1", "from": "972500000000", "text": "היי", "to": None}]

    def test_says_which_number_it_was_sent_to(self):
        """Meta names the receiving number in metadata; that is how a clinic is found."""
        body = payload(text_message())
        body["entry"][0]["changes"][0]["value"]["metadata"] = {"phone_number_id": "1072796699244200"}
        assert extract_messages(body)[0]["to"] == "1072796699244200"

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


class TestEachMessageGoesToItsClinic:
    """One server answers for many clinics, each with its own WhatsApp number."""

    @pytest.fixture
    def clinics(self, monkeypatch):
        from app.config import settings
        from app.core import tenancy

        tenancy.forget_channels()
        monkeypatch.setattr(settings, "BUSINESS_ID", "home-clinic")
        monkeypatch.setattr(settings, "WHATSAPP_PHONE_ID", "111")
        monkeypatch.setattr(settings, "WHATSAPP_TOKEN", "server-token")

        async def load():
            tenancy._channels.update({"clinic-b": tenancy.Channel("222", "b-token")})
            tenancy._owners.update({"222": "clinic-b"})
            tenancy._known.update({"home-clinic", "clinic-b"})
            tenancy._last_refresh = __import__("time").monotonic()
        monkeypatch.setattr(tenancy, "refresh_channels", load)
        yield tenancy
        tenancy.forget_channels()

    def post(self, phone_number_id, monkeypatch):
        from fastapi.testclient import TestClient
        from app.core import tenancy
        from app.main import app

        answered = []

        async def handle_message(phone, text):
            answered.append((tenancy.current_business_id(), phone, text))
        monkeypatch.setattr(webhook, "handle_message", handle_message)

        body = payload(text_message())
        body["entry"][0]["changes"][0]["value"]["metadata"] = {"phone_number_id": phone_number_id}
        response = TestClient(app).post("/api/v1/webhook", json=body)
        assert response.status_code == 200
        return answered

    def test_a_clinic_answers_messages_to_its_own_number(self, clinics, monkeypatch):
        assert self.post("222", monkeypatch) == [("clinic-b", "972500000000", "שלום")]

    def test_the_number_in_env_belongs_to_the_home_clinic(self, clinics, monkeypatch):
        assert self.post("111", monkeypatch) == [("home-clinic", "972500000000", "שלום")]

    def test_a_number_no_clinic_has_is_not_answered(self, clinics, monkeypatch):
        """Answering it as some other clinic would put that clinic's data in a stranger's chat."""
        assert self.post("999", monkeypatch) == []

    @pytest.mark.asyncio
    async def test_unknown_numbers_do_not_reload_on_every_message(self, clinics, monkeypatch):
        loads = []

        async def counting_load():
            loads.append(1)
            clinics._last_refresh = __import__("time").monotonic()
        monkeypatch.setattr(clinics, "refresh_channels", counting_load)
        for _ in range(5):
            assert await clinics.business_for_phone_number("999") is None
        assert len(loads) == 1


class TestOnlyMetaCanPostMessages:
    """Online the webhook is public: unsigned or forged posts get nowhere."""

    SECRET = "app-secret-from-meta"

    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi.testclient import TestClient
        from app.config import settings
        from app.core import tenancy
        from app.main import app

        monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", self.SECRET)
        monkeypatch.setattr(settings, "BUSINESS_ID", "home-clinic")
        monkeypatch.setattr(settings, "WHATSAPP_PHONE_ID", "111")
        tenancy.forget_channels()

        async def no_database():
            tenancy._known.add("home-clinic")
            tenancy._last_refresh = __import__("time").monotonic()
        monkeypatch.setattr(tenancy, "refresh_channels", no_database)

        answered = []

        async def handle_message(phone, text):
            answered.append(text)
        monkeypatch.setattr(webhook, "handle_message", handle_message)
        yield TestClient(app), answered
        tenancy.forget_channels()

    def body(self):
        import json
        message = payload(text_message())
        message["entry"][0]["changes"][0]["value"]["metadata"] = {"phone_number_id": "111"}
        return json.dumps(message).encode()

    def sign(self, raw, secret=SECRET):
        import hashlib
        import hmac
        return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()

    def test_a_message_signed_by_meta_is_answered(self, client):
        test_client, answered = client
        raw = self.body()
        response = test_client.post("/api/v1/webhook", content=raw,
                                    headers={"X-Hub-Signature-256": self.sign(raw)})
        assert response.status_code == 200 and answered == ["שלום"]

    def test_an_unsigned_message_is_refused(self, client):
        test_client, answered = client
        response = test_client.post("/api/v1/webhook", content=self.body())
        assert response.status_code == 403 and answered == []

    def test_a_message_signed_with_another_secret_is_refused(self, client):
        test_client, answered = client
        raw = self.body()
        response = test_client.post("/api/v1/webhook", content=raw,
                                    headers={"X-Hub-Signature-256": self.sign(raw, "guessed")})
        assert response.status_code == 403 and answered == []

    def test_a_changed_message_no_longer_matches_its_signature(self, client):
        test_client, answered = client
        raw = self.body()
        signature = self.sign(raw)
        tampered = raw.replace(b"972500000000", b"972599999999")  # someone else's number
        assert tampered != raw
        response = test_client.post("/api/v1/webhook", content=tampered,
                                    headers={"X-Hub-Signature-256": signature})
        assert response.status_code == 403 and answered == []

    def test_without_a_secret_as_on_a_development_machine_it_is_answered(self, client, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", "")
        test_client, answered = client
        assert test_client.post("/api/v1/webhook", content=self.body()).status_code == 200
        assert answered == ["שלום"]


class TestWebhookVerification:
    """Meta's one-time check of the address, with the verify token."""

    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi.testclient import TestClient
        from app.config import settings
        from app.main import app
        monkeypatch.setattr(settings, "WHATSAPP_VERIFY_TOKEN", "my-word")
        return TestClient(app)

    def check(self, client, token, challenge="12345"):
        return client.get("/api/v1/webhook", params={
            "hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": challenge})

    def test_the_right_word_gets_the_challenge_back(self, client):
        response = self.check(client, "my-word")
        assert response.status_code == 200 and response.json() == 12345

    def test_a_wrong_word_is_refused(self, client):
        assert self.check(client, "guess").status_code == 403

    def test_no_word_set_refuses_everyone(self, client, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "WHATSAPP_VERIFY_TOKEN", "")
        assert self.check(client, "").status_code == 403

    def test_a_challenge_that_is_not_a_number_is_refused_not_a_crash(self, client):
        assert self.check(client, "my-word", challenge="abc").status_code == 403
