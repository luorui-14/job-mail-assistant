from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from test_integration import FakeCalendar, FakeFeishu, FakeMailbox, config

from job_mail_assistant.app import RecordIndex, repair_relative_deadlines
from job_mail_assistant.deadlines import SHANGHAI, resolve_time
from job_mail_assistant.models import BaseRecord, ParsedEmail, TimeExpression


@pytest.mark.parametrize("kind,unit", [("relative", "day"), ("absolute", None), ("none", None)])
def test_original_hours_override_ai_wrong_unit_or_invented_date(kind, unit):
    parsed = ParsedEmail(
        classification="action", item_type="笔试", time_type="deadline",
        time_expression=TimeExpression(
            kind=kind, relative_value=72, relative_unit=unit,
            year=2026, month=12, day=20, hour=21, minute=21,
        ),
    )
    received = datetime(2026, 10, 9, 21, 21, 36, tzinfo=SHANGHAI)
    result = resolve_time(parsed, received, body="请在收到本邮件的72小时内完成。")
    assert result.start == received + timedelta(hours=72)
    assert not result.inferred
    assert not result.needs_confirmation


@pytest.mark.parametrize("body,days", [
    ("收到邮件后3天内完成。", 3),
    ("测试链接有效期：72小时（收到本邮件的72小时内完成）。答题时长90分钟。", 3),
])
def test_deadline_duration_excludes_exam_length(body, days):
    parsed = ParsedEmail(classification="action", time_type="deadline")
    received = datetime(2026, 8, 27, 10, tzinfo=SHANGHAI)
    assert resolve_time(parsed, received, body=body).start == received + timedelta(days=days)


def test_conflicting_relative_deadlines_require_confirmation():
    parsed = ParsedEmail(
        classification="action", time_type="deadline",
        time_expression=TimeExpression(kind="relative", relative_value=72, relative_unit="day"),
    )
    result = resolve_time(
        parsed, FakeMailbox.message.received_at,
        body="链接有效期：48小时；收到邮件后72小时内完成。",
    )
    assert result.start is None
    assert result.needs_confirmation


@pytest.mark.parametrize("body", [
    "答题时长90分钟。",
    "登录后72小时内完成；链接有效期：72小时。",
    "链接有效期：72小时；2026年8月30日10:00截止。",
])
def test_ambiguous_or_other_anchored_duration_is_not_used_for_repair(body):
    message = replace(FakeMailbox.message, body=body)
    record = existing_record(message)
    assert RecordIndex([record]).is_exact_duplicate(message)


def existing_record(message=FakeMailbox.message):
    return BaseRecord("rec1", {
        "公司": "百度", "类型": "测评", "时间类型": "deadline",
        "Message-ID": message.message_id,
        "邮件接收时间": int(message.received_at.timestamp() * 1000),
        "截止/面试时间": int((message.received_at + timedelta(days=72)).timestamp() * 1000),
        "Calendar Event ID": "jma-rec1@job-mail-assistant", "Calendar 状态": "created",
        "需要人工确认": False,
    })


def test_wrong_relative_deadline_is_reprocessed_then_stays_duplicate():
    message = FakeMailbox.message
    record = existing_record()
    index = RecordIndex([record])
    assert not index.is_exact_duplicate(message)
    resolved = resolve_time(
        ParsedEmail(classification="action", time_type="deadline"),
        message.received_at, body=message.body,
    )
    match = index.match(message, ParsedEmail(classification="action"), resolved)
    assert match.record is record
    assert not match.duplicate
    record.fields["截止/面试时间"] = int(resolved.start.timestamp() * 1000)
    assert index.is_exact_duplicate(message)


def test_old_thread_source_cannot_overwrite_newer_deadline():
    record = existing_record()
    record.fields["邮件接收时间"] += 86400000
    assert RecordIndex([record]).is_exact_duplicate(FakeMailbox.message)


@pytest.mark.parametrize("dry_run", [True, False])
def test_targeted_repair_preserves_identity_cursor_and_report_and_updates_calendar(
    monkeypatch, dry_run
):
    record = existing_record()
    other = existing_record()
    other.record_id = "rec-other"
    other.fields["公司"] = "其他公司"
    original_other = dict(other.fields)
    FakeFeishu.records = [record, other]
    FakeFeishu.cursor = datetime(2026, 8, 28, 8, tzinfo=SHANGHAI)
    original_cursor = FakeFeishu.cursor
    FakeMailbox.reports = []
    FakeCalendar.calls = 0
    events = []

    class CaptureCalendar(FakeCalendar):
        def upsert_event(self, **kwargs):
            events.append(kwargs)
            return "updated"

    monkeypatch.setattr("job_mail_assistant.app.QQMailbox", FakeMailbox)
    monkeypatch.setattr("job_mail_assistant.app.FeishuClient", FakeFeishu)
    monkeypatch.setattr("job_mail_assistant.app.AppleCalendar", CaptureCalendar)
    assert repair_relative_deadlines(config(), company="百度", dry_run=dry_run) == 0
    assert FakeMailbox.reports == []
    assert FakeFeishu.cursor == original_cursor
    assert len(FakeFeishu.records) == 2
    assert other.fields == original_other
    if dry_run:
        assert not events
        assert record.fields == existing_record().fields
    else:
        corrected = FakeMailbox.message.received_at + timedelta(hours=72)
        assert record.fields["截止/面试时间"] == int(corrected.timestamp() * 1000)
        assert events[0]["uid"] == "jma-rec1@job-mail-assistant"
        assert events[0]["start"] == corrected
        assert events[0]["end"] == corrected + timedelta(minutes=15)
        assert repair_relative_deadlines(config(), company="百度") == 0
        assert len(events) == 1
