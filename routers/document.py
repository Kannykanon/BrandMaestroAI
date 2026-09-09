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


def _upload_idempotency_key(business_id: str, content_type: str,
                            file_content: bytes) -> str:
    """Single source of truth for the upload dedupe key.

    Upload and delete previously built this key differently (sha256 vs md5,
    different prefixes), so deleting a document never cleared its key and
    re-uploading the same file returned 409 forever.

    content_type is part of the key because retrieval is scoped to
    (business_id, content_type): one reference sheet legitimately belongs to
    several content types, and each needs its own indexed copy. Without it the
    first upload succeeded and every other content type was refused as a
    duplicate — so a fact sheet could serve press releases or social captions,
    never both.
    """
    return f"upload:{business_id}:{content_type}:{create_idempotency_key(file_content)}"


# Two kinds of document enter this system by two different paths, and which
# path a file takes is not a detail the caller should have to encode in a form
# field on a shared endpoint.
#
#   /brand-voice  a previously published, best-performing piece. Read whole,
#                 through long context, into the Brand Brain: how this brand
#                 writes. Never indexed for retrieval - handing the writer its
#                 own style references as material makes it reproduce them.
#
#   /product      a product sheet, press kit or fact sheet. Chunked and indexed
#                 for RAG: what the brand is writing about. Held out of the
#                 Brand Brain so press-kit register does not leak into voice.
#
# /top-performing remains as the original combined endpoint, taking doc_role,
# so existing clients and scripts keep working.


@router.post("/brand-voice", response_model=TaskResponse)
@limiter.limit("20/minute")
async def upload_brand_voice(
        request: Request,
        db: Annotated[Session, Depends(get_db)],
        current_user: Annotated[object, Depends(get_current_user)],
        business_id: str = Form(...),
        content_type: str = Form(...),
        platform: Optional[str] = Form(None),
        performance_metric: Optional[str] = Form(None),
        file: UploadFile = File(...)
):
    """Previously published work, extracted into the Brand Brain."""
    return await _ingest_document(
        request=request, db=db, current_user=current_user,
        business_id=business_id, content_type=content_type, platform=platform,
        performance_metric=performance_metric, doc_role="voice", file=file,
    )


@router.post("/product", response_model=TaskResponse)
@limiter.limit("20/minute")
async def upload_product_document(
        request: Request,
        db: Annotated[Session, Depends(get_db)],
        current_user: Annotated[object, Depends(get_current_user)],
        business_id: str = Form(...),
        content_type: str = Form(...),
        platform: Optional[str] = Form(None),
        performance_metric: Optional[str] = Form(None),
        file: UploadFile = File(...)
):
    """Product and fact material, indexed for RAG retrieval."""
    return await _ingest_document(
        request=request, db=db, current_user=current_user,
        business_id=business_id, content_type=content_type, platform=platform,
        performance_metric=performance_metric, doc_role="reference", file=file,
    )


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
    """Combined endpoint kept for existing callers; prefer the two above."""
    return await _ingest_document(
        request=request, db=db, current_user=current_user,
        business_id=business_id, content_type=content_type, platform=platform,
        performance_metric=performance_metric, doc_role=doc_role, file=file,
    )


