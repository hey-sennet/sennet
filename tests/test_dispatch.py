"""Direct mode: stop and cancel words, and routing by a leading project name."""
import pytest

from agent_talk.voice.dispatch import Cancel, Request, Stop, parse

PROJECTS = [
    {"id": 1, "name": "backend", "aliases": ["api"]},
    {"id": 2, "name": "web client", "aliases": ["frontend"]},
    {"id": 3, "name": "to-do", "aliases": []},
]


@pytest.mark.parametrize("text", ["Stop.", "stop", "Stop speaking!", "Be quiet."])
def test_stop_words(text):
    assert isinstance(parse(text, PROJECTS), Stop)


@pytest.mark.parametrize("text", ["Cancel.", "Cancel that.", "cancel the task"])
def test_cancel_words(text):
    assert isinstance(parse(text, PROJECTS), Cancel)


def test_stop_inside_a_request_is_a_request():
    assert parse("Stop the dev server before the tests.", PROJECTS) == Request("Stop the dev server before the tests.")


@pytest.mark.parametrize("text, project_id, rest", [
    ("In backend, run the tests.", 1, "Run the tests."),
    ("Backend: run the tests.", 1, "Run the tests."),
    ("API, add a health check.", 1, "Add a health check."),
    ("In back end, run the tests.", 1, "Run the tests."),      # Parakeet's spelling
    ("In the back-end, run the tests.", None, None),           # "the" is not part of the name
    ("For web client, fix the header.", 2, "Fix the header."),
    ("Web client: fix the header.", 2, "Fix the header."),
    ("In frontend.", 2, ""),
    ("To-do, sort the list.", 3, "Sort the list."),
    ("To do, sort the list.", 3, "Sort the list."),
])
def test_a_leading_project_name_routes(text, project_id, rest):
    result = parse(text, PROJECTS)
    if project_id is None:
        assert result == Request(text)
    else:
        assert result == Request(rest, project_id)


@pytest.mark.parametrize("text", [
    "API docs need updating.",
    "To API clients we send JSON, so check the schema.",
    "In backend run the tests.",            # no comma: not sure it's a route
    "Run the tests in backend.",            # the name has to lead
    "Rename the backend folder.",
])
def test_anything_else_is_sent_verbatim(text):
    assert parse(text, PROJECTS) == Request(text)
