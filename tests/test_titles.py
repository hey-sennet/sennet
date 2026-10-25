"""Task titles: cleaned from what was said, then the agent's own, taken out of its reply."""
import pytest

from sennet import titles
from conftest import wait_for


@pytest.mark.parametrize("said, title", [
    ("Um another agent tried to put a rule in", "Another agent tried to put a rule in"),
    ("Okay, I just did a fresh install from uh UV tool", "I just did a fresh install from UV tool"),
    ("So the build fails\nand here's the log", "The build fails"),
    ("Sorting is broken", "Sorting is broken"),
    ("Make it 5 mm wider", "Make it 5 mm wider"),
    ("um", "um"),
    ("x" * 100, "x" * 77 + "…"),
    ("fix the build", "fix the build"),
])
def test_a_title_from_what_was_said_drops_the_fillers(said, title):
    assert titles.from_request(said) == title


def test_the_agents_title_comes_out_of_its_message():
    assert titles.take("<sennet-title>Fix the build.</sennet-title>\n\n<spoken>Done.</spoken> More") == \
        ("Fix the build", "<spoken>Done.</spoken> More")
    assert titles.take("No title here <b>bold</b>") == (None, "No title here <b>bold</b>")
    assert titles.take("<sennet-title>Only</sennet-title>") == ("Only", "")


def test_a_streamed_title_is_held_back_until_its_tag_closes():
    stream = titles.Stream()
    fed = [stream.feed(d) for d in ["\n<sen", "net-title>Fix ", "it</sennet-ti", "tle>\n\nI'll", " look"]]
    assert fed == [(None, ""), (None, ""), (None, ""), ("Fix it", "I'll"), (None, " look")]


def test_streamed_text_without_a_title_is_passed_on():
    stream = titles.Stream()
    assert [stream.feed(d) for d in [" ", "<b>bold", "</b>"]] == [(None, ""), (None, " <b>bold"), (None, "</b>")]
    unclosed = titles.Stream()
    assert unclosed.feed("<sennet-title>" + "x" * titles.HOLD_CHARS) == (None, "<sennet-title>" + "x" * titles.HOLD_CHARS)


def test_the_agent_renames_the_task_once_and_its_title_is_never_shown(client, project):
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "title:Fix the login page"}).json()
    assert task["title"] == "title:Fix the login page"
    events = []
    with client.websocket_connect("/api/events") as socket:
        task = wait_for(client, task["id"], lambda t: t["status"] == "completed")
        assert task["title"] == "Fix the login page"
        assert task["messages"][-1]["text"] == "On it, done."
        # A later title, from a follow-up, doesn't rename it again.
        client.post(f"/api/tasks/{task['id']}/messages", json={"text": "title:Something else"})
        while not (events and events[-1].get("type") == "task" and events[-1]["task"]["status"] == "completed"
                   and len([e for e in events if e.get("type") == "message" and e["message"]["role"] == "agent"]) == 1):
            events.append(socket.receive_json())
    streamed = "".join(e["delta"] for e in events if e.get("type") == "delta")
    assert "sennet-title" not in streamed and "On it, done." in streamed
    assert client.get(f"/api/tasks/{task['id']}").json()["title"] == "Fix the login page"
