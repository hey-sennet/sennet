"""The task list: the page starts with the latest, pages through the rest, deletes and searches."""

from conftest import wait_for

from sennet.db import SCHEMA_VERSION, Database
from sennet.search import search


def finished(db: Database, project_id: int, title: str, status: str = "completed", at: float | None = None) -> int:
    task_id = db.add_task(project_id, "codex", title)
    db.update_task(task_id, status=status)
    if at is not None:
        db.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", at, task_id)
    return task_id


def test_the_page_starts_with_unfinished_tasks_and_the_latest_finished(client, project):
    db = client.app.state.db
    for i in range(12):
        finished(db, project["id"], f"old {i}", at=1000 + i)
    waiting = db.add_task(project["id"], "codex", "still queued")
    db.execute("UPDATE tasks SET updated_at = 1 WHERE id = ?", waiting)  # the oldest, but not finished
    state = client.get("/api/state").json()
    assert [t["title"] for t in state["tasks"]] == ["still queued", *(f"old {i}" for i in range(11, 1, -1))]
    assert state["more_tasks"] is True


def test_show_more_pages_through_older_finished_tasks(client, project):
    db = client.app.state.db
    ids = [finished(db, project["id"], f"t{i}", at=1000 + i // 2) for i in range(6)]  # ties on updated_at
    last = db.task(ids[3])
    page = client.get("/api/tasks", params={"before": last["updated_at"], "before_id": last["id"], "limit": 2}).json()
    assert [t["title"] for t in page["tasks"]] == ["t2", "t1"] and page["more"] is True
    oldest = db.task(ids[1])
    page = client.get("/api/tasks", params={"before": oldest["updated_at"], "before_id": oldest["id"]}).json()
    assert [t["title"] for t in page["tasks"]] == ["t0"] and page["more"] is False


def test_a_finished_task_is_deleted_with_everything_in_it(client, project):
    created = client.post("/api/tasks", json={"project_id": project["id"], "text": "hello"}).json()
    wait_for(client, created["id"], lambda t: t["status"] == "completed")
    assert client.delete(f"/api/tasks/{created['id']}").status_code == 204
    assert client.get(f"/api/tasks/{created['id']}").status_code == 404
    db = client.app.state.db
    assert db.query("SELECT * FROM task_messages") == [] and db.query("SELECT * FROM task_events") == []
    assert search(db, "hello") == []
    assert client.delete(f"/api/tasks/{created['id']}").status_code == 404


def test_a_working_task_has_to_stop_before_it_is_deleted(client, project):
    created = client.post("/api/tasks", json={"project_id": project["id"], "text": "hang"}).json()
    wait_for(client, created["id"], lambda t: t["status"] == "running")
    response = client.delete(f"/api/tasks/{created['id']}")
    assert response.status_code == 409 and "stop it" in response.json()["detail"]
    client.post(f"/api/tasks/{created['id']}/cancel")
    wait_for(client, created["id"], lambda t: t["status"] == "cancelled")
    assert client.delete(f"/api/tasks/{created['id']}").status_code == 204


def test_deleting_tells_every_page(client, project):
    db = client.app.state.db
    task_id = finished(db, project["id"], "gone soon")
    with client.websocket_connect("/api/events") as events:
        client.delete(f"/api/tasks/{task_id}")
        while (event := events.receive_json())["type"] != "task_removed":
            pass
    assert event == {"type": "task_removed", "task_id": task_id}


def test_search_finds_part_of_a_word_in_titles_and_messages(client, project):
    db = client.app.state.db
    login = finished(db, project["id"], "Fix the login page", at=1000)
    db.add_message(login, "agent", "The <spoken>authentication</spoken> token expired, so I refresh it now.", "done")
    other = finished(db, project["id"], "Write the README", at=2000)
    db.add_message(other, "user", "mention authentication in the README", "done")
    unrelated = finished(db, project["id"], "Bump the version")
    hits = client.get("/api/search", params={"q": "thentic"}).json()
    assert {h["task"]["id"] for h in hits} == {login, other}
    first = next(h for h in hits if h["task"]["id"] == login)
    assert "<spoken>" not in first["snippet"]
    start, end = first["highlights"][0]
    assert first["snippet"][start:end].lower() == "thentic"
    # Every word has to be there.
    assert [h["task"]["id"] for h in client.get("/api/search", params={"q": "readme auth"}).json()] == [other]
    assert unrelated not in [h["task"]["id"] for h in client.get("/api/search", params={"q": "login"}).json()]


def test_a_typo_still_finds_the_task(client, project):
    db = client.app.state.db
    task_id = finished(db, project["id"], "Speed up the tests")
    db.add_message(task_id, "agent", "The authentication middleware was slow.", "done")
    hits = client.get("/api/search", params={"q": "authentcation"}).json()
    assert [h["task"]["id"] for h in hits] == [task_id]
    snippet, (start, end) = hits[0]["snippet"], hits[0]["highlights"][0]
    assert snippet[start:end] == "authentication"
    assert client.get("/api/search", params={"q": "zebra"}).json() == []
    assert client.get("/api/search", params={"q": "a b"}).json() == []  # nothing three letters long


def test_an_older_database_gets_its_tasks_indexed(paths):
    db = Database(paths.database)
    project = db.add_project("app", "/tmp/app", "codex")
    task_id = db.add_task(project["id"], "codex", "Deploy the website")
    db.add_message(task_id, "agent", "Done, it is live.", "done")
    db.conn.executescript("DROP TABLE search_messages; DROP TABLE search_titles; PRAGMA user_version = 7;")
    db.close()
    db = Database(paths.database)
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert [h["task"]["id"] for h in search(db, "live")] == [task_id]
    assert [h["task"]["id"] for h in search(db, "website")] == [task_id]
    db.close()
