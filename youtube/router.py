"""HTTP routes for YouTube Automation, mounted at /youtube."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from youtube.eligibility import get_eligible_script, list_eligible_scripts
from youtube.storage import StorageSingleton

router = APIRouter()


@router.get("/status")
async def youtube_status(current_user: Annotated[object, Depends(get_current_user)]):
    """What is set up so far, so the UI can say what is missing instead of failing later."""
    try:
        storage = StorageSingleton.provider_class()
        storage_status = {
            "provider": storage.name,
            "configured": not storage.missing_env(),
            "missing": storage.missing_env(),
        }
    except ValueError as e:
        storage_status = {"provider": None, "configured": False, "error": str(e)}

    return {
        "storage": storage_status,
        # Filled in by later phases.
        "channel_connected": False,
    }


@router.get("/scripts")
async def eligible_scripts(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[object, Depends(get_current_user)],
):
    """Approved marketing scripts this business can turn into videos, newest first.

    Each carries `approval`: "human" (a person approved it) or "enforcer"
    (approved automatically, not reviewed by a person).
    """
    scripts = list_eligible_scripts(db, current_user.business_id)
    return {"scripts": [s.to_summary() for s in scripts]}


@router.get("/scripts/{generation_id}")
async def eligible_script(
    generation_id: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[object, Depends(get_current_user)],
):
    """The full text of one eligible script. 404 if it is missing, not yours, or not eligible."""
    script = get_eligible_script(db, current_user.business_id, generation_id)
    if script is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No eligible script with that id for this business",
        )
    return {**script.to_summary(), "content": script.content}
