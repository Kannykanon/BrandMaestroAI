import hashlib
import logging

logger = logging.getLogger(__name__)

def create_idempotency_key(file_content: bytes) -> str:
    return hashlib.sha256(file_content).hexdigest()
