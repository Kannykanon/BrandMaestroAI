"""Manual check: hit /conversation/generate/stream against a local API.

Run with the API already up:  python scripts/manual_stream_check.py
Not a pytest test - it needs a live API, database and Gemini key.
"""
import asyncio
import os
import sys

import httpx
from jose import jwt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_db_session, User  # noqa: E402

# Must match the app's signing key (Settings.secret_key), otherwise the API
# rejects the token. No fallback: a hardcoded default would silently mint
# tokens the server cannot verify.
SECRET_KEY = os.environ["SECRET_KEY"]
ALGORITHM = os.getenv("ALGORITHM", "HS256")
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")


async def main():
    with get_db_session() as session:
        user = session.query(User).first()
        if not user:
            print("No users found. Register one first.")
            return
        business_id = user.business_id
        user_id = user.id

    token = jwt.encode({"sub": str(user_id)}, SECRET_KEY, algorithm=ALGORITHM)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "business_id": business_id,
        "content_type": "blog",
        "topic": "test topic for debugging stream",
        "format_type": "test format",
        "use_search": False,
    }

    print("Hitting endpoint...")
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream(
            "POST", f"{API_URL}/conversation/generate/stream",
            headers=headers, json=payload
        ) as response:
            print(f"Status: {response.status_code}")
            async for chunk in response.aiter_text():
                print(f"Chunk received: {chunk!r}")


if __name__ == "__main__":
    asyncio.run(main())