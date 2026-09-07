"""Manual check: dump buffered generation streams from Redis.

Run with Redis reachable:  python scripts/manual_redis_check.py
"""
import os

import redis


def main():
    r = redis.Redis.from_url(
        os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
        decode_responses=True,
    )
    keys = r.keys("stream:*")
    if not keys:
        print("No stream:* keys found.")
        return
    for k in keys:
        print(f"Key: {k}")
        items = r.lrange(k, 0, -1)
        for i, item in enumerate(items[-5:]):  # just last 5
            print(f"[{i}]: {item[:200]}...")


if __name__ == "__main__":
    main()
