from datetime import datetime, timedelta

from icalendar import Calendar

from job_mail_assistant.app import RecordIndex, record_fields
from job_mail_assistant.apple_calendar import AppleCalendar, build_ical
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


def test_base_record_text_reads_hyperlink_url() -> None:
    record = BaseRecord(
        "rec-1",
        {"链接": {"text": "打开链接", "link": "https://example.com/assessment"}},
    )

    assert record.text("链接") == "https://example.com/assessment"
