"""Which reply the hub speaks, fed bus events directly."""
from agent_talk.events import EventBus
from agent_talk.speech import Speech
from agent_talk.voice.hub import VoiceHub


class Session:
    def __init__(self):
        self.spoken = []

    def speak(self, segments, task_id=None, approval_id=None):
        self.spoken.append((" ".join(segments), task_id))


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
    assert session.spoken == [("Spoken answer.", 7)]
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
