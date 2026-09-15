"""YouTube Data API quota, counted per Pacific-time day so work waits instead of failing.

YouTube resets quotas at midnight Pacific time and keeps two buckets that
matter here: uploads (videos.insert, 1 each, 100 a day) and general units
(10,000 a day, which videos.update and thumbnails.set draw 50 from). Checked
2026-09-15 at developers.google.com/youtube/v3/determine_quota_cost.

    YT_DAILY_UPLOADS      uploads allowed per day (default 100)
    YT_DAILY_QUOTA_UNITS  general units allowed per day (default 10000)

The ledger is a guard, not the authority: YouTube can still answer
quotaExceeded (for example when another tool uses the same Google project),
and that is handled the same way.
"""
from __future__ import annotations

import os
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from youtube.models import YTQuotaUsage

PACIFIC = ZoneInfo("America/Los_Angeles")

# action -> (bucket, cost)
COSTS = {
    "upload": ("uploads", 1),
    "update": ("units", 50),
    "thumbnail": ("units", 50),
    "list": ("units", 1),
    "channels": ("units", 1),
}


class QuotaExceeded(RuntimeError):
    """The day's quota for this action is used up."""

    def __init__(self, message: str, retry_at: datetime):
        super().__init__(message)
        self.retry_at = retry_at


def _limit(bucket: str) -> int:
    name, default = {"uploads": ("YT_DAILY_UPLOADS", 100), "units": ("YT_DAILY_QUOTA_UNITS", 10000)}[bucket]
    value = os.getenv(name, "").strip()
    return int(value) if value else default


def _now(now: datetime | None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)


def pacific_day(now: datetime | None = None) -> str:
    return _now(now).astimezone(PACIFIC).date().isoformat()


def next_reset(now: datetime | None = None) -> datetime:
    """The next midnight Pacific time, in UTC, plus a minute of margin."""
    local = _now(now).astimezone(PACIFIC)
    midnight = datetime.combine(local.date() + timedelta(days=1), time(0, 1), tzinfo=PACIFIC)
    return midnight.astimezone(timezone.utc)


def _row(db: Session, day: str, bucket: str, lock: bool = False) -> YTQuotaUsage | None:
    query = select(YTQuotaUsage).where(YTQuotaUsage.day == day, YTQuotaUsage.bucket == bucket)
    if lock:
        query = query.with_for_update()
    return db.execute(query).scalars().first()


def reserve(db: Session, action: str, now: datetime | None = None) -> None:
    """Count one call of `action` against today's quota, or raise QuotaExceeded. Commits."""
    bucket, cost = COSTS[action]
    day = pacific_day(now)
    row = _row(db, day, bucket, lock=True)
    if row is None:
        row = YTQuotaUsage(day=day, bucket=bucket, used=0)
        db.add(row)
        db.flush()
    limit = _limit(bucket)
    if row.used + cost > limit:
        db.commit()
        kind = "uploads" if bucket == "uploads" else "API units"
        raise QuotaExceeded(f"Today's YouTube quota is used up ({limit} {kind}); it resets at midnight Pacific time",
                            next_reset(now))
    row.used += cost
    db.commit()


def exhaust(db: Session, action: str, now: datetime | None = None) -> datetime:
    """Record that YouTube itself reported the quota as used up. Returns when to retry."""
    bucket, _ = COSTS[action]
    day = pacific_day(now)
    row = _row(db, day, bucket, lock=True)
    if row is None:
        row = YTQuotaUsage(day=day, bucket=bucket, used=0)
        db.add(row)
    row.used = max(row.used or 0, _limit(bucket))
    db.commit()
    return next_reset(now)


def usage(db: Session, now: datetime | None = None) -> dict:
    day = pacific_day(now)
    result = {"day": day, "resets_at": next_reset(now).isoformat()}
    for bucket in ("uploads", "units"):
        row = _row(db, day, bucket)
        result[bucket] = {"used": row.used if row else 0, "limit": _limit(bucket)}
    return result
