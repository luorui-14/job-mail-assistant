from datetime import datetime, timedelta

from icalendar import Calendar

from job_mail_assistant.app import (
    RecordIndex,
    _is_trackable_action,
    delete_calendar_events,
    record_fields,
)
from job_mail_assistant.apple_calendar import AppleCalendar, build_ical
from job_mail_assistant.config import Config
from job_mail_assistant.deadlines import SHANGHAI
from job_mail_assistant.models import (
    BaseRecord,
    MailMessage,
    ParsedEmail,
    ResolvedTime,
    TimeExpression,
)


def mail(message_id: str = "mail@example.com") -> MailMessage:
    return MailMessage(
        uid="1",
        message_id=message_id,
        fingerprint="f" * 64,
        subject="测评",
        sender="hr@example.com",
        received_at=datetime(2026, 8, 27, 10, tzinfo=SHANGHAI),
        body="测评",
        urls=[],
    )


def parsed() -> ParsedEmail:
    return ParsedEmail(
        classification="action",
        company="百度",
        position="产品经理",
        item_type="测评",
        time_type="deadline",
        time_expression=TimeExpression(kind="none"),
    )


def test_same_message_id_is_duplicate() -> None:
    record = BaseRecord("rec1", {"Message-ID": "mail@example.com"})
    match = RecordIndex([record]).match(
        mail(), parsed(), ResolvedTime(None, None, False, True)
    )
    assert match.duplicate
    assert match.record is record


def test_incomplete_link_record_is_reprocessed_for_repair() -> None:
    record = BaseRecord(
        "rec1",
        {
            "Message-ID": "mail@example.com",
            "截止/面试时间": int(
                datetime(2026, 8, 30, 10, tzinfo=SHANGHAI).timestamp() * 1000
            ),
            "链接": "",
            "确认说明": "候选链接[0]可能为确认入口，但无法可靠确定",
        },
    )
    index = RecordIndex([record])

    assert not index.is_exact_duplicate(mail())
    match = index.match(
        mail(),
        parsed(),
        ResolvedTime(datetime(2026, 8, 30, 10, tzinfo=SHANGHAI), None, False, False),
    )

    assert not match.duplicate
    assert match.record is record


def test_thread_reference_updates_existing_record() -> None:
    record = BaseRecord("rec1", {"Message-ID": "parent@example.com"})
    child = mail("child@example.com")
    child = MailMessage(**{**child.__dict__, "references": {"parent@example.com"}})
    match = RecordIndex([record]).match(
        child,
        parsed(),
        ResolvedTime(datetime(2026, 8, 30, tzinfo=SHANGHAI), None, False, False),
    )
    assert not match.duplicate
    assert match.record is record


def test_exact_event_matches_when_position_is_missing() -> None:
    start = datetime(2026, 8, 30, 10, tzinfo=SHANGHAI)
    record = BaseRecord(
        "rec1",
        {
            "公司": "OPPO",
            "岗位": "",
            "类型": "AI面试",
            "截止/面试时间": int(start.timestamp() * 1000),
        },
    )
    message = mail("reminder@example.com")
    extraction = parsed()
    extraction.company = "OPPO"
    extraction.position = None
    extraction.item_type = "AI面试"

    match = RecordIndex([record]).match(
        message, extraction, ResolvedTime(start, None, False, False)
    )

    assert not match.duplicate
    assert match.record is record


def test_human_interview_deadline_is_not_a_trackable_arrangement() -> None:
    message = mail()
    message = MailMessage(
        **{
            **message.__dict__,
            "subject": "校园招聘-面试邀请",
            "body": "请在截止时间前确认是否参加。",
        }
    )
    extraction = parsed()
    extraction.item_type = "其他面试"
    extraction.time_type = "deadline"

    assert not _is_trackable_action(
        message, extraction, ResolvedTime(None, None, False, False)
    )


def test_human_interview_fixed_time_is_trackable() -> None:
    message = mail()
    message = MailMessage(
        **{
            **message.__dict__,
            "subject": "校园招聘-面试安排",
            "body": "面试时间：2026-09-10 10:00。",
        }
    )
    extraction = parsed()
    extraction.item_type = "其他面试"
    extraction.time_type = "fixed"

    assert _is_trackable_action(
        message,
        extraction,
        ResolvedTime(
            datetime(2026, 9, 10, 10, tzinfo=SHANGHAI), None, False, False
        ),
    )


