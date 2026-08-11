"""
backend/routers/writing.py
F25 writing notepad · F31 autosave · F48 template recorded on the document

Endpoints: GET /writing/autosave, PATCH /writing/autosave

Autosave writes into `saved_documents`, not `writing_sessions` — the PRD's
writing_sessions table holds analytics counters only and has no content column.

There is no separate "draft" row: the draft *is* a saved document. A client with
no document id yet creates one on its first save and reuses the returned id
afterwards; a client that has lost its id (fresh browser, another device) calls
GET and picks up the most recently updated document. That is the same document
Phase 7's CRUD will list and open, so nothing here needs revisiting then.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.dependencies import get_current_user
from backend.models import SavedDocument, User

router = APIRouter()

MAX_CONTENT_CHARS = 50_000  # matches the cap documented on SavedDocument.content
MAX_TITLE_CHARS = 150       # matches SavedDocument.title's column width
DEFAULT_TITLE = "Untitled"


# ── request / response models ──────────────────────────────────────────
class AutosaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str | None = None
    title: str | None = Field(default=None, max_length=MAX_TITLE_CHARS)
    content: str = Field(max_length=MAX_CONTENT_CHARS)
    # F48 — which structure template the writer started from, recorded on the
    # document so it can be logged with the session. Omitted (or null) leaves
    # whatever is stored alone, the same "no change" meaning `title` has; a
    # client that hasn't touched the dropdown must not blank an earlier choice.
    template: Literal["essay", "email", "report"] | None = None


class DocumentOut(BaseModel):
    id: str
    title: str
    content: str
    template: str | None
    created_at: datetime
    updated_at: datetime


class DraftOut(BaseModel):
    """GET response — null when the account has nothing saved yet."""

    document: DocumentOut | None


class AutosaveOut(BaseModel):
    """PATCH response — no content echo, the client already has the text."""

    id: str
    title: str
    template: str | None
    updated_at: datetime


def _serialize(doc: SavedDocument) -> DocumentOut:
    return DocumentOut(
        id=doc.id,
        title=doc.title,
        content=doc.content,
        template=doc.template,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


# ── endpoints ──────────────────────────────────────────────────────────
@router.get("/writing/autosave", response_model=DraftOut)
def get_draft(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """F31 — the work to restore on load: this user's most recently saved document."""
    doc = (
        db.query(SavedDocument)
        .filter(SavedDocument.user_id == current_user.id)
        .order_by(SavedDocument.updated_at.desc())
        .first()
    )
    return DraftOut(document=_serialize(doc) if doc else None)


@router.patch("/writing/autosave", response_model=AutosaveOut)
def autosave(
    req: AutosaveRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """F31 — upsert the draft. Creates a document when no id is supplied."""
    if req.document_id:
        doc = db.get(SavedDocument, req.document_id)
        # Same 404 for "no such document" and "someone else's document", so the
        # response can't be used to probe which ids exist.
        if doc is None or doc.user_id != current_user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
        doc.content = req.content
        if req.title is not None:
            doc.title = req.title.strip() or DEFAULT_TITLE
        if req.template is not None:
            doc.template = req.template
    else:
        # Don't leave an empty row behind for a page that was only ever opened.
        if not req.content.strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to save yet.")
        doc = SavedDocument(
            user_id=current_user.id,
            title=(req.title or "").strip() or DEFAULT_TITLE,
            content=req.content,
            template=req.template,
        )
        db.add(doc)

    db.commit()
    db.refresh(doc)

    return AutosaveOut(
        id=doc.id, title=doc.title, template=doc.template, updated_at=doc.updated_at
    )
