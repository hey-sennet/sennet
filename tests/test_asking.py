"""Voice approvals: hearing the answer."""
import pytest

from agent_talk.voice.asking import answer


@pytest.mark.parametrize("text, expected", [
    ("Yes.", "approve"), ("Yeah, go ahead", None), ("Go ahead.", "approve"), ("OK", "approve"),
    ("No.", "decline"), ("Don't.", "decline"), ("Nope!", "decline"),
    ("Always.", "approve_always"), ("Yes, always.", "approve_always"),
    ("Cancel.", "cancel"), ("Confirm.", "confirm"),
    ("Yes, but run it in the backend instead.", None), ("Maybe.", None), ("", None),
])
def test_only_clear_answers_count(text, expected):
    assert answer(text) == expected