def test_ical_has_24_hour_display_alarm() -> None:
    start = datetime(2026, 8, 30, 16, 43, tzinfo=SHANGHAI)
    data = build_ical(
        uid="jma-rec1@job-mail-assistant",
        title="百度｜产品经理｜测评截止",
        start=start,
        end=start + timedelta(minutes=15),
        description="测试",
        url="https://exam.example.com",
    )
    calendar = Calendar.from_ical(data)
    event = next(component for component in calendar.walk() if component.name == "VEVENT")
    alarm = next(component for component in event.subcomponents if component.name == "VALARM")
    assert alarm["TRIGGER"].dt == timedelta(hours=-24)
    assert str(event["UID"]) == "jma-rec1@job-mail-assistant"


def test_icloud_upsert_uses_deterministic_put_without_uid_report() -> None:
    class FakeRemoteCalendar:
        def __init__(self) -> None:
            self.saved: list[bytes] = []

        def event_by_uid(self, _: str) -> None:
            raise AssertionError("iCloud UID REPORT must not be used")

        def save_event(self, data: bytes) -> None:
            self.saved.append(data)

    remote = FakeRemoteCalendar()
    calendar = AppleCalendar.__new__(AppleCalendar)
    calendar.calendar = remote
    start = datetime(2026, 8, 30, 9, tzinfo=SHANGHAI)

    result = calendar.upsert_event(
        uid="jma-rec1@job-mail-assistant",
        title="测试事件",
        start=start,
        end=start + timedelta(hours=1),
        description="测试",
        url=None,
    )

    assert result == "created"
    assert len(remote.saved) == 1
    payload = Calendar.from_ical(remote.saved[0])
    event = next(component for component in payload.walk() if component.name == "VEVENT")
    assert str(event["UID"]) == "jma-rec1@job-mail-assistant"


def test_delete_calendar_events_uses_record_derived_uids(monkeypatch) -> None:
    deleted: list[str] = []

    class FakeCalendar:
        def __init__(self, *_: object) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def delete_event(self, uid: str) -> None:
            deleted.append(uid)

    monkeypatch.setattr("job_mail_assistant.app.AppleCalendar", FakeCalendar)
    config = Config(
        qq_email="me@example.com",
        qq_auth_code="secret",
        feishu_app_id="app",
        feishu_app_secret="secret",
        feishu_wiki_url="https://example.feishu.cn/wiki/token",
        icloud_username="me@icloud.com",
        icloud_app_password="secret",
        ai_api_key="secret",
        ai_base_url="https://ai.example.com/v1",
        ai_model="model",
    )

    assert delete_calendar_events(config, ["recOne", "recOne", "recTwo2"]) == 0
    assert deleted == [
        "jma-recOne@job-mail-assistant",
        "jma-recTwo2@job-mail-assistant",
    ]


def test_url_field_uses_feishu_hyperlink_shape() -> None:
    message = mail()
    message.urls.append("https://example.com/assessment")
    extraction = parsed()
    extraction.action_url_index = 0

    fields = record_fields(
        message,
        extraction,
        ResolvedTime(None, None, False, True),
        now=datetime(2026, 8, 28, 10, tzinfo=SHANGHAI),
    )

    assert fields["链接"] == {
        "text": "打开链接",
        "link": "https://example.com/assessment",
    }


def test_record_does_not_write_misleading_qq_mail_homepage_link() -> None:
    fields = record_fields(
        mail(),
        parsed(),
        ResolvedTime(None, None, False, True),
        now=datetime(2026, 8, 28, 10, tzinfo=SHANGHAI),
    )

    assert "原邮件" not in fields


def test_base_record_text_reads_hyperlink_url() -> None:
    record = BaseRecord(
        "rec-1",
        {"链接": {"text": "打开链接", "link": "https://example.com/assessment"}},
    )

    assert record.text("链接") == "https://example.com/assessment"


def test_missing_position_is_stored_without_confirmation() -> None:
    extraction = parsed()
    extraction.position = None
    extraction.needs_confirmation = True
    extraction.confirmation_reason = "岗位名称无法确定"
    deadline = datetime(2026, 8, 30, 10, tzinfo=SHANGHAI)

    fields = record_fields(
        mail(),
        extraction,
        ResolvedTime(deadline, None, False, True, "岗位名称无法确定"),
        now=datetime(2026, 8, 28, 10, tzinfo=SHANGHAI),
    )

    assert fields["岗位"] == ""
    assert fields["需要人工确认"] is False
    assert fields["确认说明"] == ""
