"""Which reply the hub speaks, fed bus events directly."""
from sennet.events import EventBus
from sennet.speech import Speech
from sennet.voice.hub import VoiceHub


class Session:
    def __init__(self):
        self.spoken = []
        self.cues = []

    def speak(self, segments, task_id=None, approval_id=None, cue=False):
        self.spoken.append((" ".join(segments), task_id))
        self.cues.append(cue)


def user(task_id, message_id, state):
    return {"type": "message", "task_id": task_id, "message": {"id": message_id, "role": "user", "state": state}}


def agent(task_id, text):
    return {"type": "message", "task_id": task_id, "message": {"id": 0, "role": "agent", "text": text}}


def completed(task_id, status="completed", error=None):
    return {"type": "event", "task_id": task_id,
            "event": {"kind": "turn_completed", "data": {"status": status, "error": error}}}


def hub_with_request(message_id=2):
    speech = Speech.unavailable("test")
    hub = VoiceHub(speech, tasks=None, bus=EventBus())
    session = Session()
    hub.sessions.add(session)
    hub.requests[message_id] = session
    return hub, session, speech


def test_only_the_reply_to_the_spoken_request_is_spoken():
    hub, session, speech = hub_with_request(message_id=2)
    # Message 1 was typed earlier and runs first; message 2 was spoken and queued behind it.
    for event in [user(7, 1, "sent"), agent(7, "Typed answer."), completed(7), user(7, 1, "done"),
                  user(7, 2, "sent"), agent(7, "Thinking…"), agent(7, "**Spoken** answer."), completed(7),
                  user(7, 2, "done")]:
        hub._observe(event)
    assert session.spoken == [("Thinking…", 7), ("Spoken answer.", 7)]
    speech.close()


def test_a_message_is_spoken_as_it_arrives_and_a_failure_after_it():
    hub, session, speech = hub_with_request(message_id=2)
    hub._observe(user(7, 2, "sent"))
    hub._observe(agent(7, "I'll move the email to Trash."))
    assert session.spoken == [("I'll move the email to Trash.", 7)]  # before the turn ends
    hub._observe(completed(7, "failed", "rate limited"))
    hub._observe(user(7, 2, "done"))
    assert session.spoken == [("I'll move the email to Trash.", 7), ("That task failed: rate limited.", 7)]
    speech.close()


def test_a_request_cancelled_while_queued_is_forgotten():
    hub, session, speech = hub_with_request(message_id=2)
    hub._observe(user(7, 2, "cancelled"))
    assert hub.requests == {}
    speech.close()


def test_a_task_that_fails_before_finishing_the_turn_says_so():
    hub, session, speech = hub_with_request(message_id=2)
    hub._observe(user(7, 2, "sent"))
    hub._observe({"type": "task", "task": {"id": 7, "status": "failed", "error": "project directory is gone"}})
    assert session.spoken == [("That task failed: project directory is gone.", 7)]
    speech.close()


def test_a_disconnected_session_hears_nothing():
    hub, session, speech = hub_with_request(message_id=2)
    hub._observe(user(7, 2, "sent"))
    hub.sessions.clear()
    hub._observe(completed(7))
    hub._observe(user(7, 2, "done"))
    assert session.spoken == []
    speech.close()


def delta(task_id, text, item_id="msg1"):
    return {"type": "delta", "task_id": task_id, "item_id": item_id, "delta": text}


REPLY = ("<spoken>The retry is in, and all 12 tests pass. Should it add a backoff limit?</spoken>\n\n"
         "I updated `src/app.py` to retry failed requests.\n\n- `tests/test_retry.py`: 4 new tests\n\n"
         "All 12 tests pass. Should I also add a backoff limit?")
HEARD = "The retry is in, and all 12 tests pass. Should it add a backoff limit?"


def test_the_spoken_version_is_heard_as_soon_as_it_streams_and_only_once():
    hub, session, speech = hub_with_request(message_id=2)
    hub._observe(user(7, 2, "sent"))
    for i in range(0, len(REPLY), 7):   # in pieces that split the tags
        hub._observe(delta(7, REPLY[i:i + 7]))
        if i + 7 < REPLY.index("</spoken>") + len("</spoken>"):
            assert session.spoken == []
    assert session.spoken == [(HEARD, 7)]  # before the message ends
    hub._observe(agent(7, REPLY))
    hub._observe(completed(7))
    hub._observe(user(7, 2, "done"))
    assert session.spoken == [(HEARD, 7)]
    assert hub.streams == {} and hub.early == {}
    speech.close()


def test_a_spoken_version_that_was_not_streamed_is_heard_with_the_message():
    hub, session, speech = hub_with_request(message_id=2)
    hub._observe(user(7, 2, "sent"))
    hub._observe(agent(7, REPLY))
    assert session.spoken == [(HEARD, 7)]
    speech.close()


def test_a_message_without_a_spoken_version_waits_for_its_end():
    hub, session, speech = hub_with_request(message_id=2)
    hub._observe(user(7, 2, "sent"))
    for word in "I'll look at the <spoken> tests.".split(" "):
        hub._observe(delta(7, word + " "))
    assert session.spoken == [] and hub.streams[7]["msg1"] is None
    hub._observe(agent(7, "I'll look at the tests."))
    assert session.spoken == [("I'll look at the tests.", 7)]
    speech.close()


def agent_turn(task_id):
    return {"type": "event", "task_id": task_id, "event": {"kind": "turn_started", "data": {"by": "agent"}}}


def test_a_turn_the_agent_starts_is_spoken_on_the_owner_after_a_chime():
    hub, session, speech = hub_with_request()
    hub.owners[7] = session
    hub._observe(agent_turn(7))
    hub._observe(agent(7, "The build finished."))
    hub._observe(agent(7, "All 12 tests pass."))
    hub._observe(completed(7))
    assert session.spoken == [("The build finished.", 7), ("All 12 tests pass.", 7)]
    assert session.cues == [True, False]
    assert 7 not in hub.turns
    hub._observe(agent(7, "Late."))  # after the turn: not spoken
    assert len(session.spoken) == 2
    speech.close()


def test_an_agent_turn_without_an_owner_is_only_shown():
    hub, session, speech = hub_with_request()
    hub._observe(agent_turn(7))
    hub._observe(agent(7, "The build finished."))
    hub._observe(completed(7))
    assert session.spoken == []
    speech.close()


def test_an_agent_turn_that_fails_says_so():
    hub, session, speech = hub_with_request()
    hub.owners[7] = session
    hub._observe(agent_turn(7))
    hub._observe(completed(7, "failed", "rate limited"))
    assert session.spoken == [("That task failed: rate limited.", 7)]
    assert session.cues == [True]
    speech.close()
