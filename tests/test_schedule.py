from datetime import UTC, datetime, timedelta, timezone

import pytest

from tao2typ.schedule import next_scheduled_check, plan_refresh


def instant(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def test_unchecked_post_is_due_at_publication() -> None:
    published = instant(2026, 10, 1)
    assert next_scheduled_check(published, None) == published
    assert not plan_refresh(published, None, now=published - timedelta(seconds=1)).due
    plan = plan_refresh(published, None, now=published)
    assert plan.due
    assert plan.reasons == ("initial",)


def test_week_month_and_first_birthday_follow_publication() -> None:
    published = instant(2026, 10, 1)
    week = instant(2026, 10, 8)
    month = instant(2026, 11, 1)
    year = instant(2027, 10, 1, 0)
    assert next_scheduled_check(published, published) == week
    assert next_scheduled_check(published, week) == month
    assert next_scheduled_check(published, month) == year
    assert next_scheduled_check(published, year) == instant(2028, 10, 1, 0)


@pytest.mark.parametrize(
    ("published", "expected"),
    [
        (instant(2026, 1, 31), instant(2026, 2, 28)),
        (instant(2024, 1, 31), instant(2024, 2, 29)),
        (instant(2026, 12, 31), instant(2027, 1, 31)),
        (instant(2026, 3, 31), instant(2026, 4, 30)),
    ],
)
def test_month_uses_calendar_arithmetic(published: datetime, expected: datetime) -> None:
    assert next_scheduled_check(published, published + timedelta(days=7)) == expected


def test_leap_day_birthdays_return_to_original_date() -> None:
    published = instant(2020, 2, 29)
    assert next_scheduled_check(published, instant(2020, 3, 29)) == instant(2021, 2, 28, 0)
    assert next_scheduled_check(published, instant(2023, 2, 28)) == instant(2024, 2, 29, 0)
    assert next_scheduled_check(published, instant(2024, 2, 28)) == instant(2024, 2, 29, 0)
    assert next_scheduled_check(published, instant(2024, 2, 29)) == instant(2025, 2, 28, 0)


def test_old_post_is_placed_on_next_birthday_after_initial_batch() -> None:
    published = instant(2007, 4, 15)
    assert plan_refresh(published, None, now=instant(2026, 10, 13)).due
    checked = instant(2026, 10, 13)
    assert next_scheduled_check(published, checked) == instant(2027, 4, 15, 0)


def test_old_post_checked_before_this_year_birthday_is_due_on_birthday() -> None:
    published = instant(2007, 10, 15)
    checked = instant(2026, 10, 13)
    birthday = instant(2026, 10, 15, 0)
    assert next_scheduled_check(published, checked) == birthday
    assert not plan_refresh(published, checked, now=birthday - timedelta(seconds=1)).due
    assert plan_refresh(published, checked, now=birthday).reasons == ("birthday",)


def test_missed_milestones_require_one_catch_up_check() -> None:
    published = instant(2020, 5, 10)
    now = instant(2026, 10, 13)
    assert plan_refresh(published, published, now=now).due
    # Persisting one successful check covers all of the missed dates.
    assert next_scheduled_check(published, now) == instant(2027, 5, 10, 0)
    assert not plan_refresh(published, now, now=now).due


def test_failed_refresh_remains_due() -> None:
    published = instant(2026, 10, 1)
    checked = published
    assert plan_refresh(published, checked, now=instant(2026, 10, 8)).due
    assert plan_refresh(published, checked, now=instant(2026, 10, 9)).due


def test_unseen_recent_comment_triggers_early_refresh() -> None:
    published = instant(2007, 4, 15)
    checked = instant(2026, 10, 12)
    now = instant(2026, 10, 13)
    plan = plan_refresh(published, checked, now=now, recent_comment=True)
    assert plan.due
    assert plan.reasons == ("recent-comment",)
    assert plan.next_check_at == instant(2027, 4, 15, 0)
    assert not plan_refresh(published, now, now=now, recent_comment=False).due


def test_recent_comment_and_scheduled_reasons_are_both_reported() -> None:
    published = instant(2026, 10, 1)
    plan = plan_refresh(published, published, now=instant(2026, 10, 8), recent_comment=True)
    assert plan.reasons == ("one-week", "recent-comment")


def test_future_publication_stays_not_due_even_with_comment_signal() -> None:
    published = instant(2026, 10, 15)
    assert not plan_refresh(published, None, now=instant(2026, 10, 13), recent_comment=True).due


def test_check_before_publication_requires_initial_refresh() -> None:
    published = instant(2026, 10, 1)
    checked = published - timedelta(days=1)
    assert next_scheduled_check(published, checked) == published


def test_inputs_are_normalized_to_utc() -> None:
    published = datetime(2026, 10, 1, 13, tzinfo=timezone(timedelta(hours=1)))
    assert next_scheduled_check(published, published) == instant(2026, 10, 8)
    assert next_scheduled_check(published, published).tzinfo is UTC


def test_calendar_arithmetic_preserves_source_date_across_utc_boundary() -> None:
    published = datetime(2024, 2, 28, 23, tzinfo=timezone(timedelta(hours=-4)))
    month = datetime(2024, 3, 29, 3, tzinfo=UTC)
    assert next_scheduled_check(published, published + timedelta(days=7)) == month
    # The source birthday is February 28 even though publication was February 29
    # in UTC. The anniversary becomes due at midnight of the source date.
    assert next_scheduled_check(published, month) == datetime(2025, 2, 28, 4, tzinfo=UTC)


def test_birthday_year_uses_publication_timezone() -> None:
    published = datetime(2007, 1, 1, 1, tzinfo=timezone(timedelta(hours=14)))
    checked = datetime(2026, 12, 31, 22, tzinfo=UTC)
    assert next_scheduled_check(published, checked) == datetime(2027, 12, 31, 10, tzinfo=UTC)


def test_early_daily_scan_checks_birthday_before_publication_hour() -> None:
    published = datetime(2020, 10, 13, 23, 45, tzinfo=UTC)
    checked = instant(2026, 10, 12)
    scan = datetime(2026, 10, 13, 6, 23, tzinfo=UTC)
    assert plan_refresh(published, checked, now=scan).reasons == ("birthday",)
    assert next_scheduled_check(published, scan) == instant(2027, 10, 13, 0)


@pytest.mark.parametrize("argument", ["published_at", "last_checked_at", "now"])
def test_naive_datetimes_are_rejected(argument: str) -> None:
    naive = datetime(2026, 10, 1)
    published = naive if argument == "published_at" else instant(2026, 10, 1)
    checked = naive if argument == "last_checked_at" else instant(2026, 10, 1)
    now = naive if argument == "now" else instant(2026, 10, 8)
    with pytest.raises(ValueError, match="timezone-aware"):
        plan_refresh(published, checked, now=now)
