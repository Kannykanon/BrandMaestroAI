# utils/observe.py
import time
import logging
from functools import wraps

logger = logging.getLogger(__name__)

def observe(operation: str):
    """Simple decorator to measure and log operation health."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            start = time.monotonic()
            try:
                result = fn(*args, **kwargs)
                duration = time.monotonic() - start
                logger.info(
                    "operation=%s status=success duration=%.2fs",
                    operation, duration
                )
                return result
            except Exception as exc:
                duration = time.monotonic() - start
                logger.error(
                    "operation=%s status=failed duration=%.2fs error=%s",
                    operation, duration, exc
                )
                raise
        return wrapper
    return decorator