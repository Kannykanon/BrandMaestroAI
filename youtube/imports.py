"""Scripts a person brings in themselves, instead of generating them in content writing.

An imported script is approved by the person who imports it, labelled "imported"
so it is never mistaken for a brand-checked script, and kept in YouTube
Automation's own table: content writing's data is never written to. It becomes
a project exactly like a generated script, through the same word-exact planning.
"""
from __future__ import annotations

from datetime import timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from youtube.eligibility import EligibleScript
from youtube.models import APPROVAL_IMPORTED, YTImportedScript
from youtube.projects import ProjectError
from youtube.script_parser import ScriptError, plan_shots

PREFIX = "import-"
MAX_CHARS = 20000
MIN_WORDS = 5


def is_import_id(script_id: str) -> bool:
    return str(script_id).startswith(PREFIX)


def _to_eligible(row: YTImportedScript) -> EligibleScript:
    created = row.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return EligibleScript(f"{PREFIX}{row.id}", row.title, row.content, APPROVAL_IMPORTED, None, created)


def list_imports(db: Session, business_id: str) -> list[EligibleScript]:
    rows = db.execute(select(YTImportedScript).where(YTImportedScript.business_id == business_id)
                      .order_by(YTImportedScript.id.desc())).scalars().all()
    return [_to_eligible(r) for r in rows]


def _row(db: Session, business_id: str, script_id: str) -> Optional[YTImportedScript]:
    if not is_import_id(script_id) or not script_id[len(PREFIX):].isdigit():
        return None
    return db.execute(select(YTImportedScript).where(
        YTImportedScript.id == int(script_id[len(PREFIX):]), YTImportedScript.business_id == business_id)).scalar_one_or_none()


def get_import(db: Session, business_id: str, script_id: str) -> Optional[EligibleScript]:
    row = _row(db, business_id, script_id)
    return _to_eligible(row) if row else None


def import_script(db: Session, business_id: str, title: Optional[str], content: str) -> EligibleScript:
    content = (content or "").replace("\r\n", "\n").strip()
    if len(content.split()) < MIN_WORDS:
        raise ProjectError("Paste or upload a script with at least a few lines")
    if len(content) > MAX_CHARS:
        raise ProjectError(f"Scripts can be at most {MAX_CHARS:,} characters")
    try:
        _, shots = plan_shots(content)
    except ScriptError as e:
        raise ProjectError(f"This script cannot be split into shots: {e}") from e
    if not shots:
        raise ProjectError("No spoken lines were found in this script")
    title = " ".join((title or "").split())[:200]
    if not title:
        first = next((line.strip() for line in content.splitlines() if line.strip()), "Imported script")
        title = first[:80]
    row = YTImportedScript(business_id=business_id, title=title, content=content)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_eligible(row)


def delete_import(db: Session, business_id: str, script_id: str) -> bool:
    """Delete an imported script. Projects made from it keep their own copy."""
    row = _row(db, business_id, script_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def decode_upload(data: bytes) -> str:
    """Text from an uploaded .txt or .md file."""
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            text = data.decode(encoding)
            if "\x00" not in text:
                return text
        except UnicodeDecodeError:
            continue
    raise ProjectError("Upload the script as a plain text (.txt) or Markdown (.md) file, or paste it")