async def _ingest_document(
        *, request: Request, db: Session, current_user, business_id: str,
        content_type: str, platform: Optional[str],
        performance_metric: Optional[str], doc_role: str, file: UploadFile
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

    _idempotency_key = _upload_idempotency_key(business_id, content_type, file_content)
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

    # The two roles feed two different channels and never both.
    #
    # A voice document is extracted into the Brand Brain, which describes how the
    # brand writes. It is deliberately NOT indexed for retrieval: the writer must
    # not be handed the style references as material, or it reproduces them.
    #
    # A reference document — a product sheet, a press kit, a brief — is indexed
    # for retrieval and held out of the Brand Brain. It supplies facts, and its
    # own register must not pull generated copy toward press-kit prose.
    tasks = []
    if doc_role == DOC_ROLE_VOICE:
        tasks.append(
            extract_metrics.s(business_id=business_id, content_type=content_type,
                              doc_id=doc_id, doc_content=doc_content)
        )
        logger.info(
            "Document %s uploaded as role=voice — extracted into the Brand Brain, "
            "not indexed for retrieval", doc_id,
        )
    else:
        tasks.append(
            refresh_rag.s(business_id=business_id, content_type=content_type,
                          new_doc_content=doc_content, doc_id=doc_id)
        )
        logger.info(
            "Document %s uploaded as role=reference — indexed for retrieval, "
            "held out of Brand Brain extraction", doc_id,
        )
    result = group(*tasks).delay()
    request.app.state.redis.set(_idempotency_key, "processing", ex=86400)

    # Return required generation_id along with task_id and status to satisfy TaskResponse schema
    return {"generation_id": "", "task_id": result.id, "status": "queued"}


def _purge_vectors(business_id: str, content_type: str) -> bool:
    """Drop every embedded chunk for one (business_id, content_type).

    Deleting the brand_documents row leaves the vectors derived from it in
    place, so without this a deleted document keeps being retrieved and keeps
    steering generation. Run inline rather than queued: a delete has to be
    true by the time it returns, otherwise a generation started immediately
    afterwards still sees the document the user just removed.

    A failure here is reported but does not fail the request — the row is
    already gone, and the index rebuild clears the table anyway.
    """
    try:
        from brand_rag import BrandRAG
        from embedding_stategy import build_embedding

        BrandRAG(
            business_id=business_id,
            content_type=content_type,
            embedding=build_embedding(),
        ).purge()
        return True
    except Exception as e:
        logger.error(
            "Vector purge failed business_id=%s content_type=%s: %s",
            business_id, content_type, e,
        )
        return False


@router.get("")
async def list_documents(
        db: Annotated[Session, Depends(get_db)],
        current_user: Annotated[object, Depends(get_current_user)],
        content_type: Optional[str] = None,
):
    """The documents currently feeding this business's Brand Brain and RAG.

    The UI previously kept this list only in browser memory, so it emptied on
    reload and there was no way to see — or remove — what had been uploaded in
    an earlier session.

    file_content is deliberately not returned: these documents can be large,
    and the list view only needs their identity and status.
    """
    from database import BrandDocument

    business_id = current_user.business_id
    require_business_access(current_user, business_id)

    query = db.query(BrandDocument).filter(BrandDocument.business_id == business_id)
    if content_type:
        query = query.filter(BrandDocument.content_type == content_type)

    docs = query.order_by(BrandDocument.uploaded_at.desc()).all()

    return [
        {
            "id": d.id,
            "filename": d.filename,
            "content_type": d.content_type,
            "doc_role": getattr(d, "doc_role", "voice"),
            "status": d.status,
            # Returned so a failed document can say why in the UI instead of
            # only that it failed.
            "error_message": d.error_message,
            "file_size_bytes": d.file_size_bytes,
            "chunk_count": d.chunk_count,
            "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
            "processed_at": d.processed_at.isoformat() if d.processed_at else None,
        }
        for d in docs
    ]


@router.get("/brand-brain/{content_type}")
async def get_brand_brain(
        content_type: str,
        current_user: Annotated[object, Depends(get_current_user)],
):
    """The synthesised voice profile for one content type, as the pipeline sees it.

    The Brain is what the whole system rests on - the writer is steered by it and
    the enforcer scores against the rates counted in it - and until now it could
    be deleted through the API but never read. Nothing outside the graph could
    show what had actually been learned from a brand's documents, so "measured,
    not described" was a claim a user had to take on trust.

    Serves exactly what the researcher and enforcer receive, by calling the same
    get_context(), so what is displayed cannot drift from what is used.
    """
    from schema import CONTENT_TYPES
    if content_type not in CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"content_type must be one of {', '.join(CONTENT_TYPES)}",
        )

    business_id = current_user.business_id

    from brand_metrics import BrandMetricsSQL
    try:
        brain = BrandMetricsSQL(business_id=business_id, content_type=content_type).get_context() or ""
    except Exception as e:
        logger.warning("Brand Brain read failed business_id=%s content_type=%s: %s",
                       business_id, content_type, e)
        brain = ""

    brain = brain.strip()
    return {
        "business_id": business_id,
        "content_type": content_type,
        # An empty Brain is a normal state, not an error: it means no voice
        # documents have been synthesised for this content type yet.
        "exists": bool(brain),
        "brand_brain": brain,
        "characters": len(brain),
    }


