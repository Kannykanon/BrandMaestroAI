from fastapi import HTTPException, Depends, Request, Form, File, UploadFile
from sqlalchemy.orm import Session
from database import get_db
from schema import TaskResponse
from typing import Annotated, Optional
from fastapi import APIRouter
from auth import get_current_user, require_business_access
from limiter import limiter
from utils import create_idempotency_key, logger
from celery import group

router = APIRouter()


def _upload_idempotency_key(business_id: str, file_content: bytes) -> str:
    """Single source of truth for the upload dedupe key.

    Upload and delete previously built this key differently (sha256 vs md5,
    different prefixes), so deleting a document never cleared its key and
    re-uploading the same file returned 409 forever.
    """
    return f"upload:{business_id}:{create_idempotency_key(file_content)}"


@router.post("/top-performing", response_model=TaskResponse)
@limiter.limit("20/minute")
async def upload(
        request: Request,
        db: Annotated[Session, Depends(get_db)],
        current_user: Annotated[object, Depends(get_current_user)],
        business_id: str = Form(...),
        content_type: str = Form(...),
        platform: Optional[str] = Form(None),
        performance_metric: Optional[str] = Form(None),
        doc_role: str = Form("voice"),
        file: UploadFile = File(...)
):
    from celery_task import refresh_rag, extract_metrics
    from database import BrandDocument, DOC_ROLES, DOC_ROLE_VOICE

    require_business_access(current_user, business_id)

    doc_role = (doc_role or DOC_ROLE_VOICE).strip().lower()
    if doc_role not in DOC_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"doc_role must be one of {', '.join(DOC_ROLES)}",
        )

    ALLOWED_TYPES = {"application/pdf", "text/plain", "text/csv", "tex/docx",
                     "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="File type not supported")

    file_content = await file.read()
    if len(file_content) == 0:
        raise HTTPException(status_code=400, detail="File is empty")

    try:
        doc_content = file_content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded text")

    if len(doc_content) > 200000:
        raise HTTPException(status_code=400, detail="File is too large")

    _idempotency_key = _upload_idempotency_key(business_id, file_content)
    if request.app.state.redis.exists(_idempotency_key):
        raise HTTPException(status_code=409, detail="File already processed")

    user_id = current_user.id

    doc = BrandDocument(
        user_id=user_id,
        business_id=business_id,
        content_type=content_type,
        doc_role=doc_role,
        file_content=doc_content,
        filename=file.filename or "upload",
    )
    try:
        db.add(doc)
        db.flush()
        db.commit()
        doc_id = doc.id
    except Exception as e:
        db.rollback()
        logger.error("Database error while saving document: %s", e)
        raise HTTPException(status_code=500, detail="Failed to save document")

    # Both roles feed the RAG index — a reference document supplies facts, and
    # past content supplies retrievable structural examples. Only a "voice"
    # document is extracted into the Brand Brain: a product doc or press kit is
    # written in its own register, and letting it define the brand's voice pulls
    # generated copy toward press-kit prose instead of the brand's own.
    tasks = [
        refresh_rag.s(business_id=business_id, content_type=content_type, new_doc_content=doc_content)
    ]
    if doc_role == DOC_ROLE_VOICE:
        tasks.append(
            extract_metrics.s(business_id=business_id, content_type=content_type,
                              doc_id=doc_id, doc_content=doc_content)
        )
    else:
        logger.info(
            "Document %s uploaded as role=reference — indexed for retrieval, "
            "held out of Brand Brain extraction", doc_id,
        )
    result = group(*tasks).delay()
    request.app.state.redis.set(_idempotency_key, "processing", ex=86400)

    # Return required generation_id along with task_id and status to satisfy TaskResponse schema
    return {"generation_id": "", "task_id": result.id, "status": "queued"}


@router.delete("/{document_id}")
async def delete_document(
        document_id: str,
        request: Request,
        db: Annotated[Session, Depends(get_db)],
        current_user: Annotated[object, Depends(get_current_user)]
):
    from database import BrandDocument
    from celery_task import refresh_rag, extract_metrics

    doc = db.query(BrandDocument).filter(BrandDocument.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    business_id = doc.business_id
    content_type = doc.content_type
    doc_content = doc.file_content
    doc_role = getattr(doc, "doc_role", "voice")

    require_business_access(current_user, business_id)

    # 1. Delete from DB
    db.delete(doc)
    db.commit()

    # 2. Clear the upload dedupe key so the same file can be uploaded again
    if doc_content:
        request.app.state.redis.delete(
            _upload_idempotency_key(business_id, doc_content.encode())
        )

    # 3. Trigger Background Tasks to rebuild the Brand Brain and Vector Index
    # We pass None for new_doc_content to force a full rebuild of the RAG index
    # We trigger extract_metrics without a doc_id so it re-synthesizes from the remaining DB docs
    from celery import group
    tasks = [
        refresh_rag.s(business_id=business_id, content_type=content_type, new_doc_content=None)
    ]
    # Only re-synthesize the Brand Brain if the deleted document was actually
    # part of it. Removing a reference document changes the facts available for
    # retrieval, not the brand's voice.
    if doc_role == "voice":
        tasks.append(
            extract_metrics.s(business_id=business_id, content_type=content_type,
                              doc_id=None, doc_content=None)
        )
    group(*tasks).delay()

    return {"message": "Document deleted and system rebuilding successfully"}