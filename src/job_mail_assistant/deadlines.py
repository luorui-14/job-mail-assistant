from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from chinese_calendar import is_workday

from .models import ParsedEmail, ResolvedTime, TimeExpression

SHANGHAI = ZoneInfo("Asia/Shanghai")


class TimeResolutionError(ValueError):
    pass


EXPLICIT_DATETIME_RE = re.compile(
    r"(?P<year>\d{4})\s*(?:年|[-/.])\s*"
    r"(?P<month>\d{1,2})\s*(?:月|[-/.])\s*"
    r"(?P<day>\d{1,2})\s*(?:日)?\s+"
    r"(?P<hour>\d{1,2})\s*:\s*(?P<minute>\d{2})"
    r"(?:\s*:\s*\d{2})?"
)
RANGE_END_TIME_RE = re.compile(
    r"(?:--|—|–|至|到|~|～)\s*"
    r"(?P<hour>\d{1,2})\s*:\s*(?P<minute>\d{2})"
    r"(?:\s*:\s*\d{2})?"
)


def _aware_shanghai(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise TimeResolutionError("received time must be timezone-aware")
    return value.astimezone(SHANGHAI)


def _add_workdays(received: datetime, count: int) -> datetime:
    if count <= 0:
        raise TimeResolutionError("workday count must be positive")
    current = received.date()
    found = 0
    for _ in range(370):
        current += timedelta(days=1)
        try:
            working = is_workday(current)
        except Exception as exc:
            raise TimeResolutionError(
                f"中国工作日历不覆盖 {current.isoformat()}，请人工确认"
            ) from exc
        if working:
            found += 1
            if found == count:
                return datetime.combine(current, received.timetz()).astimezone(SHANGHAI)
    raise TimeResolutionError("workday calculation exceeded safety limit")


def _year_for_month_day(received: datetime, month: int, day: int) -> int:
    candidate = date(received.year, month, day)
    if candidate >= received.date() - timedelta(days=1):
        return received.year
    if received.month >= 11 and month <= 2:
        return received.year + 1
    raise TimeResolutionError("日期缺少年份且按当前年度已明显过期")


def _resolve_absolute(
    expr: TimeExpression,
    received: datetime,
    *,
    time_type: str,
    inherited_date: date | None = None,
) -> tuple[datetime, bool]:
    month = expr.month or (inherited_date.month if inherited_date else None)
    day = expr.day or (inherited_date.day if inherited_date else None)
    if month is None or day is None:
        raise TimeResolutionError("明确日期缺少月或日")
    year = expr.year
    if year is None:
        if inherited_date and expr.month is None and expr.day is None:
            year = inherited_date.year
        else:
            year = _year_for_month_day(received, month, day)

    inferred = False
    hour = expr.hour
    minute = expr.minute
    if hour is None:
        if time_type == "deadline":
            hour, minute, inferred = 23, 59, True
        else:
            raise TimeResolutionError("固定事项只有日期，没有具体开始时间")
    if minute is None:
        minute = 0
    try:
        resolved = datetime(year, month, day, hour, minute, tzinfo=SHANGHAI)
    except ValueError as exc:
        raise TimeResolutionError(f"无效日期时间：{exc}") from exc
    return resolved, inferred


def _resolve_weekday(expr: TimeExpression, received: datetime) -> datetime:
    if expr.weekday is None or expr.hour is None:
        raise TimeResolutionError("星期表达缺少星期或具体时间")
    minute = expr.minute or 0
    monday = received.date() - timedelta(days=received.weekday())
    target_date = monday + timedelta(days=7 * (expr.week_offset or 0) + expr.weekday - 1)
    target = datetime.combine(target_date, time(expr.hour, minute), tzinfo=SHANGHAI)
    if target < received:
        raise TimeResolutionError("星期表达解析后已早于邮件接收时间")
    return target


def _resolve_expression(
    expr: TimeExpression,
    received: datetime,
    *,
    time_type: str,
    inherited_date: date | None = None,
) -> tuple[datetime, bool]:
    if expr.kind == "relative":
        value = expr.relative_value or 0
        if value <= 0:
            raise TimeResolutionError("相对时间必须为正数")
        if expr.relative_unit == "hour":
            return received + timedelta(hours=value), False
        if expr.relative_unit == "day":
            return received + timedelta(hours=value * 24), False
        if expr.relative_unit == "workday":
            return _add_workdays(received, value), False
    if expr.kind == "absolute":
        return _resolve_absolute(
            expr, received, time_type=time_type, inherited_date=inherited_date
        )
    if expr.kind == "weekday":
        return _resolve_weekday(expr, received), False
    if expr.kind == "ambiguous":
        raise TimeResolutionError("时间表达存在歧义")
    raise TimeResolutionError("邮件没有提供可确定的时间")


def _explicit_datetimes_from_text(value: str | None) -> tuple[datetime, datetime | None] | None:
    """Recover a complete explicit datetime that the AI represented incompletely."""
    if not value:
        return None
    match = EXPLICIT_DATETIME_RE.search(value)
    if not match:
        return None
    try:
        start = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
            tzinfo=SHANGHAI,
        )
    except ValueError:
        return None
    end_match = RANGE_END_TIME_RE.search(value, match.end())
    if not end_match:
        return start, None
    try:
        end = datetime.combine(
            start.date(),
            time(int(end_match.group("hour")), int(end_match.group("minute"))),
            tzinfo=SHANGHAI,
        )
    except ValueError:
        return start, None
    return start, end if end > start else None


def resolve_time(parsed: ParsedEmail, received_at: datetime) -> ResolvedTime:
    received = _aware_shanghai(received_at)
    if parsed.classification != "action":
        return ResolvedTime(None, None, False, False)
    try:
        start, inferred = _resolve_expression(
            parsed.time_expression, received, time_type=parsed.time_type
        )
    except TimeResolutionError as exc:
        recovered = _explicit_datetimes_from_text(parsed.original_time_text)
        if not recovered:
            return ResolvedTime(None, None, False, True, str(exc))
        start, recovered_end = recovered
        inferred = False
    else:
        recovered_end = None

    end = recovered_end
    if parsed.end_time_expression:
        try:
            end, _ = _resolve_expression(
                parsed.end_time_expression,
                received,
                time_type="fixed",
                inherited_date=start.date(),
            )
            if end <= start:
                raise TimeResolutionError("结束时间不晚于开始时间")
        except TimeResolutionError:
            end = None

    return ResolvedTime(
        start=start,
        end=end,
        inferred=inferred,
        needs_confirmation=parsed.needs_confirmation,
        reason=parsed.confirmation_reason if parsed.needs_confirmation else None,
    )
