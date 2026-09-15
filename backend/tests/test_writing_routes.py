"""The six /writing routes, as behaviour (M4 phase 4).

test_isolation.py already proves one account cannot reach another's documents.
This file is the other half: that the routes do what the notepad needs when
the caller *is* the owner.

Most of it is ordinary CRUD. Two things are not, and both are documented
decisions in routers/writing.py that a well-meaning refactor would undo:

  * autosave treats an omitted `title` or `template` as "leave it alone",
    not as "set it to null". A client that has not touched the dropdown must
    not blank a choice the writer made earlier.
  * DELETE answers with a body rather than a 204, because api.js parses every
    successful response as JSON — and a repeat delete 404s rather than
    pretending to succeed.
"""

from __future__ import annotations

import pytest

ESSAY = "The first paragraph of something someone is actually writing."


@pytest.fixture
def writer(api, new_user):
    """A logged-in account plus helpers bound to it."""
    _, headers = new_user("writing")

    class Writer:
        def create(self, **body):
            body.setdefault("content", ESSAY)
            return api.post("/writing/documents", headers=headers, json=body)

        def autosave(self, **body):
            body.setdefault("content", ESSAY)
            return api.patch("/writing/autosave", headers=headers, json=body)

        def get(self, document_id):
            return api.get(f"/writing/documents/{document_id}", headers=headers)

        def delete(self, document_id):
            return api.delete(f"/writing/documents/{document_id}", headers=headers)

        def list(self):
            return api.get("/writing/documents", headers=headers)

        def draft(self):
            return api.get("/writing/autosave", headers=headers)

    return Writer()


# ── documents ──────────────────────────────────────────────────────────

def test_creating_a_document_returns_the_whole_thing(writer):
    """Not just the id.

    The client switches to editing this copy, so it needs the title the
    server settled on and the timestamps to baseline autosave against.
    """
    response = writer.create(title="Lab report")
    assert response.status_code == 201

    body = response.json()
    assert body["id"]
    assert body["title"] == "Lab report"
    assert body["content"] == ESSAY
    assert body["created_at"] and body["updated_at"]


def test_a_document_saved_without_a_title_gets_a_dated_one(writer):
    """'Untitled - 12 Aug 2026', so the library never shows a blank row."""
    body = writer.create().json()
    assert body["title"].startswith("Untitled")
    assert body["title"].strip() != "Untitled", "the date half is missing"


def test_an_empty_document_is_refused(writer):
    """400 rather than an empty row nobody asked for."""
    assert writer.create(content="   ").status_code == 400


def test_a_document_round_trips(writer):
    document_id = writer.create(title="Round trip").json()["id"]

    fetched = writer.get(document_id)
    assert fetched.status_code == 200
    assert fetched.json()["content"] == ESSAY
    assert fetched.json()["title"] == "Round trip"


def test_the_list_carries_a_word_count_but_not_the_content(writer):
    """A long library has to stay a small response."""
    writer.create(title="Listed", content="one two three four five")

    response = writer.list()
    assert response.status_code == 200

    row = next(r for r in response.json() if r["title"] == "Listed")
    assert row["word_count"] == 5
    assert "content" not in row, "the list is sending full documents"


def test_deleting_answers_with_a_body_and_is_not_repeatable(writer):
    """204 would break api.js, which parses every success as JSON."""
    document_id = writer.create(title="Doomed").json()["id"]

    deleted = writer.delete(document_id)
    assert deleted.status_code == 200
    assert deleted.json() == {"status": "deleted", "id": document_id}

    assert writer.delete(document_id).status_code == 404, (
        "a second delete reported success for a document that is already gone"
    )
    assert writer.get(document_id).status_code == 404


def test_an_unknown_document_is_a_404(writer):
    assert writer.get("00000000-0000-0000-0000-000000000000").status_code == 404


# ── autosave ───────────────────────────────────────────────────────────

def test_autosave_without_an_id_creates_a_document(writer):
    response = writer.autosave(content="Fresh work.")
    assert response.status_code == 200

    document_id = response.json()["id"]
    assert writer.get(document_id).json()["content"] == "Fresh work."


def test_autosave_with_an_id_updates_that_document(writer):
    document_id = writer.create(title="Draft").json()["id"]

    writer.autosave(document_id=document_id, content="Second version.")

    assert writer.get(document_id).json()["content"] == "Second version."


def test_autosave_does_not_create_a_row_for_an_untouched_page(writer):
    """A page that was opened and never typed into leaves nothing behind."""
    assert writer.autosave(content="").status_code == 400


def test_an_omitted_title_leaves_the_stored_one_alone(writer):
    """"Not supplied" means "no change", not "clear it".

    The failure this prevents: a client that autosaves content without
    echoing the title back would silently rename every document to Untitled.
    """
    document_id = writer.create(title="Carefully named").json()["id"]

    writer.autosave(document_id=document_id, content="More text.")

    assert writer.get(document_id).json()["title"] == "Carefully named"


def test_an_omitted_template_leaves_the_stored_one_alone(writer):
    """Same rule for the structure template chosen from the dropdown."""
    document_id = writer.create(title="Essay", template="essay").json()["id"]

    writer.autosave(document_id=document_id, content="More text.")

    assert writer.get(document_id).json()["template"] == "essay", (
        "autosaving without a template blanked the writer's earlier choice"
    )


def test_a_blank_title_falls_back_rather_than_saving_empty(writer):
    """`title.strip() or DEFAULT_TITLE` - a cleared field is not a valid name."""
    document_id = writer.create(title="Named").json()["id"]

    writer.autosave(document_id=document_id, content="Text.", title="   ")

    assert writer.get(document_id).json()["title"] == "Untitled"


def test_an_unknown_template_is_rejected(writer):
    assert writer.create(template="haiku").status_code == 422


# ── the restored draft ─────────────────────────────────────────────────

def test_the_draft_is_the_most_recently_updated_document(writer):
    """F31 - what the notepad restores on load."""
    first = writer.create(title="Older").json()["id"]
    second = writer.create(title="Newer").json()["id"]

    # Touch the older one so it becomes the most recent.
    writer.autosave(document_id=first, content="Revisited.")

    document = writer.draft().json()["document"]
    assert document["id"] == first, (
        f"expected the just-updated document, got {document['title']!r}"
    )
    assert document["id"] != second


def test_an_account_with_nothing_saved_has_no_draft(api, new_user):
    """null, not a 404 - the page renders an empty editor."""
    _, headers = new_user("no-draft")

    response = api.get("/writing/autosave", headers=headers)
    assert response.status_code == 200
    assert response.json()["document"] is None


def test_unknown_fields_are_rejected(writer):
    """extra="forbid" on both request models, so a typo is not silently dropped."""
    assert writer.create(titel="typo").status_code == 422
    assert writer.autosave(documentid="typo").status_code == 422
