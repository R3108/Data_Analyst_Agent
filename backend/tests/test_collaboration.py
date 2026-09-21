"""Comments, the activity log, and the identity the API attributes them to."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.errors import InvalidInputError, NotFoundError
from app.db import Database
from app.main import create_app
from tests.conftest import sign_in
from app.services.activity import ActivityService
from app.services.comments import CommentService


@pytest.fixture
def db(settings) -> Database:
    return Database(settings.database_path)


@pytest.fixture
def comments(db) -> CommentService:
    return CommentService(db)


@pytest.fixture
def activity(db) -> ActivityService:
    return ActivityService(db)


# --------------------------------------------------------------------------- comments


def test_a_thread_collects_its_replies(comments):
    root = comments.add("board", "brd_1", "Is this the net or gross figure?", "Ada")
    comments.add("board", "brd_1", "Net — refunds are excluded.", "Grace", parent_id=root["id"])

    listing = comments.list("board", "brd_1")
    assert len(listing["threads"]) == 1
    assert len(listing["threads"][0]["replies"]) == 1
    assert listing["open_count"] == 1
    assert listing["total"] == 2


def test_replies_are_flattened_to_one_level(comments):
    root = comments.add("session", "ses_1", "Top comment", "Ada")
    reply = comments.add("session", "ses_1", "A reply", "Grace", parent_id=root["id"])
    nested = comments.add("session", "ses_1", "A reply to the reply", "Ada",
                          parent_id=reply["id"])

    # A reply to a reply belongs to the same thread, not a new one.
    assert nested["parent_id"] == root["id"]
    assert len(comments.list("session", "ses_1")["threads"]) == 1


def test_resolving_a_thread_resolves_its_replies(comments):
    root = comments.add("board", "brd_1", "Question", "Ada")
    comments.add("board", "brd_1", "Answer", "Grace", parent_id=root["id"])

    comments.update(root["id"], resolved=True)
    listing = comments.list("board", "brd_1")
    assert listing["threads"][0]["resolved"] is True
    assert all(reply["resolved"] for reply in listing["threads"][0]["replies"])
    assert listing["open_count"] == 0


def test_resolving_from_a_reply_resolves_the_whole_thread(comments):
    root = comments.add("board", "brd_1", "Question", "Ada")
    reply = comments.add("board", "brd_1", "Answer", "Grace", parent_id=root["id"])

    comments.update(reply["id"], resolved=True)
    assert comments.list("board", "brd_1")["threads"][0]["resolved"] is True


def test_deleting_a_thread_takes_its_replies_with_it(comments):
    root = comments.add("board", "brd_1", "Question", "Ada")
    comments.add("board", "brd_1", "Answer", "Grace", parent_id=root["id"])

    comments.delete(root["id"])
    assert comments.list("board", "brd_1")["total"] == 0


def test_unresolved_counts_come_back_for_many_subjects_at_once(comments):
    comments.add("board_item", "itm_1", "One", "Ada")
    comments.add("board_item", "itm_1", "Two", "Ada")
    resolved = comments.add("board_item", "itm_2", "Three", "Ada")
    comments.update(resolved["id"], resolved=True)

    counts = comments.counts("board_item", ["itm_1", "itm_2", "itm_3"])
    assert counts == {"itm_1": 2}


def test_comments_validate_their_input(comments):
    with pytest.raises(InvalidInputError, match="cannot be empty"):
        comments.add("board", "brd_1", "   ", "Ada")
    with pytest.raises(InvalidInputError, match="Comments attach to"):
        comments.add("galaxy", "g_1", "Hello", "Ada")
    with pytest.raises(InvalidInputError, match="limited to"):
        comments.add("board", "brd_1", "x" * 5000, "Ada")
    with pytest.raises(NotFoundError):
        comments.update("cmt_missing", body="edited")


def test_a_reply_cannot_be_moved_to_another_subject(comments):
    root = comments.add("board", "brd_1", "Question", "Ada")
    with pytest.raises(InvalidInputError, match="different discussion"):
        comments.add("board", "brd_2", "Sneaky", "Eve", parent_id=root["id"])


# --------------------------------------------------------------------------- activity


def test_activity_summarises_an_event_in_words(activity):
    entry = activity.record("dataset.upload", actor="Ada", subject_kind="dataset",
                            subject_id="ds_1", subject_title="Retail Sales v2",
                            detail="1,000 rows")
    assert entry["summary"] == "Ada uploaded Retail Sales v2"
    assert entry["icon"] == "upload"


def test_activity_is_newest_first_and_filterable(activity):
    activity.record("dataset.upload", actor="Ada", subject_id="ds_1", subject_title="A")
    activity.record("board.pin", actor="Grace", subject_id="brd_1", subject_title="B")

    entries = activity.list()
    assert entries[0]["action"] == "board.pin"
    assert [e["subject_id"] for e in activity.list(subject_id="ds_1")] == ["ds_1"]


def test_an_unknown_action_still_renders(activity):
    entry = activity.record("something.new", actor="Ada", subject_title="a thing")
    assert "Ada" in entry["summary"]
    assert entry["icon"] == "dot"


def test_recording_activity_never_raises(monkeypatch, activity):
    def explode(*args, **kwargs):
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(activity.db, "add_activity", explode)
    # An audit line must never be able to fail the action it describes.
    assert activity.record("dataset.upload", actor="Ada") is None


# --------------------------------------------------------------------------- attribution


def test_the_workspace_reports_itself_as_protected_and_isolated(settings):
    with TestClient(create_app(settings)) as client:
        sign_in(client, email="ada@example.com", name="Ada Lovelace")
        workspace = client.get("/api/health").json()["workspace"]
        assert workspace == {"protected": True, "isolation": "per-user database"}
        assert client.get("/api/me").json()["name"] == "Ada Lovelace"
        assert client.get("/api/datasets").status_code == 200


def test_a_comment_is_attributed_to_the_signed_in_account(settings):
    with TestClient(create_app(settings)) as client:
        sign_in(client, email="ada@example.com", name="Ada Lovelace")
        created = client.post("/api/comments", json={
            "subject_kind": "board", "subject_id": "brd_1", "body": "Looks right to me.",
            "author": "Someone Else",
        }).json()
        # The session decides the name; a client-supplied one cannot override it.
        assert created["author"] == "Ada Lovelace"
