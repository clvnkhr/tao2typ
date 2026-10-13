"""Calendar-based refresh dates for posts.

A successful check covers all milestones at or before ``last_checked_at``. A
missed milestone therefore causes a single catch-up check, rather than replaying
each missed check. Keep ``last_checked_at`` unchanged when a refresh fails so the
post remains due on the next run.
"""

from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class RefreshPlan:
    """The reasons to refresh and the earliest uncovered scheduled check.

    ``next_check_at`` may be in the past when a scheduled check is overdue. An
    unseen recent-comment event can make a post due before this date.
    """

    due: bool
    reasons: tuple[str, ...]
    next_check_at: datetime


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Scheduling requires timezone-aware datetimes")
    return value


def _utc(value: datetime) -> datetime:
    return _aware(value).astimezone(UTC)


def _calendar_month_after(value: datetime) -> datetime:
    year = value.year + (value.month == 12)
    month = value.month % 12 + 1
    return value.replace(year=year, month=month, day=min(value.day, monthrange(year, month)[1]))


def _birthday(value: datetime, year: int) -> datetime:
    # Anchor each anniversary to the original date: a leap-day post returns to
    # February 29 in leap years even after being checked on February 28.
    return value.replace(
        year=year,
        day=min(value.day, monthrange(year, value.month)[1]),
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def _next_milestone(
    published_at: datetime, last_checked_at: datetime | None
) -> tuple[datetime, str]:
    if last_checked_at is None or last_checked_at < published_at:
        return published_at, "initial"

    candidates: list[tuple[datetime, str]] = [
        (published_at + timedelta(days=7), "one-week"),
        (_calendar_month_after(published_at), "one-month"),
    ]
    checked_year = last_checked_at.astimezone(published_at.tzinfo).year
    birthday_year = max(published_at.year + 1, checked_year)
    birthday = _birthday(published_at, birthday_year)
    if birthday <= last_checked_at:
        birthday = _birthday(published_at, birthday_year + 1)
    candidates.append((birthday, "birthday"))
    return min(candidate for candidate in candidates if candidate[0] > last_checked_at)


def next_scheduled_check(published_at: datetime, last_checked_at: datetime | None) -> datetime:
    """Return the next check on the publication-based timeline, in UTC.

    Check initially, at seven days, one calendar month, one calendar year, and
    every annual birthday thereafter. Calendar arithmetic retains the source
    publication timezone; returned dates are in UTC. A date absent in the target
    month or year is clamped to its final day. Birthdays become due at midnight
    of the source calendar date so a daily scan checks them on that date. Week
    and month milestones retain the publication time.
    """
    published_at = _aware(published_at)
    if last_checked_at is not None:
        last_checked_at = _utc(last_checked_at)
    return _utc(_next_milestone(published_at, last_checked_at)[0])


def plan_refresh(
    published_at: datetime,
    last_checked_at: datetime | None,
    *,
    now: datetime,
    recent_comment: bool = False,
) -> RefreshPlan:
    """Decide whether a post needs a refresh for a batch run.

    ``recent_comment`` represents an unseen comment event, deduplicated by the
    caller. A schedule boundary is inclusive: a post is due at its check time.
    """
    published_at = _aware(published_at)
    now = _utc(now)
    if last_checked_at is not None:
        last_checked_at = _utc(last_checked_at)
    next_check_at, milestone = _next_milestone(published_at, last_checked_at)
    next_check_at = _utc(next_check_at)
    reasons: list[str] = []
    if next_check_at <= now:
        reasons.append(milestone)
    if recent_comment and published_at <= now:
        reasons.append("recent-comment")
    return RefreshPlan(bool(reasons), tuple(reasons), next_check_at)
