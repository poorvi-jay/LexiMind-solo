"""
backend/routers/writing.py
F25 writing notepad · F31 autosave · F32 named documents · F48 template

Endpoints:
    GET    /writing/autosave          — the document to restore on load
    PATCH  /writing/autosave          — upsert the draft
    PATCH  /writing/session           — deprecated alias of POST /sessions/writing
    GET    /writing/documents         — list this user's documents
    POST   /writing/documents         — save the current work as a new document
    GET    /writing/documents/{id}    — load one into the notepad
    DELETE /writing/documents/{id}    — remove one

Autosave writes into `saved_documents`, not `writing_sessions` — the PRD's
writing_sessions table holds analytics counters only and has no content column.

There is no separate "draft" row: the draft *is* a saved document. A client with
no document id yet creates one on its first save and reuses the returned id
afterwards; a client that has lost its id (fresh browser, another device) calls
GET /writing/autosave and picks up the most recently updated document. The CRUD
endpoints below list, open and delete those same rows.

Writing-session logging (F38, and F48's template_used) lives in
routers/sessions.py as POST /sessions/writing. It started here as PATCH
/writing/session, a stand-in for that M3 endpoint with the same payload; the old
path is still registered below on the very same handler, marked deprecated, so
any caller of it keeps working and nothing is logged twice.

Every lookup filters on the owner and returns the same 404 for "no such
document" and "someone else's document", so ids can't be probed for existence.
"""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_serializer
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.dependencies import get_current_user
from backend.models import SavedDocument, User
from backend.routers.sessions import WritingSessionOut, log_writing_session

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


class NewDocumentRequest(BaseModel):
    """F32 'Save as' — the current work kept under a name of its own."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=MAX_TITLE_CHARS)
    content: str = Field(max_length=MAX_CONTENT_CHARS)
    template: Literal["essay", "email", "report"] | None = None


class UtcTimestamps(BaseModel):
    """Marks the stored timestamps as UTC on the way out.

    `models.py` writes `datetime.now(timezone.utc)` but the columns are plain
    DateTime, so SQLite hands the values back with no tzinfo and they serialise
    as a bare '2026-08-11T16:15:06'. A browser reads that as *local* time, which
    would show every document as saved hours out. Stamping the offset here fixes
    it for every client without touching the schema.
    """

    @field_serializer("created_at", "updated_at", check_fields=False)
    def _stamp_utc(self, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class DocumentOut(UtcTimestamps):
    id: str
    title: str
    content: str
    template: str | None
    created_at: datetime
    updated_at: datetime


class DocumentSummary(UtcTimestamps):
    """List row — no content, so a long library stays a small response."""

    id: str
    title: str
    template: str | None
    word_count: int
    created_at: datetime
    updated_at: datetime


class DraftOut(BaseModel):
    """GET response — null when the account has nothing saved yet."""

    document: DocumentOut | None


class DeletedOut(BaseModel):
    status: str
    id: str


class AutosaveOut(UtcTimestamps):
    """PATCH response — no content echo, the client already has the text."""

    id: str
    title: str
    template: str | None
    updated_at: datetime


def _dated_title() -> str:
    """Default name for a document saved without one, e.g. 'Untitled — 12 Aug 2026'."""
    return f"{DEFAULT_TITLE} — {datetime.now().strftime('%d %b %Y')}"


def _owned_document(document_id: str, user: User, db: Session) -> SavedDocument:
    """Fetch a document or 404 — identically for missing and for another user's."""
    doc = db.get(SavedDocument, document_id)
    if doc is None or doc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    return doc


def _summarize(doc: SavedDocument) -> DocumentSummary:
    return DocumentSummary(
        id=doc.id,
        title=doc.title,
        template=doc.template,
        word_count=len(doc.content.split()),
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


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
        doc = _owned_document(req.document_id, current_user, db)
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


# ── writing session analytics (F38 / F48) ──────────────────────────────
# Moved to POST /sessions/writing (routers/sessions.py). The old path stays
# registered on the same handler so existing callers keep working; one handler
# means one row per session whichever path reports it.
router.add_api_route(
    "/writing/session",
    log_writing_session,
    methods=["PATCH"],
    response_model=WritingSessionOut,
    deprecated=True,
    summary="Deprecated: use POST /sessions/writing",
)


# ── named documents (F32) ──────────────────────────────────────────────
@router.get("/writing/documents", response_model=list[DocumentSummary])
def list_documents(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Most recently worked on first — the same order GET /writing/autosave picks from."""
    docs = (
        db.query(SavedDocument)
        .filter(SavedDocument.user_id == current_user.id)
        .order_by(SavedDocument.updated_at.desc())
        .all()
    )
    # Rows carry their content because word_count is computed from it. That is
    # fine for one writer's library; a shared deployment would want a stored
    # counter or a SQL length() instead of loading every document to list them.
    return [_summarize(doc) for doc in docs]


@router.post("/writing/documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
def create_document(
    req: NewDocumentRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """'Save as' — keep the current work as a new document and leave the old one alone.

    Returns the whole document, not just its id as the build guide's table
    suggests: the client switches to editing this copy, so it needs the title
    the server settled on and the timestamps to baseline autosave against.
    """
    if not req.content.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to save yet.")

    doc = SavedDocument(
        user_id=current_user.id,
        title=(req.title or "").strip() or _dated_title(),
        content=req.content,
        template=req.template,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return _serialize(doc)


@router.get("/writing/documents/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Load one document into the notepad."""
    return _serialize(_owned_document(document_id, current_user, db))


@router.delete("/writing/documents/{document_id}", response_model=DeletedOut)
def delete_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a document.

    Answers with a body rather than a 204: `api.js` parses every successful
    response as JSON, and a repeat delete 404s instead of pretending to succeed.
    """
    doc = _owned_document(document_id, current_user, db)
    db.delete(doc)
    db.commit()
    return DeletedOut(status="deleted", id=document_id)
