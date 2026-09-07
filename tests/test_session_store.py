import json

import pytest

from app.services.conversation import append_turn
from app.services.session_store import (
    MAX_DATA_CHARS,
    MAX_HISTORY_MESSAGES,
    pack,
    unpack,
)


def turns(n: int, text: str = "hello") -> list:
    """n messages alternating user and assistant."""
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"{text} {i}"}
        for i in range(n)
    ]


class TestRoundTrip:
    def test_history_and_booking_survive(self):
        history = turns(4)
        booking = {"treatment": "עיסוי שוודי", "date": "2027-06-25", "time": "14:00"}
        back_history, back_booking = unpack(pack(history, booking))
        assert back_history == history
        assert back_booking == booking

    def test_hebrew_is_preserved(self):
        booking = {"treatment": "רפלקסולוגיה"}
        _, back = unpack(pack([], booking))
        assert back["treatment"] == "רפלקסולוגיה"

    def test_empty_state(self):
        assert unpack(pack([], {})) == ([], {})


class TestTrimming:
    def test_history_is_capped(self):
        history, _ = unpack(pack(turns(100), {}))
        assert len(history) <= MAX_HISTORY_MESSAGES

    def test_most_recent_turns_are_the_ones_kept(self):
        history, _ = unpack(pack(turns(100), {}))
        assert history[-1]["content"] == "hello 99"

    def test_long_conversation_still_fits_the_column(self):
        """A write that exceeds the column would fail and lose everything."""
        blob = pack(turns(20, "x" * 600), {"treatment": "y" * 200})
        assert len(blob) <= MAX_DATA_CHARS

    def test_booking_survives_aggressive_trimming(self):
        """The booking matters more than old chatter; it must not be dropped."""
        booking = {"treatment": "עיסוי", "date": "2027-06-25", "time": "14:00"}
        _, back = unpack(pack(turns(20, "x" * 800), booking))
        assert back == booking

    def test_trimming_keeps_pairs_aligned(self):
        """Dropping one message would leave the roles out of step."""
        history, _ = unpack(pack(turns(20, "x" * 500), {}))
        for i, message in enumerate(history):
            assert message["role"] == ("user" if i % 2 == 0 else "assistant")


class TestUnpackIsForgiving:
    @pytest.mark.parametrize("blob", ["", None, "not json", "[]", "null", "123"])
    def test_bad_input_yields_empty_state(self, blob):
        assert unpack(blob) == ([], {})

    def test_missing_keys(self):
        assert unpack(json.dumps({})) == ([], {})

    def test_wrong_types_are_ignored(self):
        assert unpack(json.dumps({"history": "nope", "booking": 5})) == ([], {})


class TestAppendTurn:
    def test_adds_both_sides_of_the_exchange(self):
        history = append_turn([], "היי", "שלום")
        assert history == [
            {"role": "user", "content": "היי"},
            {"role": "assistant", "content": "שלום"},
        ]

    def test_does_not_mutate_the_input(self):
        original = turns(2)
        append_turn(original, "a", "b")
        assert len(original) == 2

    def test_caps_length(self):
        history = turns(MAX_HISTORY_MESSAGES)
        assert len(append_turn(history, "a", "b")) == MAX_HISTORY_MESSAGES

    def test_newest_exchange_is_last(self):
        history = append_turn(turns(MAX_HISTORY_MESSAGES), "newest", "reply")
        assert history[-2]["content"] == "newest"
        assert history[-1]["content"] == "reply"


class TestStateSerialization:
    def test_enum_stores_as_its_value(self):
        """
        str() on a (str, Enum) member yields "ConversationState.IDLE", which
        would never compare equal to the enum on the way back in, silently
        breaking every state check.
        """
        from app.core.enums import ConversationState
        state = ConversationState.CONFIRMING
        stored = getattr(state, "value", state)
        assert stored == "confirming"
        assert stored == ConversationState.CONFIRMING
        assert str(state) != "confirming"  # the trap this guards against

    def test_plain_string_state_passes_through(self):
        assert getattr("idle", "value", "idle") == "idle"