@router.delete("/brand-brain/{content_type}")
async def reset_brand_brain(
        content_type: str,
        request: Request,
        db: Annotated[Session, Depends(get_db)],
        current_user: Annotated[object, Depends(get_current_user)],
):
    """Delete everything this business has taught the system for one content type.

    Removes the uploaded documents, their embeddings, the synthesised voice
    profile and its cache. The next generation for this content type starts
    from nothing, exactly as a new account would.

    Scoped to one content_type rather than the whole business so a user can
    retrain their press-release voice without discarding their social voice.
    Declared before /{document_id} for clarity; the two cannot collide anyway,
    since this path has an extra segment.
    """
    from database import BrandDocument
    from brand_metrics import BrandMetricsSQL
    from schema import CONTENT_TYPES

    # Without this an unrecognised content type would delete nothing and still
    # report success, so a typo would look like a completed reset.
    if content_type not in CONTENT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown content_type. Expected one of: {', '.join(CONTENT_TYPES)}",
        )

    business_id = current_user.business_id
    require_business_access(current_user, business_id)

    docs = (
        db.query(BrandDocument)
        .filter(
            BrandDocument.business_id == business_id,
            BrandDocument.content_type == content_type,
        )
        .all()
    )

    # Collect the dedupe keys before the rows go, so the same files can be
    # uploaded again afterwards.
    contents = [d.file_content for d in docs if d.file_content]
    deleted = len(docs)

    for doc in docs:
        db.delete(doc)
    db.commit()

    for content in contents:
        request.app.state.redis.delete(
            _upload_idempotency_key(business_id, content_type, content.encode())
        )

    purged = _purge_vectors(business_id, content_type)

    # delete_all() drops the metric rows and hard-evicts the cached synthesis,
    # so no stale voice profile can be served during a grace period.
    BrandMetricsSQL(business_id=business_id, content_type=content_type).delete_all()

    logger.info(
        "Brand Brain reset business_id=%s content_type=%s documents=%d vectors_purged=%s",
        business_id, content_type, deleted, purged,
    )

    return {
        "status": "reset",
        "business_id": business_id,
        "content_type": content_type,
        "documents_deleted": deleted,
        "vectors_purged": purged,
    }


@router.delete("/{document_id}")
async def delete_document(
        document_id: int,
        request: Request,
        db: Annotated[Session, Depends(get_db)],
        current_user: Annotated[object, Depends(get_current_user)]
):
    """Remove one uploaded document and everything derived from it.

    document_id is typed int so a non-numeric path gives 422 rather than a
    database cast error surfacing as a 500.
    """
    from database import BrandDocument
    from brand_metrics import BrandMetricsSQL
    from celery_task import synthesize_metrics

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
            _upload_idempotency_key(business_id, content_type, doc_content.encode())
        )

    # 3. Drop the embeddings inline. This replaces the previous
    # refresh_rag(new_doc_content=None) call, which only evicted an in-process
    # index cache in whichever worker happened to run it — the vectors
    # themselves were never touched, so a deleted document went on being
    # retrieved. The next query rebuilds from the documents that remain.
    purged = _purge_vectors(business_id, content_type)

    # 4. Re-synthesise the voice profile, but only if this document was part of
    # it. Removing a reference document changes the facts available for
    # retrieval, not the brand's voice.
    #
    # The cache is invalidated here, synchronously, rather than left to the
    # task: soft invalidation keeps serving the previous profile until the
    # rebuild lands, which is right for latency but wrong for a deletion —
    # the user asked for this document's influence to be gone.
    brain_deleted = False
    resynthesizing = False
    if doc_role == "voice":
        analyzer = BrandMetricsSQL(business_id=business_id, content_type=content_type)
        analyzer.invalidate_cache(soft=False)

        # The metric row for this document goes with it via ON DELETE CASCADE,
        # so what remains here decides between rebuilding and clearing.
        voice_left = (
            db.query(BrandDocument)
            .filter(
                BrandDocument.business_id == business_id,
                BrandDocument.content_type == content_type,
                BrandDocument.doc_role == "voice",
            )
            .count()
        )
        if voice_left:
            # Rebuild from the documents that remain. This previously called
            # extract_metrics(doc_id=None, doc_content=None), which is not a
            # rebuild — extract_and_save hashes doc_content immediately, so the
            # task raised AttributeError on None, retried three times and died.
            # Deleting a voice document therefore never re-synthesised anything.
            synthesize_metrics.delay(
                business_id=business_id, content_type=content_type
            )
            resynthesizing = True
        else:
            # That was the last voice document. There is nothing to synthesise a
            # voice profile from, so the profile itself goes — otherwise the user
            # has deleted every document they uploaded and the Brand Brain is
            # still there.
            analyzer.delete_all()
            brain_deleted = True

    logger.info(
        "Document %s deleted business_id=%s content_type=%s role=%s "
        "vectors_purged=%s brain_deleted=%s",
        document_id, business_id, content_type, doc_role, purged, brain_deleted,
    )

    return {
        "status": "deleted",
        "document_id": document_id,
        "content_type": content_type,
        "vectors_purged": purged,
        "brand_brain_resynthesizing": resynthesizing,
        "brand_brain_deleted": brain_deleted,
    }