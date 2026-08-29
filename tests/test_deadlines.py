from datetime import datetime, timedelta

import pytest
from chinese_calendar import is_workday

from job_mail_assistant.deadlines import SHANGHAI, resolve_time
from job_mail_assistant.models import ParsedEmail, TimeExpression


def action(expr: TimeExpression, *, time_type: str = "deadline") -> ParsedEmail:
    return ParsedEmail(
        classification="action",
        company="测试公司",
        position="测试岗位",
        item_type="测评",
        time_type=time_type,
        time_expression=expr,
    )


@pytest.mark.parametrize("hours", [48, 72])
def test_relative_hours(hours: int) -> None:
    received = datetime(2026, 8, 27, 16, 43, tzinfo=SHANGHAI)
    result = resolve_time(
        action(TimeExpression(kind="relative", relative_value=hours, relative_unit="hour")),
        received,
    )
    assert result.start == received + timedelta(hours=hours)


def test_three_days_are_exact_24_hour_periods() -> None:
    received = datetime(2026, 8, 27, 16, 43, tzinfo=SHANGHAI)
    result = resolve_time(
        action(TimeExpression(kind="relative", relative_value=3, relative_unit="day")),
        received,
    )
    assert result.start == datetime(2026, 8, 30, 16, 43, tzinfo=SHANGHAI)


def test_five_workdays_start_counting_next_day() -> None:
    received = datetime(2026, 8, 27, 18, 32, tzinfo=SHANGHAI)
    result = resolve_time(
        action(TimeExpression(kind="relative", relative_value=5, relative_unit="workday")),
        received,
    )
    assert result.start == datetime(2026, 9, 3, 18, 32, tzinfo=SHANGHAI)


def test_calendar_knows_holiday_and_makeup_workday() -> None:
    assert not is_workday(datetime(2025, 1, 29).date())
    assert is_workday(datetime(2025, 1, 26).date())


def test_absolute_time_without_year_uses_safe_current_year() -> None:
    received = datetime(2026, 8, 27, 16, 43, tzinfo=SHANGHAI)
    result = resolve_time(
        action(TimeExpression(kind="absolute", month=8, day=31, hour=23, minute=59)),
        received,
    )
    assert result.start == datetime(2026, 8, 31, 23, 59, tzinfo=SHANGHAI)
    assert not result.inferred


def test_current_week_friday() -> None:
    received = datetime(2026, 8, 24, 10, 0, tzinfo=SHANGHAI)
    result = resolve_time(
        action(TimeExpression(kind="weekday", week_offset=0, weekday=5, hour=18)),
        received,
    )
    assert result.start == datetime(2026, 8, 28, 18, 0, tzinfo=SHANGHAI)


def test_date_only_deadline_defaults_to_2359() -> None:
    received = datetime(2026, 8, 27, 10, 0, tzinfo=SHANGHAI)
    result = resolve_time(
        action(TimeExpression(kind="absolute", month=8, day=31)), received
    )
    assert result.start == datetime(2026, 8, 31, 23, 59, tzinfo=SHANGHAI)
    assert result.inferred


def test_date_only_interview_needs_confirmation() -> None:
    received = datetime(2026, 8, 27, 10, 0, tzinfo=SHANGHAI)
    result = resolve_time(
        action(TimeExpression(kind="absolute", month=8, day=31), time_type="fixed"),
        received,
    )
    assert result.start is None
    assert result.needs_confirmation


def test_ambiguous_time_is_never_guessed() -> None:
    result = resolve_time(
        action(TimeExpression(kind="ambiguous")),
        datetime(2026, 8, 27, 10, 0, tzinfo=SHANGHAI),
    )
    assert result.start is None
    assert result.needs_confirmation


def test_workday_outside_dataset_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_2027(day):
        if day.year >= 2027:
            raise NotImplementedError
        return True

    monkeypatch.setattr("job_mail_assistant.deadlines.is_workday", no_2027)
    result = resolve_time(
        action(TimeExpression(kind="relative", relative_value=5, relative_unit="workday")),
        datetime(2026, 12, 29, 10, 0, tzinfo=SHANGHAI),
    )
    assert result.start is None
    assert result.needs_confirmation
    assert "不覆盖" in (result.reason or "")
