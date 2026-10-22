from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from test_integration import FakeCalendar, FakeFeishu, FakeMailbox, config

from job_mail_assistant.ai_parser import AIParser
from job_mail_assistant.app import RecordIndex, record_fields, repair_deadlines
from job_mail_assistant.deadlines import SHANGHAI, assessment_window_from_text, resolve_time
from job_mail_assistant.models import BaseRecord, ParsedEmail, TimeExpression

START = datetime(2026, 11, 5, 9, tzinfo=SHANGHAI)
END = datetime(2026, 11, 6, 22, 30, tzinfo=SHANGHAI)
BODY = (
    "开始时间：2026-11-05 09:00 星期四\n"
    "测试时长：75 分钟\n结束时间：2026-11-06 22:30 星期五"
)


def window_mail():
    return replace(FakeMailbox.message, body=BODY)


def window_record():
    message = window_mail()
    return BaseRecord("rec-window", {
        "公司": "示例企业", "类型": "笔试", "时间类型": "fixed",
        "Message-ID": message.message_id,
        "邮件接收时间": int(message.received_at.timestamp() * 1000),
        "截止/面试时间": int(START.timestamp() * 1000),
        "结束时间": int(END.timestamp() * 1000),
        "Calendar Event ID": "jma-rec-window@job-mail-assistant",
        "Calendar 状态": "created", "需要人工确认": False,
    })


@pytest.mark.parametrize("time_type", ["deadline", "none", "fixed"])
@pytest.mark.parametrize("kind", ["absolute", "none"])
def test_flexible_exam_uses_window_end_and_discards_ai_event_end(time_type, kind):
    parsed = ParsedEmail(
        classification="action", item_type="笔试", time_type=time_type,
        time_expression=TimeExpression(kind=kind, year=2026, month=11, day=5, hour=9),
        end_time_expression=TimeExpression(kind="absolute", month=11, day=6, hour=22, minute=30),
    )
    message = window_mail()
    result = resolve_time(parsed, message.received_at, body=message.body)
    assert result.start == END
    assert result.end is None
    assert not result.inferred
    assert not result.needs_confirmation
    assert parsed.time_type == "deadline"
    fields = record_fields(message, parsed, result, existing=window_record(), now=START)
    assert fields["时间类型"] == "deadline"
    assert fields["结束时间"] is None


def test_window_parser_handles_whitespace_and_weekday_before_time():
    body = "开始时间:\n2026年11月5日 星期四 09:00\n结束时间:2026年11月6日 周五 22:30"
    assert assessment_window_from_text(body) == (START, END)


@pytest.mark.parametrize("body", [
    BODY + "；结束时间：2026-11-07 22:30",
    BODY.replace("2026-11-06", "2026-11-04"),
    BODY.replace("2026-11-06", "2026-11-31"),
    BODY.replace("2026-11-06", "11-06"),
    BODY + "；结束时间：待定",
])
def test_nonunique_incomplete_or_invalid_window_is_not_repaired(body):
    assert assessment_window_from_text(body) is None
    assert RecordIndex([window_record()]).is_exact_duplicate(replace(window_mail(), body=body))


@pytest.mark.parametrize("item_type,body", [
    ("其他面试", BODY),
    ("笔试", BODY.replace("2026-11-06 22:30", "2026-11-05 10:15")),
    ("笔试", BODY + "。请准时参加统一开考。"),
    ("笔试", BODY.replace("测试时长：75 分钟", "")),
])
def test_fixed_interview_or_exam_keeps_actual_start(item_type, body):
    parsed = ParsedEmail(
        classification="action", item_type=item_type, time_type="fixed",
        time_expression=TimeExpression(kind="absolute", year=2026, month=11, day=5, hour=9),
    )
    result = resolve_time(parsed, window_mail().received_at, body=body)
    assert result.start == START
    assert parsed.time_type == "fixed"


def test_parser_normalizes_window_and_keeps_other_confirmation(monkeypatch):
    parser = AIParser("key", "https://ai.example.com/v1", "model")
    content = ParsedEmail(
        classification="action", item_type="笔试", time_type="fixed",
        needs_confirmation=True, confirmation_reason="无法确定截止时间；公司名称不确定",
    ).model_dump_json()
    monkeypatch.setattr(parser, "_request", lambda *_: content)
    parsed = parser.parse(window_mail())
    assert parsed.time_type == "deadline"
    assert parsed.needs_confirmation
    assert parsed.confirmation_reason == "公司名称不确定"
    assert resolve_time(parsed, window_mail().received_at, body=BODY).start == END


def test_repairable_window_reenters_dedup_after_correction():
    record = window_record()
    index = RecordIndex([record])
    assert not index.is_exact_duplicate(window_mail())
    record.fields["截止/面试时间"] = int(END.timestamp() * 1000)
    assert index.is_exact_duplicate(window_mail())


@pytest.mark.parametrize("field,value", [
    ("邮件接收时间", int(START.timestamp() * 1000)),
    ("截止/面试时间", int((START + timedelta(hours=1)).timestamp() * 1000)),
])
def test_window_does_not_replace_newer_thread_or_other_selected_time(field, value):
    record = window_record()
    record.fields[field] = value
    assert RecordIndex([record]).is_exact_duplicate(window_mail())


@pytest.mark.parametrize("dry_run", [True, False])
def test_targeted_window_repair_updates_same_calendar_without_report_or_cursor(
    monkeypatch, dry_run
):
    record = window_record()
    original = dict(record.fields)
    FakeFeishu.records = [record]
    FakeFeishu.cursor = START
    FakeMailbox.reports = []
    events = []

    class WindowMailbox(FakeMailbox):
        message = window_mail()

    class CaptureCalendar(FakeCalendar):
        def upsert_event(self, **kwargs):
            events.append(kwargs)
            return "updated"

    monkeypatch.setattr("job_mail_assistant.app.QQMailbox", WindowMailbox)
    monkeypatch.setattr("job_mail_assistant.app.FeishuClient", FakeFeishu)
    monkeypatch.setattr("job_mail_assistant.app.AppleCalendar", CaptureCalendar)
    assert repair_deadlines(config(), company="示例企业", dry_run=dry_run) == 0
    assert FakeMailbox.reports == []
    assert FakeFeishu.cursor == START
    assert len(FakeFeishu.records) == 1
    if dry_run:
        assert record.fields == original
        assert events == []
    else:
        assert record.fields["截止/面试时间"] == int(END.timestamp() * 1000)
        assert record.fields["时间类型"] == "deadline"
        assert record.fields["结束时间"] is None
        assert events[0]["uid"] == "jma-rec-window@job-mail-assistant"
        assert events[0]["start"] == END
        assert events[0]["end"] == END + timedelta(minutes=15)
        assert repair_deadlines(config(), company="示例企业") == 0
        assert len(events) == 1
