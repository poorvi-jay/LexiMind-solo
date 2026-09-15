"""One account cannot reach another's writing data (M4 phase 5).

The word bank, analytics and session routes already have isolation tests
(test_wordbank.py, test_wordbank_stats.py, test_analytics.py,
test_sessions.py). Writing did not, which left the routes carrying the most
personal content — the documents someone actually wrote — as the gap.

Every path here goes through one helper:

    def _owned_document(document_id, user, db):
        doc = db.get(SavedDocument, document_id)
        if doc is None or doc.user_id != user.id:
            raise HTTPException(404, "Document not found.")

Two properties follow from that single `or`, and both are tested below,
because each could break without the other noticing:

  * another user's document is not readable, writable or deletable;
  * it is not *distinguishable* from one that does not exist. A 403, or a
    404 with different wording, would confirm the id is real — enough to
    enumerate who has what.

The delete and overwrite cases assert the owner's data afterwards rather than
just the status code. A handler that returned 404 and still committed the
change would pass a status-only test while destroying someone's work.
"""

from __future__ import annotations

import uuid

import pytest

# Long enough that a truncating bug is visible, and distinctive enough to find
# in a response body that should never contain it.
ALICE_CONTENT = "Alice's private notes about the mitochondria lab report."
ALICE_TITLE = "Alice's lab notes"


@pytest.fixture
def two_accounts(new_user):
    """(alice_headers, bob_headers) — two throwaway accounts.

    new_user tags each email, so a failure names which side it came from.
    """
    _, alice = new_user("alice")
    _, bob = new_user("bob")
    return alice, bob


def _create_document(api, headers, title=ALICE_TITLE, content=ALICE_CONTENT) -> str:
    response = api.post(
        "/writing/documents",
        headers=headers,
        json={"title": title, "content": content},
    )
    assert response.status_code == 201, (
        f"could not create the fixture document: {response.status_code} "
        f"{response.text[:200]}"
    )
    return response.json()["id"]


def test_another_users_document_cannot_be_read(api, two_accounts):
    alice, bob = two_accounts
    document_id = _create_document(api, alice)

    response = api.get(f"/writing/documents/{document_id}", headers=bob)

    assert response.status_code == 404, (
        f"Bob read Alice's document: {response.status_code}"
    )
    assert ALICE_CONTENT not in response.text, "the body leaked Alice's content"


def test_another_users_document_cannot_be_deleted(api, two_accounts):
    """Status *and* survival.

    A handler that 404s but deletes anyway would pass an assertion on the
    status code alone, while silently destroying the owner's work — which is
    the worst version of this bug, because nothing visibly fails.
    """
    alice, bob = two_accounts
    document_id = _create_document(api, alice)

    response = api.delete(f"/writing/documents/{document_id}", headers=bob)
    assert response.status_code == 404, f"Bob deleted Alice's document"

    still_there = api.get(f"/writing/documents/{document_id}", headers=alice)
    assert still_there.status_code == 200, (
        "Alice's document is gone after Bob's rejected delete"
    )
    assert still_there.json()["content"] == ALICE_CONTENT


def test_another_users_document_cannot_be_overwritten_by_autosave(api, two_accounts):
    """PATCH /writing/autosave takes a document_id, so it is a write path in.

    Easy to miss: the obvious isolation tests are on the /documents/{id}
    routes, but autosave reaches the same rows by id and would be a way
    around them if it resolved ownership differently.
    """
    alice, bob = two_accounts
    document_id = _create_document(api, alice)

    response = api.patch(
        "/writing/autosave",
        headers=bob,
        json={"document_id": document_id, "content": "Bob was here."},
    )
    assert response.status_code == 404, (
        f"Bob wrote into Alice's document: {response.status_code}"
    )

    unchanged = api.get(f"/writing/documents/{document_id}", headers=alice)
    assert unchanged.status_code == 200
    assert unchanged.json()["content"] == ALICE_CONTENT, (
        "Alice's content was modified by Bob's rejected autosave"
    )


def test_someone_elses_document_is_indistinguishable_from_a_missing_one(
    api, two_accounts
):
    """The anti-enumeration property.

    If a real-but-someone-else's id answered differently from a nonexistent
    one — a 403, or a 404 whose message differed — then the response itself
    confirms which ids exist. Same status and same body is the whole point of
    the single `or` in _owned_document.
    """
    alice, bob = two_accounts
    real_id = _create_document(api, alice)
    imaginary_id = str(uuid.uuid4())

    someone_elses = api.get(f"/writing/documents/{real_id}", headers=bob)
    nonexistent = api.get(f"/writing/documents/{imaginary_id}", headers=bob)

    assert someone_elses.status_code == nonexistent.status_code == 404
    assert someone_elses.json() == nonexistent.json(), (
        "a real-but-not-yours id answers differently from an imaginary one, "
        f"which confirms it exists: {someone_elses.json()} vs "
        f"{nonexistent.json()}"
    )


def test_document_list_never_includes_another_users(api, two_accounts):
    alice, bob = two_accounts
    document_id = _create_document(api, alice)

    response = api.get("/writing/documents", headers=bob)
    assert response.status_code == 200

    ids = {doc["id"] for doc in response.json()}
    assert document_id not in ids, "Alice's document appeared in Bob's list"
    assert ALICE_TITLE not in response.text, "the listing leaked Alice's title"


def test_the_autosave_draft_is_per_account(api, two_accounts):
    """GET /writing/autosave returns "most recently updated", scoped by user.

    Worth its own test because the ordering is global-looking — the filter on
    user_id is the only thing separating the two accounts, and Alice writing
    last must not change what Bob restores.
    """
    alice, bob = two_accounts

    bob_id = _create_document(api, bob, title="Bob's draft", content="Bob's own work.")
    # Alice writes afterwards, so she holds the newest row overall.
    _create_document(api, alice)

    response = api.get("/writing/autosave", headers=bob)
    assert response.status_code == 200

    document = response.json()["document"]
    assert document is not None, "Bob has a document but got an empty draft"
    assert document["id"] == bob_id, (
        "Bob's autosave restored a document that is not his - the query is "
        "ordering by updated_at without scoping to the caller"
    )
    assert ALICE_CONTENT not in response.text


def test_each_token_resolves_to_its_own_account(api, two_accounts):
    """The premise everything above rests on.

    If both tokens resolved to the same user the isolation tests would pass
    trivially and prove nothing, so assert the two accounts really are
    distinct before trusting any of it.
    """
    alice, bob = two_accounts

    alice_me = api.get("/auth/me", headers=alice)
    bob_me = api.get("/auth/me", headers=bob)

    assert alice_me.status_code == bob_me.status_code == 200
    assert alice_me.json()["id"] != bob_me.json()["id"]
    assert alice_me.json()["email"] != bob_me.json()["email"]
