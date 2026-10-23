"""Where open-mic turns start and end (voice/endpoint.py), frame by frame."""
from sennet.voice.endpoint import CHECK_FRAMES, FALLBACK_FRAMES, GRACE_FRAMES, Endpointer


def feed(endpoint: Endpointer, pattern: str, over_reply: bool = False) -> list[str]:
    """Feed frames written as S (speech) and . (silence); returns the events in order."""
    events = []
    for frame in pattern:
        if event := endpoint.frame(frame == "S", over_reply):
            events.append(event)
    return events


def test_a_short_sound_does_not_open_a_turn():
    endpoint = Endpointer()
    assert feed(endpoint, "SSS....." * 5) == []  # clicks, a cough
    assert endpoint.state == "waiting"


def test_sustained_speech_opens_a_turn_and_says_where_it_began():
    endpoint = Endpointer()
    assert feed(endpoint, "...SSSSSS") == ["start"]
    assert endpoint.start_back == 6


def test_a_pause_asks_smart_turn_once_and_a_finished_turn_ends():
    endpoint = Endpointer()
    feed(endpoint, "SSSSSS")
    assert feed(endpoint, "." * CHECK_FRAMES) == ["check"]
    assert endpoint.checked(0.9, endpoint.pause) == "end"
    assert feed(endpoint, "." * GRACE_FRAMES) == ["settled"]
    assert endpoint.state == "waiting"


def test_an_unfinished_turn_waits_for_the_fallback():
    endpoint = Endpointer()
    feed(endpoint, "SSSSSS")
    assert feed(endpoint, "." * CHECK_FRAMES) == ["check"]
    assert endpoint.checked(0.2, endpoint.pause) is None
    # Never asked again in the same pause; the silence alone ends it.
    assert feed(endpoint, "." * (FALLBACK_FRAMES - CHECK_FRAMES)) == ["end"]


def test_speech_resuming_within_the_grace_reopens_the_turn():
    endpoint = Endpointer()
    feed(endpoint, "SSSSSS" + "." * CHECK_FRAMES)
    endpoint.checked(0.9, endpoint.pause)
    assert feed(endpoint, "..SSS") == ["resume"]
    assert endpoint.state == "turn"
    assert feed(endpoint, "." * CHECK_FRAMES) == ["check"]  # the next pause is checked afresh


def test_speech_after_the_grace_is_a_new_turn():
    endpoint = Endpointer()
    feed(endpoint, "SSSSSS" + "." * CHECK_FRAMES)
    endpoint.checked(0.9, endpoint.pause)
    assert feed(endpoint, "." * GRACE_FRAMES + "SSSSSS") == ["settled", "start"]


def test_a_late_answer_for_an_earlier_pause_is_ignored():
    endpoint = Endpointer()
    feed(endpoint, "SSSSSS" + "." * CHECK_FRAMES)
    first = endpoint.pause
    feed(endpoint, "SS" + "." * CHECK_FRAMES)
    assert endpoint.checked(0.9, first) is None
    assert endpoint.checked(0.9, endpoint.pause) == "end"


def test_over_a_reply_less_speech_opens_a_turn_which_ends_as_any_other():
    endpoint = Endpointer()
    assert feed(endpoint, "SSSSS", over_reply=True) == ["start"]
    assert feed(endpoint, "." * CHECK_FRAMES) == ["check"]
    assert endpoint.checked(0.9, endpoint.pause) == "end"


def test_a_held_turn_ignores_pauses_until_released():
    endpoint = Endpointer()
    endpoint.hold()
    assert feed(endpoint, "SS" + "." * (FALLBACK_FRAMES * 2)) == []
    endpoint.release()
    assert endpoint.state == "waiting"


def test_holding_during_the_grace_takes_the_turn_back():
    endpoint = Endpointer()
    feed(endpoint, "SSSSSS" + "." * CHECK_FRAMES)
    endpoint.checked(0.9, endpoint.pause)
    endpoint.hold()
    assert endpoint.state == "turn"
    assert feed(endpoint, "." * FALLBACK_FRAMES) == []
